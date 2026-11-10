"""Dispatch wiring only; real restricted-role execution remains an Actions gate."""

from copy import deepcopy
from unittest import TestCase
from unittest.mock import MagicMock, patch
from uuid import uuid4

from labelloop_api.management_dispatch import CompilationDispatch, LocalMLDispatch, dispatch_kind
from labelloop_worker_core.jobs import JobStore


class DispatchContracts(TestCase):
    def fixture(self, kind="compile_variant"):
        workspace = uuid4()
        target = {
            "compile_variant": "release_id",
            "train_local": "variant_id",
            "evaluate_local": "model_id",
        }[kind]
        inputs = {"workspace_id": str(workspace), "requested_by": "admin", target: "target"}
        job = {"kind": kind, "input": inputs, "project_id": str(uuid4())}
        receipt = {
            "aggregate_id": "target",
            "actor_kind": "human",
            "actor_id": "admin",
            "payload": {
                "workspace_id": str(workspace),
                "result": {"project_id": job["project_id"]},
                "evidence": {"inputs": deepcopy(inputs)},
            },
        }
        connection = MagicMock()
        connection.__enter__.return_value = connection
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = job
        cursor.fetchall.return_value = [receipt]
        store = JobStore(MagicMock(return_value=connection))
        return store, workspace, connection, cursor, job, receipt

    def test_read_only_dispatch_uses_owned_job_and_bounded_receipt_lookup(self):
        for kind, event in (
            ("compile_variant", "management.compile_variant"),
            ("train_local", "management.start_training"),
            ("evaluate_local", "management.start_evaluation"),
        ):
            with self.subTest(kind=kind):
                store, workspace, connection, cursor, _, _ = self.fixture(kind)
                self.assertEqual(dispatch_kind(store, workspace, "job", (kind,)), kind)
                connection.execute.assert_called_once_with(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                calls = cursor.execute.call_args_list
                self.assertEqual(calls[0].args[1], ("job", workspace))
                self.assertIn("p.workspace_id=%s", calls[0].args[0])
                self.assertIn("p.project_key=j.project_key", calls[0].args[0])
                self.assertEqual(calls[1].args[1], (event, "job"))
                self.assertIn("LIMIT 2", calls[1].args[0])

    def test_invalid_identity_refuses_before_connection(self):
        for workspace, job_id in (("workspace", "job"), (uuid4(), ""), (uuid4(), None)):
            with self.subTest(workspace=workspace, job_id=job_id):
                store = JobStore(MagicMock())
                with self.assertRaises(ValueError):
                    dispatch_kind(store, workspace, job_id, ("compile_variant",))
                store.connect.assert_not_called()

    def test_missing_job_or_wrong_lane_refuses_before_receipt_lookup(self):
        for job in (None, {"kind": "train_local"}, {"kind": "provider_selected"}):
            with self.subTest(job=job):
                store, workspace, _, cursor, _, _ = self.fixture()
                cursor.fetchone.return_value = job
                with self.assertRaises(ValueError):
                    dispatch_kind(store, workspace, "job", ("compile_variant",))
                cursor.fetchall.assert_not_called()

    def test_malformed_or_foreign_input_refuses(self):
        for field, value in (
            ("workspace_id", str(uuid4())),
            ("requested_by", ""),
            ("requested_by", None),
            ("release_id", None),
            ("release_id", ""),
        ):
            with self.subTest(field=field, value=value):
                store, workspace, _, cursor, job, _ = self.fixture()
                job["input"][field] = value
                with self.assertRaises(ValueError):
                    dispatch_kind(store, workspace, "job", ("compile_variant",))
                cursor.fetchall.assert_not_called()

    def test_missing_or_duplicate_receipts_refuse(self):
        for count in (0, 2):
            with self.subTest(count=count):
                store, workspace, _, cursor, _, receipt = self.fixture()
                cursor.fetchall.return_value = [receipt] * count
                with self.assertRaises(ValueError):
                    dispatch_kind(store, workspace, "job", ("compile_variant",))

    def test_each_receipt_identity_must_match(self):
        for field, value in (
            ("aggregate_id", "other"),
            ("actor_kind", "system"),
            ("actor_id", "other"),
        ):
            with self.subTest(field=field):
                store, workspace, _, _, _, receipt = self.fixture()
                receipt[field] = value
                with self.assertRaises(ValueError):
                    dispatch_kind(store, workspace, "job", ("compile_variant",))

    def test_corrupt_receipt_payload_refuses(self):
        for change in ("workspace", "project", "inputs", "result", "evidence", "payload"):
            with self.subTest(change=change):
                store, workspace, _, _, _, receipt = self.fixture()
                payload = receipt["payload"]
                if change == "workspace":
                    payload["workspace_id"] = str(uuid4())
                elif change == "project":
                    payload["result"]["project_id"] = str(uuid4())
                elif change == "inputs":
                    payload["evidence"]["inputs"]["release_id"] = "other"
                elif change == "payload":
                    receipt["payload"] = None
                else:
                    payload[change] = None
                with self.assertRaises(ValueError):
                    dispatch_kind(store, workspace, "job", ("compile_variant",))

    def test_fixed_lanes_use_only_persisted_kind_and_exact_job(self):
        for kind, factory_name in (
            ("compile_variant", "compilation_runner"),
            ("train_local", "local_training_runner"),
            ("evaluate_local", "local_evaluation_runner"),
        ):
            with self.subTest(kind=kind):
                store, workspace, _, _, _, _ = self.fixture(kind)
                objects, engine, backend, exporter, predictor = (object() for _ in range(5))
                dispatch = (
                    CompilationDispatch(store, objects, workspace)
                    if kind == "compile_variant"
                    else LocalMLDispatch(
                        store, engine, objects, workspace, backend, exporter, predictor
                    )
                )
                with patch("labelloop_api.management_dispatch." + factory_name) as factory:
                    factory.return_value.run_once.return_value = False
                    self.assertFalse(dispatch.run_once("job", "worker"))
                    factory.assert_called_once()
                    self.assertEqual(factory.call_args.kwargs, {"job_id": "job"})
                    args = factory.call_args.args
                    if kind == "compile_variant":
                        self.assertEqual(args, (store, objects, workspace))
                    elif kind == "train_local":
                        self.assertEqual(
                            args, (store, engine, objects, workspace, backend, exporter)
                        )
                    else:
                        self.assertEqual(args, (store, engine, objects, workspace, predictor))
                    factory.return_value.run_once.assert_called_once_with("worker")

    def test_invalid_worker_identity_refuses_before_connection(self):
        store = JobStore(MagicMock())
        for dispatch in (
            CompilationDispatch(store, object(), uuid4()),
            LocalMLDispatch(store, object(), object(), uuid4(), object(), object(), object()),
        ):
            for worker in (None, ""):
                with self.assertRaises(ValueError):
                    dispatch.run_once("job", worker)
        store.connect.assert_not_called()

    def test_foreign_job_never_constructs_a_runner(self):
        store, workspace, _, cursor, _, _ = self.fixture()
        cursor.fetchone.return_value = None
        with patch("labelloop_api.management_dispatch.compilation_runner") as factory:
            with self.assertRaises(ValueError):
                CompilationDispatch(store, object(), workspace).run_once("job", "worker")
            factory.assert_not_called()
