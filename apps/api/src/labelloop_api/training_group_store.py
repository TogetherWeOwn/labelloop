"""Current groups of the candidate's compiled training inputs, not asserted lineage.

This covers the selected training run only. Checkpoint ancestors, reference use,
selection and calibration provenance still need their own authoritative producers.
The caller retains the project and canonical group locks through its decision.
"""

import sqlalchemy as sa

from labelloop_api import model_schema
from labelloop_api.qualification_store import release_membership
from labelloop_api.schema import canonical_dataset_release, yolo_dataset_variant
from labelloop_api.source_group_store import current_release_groups
from labelloop_core.qualification_data import require_name


def model_training_groups(
    connection: sa.Connection, *, project_id: object, model_id: object
) -> tuple[str, ...]:
    """Load the immutable compiled input and check every sample's current group.

    Validation/test samples are checked but never counted as weight-fitting groups.
    Multiple derivatives may name one source, but cannot disagree about its split
    or group. Missing or substituted manifest membership fails closed.
    """
    row = (
        connection.execute(
            sa.select(
                model_schema.training_run.c.input_manifest,
                canonical_dataset_release.c.release_key,
                canonical_dataset_release.c.assignment_version,
                canonical_dataset_release.c.sample_revisions,
            )
            .select_from(
                model_schema.model_release.join(model_schema.training_run)
                .join(yolo_dataset_variant)
                .join(canonical_dataset_release)
            )
            .where(
                model_schema.model_release.c.id == model_id,
                model_schema.model_release.c.project_id == project_id,
                canonical_dataset_release.c.project_id == project_id,
                canonical_dataset_release.c.frozen,
            )
        )
        .mappings()
        .one()
    )
    manifest = row["input_manifest"]
    if (
        not isinstance(manifest, dict)
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
    return groups
