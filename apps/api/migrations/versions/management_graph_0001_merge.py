"""Join parallel no-DDL integration heads without rewriting either history."""

revision = "management_graph_0001"
down_revision = ("auth_jobs_0001", "auth_proposal_0001")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
