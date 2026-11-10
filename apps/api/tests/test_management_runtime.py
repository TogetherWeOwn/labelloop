"""Source-composition contracts, not production connection or grant evidence."""

import hashlib
import inspect
import io
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import Mock, patch
from uuid import uuid4

from sqlalchemy import create_engine, event

from labelloop_api.main import app
from labelloop_api.management_dispatch import CompilationDispatch, LocalMLDispatch
from labelloop_api.management_ports import ManagementFailure
from labelloop_api.management_runtime import api_operations, compiler_dispatch, local_ml_dispatch
from labelloop_api.management_training import ApprovedTrainingProfile
from labelloop_worker_core.jobs import JobStore


class RuntimeCompositionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("postgresql+psycopg://")
        self.addCleanup(self.engine.dispose)
        event.listen(self.engine, "do_connect", self.forbidden)
        self.store = JobStore(self.forbidden)
        self.client = Mock()
        self.workspace = uuid4()
        self.objects = {
            "client": self.client,
            "bucket": "fixture",
            "max_object_bytes": 1024,
            "workspace_id": self.workspace,
        }

    def forbidden(self, *args, **kwargs):
        self.fail("composition must perform no connection, object or backend I/O")

    def api(self, **overrides):
        return api_operations(engine=self.engine, **{**self.objects, **overrides})

    def compiler(self, **overrides):
        return compiler_dispatch(store=self.store, **{**self.objects, **overrides})

    def ml(self, **overrides):
        return local_ml_dispatch(
            store=self.store,
            engine=self.engine,
            backend=self.forbidden,
            exporter=self.forbidden,
            predictor=self.forbidden,
            **{**self.objects, **overrides},
        )

    def test_construction_is_lazy_and_default_app_stays_unmounted(self):
        self.api()
        self.compiler()
        self.ml()
        self.assertEqual(self.client.mock_calls, [])
        self.assertNotIn("/projects", {route.path for route in app.routes})

    def test_api_keeps_only_explicit_grouping_read_and_refuses_object_writes(self):
        operations = self.api()
        self.assertFalse(hasattr(operations.grouping_objects, "put"))
        self.assertFalse(hasattr(operations.grouping_objects, "client"))
        with self.assertRaisesRegex(ManagementFailure, "management_unavailable"):
            operations.store.put("unused", b"unused")
        self.assertEqual(self.client.mock_calls, [])
        with self.assertRaises(FrozenInstanceError):
            operations.grouping_objects.read = self.forbidden

    def test_api_reader_delegates_to_bounded_content_transport(self):
        data = b"canonical raster fixture"
        digest = hashlib.sha256(data).hexdigest()
        key = f"sha256/{digest[:2]}/{digest}"
        response = Mock()
        response.read.side_effect = io.BytesIO(data).read
        self.client.get_object.return_value = response
        self.assertEqual(self.api().grouping_objects.read(key), data)
        self.client.get_object.assert_called_once_with("fixture", key)
        response.close.assert_called_once_with()
        response.release_conn.assert_called_once_with()
        self.client.put_object.assert_not_called()

    def test_api_reader_refuses_oversized_content_and_closes_response(self):
        data = b"too large"
        digest = hashlib.sha256(data).hexdigest()
        response = Mock()
        response.read.side_effect = io.BytesIO(data).read
        self.client.get_object.return_value = response
        with self.assertRaisesRegex(ValueError, "transport byte limit"):
            self.api(max_object_bytes=2).grouping_objects.read(f"sha256/{digest[:2]}/{digest}")
        response.close.assert_called_once_with()
        response.release_conn.assert_called_once_with()

    def test_profiles_are_explicit_and_preserve_native_enqueue(self):
        profile = ApprovedTrainingProfile(uuid4(), "baseline", 1, "full-letterbox-320-v1", "a" * 64)
        operations = self.api(profiles=(profile,))
        self.assertEqual(operations.profiles, (profile,))
        self.assertIs(operations.engine, self.engine)
        self.assertEqual(operations.workspace_id, self.workspace)
        self.assertIs(operations._training().enqueue, JobStore.enqueue_in_transaction)
        self.assertIs(operations._evaluation().enqueue, JobStore.enqueue_in_transaction)
        with self.assertRaises(ValueError):
            self.api(profiles=[profile])
        self.assertEqual(self.client.mock_calls, [])

    def test_compiler_has_no_ml_or_api_capabilities(self):
        dispatch = self.compiler()
        self.assertIsInstance(dispatch, CompilationDispatch)
        self.assertIs(dispatch.store, self.store)
        self.assertEqual(dispatch.workspace_id, self.workspace)
        for name in ("backend", "exporter", "predictor", "engine", "operations"):
            self.assertFalse(hasattr(dispatch, name))
        self.assertIs(dispatch.objects.client, self.client)
        self.assertEqual(dispatch.objects.bucket, "fixture")
        self.assertEqual(dispatch.objects.max_object_bytes, 1024)

    def test_ml_keeps_supplied_execution_engine_store_and_callbacks(self):
        dispatch = self.ml()
        self.assertIsInstance(dispatch, LocalMLDispatch)
        self.assertIs(dispatch.engine, self.engine)
        self.assertIs(dispatch.store, self.store)
        self.assertEqual(dispatch.workspace_id, self.workspace)
        self.assertEqual(dispatch.backend, self.forbidden)
        self.assertEqual(dispatch.exporter, self.forbidden)
        self.assertEqual(dispatch.predictor, self.forbidden)
        self.assertIs(dispatch.objects.client, self.client)
        self.assertEqual(self.client.mock_calls, [])

    def test_invalid_object_bounds_refuse_without_io_in_every_lane(self):
        for factory in (self.api, self.compiler, self.ml):
            for cap in (True, 0, -1, 256 * 1024 * 1024 + 1):
                with self.subTest(factory=factory.__name__, cap=cap), self.assertRaises(ValueError):
                    factory(max_object_bytes=cap)
            with self.assertRaises(ValueError):
                factory(bucket="")
        self.assertEqual(self.client.mock_calls, [])

    def test_every_lane_requires_a_workspace_uuid(self):
        for factory in (self.api, self.compiler, self.ml):
            with self.subTest(factory=factory.__name__), self.assertRaises(ValueError):
                factory(workspace_id=str(self.workspace))
        self.assertEqual(self.client.mock_calls, [])

    def test_invalid_ml_callbacks_and_non_postgres_engine_refuse(self):
        for name in ("backend", "exporter", "predictor"):
            arguments = dict(
                store=self.store,
                engine=self.engine,
                backend=self.forbidden,
                exporter=self.forbidden,
                predictor=self.forbidden,
                **self.objects,
            )
            arguments[name] = None
            with self.subTest(callback=name), self.assertRaisesRegex(ValueError, "callbacks"):
                local_ml_dispatch(**arguments)
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with self.assertRaisesRegex(ValueError, "Postgres"):
            api_operations(engine=engine, **self.objects)
        with self.assertRaisesRegex(ValueError, "Postgres"):
            local_ml_dispatch(
                engine=engine,
                store=self.store,
                backend=self.forbidden,
                exporter=self.forbidden,
                predictor=self.forbidden,
                **self.objects,
            )

    def test_factories_require_supplied_clients_not_endpoint_or_credential_fields(self):
        for factory in (api_operations, compiler_dispatch, local_ml_dispatch):
            parameters = inspect.signature(factory).parameters
            self.assertIn("client", parameters)
            self.assertIn("workspace_id", parameters)
            for name in ("endpoint", "access_key", "secret_key", "dsn", "start", "scan"):
                self.assertNotIn(name, parameters)
            self.assertFalse(any(p.kind == p.VAR_KEYWORD for p in parameters.values()))

    def test_composed_dispatch_preserves_exact_job_and_worker_identity(self):
        for factory, kind, runner_name, lane in (
            (self.compiler, "compile_variant", "compilation_runner", ("compile_variant",)),
            (self.ml, "train_local", "local_training_runner", ("train_local", "evaluate_local")),
            (
                self.ml,
                "evaluate_local",
                "local_evaluation_runner",
                ("train_local", "evaluate_local"),
            ),
        ):
            dispatch = factory()
            with (
                patch(
                    "labelloop_api.management_dispatch.dispatch_kind", return_value=kind
                ) as check,
                patch("labelloop_api.management_dispatch." + runner_name) as runner,
            ):
                runner.return_value.run_once.return_value = True
                self.assertTrue(dispatch.run_once("durable-job", "worker"))
                check.assert_called_once_with(self.store, self.workspace, "durable-job", lane)
                self.assertEqual(runner.call_args.kwargs, {"job_id": "durable-job"})
                self.assertIs(runner.call_args.args[0], self.store)
                self.assertIn(dispatch.objects, runner.call_args.args)
                self.assertIn(self.workspace, runner.call_args.args)
                runner.return_value.run_once.assert_called_once_with("worker")
        self.assertEqual(self.client.mock_calls, [])


if __name__ == "__main__":
    unittest.main()
