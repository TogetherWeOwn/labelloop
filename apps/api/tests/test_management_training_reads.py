"""Training detail query contracts without a database or worker runtime."""

from types import SimpleNamespace
from uuid import UUID

from sqlalchemy.dialects import postgresql

from labelloop_api.management_ports import ManagementFailure
from labelloop_api.management_training_reads import training_detail


def test_training_detail_selects_canonical_receipt_timestamp_before_missing_evidence():
    statements = []

    def execute(statement):
        statements.append(str(statement.compile(dialect=postgresql.dialect())))
        return SimpleNamespace(all=lambda: [])

    try:
        training_detail(
            SimpleNamespace(execute=execute),
            SimpleNamespace(run_key="training-detail-fixture"),
            UUID(int=1),
        )
    except ManagementFailure as error:
        assert str(error) == "missing_evidence"
    else:
        raise AssertionError("missing receipt must refuse training detail")
    assert len(statements) == 1
    assert "canonical_event.created_at" in statements[0]
    assert "occurred_at" not in statements[0]
