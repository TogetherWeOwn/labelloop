"""Current compiled input groups across stored checkpoint and teacher ancestors.

Recorded selection/calibration contributes influence even when not in a compiled
variant. Reference use and missing external/imported lineage still need trusted
producers. The caller retains project and canonical group locks through its decision.
"""

import json
from dataclasses import dataclass, replace

import sqlalchemy as sa

from labelloop_api import model_schema, qualification_schema
from labelloop_api.annotation_audit_store import training_ancestor_runs
from labelloop_api.generated_label_store import label_teacher_runs
from labelloop_api.model_store import load_release, stored_report
from labelloop_api.qualification_store import release_membership
from labelloop_api.schema import canonical_dataset_release, yolo_dataset_variant
from labelloop_api.source_group_store import current_release_groups
from labelloop_core.evaluation_lifecycle import CandidateSelection, select_candidate
from labelloop_core.qualification import QualificationEvidence
from labelloop_core.qualification_data import require_name
from labelloop_core.qualification_wire import loads


@dataclass(frozen=True, slots=True)
class ModelInputGroups:
    training: tuple[str, ...]
    selection: tuple[str, ...]
    recorded_selection: tuple[str, ...] = ()

    def require_isolated(self, evidence: QualificationEvidence) -> None:
        if not set(self.training) <= set(evidence.lineage.training_group_ids):
            raise ValueError("qualification lineage omits compiled training groups")
        if set(self.selection) & set(evidence.final_test.dataset.group_ids):
            raise ValueError("final-test groups overlap compiled checkpoint selection")
        if set(self.selection) & {source.group_id for source in evidence.canary_audit.sources}:
            raise ValueError("canary groups overlap compiled checkpoint selection")
        if set(self.recorded_selection) & set(evidence.final_test.dataset.group_ids):
            raise ValueError("final-test groups overlap recorded selection or calibration")
        if set(self.recorded_selection) & {
            source.group_id for source in evidence.canary_audit.sources
        }:
            raise ValueError("canary groups overlap recorded selection or calibration")


def model_training_groups(
    connection: sa.Connection, *, project_id: object, model_id: object
) -> tuple[str, ...]:
    return model_input_groups(connection, project_id=project_id, model_id=model_id).training


def model_input_groups(
    connection: sa.Connection, *, project_id: object, model_id: object
) -> ModelInputGroups:
    """Check all reachable immutable inputs using the veto's existing ancestry graph.

    Unregistered runs and unselected checkpoints contribute weight-fitting and
    compiled validation groups. Validation guided checkpoint selection, so it cannot
    be independent final/canary evidence even for an inherited teacher/checkpoint.
    Teacher edges conservatively retain influence after later revisions. Unrelated
    runs are excluded; compiled test members are checked but not assumed accessed.
    """
    run_id = connection.execute(
        sa.select(model_schema.model_release.c.training_run_id).where(
            model_schema.model_release.c.id == model_id,
            model_schema.model_release.c.project_id == project_id,
        )
    ).scalar_one()
    rows = (
        connection.execute(
            sa.select(
                model_schema.training_run.c.id,
                model_schema.training_run.c.record,
                model_schema.training_run.c.input_manifest,
                canonical_dataset_release.c.release_key,
                canonical_dataset_release.c.assignment_version,
                canonical_dataset_release.c.sample_revisions,
                canonical_dataset_release.c.frozen,
            )
            .select_from(
                model_schema.training_run.join(yolo_dataset_variant).join(canonical_dataset_release)
            )
            .where(canonical_dataset_release.c.project_id == project_id)
        )
        .mappings()
        .all()
    )
    inputs = {row["id"]: row for row in rows}
    ancestors = training_ancestor_runs(
        {
            key: (row["record"], release_membership(row["sample_revisions"]))
            for key, row in inputs.items()
        },
        run_id,
        label_teacher_runs(connection, project_id),
    )
    compiled = tuple(
        _compiled_input_groups(connection, project_id, inputs[ancestor])
        for ancestor in sorted(ancestors, key=str)
    )
    return ModelInputGroups(
        tuple(sorted({group for groups in compiled for group in groups.training})),
        tuple(sorted({group for groups in compiled for group in groups.selection})),
        _recorded_selection_groups(connection, project_id, ancestors),
    )


def _recorded_selection_groups(connection, project_id, ancestors):
    # Reuse the same run closure for every registered sibling checkpoint. An earlier
    # selection still influenced the run even if later report links extend its digest.
    from labelloop_api.evaluation_group_store import load_evaluation_set

    table = qualification_schema.candidate_selection
    model = model_schema.model_release
    rows = (
        connection.execute(
            sa.select(table.c.selection_key, table.c.record, model.c.id.label("model_id"))
            .select_from(
                table.join(
                    model,
                    sa.and_(
                        model.c.project_id == table.c.project_id,
                        model.c.release_key == table.c.record["payload"]["release_id"].as_string(),
                    ),
                )
            )
            .where(table.c.project_id == project_id, model.c.training_run_id.in_(ancestors))
            .order_by(table.c.selection_key)
        )
        .mappings()
        .all()
    )
    groups = set()
    for row in rows:
        selection = loads(json.dumps(row["record"]), CandidateSelection)
        release = load_release(connection, row["model_id"])
        if release is None or (
            selection.selection_id,
            selection.project_id,
            selection.release_id,
        ) != (row["selection_key"], release.project_id, release.release_id):
            raise ValueError("recorded selection identity mismatch")
        development = load_evaluation_set(
            connection,
            project_key=release.project_id,
            dataset_key=selection.development_set_id,
            role="development",
        )
        calibration = (
            load_evaluation_set(
                connection,
                project_key=release.project_id,
                dataset_key=selection.calibration_set_id,
                role="calibration",
            )
            if selection.calibration_set_id is not None
            else None
        )
        checked = select_candidate(
            release,
            stored_report(connection, release, selection.development_run_id),
            development,
            selection_id=selection.selection_id,
            calibration=stored_report(connection, release, selection.calibration_run_id)
            if selection.calibration_run_id is not None
            else None,
            calibration_set=calibration,
        )
        if replace(selection, release_sha256=checked.release_sha256) != checked:
            raise ValueError("recorded selection differs from stored evaluation evidence")
        groups.update(development.group_ids)
        if calibration is not None:
            groups.update(calibration.group_ids)
    return tuple(sorted(groups))


def _compiled_input_groups(connection, project_id, row):
    # Validate all derivatives, but never misreport val/test as weight-fitting data.
    manifest = row["input_manifest"]
    if (
        row["frozen"] is not True
        or not isinstance(manifest, dict)
        or manifest.get("release_id") != row["release_key"]
        or manifest.get("split_assignment_version") != row["assignment_version"]
        or not isinstance(manifest.get("samples"), list)
        or not manifest["samples"]
    ):
        raise ValueError("training groups require the complete pinned input manifest")
    membership = release_membership(row["sample_revisions"])
    assignments = {}
    for sample in manifest["samples"]:
        if not isinstance(sample, dict):
            raise ValueError("invalid compiled training sample")
        asset, group, split = (sample.get(key) for key in ("asset_id", "group_id", "split"))
        require_name(asset)
        require_name(group)
        if (
            asset not in membership
            or type(sample.get("revision")) is not int
            or sample["revision"] != membership[asset]
            or split not in {"train", "val", "test"}
        ):
            raise ValueError("compiled training sample is outside frozen membership or split")
        if assignments.setdefault(asset, (group, split)) != (group, split):
            raise ValueError("compiled derivatives disagree about their source group or split")
    current = current_release_groups(
        connection,
        project_id=project_id,
        assignment_version=row["assignment_version"],
        asset_keys=frozenset(assignments),
    )
    if current != {asset: group for asset, (group, _) in assignments.items()}:
        raise ValueError("compiled training groups differ from current canonical groups")
    groups = tuple(sorted({group for group, split in assignments.values() if split == "train"}))
    if not groups:
        raise ValueError("qualification requires nonempty compiled training groups")
    return ModelInputGroups(
        groups,
        tuple(sorted({group for group, split in assignments.values() if split == "val"})),
    )
