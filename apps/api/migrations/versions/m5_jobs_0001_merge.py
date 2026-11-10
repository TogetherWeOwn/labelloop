"""Join qualification and delivered proposal-job histories without changing DDL."""

revision = "m5_jobs_0001"
down_revision = ("qualification_0003", "0006_proposal_jobs")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
