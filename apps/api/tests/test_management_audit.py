"""Audit guard query/wiring contracts; effective SQL grants are tested in Actions."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

from psycopg.pq import TransactionStatus

from labelloop_api.management_canonical import CanonicalManagementMutations
from labelloop_api.management_compile_worker import CanonicalCompilationWorker
from labelloop_api.management_evaluate_worker import CanonicalLocalEvaluationPublisher
from labelloop_api.management_ports import ManagementFailure
from labelloop_api.management_train_worker import CanonicalLocalTrainingWorker


class AuditCapabilityTests(TestCase):
    def assert_query(self, query):
        sql = str(query)
        self.assertIn("has_table_privilege(current_user", sql)
        self.assertIn("'UPDATE,DELETE,TRUNCATE'", sql)
        self.assertIn("has_any_column_privilege(current_user", sql)
        self.assertIn("'UPDATE'", sql)
        self.assertIn(" OR ", sql)

    def test_canonical_guard_checks_table_and_column_authority_before_locking(self):
        connection = MagicMock()
        connection.get_isolation_level.return_value = "READ COMMITTED"
        connection.execute.return_value.scalar_one.return_value = True
        engine = Mock()
        engine.dialect.name = "postgresql"
        service = CanonicalManagementMutations(engine, Mock(), uuid4())
        with self.assertRaisesRegex(ManagementFailure, "management_unavailable"):
            service.begin_mutation(connection, Mock())
        connection.execute.assert_called_once()
        self.assert_query(connection.execute.call_args.args[0])

    def test_compiler_guard_checks_table_and_column_authority_before_lineage(self):
        worker = object.__new__(CanonicalCompilationWorker)
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (True,)
        with self.assertRaisesRegex(ValueError, "must not rewrite"):
            worker.publish(connection, Mock(), {})
        cursor.execute.assert_called_once()
        self.assert_query(cursor.execute.call_args.args[0])

    def test_training_guard_checks_table_and_column_authority_before_publication(self):
        workspace = uuid4()
        worker = object.__new__(CanonicalLocalTrainingWorker)
        object.__setattr__(worker, "workspace_id", workspace)
        lease = SimpleNamespace(kind="train_local", input={"workspace_id": str(workspace)})
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"broad": True}
        with (
            patch("labelloop_api.management_train_worker.decode_training"),
            self.assertRaisesRegex(ValueError, "must not rewrite"),
        ):
            worker.publish(connection, lease, {"training": {"result": {}}})
        cursor.execute.assert_called_once()
        self.assert_query(cursor.execute.call_args.args[0])

    def test_evaluation_guard_checks_table_and_column_authority_before_publication(self):
        workspace = uuid4()
        worker = object.__new__(CanonicalLocalEvaluationPublisher)
        object.__setattr__(worker, "workspace_id", workspace)
        lease = SimpleNamespace(kind="evaluate_local", input={"workspace_id": str(workspace)})
        connection = MagicMock()
        connection.info.transaction_status = TransactionStatus.INTRANS
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"broad": True}
        with self.assertRaisesRegex(ValueError, "must not rewrite"):
            worker.publish(connection, lease, {"evaluation": {}})
        cursor.execute.assert_called_once()
        self.assert_query(cursor.execute.call_args.args[0])
