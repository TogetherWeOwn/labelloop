"""Immutable audit evidence; qualification migrations do not change prior metadata."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from labelloop_api.schema import canonical_dataset_release, project

metadata = sa.MetaData()
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
