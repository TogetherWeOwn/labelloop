"""Trusted teacher provenance port, not model-asserted lineage or authorization.

The annotation service must append every contributing teacher with the revision
in its outer transaction. Later human acceptance does not erase that influence.
This does not infer missing historical provenance or implement cross-project imports.
"""

import hashlib

import sqlalchemy as sa

from labelloop_api import model_schema, qualification_schema
from labelloop_api.qualification_store import lock_project
from labelloop_api.schema import annotation_revision, source_asset
from labelloop_core.json_records import canonical_json
from labelloop_core.qualification_data import require_count, require_name


def proposal_teacher(
    connection: sa.Connection,
    *,
    project_key: str,
    asset_key: str,
    expected_revision: int,
    proposal_run_key: str,
) -> str:
    """Resolve a registered teacher from completed fenced evidence, never a body.

    The caller holds the project lock through annotation commit. A proposal is
    influence, not approval: partial/negative proposals still retain their teacher.
    Initial external detectors have no registered lineage and fail closed here.
    """
    # Registry veto loading imports this module, so load the codec only at use.
    from labelloop_api.model_store import json_record, load_release

    project_id, record, inputs = _completed_proposal(
        connection,
        project_key=project_key,
        asset_key=asset_key,
        expected_revision=expected_revision,
        proposal_run_key=proposal_run_key,
    )
    teacher = record["teacher"]
    if teacher is None or teacher != inputs["registry"]["champion"]:
        raise ValueError("proposal differs from its completed pinned job or annotation revision")
    model_id = connection.execute(
        sa.select(model_schema.model_release.c.id).where(
            model_schema.model_release.c.project_id == project_id,
            model_schema.model_release.c.release_key == teacher["release_id"],
        )
    ).scalar_one()
    release = load_release(connection, model_id)
    if (
        release is None
        or json_record(release) != teacher
        or release.project_id != project_key
        or record["checkpoint_sha256"] != release.weights_sha256
    ):
        raise ValueError("proposal teacher differs from its complete registered release")
    return release.release_id


def _completed_proposal(connection, *, project_key, asset_key, expected_revision, proposal_run_key):
    for value in (project_key, asset_key, proposal_run_key):
        require_name(value)
    require_count(expected_revision, minimum=1)
    project_id = lock_project(connection, project_key)
    row = (
        connection.execute(
            sa.text(
                "SELECT p.record, p.sha256, j.input, s.payload, s.sha256 AS stage_sha256 "
                "FROM worker_proposal_run p JOIN worker_job j ON j.job_id = p.job_id "
                "JOIN worker_application a ON a.job_id = j.job_id "
                "JOIN worker_stage_artifact s ON s.job_id = j.job_id AND s.stage = 'proposal' "
                "WHERE p.run_id = :run_id AND p.project_key = :project_key "
                "AND j.project_key = :project_key AND j.kind = 'proposals' "
                "AND j.status = 'succeeded' AND a.generation = j.generation FOR SHARE OF j"
            ),
            {"run_id": proposal_run_key, "project_key": project_key},
        )
        .mappings()
        .one()
    )
    record, inputs = row["record"], row["input"]
    digest = hashlib.sha256(canonical_json(record)).hexdigest()
    binding = record["binding"]
    if (
        digest != row["sha256"]
        or digest != row["stage_sha256"]
        or record != row["payload"]
        or record["run_id"] != proposal_run_key
        or inputs["run_id"] != proposal_run_key
        or record["project_id"] != project_key
        or binding != inputs["request"]
        or binding["run_id"] != proposal_run_key
        or binding["source"]["project_id"] != project_key
        or binding["source"]["asset_id"] != asset_key
        or type(binding["expected_revision"]) is not int
        or binding["expected_revision"] != expected_revision
    ):
        raise ValueError("proposal differs from its completed pinned job or annotation revision")
    return project_id, record, inputs


def proposal_reference_assets(
    connection: sa.Connection,
    *,
    project_key: str,
    asset_key: str,
    expected_revision: int,
    proposal_run_key: str,
) -> frozenset[str]:
    """Recover historical reference influence, not present-day retrieval permission.

    The immutable job pins every reference, including unused alternatives. Retain
    them conservatively even for negative/partial proposals. This does not attest
    reference-pack approval or recover omitted/unrecorded review runs.
    """
    _, record, inputs = _completed_proposal(
        connection,
        project_key=project_key,
        asset_key=asset_key,
        expected_revision=expected_revision,
        proposal_run_key=proposal_run_key,
    )
    references = inputs.get("references")
    if not isinstance(references, list) or any(not isinstance(ref, dict) for ref in references):
        raise ValueError("proposal requires its complete pinned reference inputs")
    identities = []
    assets = set()
    for reference in references:
        for key in ("reference_id", "asset_id", "class_id"):
            require_name(reference.get(key))
        if reference.get("approved") is not True:
            raise ValueError("proposal reference was not approved in the pinned job")
        identities.append(reference["reference_id"])
        assets.add(reference["asset_id"])
    if len(set(identities)) != len(identities) or record.get("evidence_ids") != sorted(identities):
        raise ValueError("proposal reference identities differ from its pinned job")
    return frozenset(assets)


def record_generated_label_origin(
    connection: sa.Connection,
    *,
    project_key: str,
    asset_key: str,
    revision: int,
    teacher_model_key: str,
    actor_id: str,
) -> None:
    """Record one teacher of the exact persisted revision, including negatives.

    The service derives teacher identity from its fenced inference job, never a
    model response or request body. Callers retain the project lock through commit.
    Multiple teachers are retained; duplicate/substituted links cannot replace one.
    """
    for value in (actor_id, asset_key, teacher_model_key):
        require_name(value)
    require_count(revision, minimum=1)
    with connection.begin_nested():
        project_id = lock_project(connection, project_key)
        asset_id = connection.execute(
            sa.select(source_asset.c.id)
            .join(annotation_revision)
            .where(
                source_asset.c.project_id == project_id,
                source_asset.c.asset_key == asset_key,
                annotation_revision.c.revision == revision,
            )
        ).scalar_one()
        teacher_id = connection.execute(
            sa.select(model_schema.model_release.c.id).where(
                model_schema.model_release.c.project_id == project_id,
                model_schema.model_release.c.release_key == teacher_model_key,
            )
        ).scalar_one()
        connection.execute(
            sa.insert(qualification_schema.generated_label_origin).values(
                project_id=project_id,
                asset_id=asset_id,
                revision=revision,
                teacher_model_id=teacher_id,
                actor_id=actor_id,
            )
        )


def label_teacher_runs(connection: sa.Connection, project_id: object):
    """Load all recorded influence, preserving exact revision and project checks."""
    origin = qualification_schema.generated_label_origin
    teacher = model_schema.model_release
    rows = connection.execute(
        sa.select(
            origin.c.project_id,
            source_asset.c.project_id.label("source_project"),
            teacher.c.project_id.label("teacher_project"),
            source_asset.c.asset_key,
            origin.c.revision,
            teacher.c.training_run_id,
        )
        .select_from(
            origin.join(source_asset, origin.c.asset_id == source_asset.c.id).join(
                teacher, origin.c.teacher_model_id == teacher.c.id
            )
        )
        .where(sa.or_(origin.c.project_id == project_id, source_asset.c.project_id == project_id))
    ).mappings()
    links: dict[str, list[tuple[int, object]]] = {}
    for row in rows:
        if (row["project_id"], row["source_project"], row["teacher_project"]) != (
            project_id,
            project_id,
            project_id,
        ):
            raise ValueError("generated-label provenance cannot cross project boundaries")
        links.setdefault(row["asset_key"], []).append((row["revision"], row["training_run_id"]))
    return links
