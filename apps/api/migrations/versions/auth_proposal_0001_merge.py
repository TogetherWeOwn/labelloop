"""Join delivered auth, budget and proposal histories without changing their DDL."""

revision = "auth_proposal_0001"
down_revision = ("auth_budget_0001", "0006_proposal_jobs")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
