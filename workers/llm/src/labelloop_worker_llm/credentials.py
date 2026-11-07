"""LLM-only unwrap adapter. The scope is trusted worker policy, not a model parameter."""

import os
from collections.abc import Mapping
from uuid import UUID

from labelloop_credentials._worker import SecretValue, WorkerUnwrapper
from labelloop_credentials.envelope import CredentialError, SecretType
from labelloop_credentials.store import AuthorizedScope, CredentialStore


class LLMCredentials:
    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._unwrap = WorkerUnwrapper.from_environment(
            SecretType.LLM, os.environ if env is None else env
        )

    def lookup(
        self, store: CredentialStore, scope: AuthorizedScope, connection_id: UUID
    ) -> SecretValue:
        if scope.secret_type != SecretType.LLM:
            raise CredentialError()
        envelope = store.lookup(scope, connection_id)
        return self._unwrap.unwrap(envelope, envelope.identity)

    def validate(
        self, store: CredentialStore, scope: AuthorizedScope, connection_id: UUID, version: int
    ) -> str:
        """Validate envelope possession locally; provider/model probes remain a separate gate."""
        if scope.secret_type != SecretType.LLM:
            raise CredentialError()
        envelope = store.lookup(scope, connection_id, pending_version=version)
        self._unwrap.unwrap(envelope, scope.identity(connection_id, version))
        return store.finish_validation(
            scope, connection_id, version, valid=True, actor_id="worker-llm"
        )
