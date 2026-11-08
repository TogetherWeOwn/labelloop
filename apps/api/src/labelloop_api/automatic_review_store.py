"""Original automatic-acceptance snapshots, not current-policy inference.

The revision writer records this only after the actual acceptance guard succeeds.
The immutable event retains the checked policy, not proof of reviewer execution,
score calibration, source/condition truth or independently measured annotation quality.
Historical revisions without this record cannot acquire invented provenance.
"""

import json
from dataclasses import asdict, dataclass

import sqlalchemy as sa

from labelloop_core.qualification import QualificationContext, QualificationPolicy
from labelloop_core.qualification_data import (
    ScopeCell,
    record_sha256,
    require_count,
    require_name,
    require_scope,
)
from labelloop_core.qualification_wire import dumps, loads
from labelloop_core.training import require_digest

from .qualification_store import lock_project
from .schema import annotation_revision, review_event, source_asset

_SCHEMA = "labelloop.automatic-acceptance.v1"
_REVISION_FIELDS = (
    "revision",
    "parent_revision",
    "actor_kind",
    "actor_id",
    "objects",
    "approval_state",
    "review_tier",
    "scope_completeness",
    "training_eligible",
    "gold_eligible",
)
_FIELDS = {
    "schema",
    "project_key",
    "asset_key",
    "revision_sha256",
    "qualification_id",
    "release_sha256",
    "evaluation_key",
    "policy",
    "scope",
}


@dataclass(frozen=True, slots=True)
class StoredAutomaticAcceptance:
    qualification_id: str
    release_sha256: str
    evaluation_key: str
    policy: QualificationPolicy
    scope: tuple[ScopeCell, ...]


def automatic_acceptance_record(
    *, project_key, asset_key, revision, evaluation_key, scope, qualification: QualificationContext
):
    """Called inside the revision savepoint, after the current-state guard."""
    require_name(project_key)
    require_name(asset_key)
    require_name(evaluation_key)
    require_scope(scope)
    evidence = qualification.evidence
    if (
        evidence.pipeline != qualification.current_pipeline
        or evidence.pipeline.project_id != project_key
        or not set(scope) <= set(evidence.scope)
    ):
        raise ValueError("automatic acceptance snapshot differs from the checked pipeline or scope")
    policy = QualificationPolicy(evidence.pipeline, evidence.scope, evidence.targets)
    require_name(evidence.qualification_id)
    require_digest(evidence.release_sha256)
    return {
        "schema": _SCHEMA,
        "project_key": project_key,
        "asset_key": asset_key,
        "revision_sha256": record_sha256({key: revision[key] for key in _REVISION_FIELDS}),
        "qualification_id": evidence.qualification_id,
        "release_sha256": evidence.release_sha256,
        "evaluation_key": evaluation_key,
        "policy": json.loads(dumps(policy)),
        "scope": [asdict(cell) for cell in scope],
    }


def load_automatic_acceptance(
    connection: sa.Connection, *, project_key: str, asset_key: str, revision: int
) -> StoredAutomaticAcceptance:
    """Read one exact original decision; never substitute today's champion or policy.

    This is historical evidence, not an acceptance grant. The caller must still
    validate the actual pipeline execution, score/threshold and sampling protocol.
    """
    require_name(project_key)
    require_name(asset_key)
    require_count(revision, minimum=2)
    project_id = lock_project(connection, project_key)
    rows = (
        connection.execute(
            sa.select(
                annotation_revision,
                review_event.c.actor_kind.label("event_actor_kind"),
                review_event.c.actor_id.label("event_actor_id"),
                review_event.c.correction,
            )
            .select_from(
                source_asset.join(annotation_revision).join(
                    review_event,
                    sa.and_(
                        review_event.c.asset_id == source_asset.c.id,
                        review_event.c.expected_revision == revision - 1,
                        review_event.c.decision == "apply_revision",
                    ),
                )
            )
            .where(
                source_asset.c.project_id == project_id,
                source_asset.c.asset_key == asset_key,
                annotation_revision.c.revision == revision,
            )
        )
        .mappings()
        .all()
    )
    if len(rows) != 1:
        raise ValueError("automatic acceptance requires one exact original review event")
    row = rows[0]
    correction = row["correction"]
    if (
        row["actor_kind"] != "system"
        or row["approval_state"] != "auto_accepted"
        or row["parent_revision"] != revision - 1
        or row["event_actor_kind"] != row["actor_kind"]
        or row["event_actor_id"] != row["actor_id"]
        or not isinstance(correction, dict)
        or type(correction.get("new_revision")) is not int
        or correction["new_revision"] != revision
    ):
        raise ValueError("automatic acceptance event differs from the exact canonical revision")
    record = correction.get("automatic_acceptance")
    if not isinstance(record, dict) or set(record) != _FIELDS or record["schema"] != _SCHEMA:
        raise ValueError("missing or malformed original automatic acceptance provenance")
    if (
        record["project_key"] != project_key
        or record["asset_key"] != asset_key
        or record["revision_sha256"] != record_sha256({key: row[key] for key in _REVISION_FIELDS})
    ):
        raise ValueError("automatic acceptance snapshot differs from the canonical revision")
    policy = loads(json.dumps(record["policy"]), QualificationPolicy)
    for key in ("qualification_id", "evaluation_key"):
        require_name(record[key])
    require_digest(record["release_sha256"])
    raw_scope = record["scope"]
    if not isinstance(raw_scope, list) or any(
        not isinstance(cell, dict) or set(cell) != {"class_id", "source_id", "condition"}
        for cell in raw_scope
    ):
        raise ValueError("malformed original automatic acceptance scope")
    scope = tuple(ScopeCell(**cell) for cell in raw_scope)
    require_scope(scope)
    if policy.pipeline.project_id != project_key or not set(scope) <= set(policy.scope):
        raise ValueError("automatic acceptance policy differs from the original project or scope")
    return StoredAutomaticAcceptance(
        record["qualification_id"],
        record["release_sha256"],
        record["evaluation_key"],
        policy,
        scope,
    )
