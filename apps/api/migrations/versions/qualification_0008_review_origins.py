"""Retain review provenance used by qualification; no historical record rewrites."""

from alembic import op

revision = "qualification_0008"
down_revision = "m5_auth_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION qualification_review_immutable() RETURNS trigger
        SET search_path FROM CURRENT AS $$
        BEGIN
          RAISE EXCEPTION 'review events are immutable; append new review evidence';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER qualification_review_no_update BEFORE UPDATE OR DELETE ON review_event
        FOR EACH ROW EXECUTE FUNCTION qualification_review_immutable();
        CREATE TRIGGER qualification_review_no_truncate BEFORE TRUNCATE ON review_event
        FOR EACH STATEMENT EXECUTE FUNCTION qualification_review_immutable();
    """)


def downgrade() -> None:
    op.execute("""
        DROP TRIGGER qualification_review_no_truncate ON review_event;
        DROP TRIGGER qualification_review_no_update ON review_event;
        DROP FUNCTION qualification_review_immutable();
    """)
