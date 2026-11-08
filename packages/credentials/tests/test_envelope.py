import json
import logging
from dataclasses import asdict, replace
from io import StringIO
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from labelloop_credentials._worker import SecretRedactionFilter, WorkerUnwrapper
from labelloop_credentials.envelope import (
    CredentialError,
    PublicSealer,
    SecretType,
    public_view,
)
from labelloop_credentials.public_keys import PublicKeyRing
from labelloop_worker_llm.credentials import LLMCredentials
from labelloop_worker_platform.credentials import PlatformCredentials

# Inert test input, not a real credential or a saved fixture key.
PLAINTEXT = b"fixture-only-provider-value-7239"


def unwrap(key_material, kind=SecretType.PLATFORM):
    return WorkerUnwrapper(kind, key_material[kind][0])


def test_standard_round_trip_randomized_per_secret(sealer, scope, key_material):
    identity = scope.identity(uuid4(), 1)
    first = sealer.seal(identity, PLAINTEXT)
    second = sealer.seal(identity, PLAINTEXT)
    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext
    assert first.wrapped_key != second.wrapped_key
    assert len(first.wrapped_key) == 384
    assert len(first.nonce) == 12
    assert unwrap(key_material).unwrap(first, identity).use(lambda key: key == PLAINTEXT)
    assert PLAINTEXT not in first.ciphertext
    assert PLAINTEXT.decode() not in repr(first)
    view = asdict(public_view(first))
    assert set(view) == {"identity", "fingerprint"}
    assert view["fingerprint"] == {
        "last_four": "7239",
        "sha256_prefix": first.fingerprint.sha256_prefix,
    }


@pytest.mark.parametrize(
    "field", ["project_id", "workspace_id", "connection_id", "version", "secret_type"]
)
def test_identity_substitution_fails_even_with_matching_expected(
    field, sealer, scope, key_material
):
    identity = scope.identity(uuid4(), 1)
    envelope = sealer.seal(identity, PLAINTEXT)
    value = 2 if field == "version" else SecretType.LLM if field == "secret_type" else uuid4()
    substituted = replace(identity, **{field: value})
    with pytest.raises(CredentialError, match="^credential operation refused$"):
        unwrap(key_material).unwrap(replace(envelope, identity=substituted), substituted)


@pytest.mark.parametrize(
    "field",
    ["nonce", "ciphertext", "wrapped_key", "fingerprint", "wrapping_key_id", "format_version"],
)
def test_tampering_fails(field, sealer, scope, key_material):
    identity = scope.identity(uuid4(), 1)
    envelope = sealer.seal(identity, PLAINTEXT)
    if field == "fingerprint":
        value = replace(envelope.fingerprint, last_four="xxxx")
    elif field == "wrapping_key_id":
        value = "0" * 64
    elif field == "format_version":
        value = 2
    else:
        original = getattr(envelope, field)
        value = bytes([original[0] ^ 1]) + original[1:]
    with pytest.raises(CredentialError):
        unwrap(key_material).unwrap(replace(envelope, **{field: value}), identity)


def test_wrong_private_key_and_type_fail(sealer, scope, key_material):
    identity = scope.identity(uuid4(), 1)
    envelope = sealer.seal(identity, PLAINTEXT)
    with pytest.raises(CredentialError):
        WorkerUnwrapper(SecretType.PLATFORM, key_material[SecretType.LLM][0]).unwrap(
            envelope, identity
        )
    with pytest.raises(CredentialError):
        unwrap(key_material, SecretType.LLM).unwrap(envelope, identity)
    with pytest.raises(CredentialError):
        PublicSealer(SecretType.LLM, key_material[SecretType.LLM][1]).seal(identity, PLAINTEXT)


@pytest.mark.parametrize(
    "adapter,other", [(LLMCredentials, "PLATFORM"), (PlatformCredentials, "LLM")]
)
def test_api_and_other_worker_environment_cannot_unwrap(adapter, other, key_material):
    # No private halves for API; the other worker has only its own half.
    with pytest.raises(CredentialError):
        adapter({})
    env = {
        f"LABELLOOP_{other}_UNWRAP_PRIVATE_PEM": key_material[SecretType(other.lower())][0].decode()
    }
    with pytest.raises(CredentialError):
        adapter(env)
    env = {
        "LABELLOOP_LLM_UNWRAP_PRIVATE_PEM": key_material[SecretType.LLM][1].decode(),
        "LABELLOOP_PLATFORM_UNWRAP_PRIVATE_PEM": key_material[SecretType.PLATFORM][1].decode(),
    }
    with pytest.raises(CredentialError):
        adapter(env)


def test_public_key_ring_rejects_shared_pair_and_never_loads_private(key_material):
    llm = key_material[SecretType.LLM][1]
    platform = key_material[SecretType.PLATFORM][1]
    ring = PublicKeyRing(llm, platform)
    assert not hasattr(ring, "unwrap")
    with pytest.raises(CredentialError):
        PublicKeyRing(llm, llm)
    with pytest.raises(CredentialError):
        PublicKeyRing(key_material[SecretType.LLM][0], platform)


def test_small_rsa_keys_refused():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    with pytest.raises(CredentialError):
        PublicSealer(SecretType.LLM, pem)


@pytest.mark.parametrize(
    "value", [b"short", b"space in key", b"x" * 4097, b"line\nbreaks", "snowman-☃".encode()]
)
def test_invalid_secret_input_never_echoed(value, sealer, scope):
    with pytest.raises(CredentialError) as error:
        sealer.seal(scope.identity(uuid4(), 1), value)
    assert str(error.value) == "credential operation refused"


def test_worker_secret_not_serializable_and_errors_redacted(sealer, scope, key_material):
    identity = scope.identity(uuid4(), 1)
    secret = unwrap(key_material).unwrap(sealer.seal(identity, PLAINTEXT), identity)
    assert PLAINTEXT.decode() not in str(secret)
    with pytest.raises(TypeError):
        json.dumps(secret)

    def provider_error(key):
        raise RuntimeError(f"Authorization: Bearer {key.decode()}")

    with pytest.raises(CredentialError) as error:
        secret.use(provider_error)
    assert PLAINTEXT.decode() not in str(error.value)
    output = StringIO()
    handler = logging.StreamHandler(output)
    handler.addFilter(SecretRedactionFilter(secret))
    logger = logging.getLogger("credential-redaction-fixture")
    logger.propagate = False
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        try:
            provider_error(PLAINTEXT)
        except RuntimeError:
            logger.exception("provider failed %s", PLAINTEXT.decode(), stack_info=True)
    finally:
        logger.removeHandler(handler)
    assert PLAINTEXT.decode() not in output.getvalue()
    assert "[REDACTED]" in output.getvalue()
