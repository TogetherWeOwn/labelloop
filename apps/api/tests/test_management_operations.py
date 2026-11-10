"""Composition wiring only; real transaction/HTTP evidence lives in Actions DB cases."""

import inspect
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event

from labelloop_api.management_canonical import CanonicalManagementMutations
from labelloop_api.management_compilation import CanonicalManagementCompilation
from labelloop_api.management_contracts import InitialSourceGrouping
from labelloop_api.management_evaluation import CanonicalManagementEvaluation
from labelloop_api.management_grouping_publication import CanonicalInitialGrouping
from labelloop_api.management_operations import CanonicalManagementOperations
from labelloop_api.management_ports import ManagementFailure, ManagementOperations, MutationContext
from labelloop_api.management_reads import CanonicalManagementReads
from labelloop_api.management_training import ApprovedTrainingProfile, CanonicalManagementTraining
from labelloop_compiler.ingest import LocalObjectStore
from labelloop_worker_core.jobs import JobStore


@pytest.fixture
def operations(tmp_path):
    engine = create_engine("postgresql+psycopg://")

    @event.listens_for(engine, "do_connect")
    def no_database(*args):
        pytest.fail("composition contract must not connect to a database")

    service = CanonicalManagementOperations(engine, LocalObjectStore(tmp_path), uuid4())
    yield service
    engine.dispose()


def test_facade_has_every_typed_port_and_keeps_configuration_immutable(operations):
    for name, method in vars(ManagementOperations).items():
        if name.startswith("_") or not callable(method):
            continue
        assert callable(getattr(operations, name))
        assert list(inspect.signature(getattr(operations, name)).parameters) == [
            key for key in inspect.signature(method).parameters if key != "self"
        ]
    assert operations._training().enqueue is JobStore.enqueue_in_transaction
    assert operations._evaluation().enqueue is JobStore.enqueue_in_transaction
    assert operations._training().profiles == operations._evaluation().protocols == ()
    with pytest.raises(FrozenInstanceError):
        operations.workspace_id = uuid4()


@pytest.mark.parametrize(
    "name",
    [
        "create_project",
        "save_scope_draft",
        "save_guide_draft",
        "save_class_catalog_draft",
        "publish_class_catalog",
        "save_source_policy_draft",
        "approve_setup_step",
        "enroll_reference",
        "accept_reference",
        "revalidate_reference",
        "withdraw_reference",
    ],
)
def test_setup_mutations_reuse_canonical_implementation_unchanged(name):
    assert getattr(CanonicalManagementOperations, name) is getattr(
        CanonicalManagementMutations, name
    )


@pytest.mark.parametrize(
    "adapter,name",
    [(CanonicalManagementCompilation, "compile_variant")]
    + [(CanonicalManagementTraining, name) for name in ("start_training", "list_training_profiles")]
    + [(CanonicalManagementEvaluation, "start_evaluation")]
    + [
        (CanonicalManagementReads, name)
        for name in (
            "list_projects",
            "list_resources",
            "read",
            "read_export",
            "read_project",
            "read_reference_lifecycle",
            "read_anchor_pack",
            "read_release",
            "read_recipe_preview",
            "read_training",
            "read_evaluation",
        )
    ],
)
def test_explicit_delegates_preserve_arguments_and_one_configuration(
    operations, monkeypatch, adapter, name
):
    result = object()
    args = tuple(object() for _ in inspect.signature(getattr(operations, name)).parameters)
    calls = []

    def target(bound, *actual):
        calls.append(actual)
        if isinstance(bound, CanonicalManagementReads):
            assert bound.engine is operations.engine
            assert bound.workspace_id == operations.workspace_id
        else:
            assert bound.mutations is operations
            assert bound.enqueue is JobStore.enqueue_in_transaction
        return result

    monkeypatch.setattr(adapter, name, target)
    assert getattr(operations, name)(*args) is result
    assert calls == [args]


@pytest.mark.parametrize("field", ["profiles", "protocols"])
@pytest.mark.parametrize("value", [[], None])
def test_facade_refuses_mutable_or_missing_server_configuration(operations, field, value):
    with pytest.raises((ValueError, TypeError)):
        CanonicalManagementOperations(
            operations.engine, operations.store, operations.workspace_id, **{field: value}
        )


def test_initial_grouping_requires_explicit_reader_and_preserves_canonical_arguments(
    operations, monkeypatch
):
    from test_management_contract import Actor

    actor = Actor()
    payload = InitialSourceGrouping(
        expected_revision=3,
        ontology_id="ontology-1",
        source_policy_sha256="a" * 64,
        annotation_revisions={"a": 0, "b": 1},
    )
    context = MutationContext.bind(actor, "grouping", "publish_initial_grouping", "p", payload)
    with pytest.raises(ManagementFailure, match="management_unavailable"):
        operations.publish_initial_grouping(actor, context, payload)
    with pytest.raises(ManagementFailure, match="invalid_request"):
        operations.publish_initial_grouping(actor, replace(context, target_id="foreign"), payload)
    reader = SimpleNamespace(read=lambda key: pytest.fail("composition must not read objects"))
    configured = replace(operations, grouping_objects=reader)
    result = object()
    calls = []

    def publish(bound, *args):
        assert bound.canonical is configured and bound.objects is reader
        calls.append(args)
        return result

    monkeypatch.setattr(CanonicalInitialGrouping, "publish", publish)
    assert configured.publish_initial_grouping(actor, context, payload) is result
    assert calls == [(actor, context, payload)]


@pytest.mark.parametrize("reader", [object(), SimpleNamespace(read=None)])
def test_initial_grouping_configuration_refuses_nonreaders_without_io(operations, reader):
    with pytest.raises(ValueError, match="explicit trusted object reader"):
        replace(operations, grouping_objects=reader)


def test_facade_refuses_duplicate_project_profile_identities(operations):
    profile = ApprovedTrainingProfile(uuid4(), "baseline", 1, "full-letterbox-320-v1", "1" * 64)
    with pytest.raises(ValueError, match="uniquely project-scoped"):
        CanonicalManagementOperations(
            operations.engine,
            operations.store,
            operations.workspace_id,
            profiles=(profile, profile),
        )
