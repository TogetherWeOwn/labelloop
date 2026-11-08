"""Typed stage callbacks for the trusted core runner; no service authentication here."""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Protocol

from labelloop_core.domain import ReferenceExample, SourceAsset
from labelloop_core.registry import RegistryState
from labelloop_worker_ml.proposals import (
    DiscoveryProvider,
    InitialDetectorRelease,
    ProposalProvider,
    ProposalRequest,
    record_digest,
)


class LeaseInput(Protocol):
    @property
    def input(self) -> dict: ...


class ProposalStageContext(Protocol):
    @property
    def lease(self) -> LeaseInput: ...

    @property
    def artifacts(self) -> dict: ...

    def heartbeat(self) -> None: ...


@dataclass(frozen=True)
class ProposalWorkflow:
    request: ProposalRequest
    registry: RegistryState
    references: tuple[ReferenceExample, ...]
    discovery_version: str
    provider: ProposalProvider
    discovery_provider: DiscoveryProvider
    read_source: Callable[[SourceAsset], bytes]
    initial_detector: InitialDetectorRelease | None = None

    def job_input(self) -> dict:
        # Queue input pins the entire teacher release, including policy and rollback-independent
        # registry revision. A later promotion cannot silently change a resumed stage's teacher.
        return json.loads(
            json.dumps(
                {
                    "run_id": self.request.run_id,
                    "request": self.request.binding(),
                    "registry": asdict(self.registry),
                    "initial_detector": asdict(self.initial_detector)
                    if self.initial_detector
                    else None,
                    "references": [asdict(ref) for ref in self.references],
                    "discovery_version": self.discovery_version,
                },
                allow_nan=False,
            )
        )

    def _validate(self, context: ProposalStageContext) -> bytes:
        if record_digest(context.lease.input) != record_digest(self.job_input()):
            raise ValueError("worker configuration differs from immutable job inputs")
        context.heartbeat()
        return self.read_source(self.request.source)

    def discover(self, context: ProposalStageContext) -> dict:
        original = self._validate(context)
        result = self.provider.discover(self.request, original, self.discovery_provider)
        if result["provider_version"] != self.discovery_version:
            raise ValueError("discovery version differs from pinned job")
        context.heartbeat()
        return result

    def propose(self, context: ProposalStageContext) -> dict:
        original = self._validate(context)
        discovery = context.artifacts["discovery"]
        if discovery["provider_version"] != self.discovery_version:
            raise ValueError("saved discovery version differs from pinned job")
        result = self.provider.propose(
            self.request,
            original,
            discovery,
            self.registry,
            self.references,
            self.initial_detector,
        )
        context.heartbeat()
        return result
