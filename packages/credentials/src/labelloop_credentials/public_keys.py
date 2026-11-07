"""Public halves only; this capability has no method for private-key import."""

from collections.abc import Mapping

from labelloop_credentials.envelope import CredentialError, Identity, PublicSealer, SecretType


class PublicKeyRing:
    def __init__(self, llm_public_pem: bytes, platform_public_pem: bytes) -> None:
        self._llm = PublicSealer(SecretType.LLM, llm_public_pem)
        self._platform = PublicSealer(SecretType.PLATFORM, platform_public_pem)
        if self._llm.wrapping_key_id == self._platform.wrapping_key_id:
            raise CredentialError()

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> "PublicKeyRing":
        try:
            return cls(
                env["LABELLOOP_LLM_WRAP_PUBLIC_PEM"].encode("ascii"),
                env["LABELLOOP_PLATFORM_WRAP_PUBLIC_PEM"].encode("ascii"),
            )
        except Exception:
            raise CredentialError() from None

    def for_identity(self, identity: Identity) -> PublicSealer:
        return self._llm if identity.secret_type == SecretType.LLM else self._platform
