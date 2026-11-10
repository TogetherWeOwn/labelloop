"""Immutable audit evidence; qualification migrations do not change prior metadata."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from labelloop_api.model_schema import model_release
from labelloop_api.schema import (
    annotation_revision,
    canonical_dataset_release,
    preprocessing_recipe,
    project,
)

metadata = sa.MetaData()
generated_label_origin = sa.Table(
    "qualification_generated_label_origin",
    metadata,
    sa.Column("project_id", UUID, sa.ForeignKey(project.c.id), nullable=False),
    sa.Column("asset_id", UUID, primary_key=True),
    sa.Column("revision", sa.Integer, primary_key=True),
    sa.Column("teacher_model_id", UUID, sa.ForeignKey(model_release.c.id), primary_key=True),
    sa.Column("actor_id", sa.String, nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.ForeignKeyConstraint(
        ["asset_id", "revision"], [annotation_revision.c.asset_id, annotation_revision.c.revision]
    ),
    sa.CheckConstraint("revision >= 1 AND length(trim(actor_id)) > 0"),
)
policy_event = sa.Table(
    "qualification_policy_event",
    metadata,
    sa.Column("project_id", UUID, sa.ForeignKey(project.c.id), primary_key=True),
    sa.Column("recipe_id", UUID, sa.ForeignKey(preprocessing_recipe.c.id), primary_key=True),
    sa.Column("revision", sa.Integer, primary_key=True),
    sa.Column("actor_id", sa.String, nullable=False),
    sa.Column("record", sa.JSON, nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.CheckConstraint("revision >= 1 AND length(trim(actor_id)) > 0"),
)
audit_sample = sa.Table(
    "qualification_audit_sample",
    metadata,
    sa.Column("project_id", UUID, sa.ForeignKey(project.c.id), primary_key=True),
    sa.Column("sample_key", sa.String, primary_key=True),
    sa.Column("release_id", UUID, sa.ForeignKey(canonical_dataset_release.c.id), nullable=False),
    sa.Column("actor_id", sa.String, nullable=False),
    sa.Column("population_sha256", sa.String(64), nullable=False),
    sa.Column("record", sa.JSON, nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.CheckConstraint("length(trim(actor_id)) > 0 AND length(trim(sample_key)) > 0"),
    sa.CheckConstraint("population_sha256 ~ '^[0-9a-f]{64}$'"),
)
acceptance_event = sa.Table(
    "qualification_acceptance_event",
    metadata,
    sa.Column("project_id", UUID, sa.ForeignKey(project.c.id), primary_key=True),
    sa.Column("recipe_id", UUID, sa.ForeignKey(preprocessing_recipe.c.id), primary_key=True),
    sa.Column("revision", sa.Integer, primary_key=True),
    sa.Column("actor_id", sa.String, nullable=False),
    sa.Column("action", sa.String, nullable=False),
    sa.Column("record", sa.JSON, nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.Column("qualification", sa.JSON(none_as_null=True), nullable=True),
    sa.CheckConstraint("revision >= 1 AND length(trim(actor_id)) > 0"),
    sa.CheckConstraint("action IN ('pause','resume')"),
    sa.CheckConstraint("(action = 'resume') = (qualification IS NOT NULL)"),
)
candidate_selection = sa.Table(
    "qualification_candidate_selection",
    metadata,
    sa.Column("project_id", UUID, sa.ForeignKey(project.c.id), primary_key=True),
    sa.Column("selection_key", sa.String, primary_key=True),
    sa.Column("actor_id", sa.String, nullable=False),
    sa.Column("record", sa.JSON, nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.CheckConstraint("length(trim(actor_id)) > 0 AND length(trim(selection_key)) > 0"),
)
final_test_event = sa.Table(
    "qualification_final_test_event",
    metadata,
    sa.Column("project_id", UUID, sa.ForeignKey(project.c.id), primary_key=True),
    sa.Column(
        "dataset_key",
        sa.String,
        sa.ForeignKey(canonical_dataset_release.c.release_key),
        primary_key=True,
    ),
    sa.Column("revision", sa.Integer, primary_key=True),
    sa.Column("actor_id", sa.String, nullable=False),
    sa.Column("action", sa.String, nullable=False),
    sa.Column("record", sa.JSON, nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.CheckConstraint("revision >= 0 AND length(trim(actor_id)) > 0"),
    sa.CheckConstraint("action IN ('register','expose','adapt','quarantine')"),
)
annotation_evidence = sa.Table(
    "qualification_annotation_evidence",
    metadata,
    sa.Column("project_id", UUID, sa.ForeignKey(project.c.id), primary_key=True),
    sa.Column("evidence_key", sa.String, primary_key=True),
    sa.Column("audit_id", UUID, sa.ForeignKey("qualification_annotation_audit.id"), nullable=False),
    sa.Column("actor_id", sa.String, nullable=False),
    sa.Column("record", sa.JSON, nullable=False),
    sa.Column("canary_model_id", UUID, sa.ForeignKey(model_release.c.id), nullable=True),
    sa.Column("canary_release_sha256", sa.String(64), nullable=True),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.CheckConstraint("length(trim(actor_id)) > 0 AND length(trim(evidence_key)) > 0"),
    sa.CheckConstraint("(canary_model_id IS NULL) = (canary_release_sha256 IS NULL)"),
    sa.CheckConstraint("canary_release_sha256 IS NULL OR canary_release_sha256 ~ '^[0-9a-f]{64}$'"),
)
annotation_audit = sa.Table(
    "qualification_annotation_audit",
    metadata,
    sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column("project_id", UUID, sa.ForeignKey(project.c.id), nullable=False),
    sa.Column("release_id", UUID, sa.ForeignKey(canonical_dataset_release.c.id), nullable=False),
    sa.Column("audit_key", sa.String, nullable=False),
    sa.Column("actor_id", sa.String, nullable=False),
    sa.Column("record", sa.JSON, nullable=False),
    sa.Column("vetoed_revisions", sa.JSON, nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.UniqueConstraint("project_id", "audit_key"),
    sa.CheckConstraint("length(trim(actor_id)) > 0 AND length(trim(audit_key)) > 0"),
)
