"""Platform-only unwrap; activation denied until supported vendor identity exists."""

import os
from collections.abc import Mapping
from uuid import UUID

from labelloop_credentials._worker import SecretValue, WorkerUnwrapper
from labelloop_credentials.envelope import CredentialError, SecretType
from labelloop_credentials.store import AuthorizedScope, CredentialStore


class PlatformCredentials:
    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._unwrap = WorkerUnwrapper.from_environment(
            SecretType.PLATFORM, os.environ if env is None else env
        )

    def lookup(
        self, store: CredentialStore, scope: AuthorizedScope, connection_id: UUID
    ) -> SecretValue:
        if scope.secret_type != SecretType.PLATFORM:
            raise CredentialError()
        envelope = store.lookup(scope, connection_id)
        return self._unwrap.unwrap(envelope, envelope.identity)

    def validate(
        self,
        store: CredentialStore,
        scope: AuthorizedScope,
        connection_id: UUID,
        version: int,
    ) -> str:
        if scope.secret_type != SecretType.PLATFORM:
            raise CredentialError()
        store.lookup(scope, connection_id, pending_version=version)
        # SDK/OpenAPI 0.1.107 has no authenticated immutable workspace-ID response.
        # Local authorization UUIDs, public profiles, handles and membership IDs
        # cannot satisfy exact vendor-ID equality. Do not decrypt or probe a key
        # when no possible supported response can authorize its activation.
        return store.finish_validation(
            scope,
            connection_id,
            version,
            valid=False,
            actor_id="worker-platform",
            denial="identity_unverifiable",
        )
