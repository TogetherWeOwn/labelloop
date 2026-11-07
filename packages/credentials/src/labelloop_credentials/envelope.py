"""Public-key sealing only. No environment or private-key access in the API port."""

import hashlib
import json
import os
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialError(ValueError):
    """A deliberately non-diagnostic error safe for logs and public error mapping."""

    def __init__(self) -> None:
        super().__init__("credential operation refused")


class SecretType(StrEnum):
    LLM = "llm"
    PLATFORM = "platform"


@dataclass(frozen=True)
class Identity:
    project_id: UUID
    workspace_id: UUID
    connection_id: UUID
    version: int
    secret_type: SecretType

    def __post_init__(self) -> None:
        if (
            any(
                type(x) is not UUID
                for x in (self.project_id, self.workspace_id, self.connection_id)
            )
            or type(self.version) is not int
            or self.version < 1
            or not isinstance(self.secret_type, SecretType)
        ):
            raise CredentialError()


@dataclass(frozen=True)
class Fingerprint:
    last_four: str
    sha256_prefix: str

    def __post_init__(self) -> None:
        if (
            len(self.last_four) != 4
            or any(ord(x) < 33 or ord(x) > 126 for x in self.last_four)
            or len(self.sha256_prefix) != 12
            or any(x not in "0123456789abcdef" for x in self.sha256_prefix)
        ):
            raise CredentialError()


@dataclass(frozen=True)
class Envelope:
    identity: Identity
    wrapping_key_id: str
    fingerprint: Fingerprint
    nonce: bytes = field(repr=False)
    wrapped_key: bytes = field(repr=False)
    ciphertext: bytes = field(repr=False)
    format_version: int = 1

    def __post_init__(self) -> None:
        if (
            type(self.format_version) is not int
            or self.format_version != 1
            or any(type(x) is not bytes for x in (self.nonce, self.wrapped_key, self.ciphertext))
            or len(self.wrapping_key_id) != 64
            or any(x not in "0123456789abcdef" for x in self.wrapping_key_id)
            or len(self.nonce) != 12
            or not 384 <= len(self.wrapped_key) <= 1024
            or not 24 <= len(self.ciphertext) <= 4112
        ):
            raise CredentialError()


@dataclass(frozen=True)
class PublicCredential:
    identity: Identity
    fingerprint: Fingerprint


def public_view(envelope: Envelope) -> PublicCredential:
    return PublicCredential(envelope.identity, envelope.fingerprint)


def key_id(key: rsa.RSAPublicKey) -> str:
    return hashlib.sha256(
        key.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).hexdigest()


def fingerprint(value: bytes) -> Fingerprint:
    # Provider keys are printable ASCII, not arbitrary user text. A minimum length avoids
    # a short credential being entirely exposed by the required last-four display.
    if not 8 <= len(value) <= 4096 or any(x < 33 or x > 126 for x in value):
        raise CredentialError()
    return Fingerprint(value[-4:].decode("ascii"), hashlib.sha256(value).hexdigest()[:12])


def authenticated_context(envelope: Envelope) -> bytes:
    i = envelope.identity
    return json.dumps(
        {
            "domain": "labelloop.provider-credential",
            "format": envelope.format_version,
            "project": str(i.project_id),
            "workspace": str(i.workspace_id),
            "connection": str(i.connection_id),
            "version": i.version,
            "type": i.secret_type.value,
            "wrapping_key": envelope.wrapping_key_id,
            "last_four": envelope.fingerprint.last_four,
            "sha256_prefix": envelope.fingerprint.sha256_prefix,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def oaep(context: bytes) -> padding.OAEP:
    return padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=context)


class PublicSealer:
    """Immutable type-specific public capability; safe to construct in an API process."""

    def __init__(self, secret_type: SecretType, public_pem: bytes) -> None:
        try:
            key = serialization.load_pem_public_key(public_pem)
            if not isinstance(key, rsa.RSAPublicKey) or not 3072 <= key.key_size <= 8192:
                raise CredentialError()
            if not isinstance(secret_type, SecretType):
                raise CredentialError()
        except Exception:
            raise CredentialError() from None
        self.secret_type = secret_type
        self._key = key
        self.wrapping_key_id = key_id(key)

    def seal(self, identity: Identity, plaintext: bytes) -> Envelope:
        try:
            if identity.secret_type != self.secret_type:
                raise CredentialError()
            fp = fingerprint(plaintext)
            data_key = AESGCM.generate_key(bit_length=256)
            nonce = os.urandom(12)
            # Build the authenticated header before encrypting; placeholders never persist.
            header = Envelope(
                identity,
                self.wrapping_key_id,
                fp,
                nonce,
                bytes(self._key.key_size // 8),
                bytes(len(plaintext) + 16),
            )
            context = authenticated_context(header)
            return Envelope(
                identity,
                self.wrapping_key_id,
                fp,
                nonce,
                self._key.encrypt(data_key, oaep(context)),
                AESGCM(data_key).encrypt(nonce, plaintext, context),
            )
        except Exception:
            raise CredentialError() from None
