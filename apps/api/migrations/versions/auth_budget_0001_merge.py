"""Join independently delivered auth and budget histories without changing their DDL."""

revision = "auth_budget_0001"
down_revision = ("0003_authentication", "budget_0002")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
