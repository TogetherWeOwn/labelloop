"""Platform-only unwrap and read-only workspace validation before key activation."""

import os
from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from labelloop_credentials._worker import SecretValue, WorkerUnwrapper
from labelloop_credentials.envelope import CredentialError, SecretType
from labelloop_credentials.store import AuthorizedScope, CredentialStore


class WorkspaceReader(Protocol):
    """Implemented by the pinned Platform adapter, using GET only at its fixed origin.

    The adapter must redact bearer headers, bound timeouts, disable redirects and
    return the exact workspace ID from the validated response, never an owner slug.
    """

    def get_workspace(self, credential: bytes, workspace_id: UUID) -> UUID: ...


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
        reader: WorkspaceReader,
    ) -> str:
        if scope.secret_type != SecretType.PLATFORM:
            raise CredentialError()
        envelope = store.lookup(scope, connection_id, pending_version=version)
        secret = self._unwrap.unwrap(envelope, scope.identity(connection_id, version))
        # A failed read is terminal for this intent, never activation or an automatic retry.
        # Exceptions may contain request headers; neither persist nor rethrow them.
        try:
            returned_id = secret.use(lambda key: reader.get_workspace(key, scope.workspace_id))
            valid = type(returned_id) is UUID and returned_id == scope.workspace_id
        except Exception:
            valid = False
        return store.finish_validation(
            scope, connection_id, version, valid=valid, actor_id="worker-platform"
        )
