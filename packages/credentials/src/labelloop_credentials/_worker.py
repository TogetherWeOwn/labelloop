"""Shared cryptographic primitive used only by type-specific worker adapters.

Importing this code grants no decryption authority. Environment custody of the
private key is the boundary; the API must never receive either private half.
"""

import logging
import traceback
from collections.abc import Callable, Mapping
from typing import TypeVar

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from labelloop_credentials.envelope import (
    CredentialError,
    Envelope,
    Identity,
    SecretType,
    authenticated_context,
    fingerprint,
    key_id,
    oaep,
)

T = TypeVar("T")


class SecretValue:
    __slots__ = ("_value",)

    def __init__(self, value: bytes) -> None:
        self._value = value

    def __repr__(self) -> str:
        return "<provider credential redacted>"

    __str__ = __repr__

    def use(self, operation: Callable[[bytes], T]) -> T:
        """Worker-local provider boundary only; do not persist or serialize the callback result."""
        try:
            return operation(self._value)
        except Exception:
            raise CredentialError() from None


class SecretRedactionFilter(logging.Filter):
    """Install on every provider log handler, including exception/stack formatting."""

    def __init__(self, secret: SecretValue) -> None:
        super().__init__()
        self._text = secret._value.decode("ascii")

    def filter(self, record: logging.LogRecord) -> bool:
        rendered = record.getMessage()
        if record.exc_info:
            rendered += "\n" + "".join(traceback.format_exception(*record.exc_info))
        if record.exc_text:
            rendered += "\n" + record.exc_text
        if record.stack_info:
            rendered += "\n" + record.stack_info
        record.msg = rendered.replace(self._text, "[REDACTED]")
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


class WorkerUnwrapper:
    def __init__(self, secret_type: SecretType, private_pem: bytes) -> None:
        try:
            key = serialization.load_pem_private_key(private_pem, password=None)
            if not isinstance(key, rsa.RSAPrivateKey) or not 3072 <= key.key_size <= 8192:
                raise CredentialError()
        except Exception:
            raise CredentialError() from None
        self._key = key
        self._secret_type = secret_type
        self._key_id = key_id(key.public_key())

    @classmethod
    def from_environment(cls, secret_type: SecretType, env: Mapping[str, str]) -> "WorkerUnwrapper":
        # Deliberately never inspect a fallback, the other worker's key, or provider keys.
        name = {
            SecretType.LLM: "LABELLOOP_LLM_UNWRAP_PRIVATE_PEM",
            SecretType.PLATFORM: "LABELLOOP_PLATFORM_UNWRAP_PRIVATE_PEM",
        }[secret_type]
        try:
            return cls(secret_type, env[name].encode("ascii"))
        except Exception:
            raise CredentialError() from None

    def unwrap(self, envelope: Envelope, expected: Identity) -> SecretValue:
        try:
            if (
                envelope.identity != expected
                or expected.secret_type != self._secret_type
                or envelope.wrapping_key_id != self._key_id
            ):
                raise CredentialError()
            context = authenticated_context(envelope)
            data_key = self._key.decrypt(envelope.wrapped_key, oaep(context))
            if len(data_key) != 32:
                raise CredentialError()
            value = AESGCM(data_key).decrypt(envelope.nonce, envelope.ciphertext, context)
            if fingerprint(value) != envelope.fingerprint:
                raise CredentialError()
            return SecretValue(value)
        except Exception:
            raise CredentialError() from None
