"""Current compiled input groups across stored checkpoint and teacher ancestors.

Reference use and noncompiled selection/calibration still need trusted producers.
Missing external/imported lineage is not inferred. The caller retains the project
and canonical group locks through its decision.
"""

from dataclasses import dataclass

import sqlalchemy as sa

from labelloop_api import model_schema
from labelloop_api.annotation_audit_store import training_ancestor_runs
from labelloop_api.generated_label_store import label_teacher_runs
from labelloop_api.qualification_store import release_membership
from labelloop_api.schema import canonical_dataset_release, yolo_dataset_variant
from labelloop_api.source_group_store import current_release_groups
from labelloop_core.qualification import QualificationEvidence
from labelloop_core.qualification_data import require_name


@dataclass(frozen=True, slots=True)
class ModelInputGroups:
    training: tuple[str, ...]
    selection: tuple[str, ...]

    def require_isolated(self, evidence: QualificationEvidence) -> None:
        if not set(self.training) <= set(evidence.lineage.training_group_ids):
            raise ValueError("qualification lineage omits compiled training groups")
        if set(self.selection) & set(evidence.final_test.dataset.group_ids):
            raise ValueError("final-test groups overlap compiled checkpoint selection")
        if set(self.selection) & {source.group_id for source in evidence.canary_audit.sources}:
            raise ValueError("canary groups overlap compiled checkpoint selection")


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
    )


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
