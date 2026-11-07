"""Release-derived ancestor search for data and model lineage."""

from dataclasses import dataclass, field


@dataclass(slots=True)
class LineageGraph:
    parents: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def add(self, child: str, *parents: str) -> None:
        if not child or not parents or child in self.parents or child in parents:
            raise ValueError("lineage children and immutable parents must be explicit")
        if child in self.ancestors(parents[0]):
            raise ValueError("lineage must be acyclic")
        self.parents[child] = tuple(sorted(set(parents)))

    def ancestors(self, artifact_id: str) -> set[str]:
        seen: set[str] = set()
        pending = [artifact_id]
        while pending:
            node = pending.pop()
            if node in seen:
                continue
            seen.add(node)
            pending.extend(self.parents.get(node, ()))
        return seen

    def descendants(
        self, source_id: str, candidate_ids: set[str] | frozenset[str] | None = None
    ) -> set[str]:
        candidates = set(self.parents) if candidate_ids is None else candidate_ids
        return {
            artifact_id for artifact_id in candidates if source_id in self.ancestors(artifact_id)
        }
