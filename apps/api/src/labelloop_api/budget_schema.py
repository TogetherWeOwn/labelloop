"""Budget tables, isolated from frozen canonical and authentication metadata."""

import sqlalchemy as sa

from labelloop_api.schema import project

metadata = sa.MetaData()
head = sa.Table(
    "budget_head",
    metadata,
    sa.Column("project_id", sa.String, sa.ForeignKey(project.c.project_key), primary_key=True),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("policy", sa.JSON, nullable=False),
    sa.Column("pause_reason", sa.String),
    sa.CheckConstraint("revision > 0"),
    sa.CheckConstraint("pause_reason IN ('insufficient_credits', 'estimate_overrun')"),
)
dry_run = sa.Table(
    "budget_dry_run",
    metadata,
    sa.Column("project_id", sa.String, sa.ForeignKey(head.c.project_id), primary_key=True),
    sa.Column("operation_id", sa.String, primary_key=True),
    sa.Column("digest", sa.String(64), nullable=False),
    sa.Column("policy_revision", sa.Integer, nullable=False),
    sa.Column("request", sa.JSON, nullable=False),
    sa.Column("intent", sa.JSON, nullable=False),
)
operation = sa.Table(
    "budget_operation",
    metadata,
    sa.Column("project_id", sa.String, sa.ForeignKey(head.c.project_id), primary_key=True),
    sa.Column("operation_id", sa.String, primary_key=True),
    sa.Column("dry_run_digest", sa.String(64), nullable=False),
    sa.Column("policy_revision", sa.Integer, nullable=False),
    sa.Column("entry", sa.JSON, nullable=False),
    sa.Column("intent", sa.JSON, nullable=False),
    sa.Column("prices", sa.JSON),
    sa.Column("claim_id", sa.String(36), unique=True),
    sa.Column("claimed_by", sa.String),
    sa.Column("evidence", sa.JSON),
    sa.Column("review_pause", sa.String),
    sa.CheckConstraint("review_pause IN ('approval_exhausted')"),
    sa.CheckConstraint("(claim_id IS NULL) = (claimed_by IS NULL)"),
)
review_request = sa.Table(
    "budget_review_request",
    metadata,
    sa.Column("project_id", sa.String, primary_key=True),
    sa.Column("operation_id", sa.String, primary_key=True),
    sa.Column("request_id", sa.String, primary_key=True),
    sa.Column("image_id", sa.String, nullable=False),
    sa.Column("pass_index", sa.Integer, nullable=False),
    sa.Column("configuration_sha256", sa.String(64), nullable=False),
    sa.Column("forecast", sa.JSON, nullable=False),
    sa.Column("estimate", sa.BigInteger, nullable=False),
    sa.Column("claim_id", sa.String(36), nullable=False, unique=True),
    sa.Column("claimed_by", sa.String, nullable=False),
    sa.Column("state", sa.String, nullable=False),
    sa.Column("actual", sa.BigInteger),
    sa.Column("evidence", sa.JSON),
    sa.ForeignKeyConstraint(
        ["project_id", "operation_id"],
        [operation.c.project_id, operation.c.operation_id],
    ),
    sa.UniqueConstraint("project_id", "operation_id", "image_id", "pass_index"),
    sa.CheckConstraint("estimate >= 0 AND pass_index >= 0"),
    sa.CheckConstraint("actual >= 0"),
    sa.CheckConstraint("(state = 'settled') = (actual IS NOT NULL)"),
    sa.CheckConstraint("state IN ('dispatched', 'unconfirmed', 'settled', 'insufficient_credits')"),
)
event = sa.Table(
    "budget_event",
    metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("project_id", sa.String, sa.ForeignKey(head.c.project_id), nullable=False),
    sa.Column("operation_id", sa.String),
    sa.Column("principal_id", sa.String(256), nullable=False),
    sa.Column("principal_kind", sa.String(8), nullable=False),
    sa.Column("principal_role", sa.String(32), nullable=False),
    sa.Column("principal_version", sa.String(64), nullable=False),
    sa.Column("policy_revision", sa.Integer, nullable=False),
    sa.Column("action", sa.String(32), nullable=False),
    sa.Column("record", sa.JSON, nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
)
