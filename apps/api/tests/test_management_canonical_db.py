"""Real canonical transactions, only against a disposable Actions Postgres service.

The explicit session-composition case covers project create/read/replay only;
these are not shared-queue, setup-publication or project-to-export tests.
"""

import base64
import io
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from PIL import Image
from sqlalchemy import create_engine, event, func, insert, select, text
from sqlalchemy.exc import DBAPIError

from labelloop_api.management_canonical import CanonicalManagementMutations
from labelloop_api.management_contracts import (
    AssetIngest,
    ClassCatalogDraft,
    ClassCatalogPublish,
    GuideDraft,
    ProjectCreate,
    ReferenceAccept,
    ReferenceEnroll,
    ReferenceRevalidate,
    ReferenceWithdraw,
    ReleaseFreeze,
    ScopeDraft,
    SetupStepApprove,
    SourcePolicyDraft,
)
from labelloop_api.management_ports import ManagementFailure, MutationContext
from labelloop_api.management_reads import CanonicalManagementReads
from labelloop_api.revision_store import append_review, completeness_context_hash
from labelloop_api.schema import (
    annotation_revision,
    borderline_review,
    canonical_dataset_release,
    canonical_event,
    completeness_assessment,
    ontology_release,
    project,
    reference_example,
    similarity_relation,
    source_asset,
    source_split_assignment,
)
from labelloop_compiler.ingest import LocalObjectStore

pytestmark = pytest.mark.skipif(
    os.environ.get("LABELLOOP_MANAGEMENT_DB_TESTS") != "1",
    reason="requires disposable Actions Postgres with migrated schema",
)


@dataclass(frozen=True)
class Actor:
    id: str = "fixture-admin"
    kind: str = "human"
    role: str = "admin"

    def permits(self, permission):
        return self.kind == "human" and self.role == "admin" and permission in {"admin", "review"}


@pytest.fixture
def database(tmp_path):
    owner = create_engine(os.environ["DATABASE_URL"])
    role = "management_fixture_" + uuid4().hex
    # Generated identifiers only. Never use a request field in role or SQL names.
    with owner.begin() as connection:
        connection.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
        connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        connection.execute(
            text(
                f"GRANT SELECT, INSERT ON project, ontology_release, source_asset, "
                f"annotation_revision, canonical_event, canonical_dataset_release, "
                f"reference_example, "
                f"completeness_assessment, source_split_assignment, similarity_relation, "
                f'borderline_review TO "{role}"'
            )
        )
        connection.execute(
            text(f'GRANT UPDATE (scope, domain, access_policy) ON project TO "{role}"')
        )
        connection.execute(text(f'GRANT UPDATE (source_uri) ON source_asset TO "{role}"'))
        connection.execute(text(f'GRANT UPDATE (approved) ON reference_example TO "{role}"'))
    runtime = create_engine(
        os.environ["DATABASE_URL"],
        connect_args={
            "options": f"-c role={role} -c statement_timeout=15000 -c lock_timeout=10000"
        },
    )
    service = CanonicalManagementMutations(runtime, LocalObjectStore(tmp_path), uuid4())
    try:
        yield service, owner, role
    finally:
        runtime.dispose()
        with owner.begin() as connection:
            connection.execute(text(f'DROP OWNED BY "{role}"'))
            connection.execute(text(f'DROP ROLE "{role}"'))
        owner.dispose()


@pytest.mark.parametrize("recipe", ["full-letterbox-320-v1", "full-letterbox-640-v1"])
def test_registered_compiler_claims_only_requested_job_and_publishes(
    database, compilation, compiler_worker, recipe
):
    from labelloop_api.management_workers import compilation_runner

    service, owner, _ = database
    created, _, _, payload = release_inputs(service, owner)
    release = freeze(service, created.id, payload)
    accepted = compile_request(compilation, release.id, recipe=recipe)
    other = compile_request(
        compilation,
        release.id,
        recipe="full-letterbox-640-v1" if "320" in recipe else "full-letterbox-320-v1",
        key="other-compilation",
    )
    runner = compilation_runner(
        compiler_worker.store,
        compiler_worker.objects,
        service.workspace_id,
        job_id=accepted.job_id,
    )
    assert runner.run_once("registered-compiler")
    assert not runner.run_once("registered-compiler")
    manifest, applications, events, job = publication_state(owner, accepted)
    assert manifest["recipe"]["recipe_id"] == recipe
    assert applications == len(events) == 1 and job.status == "succeeded"
    assert events[0].actor_id == "registered-compiler"
    assert publication_state(owner, other) == (None, 0, [], ("queued", 0))


@pytest.mark.parametrize("recipe", ["full-letterbox-320-v1", "full-letterbox-640-v1"])
@pytest.mark.parametrize("saved_export", [False, True])
def test_registered_training_resumes_native_stages_and_publishes_once(
    database, compilation, compiler_worker, recipe, saved_export
):
    from types import SimpleNamespace

    from labelloop_api.management_workers import local_training_runner

    stage, _, accepted, lease, training, exported, paths = prepared_export_stage(
        database, compilation, compiler_worker, recipe
    )
    compiler_worker.store.save_stage(lease, "training", training)
    if saved_export:
        compiler_worker.store.save_stage(lease, "export", exported)
    with database[1].begin() as connection:
        connection.execute(
            text(
                "UPDATE worker_job SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE job_id=:id"
            ),
            {"id": lease.job_id},
        )

    def unexpected(*args, **kwargs):
        pytest.fail("saved training/export must not repeat ML or object I/O")

    calls = []

    def exporter(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("synthetic transient export failure")
        return stage.exporter(**kwargs)

    runner = local_training_runner(
        compiler_worker.store,
        stage.training.engine,
        SimpleNamespace(read=unexpected, put=unexpected)
        if saved_export
        else stage.training.objects,
        stage.training.workspace_id,
        unexpected,
        unexpected if saved_export else exporter,
        job_id=accepted.job_id,
    )
    assert runner.run_once("registered-ml")
    if not saved_export:
        assert model_publication_state(database[1], accepted) == ([], [], [])
        assert training_publication_state(database[1], accepted)[:4] == (None, [], [], 0)
        with database[1].begin() as connection:
            connection.execute(
                text(
                    "UPDATE worker_job SET available_at=clock_timestamp()-interval '1 second' "
                    "WHERE job_id=:id"
                ),
                {"id": lease.job_id},
            )
        assert runner.run_once("registered-ml")
    assert not runner.run_once("registered-ml")
    models, links, events = model_publication_state(database[1], accepted)
    assert len(models) == len(links) == len(events) == 1
    assert events[0].actor_id == "registered-ml"
    assert events[0].payload["requested_by"] == Actor().id
    job = training_publication_state(database[1], accepted)[4]
    assert job.status == "succeeded" and job.generation == (2 if saved_export else 3)
    assert calls == ([] if saved_export else [1, 1])
    assert all(not path.exists() for path in paths)


@pytest.mark.parametrize("recipe", ["full-letterbox-320-v1", "full-letterbox-640-v1"])
@pytest.mark.parametrize("saved_evaluation", [False, True])
def test_registered_evaluation_executes_or_resumes_and_links_once(
    database, compilation, compiler_worker, recipe, saved_evaluation
):
    from types import SimpleNamespace

    from labelloop_api.management_workers import local_evaluation_runner
    from labelloop_worker_core.jobs import StageContext

    worker, accepted, lease, paths, reads = prepared_evaluation_stage(
        database, compilation, compiler_worker, recipe
    )
    if saved_evaluation:
        record = worker.evaluate(StageContext(compiler_worker.store, lease, {}))
        compiler_worker.store.save_stage(lease, "evaluation", record)
        paths.clear()
        reads.clear()
    with database[1].begin() as connection:
        connection.execute(
            text(
                "UPDATE worker_job SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE job_id=:id"
            ),
            {"id": lease.job_id},
        )

    def unexpected(*args, **kwargs):
        pytest.fail("saved evaluation must not repeat inference or object I/O")

    runner = local_evaluation_runner(
        compiler_worker.store,
        worker.engine,
        SimpleNamespace(read=unexpected, put=unexpected) if saved_evaluation else worker.objects,
        worker.workspace_id,
        unexpected if saved_evaluation else worker.predictor,
        job_id=accepted.job_id,
    )
    assert runner.run_once("registered-evaluator")
    assert not runner.run_once("registered-evaluator")
    rows, links, events, applications, job = evaluation_publication_state(database[1], lease)
    assert len(rows) == len(links) == len(events) == applications == 1
    assert job.status == "succeeded" and job.generation == 2
    assert events[0].actor_id == "registered-evaluator"
    assert bool(paths) == bool(reads) == (not saved_evaluation)
    assert all(not path.exists() for path in paths)


@pytest.mark.parametrize("recipe", ["full-letterbox-320-v1", "full-letterbox-640-v1"])
@pytest.mark.parametrize(
    "state", ["queued", "running", "retry", "failed", "cancelled", "completed"]
)
def test_owned_job_observations_follow_native_queue_without_publishing_resources(
    database, compilation, compiler_worker, recipe, state
):
    from labelloop_api.management_workers import compilation_runner

    service, owner, _ = database
    created, _, _, payload = release_inputs(service, owner)
    release = freeze(service, created.id, payload)
    accepted = compile_request(compilation, release.id, recipe=recipe)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    if state == "completed":
        assert compilation_runner(
            compiler_worker.store,
            compiler_worker.objects,
            service.workspace_id,
            job_id=accepted.job_id,
        ).run_once("job-reader-compiler")
    elif state == "cancelled":
        with owner.connect() as connection:
            key = connection.execute(
                select(project.c.project_key).where(project.c.id == UUID(created.id))
            ).scalar_one()
        assert compiler_worker.store.cancel(key, accepted.job_id)
    elif state != "queued":
        for attempt in range(3 if state == "failed" else 1):
            if attempt:
                with owner.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE worker_job SET available_at=clock_timestamp() WHERE job_id=:id"
                        ),
                        {"id": accepted.job_id},
                    )
            lease = compiler_worker.store.claim(
                "compile_variant", "job-reader", job_id=accepted.job_id
            )
            assert lease is not None
            if state != "running":
                compiler_worker.store.fail(lease)
    expected = "queued" if state == "retry" else state
    before = counts(owner, created.id), publication_state(owner, accepted)
    page = reads.list_resources(Reader(), created.id, "job", None, 1)
    assert page.next_cursor is None and len(page.items) == 1
    observed = reads.read(Reader(), "job", accepted.job_id)
    assert page.items == [observed]
    assert observed.model_dump() == {
        "id": accepted.job_id,
        "project_id": created.id,
        "kind": "job",
        "revision": 1,
        "state": expected,
    }
    assert observed == reads.read(Actor(), "job", accepted.job_id)
    if state != "completed":
        assert reads.list_resources(Reader(), created.id, "variant", None, 100).items == []
    assert (counts(owner, created.id), publication_state(owner, accepted)) == before


@pytest.mark.parametrize(
    "overrides", [{"kind": "service"}, {"kind": "model"}, {"granted": False}, {"id": ""}]
)
def test_job_reads_deny_untrusted_principals_before_lookup(database, overrides):
    service, _, _ = database
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    with pytest.raises(ManagementFailure, match="permission_denied"):
        reads.read(Reader(**overrides), "job", "unknown")
    with pytest.raises(ManagementFailure, match="permission_denied"):
        reads.list_resources(Reader(**overrides), "unknown", "job", None, 1)


@pytest.mark.parametrize("limit", [1, 2, 100])
def test_job_pages_are_select_only_scoped_and_use_stable_intent_cursors(
    database, compilation, compiler_worker, limit
):
    from labelloop_api.management_reads import page_position

    service, owner, role = database
    created, _, _, payload = release_inputs(service, owner)
    release = freeze(service, created.id, payload)
    jobs = [
        compile_request(compilation, release.id, recipe=recipe, key=f"job-page-{index}").job_id
        for index, recipe in enumerate(["full-letterbox-320-v1", "full-letterbox-640-v1"])
    ]
    other = create(service, key="other-job-project")
    with owner.begin() as connection:
        connection.execute(
            text(f'REVOKE INSERT, UPDATE ON ALL TABLES IN SCHEMA public FROM "{role}"')
        )
    readonly = create_engine(
        os.environ["DATABASE_URL"],
        connect_args={
            "options": f"-c role={role} -c default_transaction_read_only=on",
        },
    )
    try:
        reads = CanonicalManagementReads(readonly, service.workspace_id)
        page = reads.list_resources(Reader(), created.id, "job", None, limit)
        found = list(page.items)
        if limit == 1:
            cursor = page.next_cursor
            assert cursor is not None
            with pytest.raises(ManagementFailure, match="invalid_request"):
                reads.list_resources(Reader(), other.id, "job", cursor, limit)
            with pytest.raises(ManagementFailure, match="invalid_request"):
                reads.list_resources(Reader(), created.id, "asset", cursor, limit)
            page = reads.list_resources(Reader(), created.id, "job", cursor, limit)
            found.extend(page.items)
        assert page.next_cursor is None and sorted(item.id for item in found) == sorted(jobs)
        assert reads.list_resources(Reader(), other.id, "job", None, limit).items == []
        scope, _ = page_position(service.workspace_id, other.id, "job", None, limit)
        assert (
            reads.list_resources(Reader(), other.id, "job", scope + "." + "0" * 32, limit).items
            == []
        )
        for identifier in [*jobs, "unknown"]:
            with pytest.raises(ManagementFailure, match="not_found"):
                replace(reads, workspace_id=uuid4()).read(Reader(), "job", identifier)
        with pytest.raises(ManagementFailure, match="not_found"):
            reads.read(Reader(), "job", "unknown")
    finally:
        readonly.dispose()


@pytest.mark.parametrize(
    "change", ["actor", "inputs", "identity", "target", "kind", "state", "application"]
)
def test_job_reads_refuse_inconsistent_persisted_authority(
    database, compilation, compiler_worker, change
):
    import copy

    from sqlalchemy import update

    service, owner, _ = database
    created, _, _, payload = release_inputs(service, owner)
    release = freeze(service, created.id, payload)
    accepted = compile_request(compilation, release.id)
    with owner.begin() as connection:
        receipt = connection.execute(
            select(canonical_event).where(
                canonical_event.c.event_type == "management.compile_variant",
                canonical_event.c.payload["evidence"]["job_id"].as_string() == accepted.job_id,
            )
        ).one()
        changed = copy.deepcopy(receipt.payload)
        values = {}
        if change == "actor":
            values["actor_kind"] = "model"
        elif change == "inputs":
            changed["evidence"]["inputs"]["requested_by"] = "another-actor"
        elif change == "identity":
            changed["result"]["id"] = str(uuid4())
        elif change == "target":
            values["aggregate_id"] = str(uuid4())
        elif change == "kind":
            changed["result"]["kind"] = "model"
        elif change == "state":
            changed["result"]["state"] = "completed"
        else:
            connection.execute(
                text("UPDATE worker_job SET status='succeeded' WHERE job_id=:id"),
                {"id": accepted.job_id},
            )
        connection.execute(
            update(canonical_event)
            .where(canonical_event.c.id == receipt.id)
            .values(payload=changed, **values)
        )
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        reads.read(Reader(), "job", accepted.job_id)
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        reads.list_resources(Reader(), created.id, "job", None, 100)


def test_job_reads_require_unique_matching_intent_and_current_application(
    database, compilation, compiler_worker
):
    service, owner, _ = database
    created, _, _, payload = release_inputs(service, owner)
    release = freeze(service, created.id, payload)
    accepted = compile_request(compilation, release.id)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    with owner.begin() as connection:
        receipt = (
            connection.execute(
                select(canonical_event).where(
                    canonical_event.c.event_type == "management.compile_variant",
                    canonical_event.c.payload["evidence"]["job_id"].as_string() == accepted.job_id,
                )
            )
            .mappings()
            .one()
        )
        connection.execute(
            insert(canonical_event).values(
                **{key: value for key, value in receipt.items() if key not in {"id", "created_at"}}
            )
        )
    for operation in [
        lambda: reads.read(Reader(), "job", accepted.job_id),
        lambda: reads.list_resources(Reader(), created.id, "job", None, 100),
    ]:
        with pytest.raises(ManagementFailure, match="missing_evidence"):
            operation()


def test_job_indices_include_training_and_evaluation_but_not_unowned_queue_rows(
    database, compilation, compiler_worker
):
    publisher, accepted, lease, record = prepared_evaluation_publication(
        database, compilation, compiler_worker
    )
    service, owner, _ = database
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    before = reads.list_resources(Reader(), accepted.resource.project_id, "job", None, 100)
    assert len(before.items) == 3
    assert sorted(item.state for item in before.items) == ["completed", "completed", "running"]
    assert reads.read(Reader(), "job", accepted.job_id).state == "running"
    decoy = compiler_worker.store.enqueue(lease.project_key, "unowned-job", "compile_variant", {})
    assert reads.list_resources(Reader(), accepted.resource.project_id, "job", None, 100) == before
    with pytest.raises(ManagementFailure, match="not_found"):
        reads.read(Reader(), "job", decoy)
    compiler_worker.store.save_stage(lease, "evaluation", record)

    def sink(connection, current, artifacts):
        publisher.publish(connection, current, artifacts)
        assert reads.read(Reader(), "job", accepted.job_id).state == "running"

    compiler_worker.store.complete(lease, ("evaluation",), sink)
    assert reads.read(Reader(), "job", accepted.job_id).state == "completed"
    with owner.begin() as connection:
        connection.execute(
            text("UPDATE worker_job SET generation=generation+1 WHERE job_id=:id"),
            {"id": accepted.job_id},
        )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        reads.read(Reader(), "job", accepted.job_id)


@pytest.fixture
def published_indices(database, compilation, compiler_worker):
    publisher, accepted, lease, record = prepared_evaluation_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "evaluation", record)
    compiler_worker.store.complete(lease, ("evaluation",), publisher.publish)
    identities = {
        "variant": lease.input["variant_id"],
        "training-run": lease.input["model_id"].removeprefix("model-"),
        "evaluation": accepted.resource.id,
        "model": lease.input["model_id"],
    }
    return accepted.resource.project_id, identities, lease


@pytest.mark.parametrize("recipe", ["full-letterbox-320-v1", "full-letterbox-640-v1"])
def test_published_indices_are_select_only_and_omit_checkpoint_reports(
    database, compilation, compiler_worker, recipe
):
    publisher, accepted, lease, record = prepared_evaluation_publication(
        database, compilation, compiler_worker, recipe
    )
    service, owner, role = database
    compiler_worker.store.save_stage(lease, "evaluation", record)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    project_id = accepted.resource.project_id
    # A claimed/saved standalone evaluation has no published resource yet.
    assert reads.list_resources(Reader(), project_id, "evaluation", None, 100).items == []
    with pytest.raises(ManagementFailure, match="not_found"):
        reads.read(Reader(), "evaluation", accepted.resource.id)

    def sink(connection, current, artifacts):
        publisher.publish(connection, current, artifacts)
        assert reads.list_resources(Reader(), project_id, "evaluation", None, 100).items == []

    compiler_worker.store.complete(lease, ("evaluation",), sink)
    identities = {
        "variant": lease.input["variant_id"],
        "training-run": lease.input["model_id"].removeprefix("model-"),
        "evaluation": accepted.resource.id,
        "model": lease.input["model_id"],
    }
    before = counts(owner, project_id), evaluation_publication_state(owner, lease)
    with owner.begin() as connection:
        connection.execute(
            text(f'REVOKE INSERT, UPDATE ON ALL TABLES IN SCHEMA public FROM "{role}"')
        )
    readonly = create_engine(
        os.environ["DATABASE_URL"],
        connect_args={"options": f"-c role={role} -c default_transaction_read_only=on"},
    )
    try:
        readonly_reads = CanonicalManagementReads(readonly, service.workspace_id)
        for kind, identity in identities.items():
            page = readonly_reads.list_resources(Reader(), project_id, kind, None, 1)
            assert page.next_cursor is None and len(page.items) == 1
            summary = page.items[0]
            assert summary.model_dump() == {
                "id": identity,
                "project_id": project_id,
                "kind": kind,
                "revision": 1,
                "state": "completed",
            }
            assert summary == readonly_reads.read(Reader(), kind, identity)
            assert summary == reads.read(Actor(), kind, identity)
        # The checkpoint report is not duplicated as a standalone evaluation.
        assert reads.read_training(Reader(), identities["training-run"]).checkpoints
    finally:
        readonly.dispose()
    assert (counts(owner, project_id), evaluation_publication_state(owner, lease)) == before


@pytest.mark.parametrize("kind", ["variant", "training-run", "evaluation", "model"])
@pytest.mark.parametrize("change", ["generation", "duplicate_receipt"])
def test_published_indices_require_current_unique_fenced_authority(
    database, published_indices, kind, change
):
    project_id, identities, lease = published_indices
    service, owner, _ = database
    event_type = {
        "variant": "management.variant_compiled",
        "training-run": "management.training_completed",
        "evaluation": "management.evaluation_completed",
        "model": "management.model_exported",
    }[kind]
    with owner.begin() as connection:
        receipt = (
            connection.execute(
                select(canonical_event).where(
                    canonical_event.c.event_type == event_type,
                    canonical_event.c.aggregate_id == identities[kind],
                )
            )
            .mappings()
            .one()
        )
        if change == "duplicate_receipt":
            connection.execute(
                insert(canonical_event).values(
                    **{
                        key: value
                        for key, value in receipt.items()
                        if key not in {"id", "created_at"}
                    }
                )
            )
        else:
            connection.execute(
                text("UPDATE worker_job SET generation=generation+1 WHERE job_id=:id"),
                {"id": receipt["payload"]["job_id"]},
            )
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        reads.read(Reader(), kind, identities[kind])
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        reads.list_resources(Reader(), project_id, kind, None, 100)


@pytest.mark.parametrize("kind", ["variant", "training-run", "evaluation", "model"])
@pytest.mark.parametrize(
    "overrides", [{"kind": "service"}, {"kind": "model"}, {"granted": False}, {"id": ""}]
)
def test_published_indices_deny_untrusted_principals_before_lookup(database, kind, overrides):
    service, _, _ = database
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    with pytest.raises(ManagementFailure, match="permission_denied"):
        reads.read(Reader(**overrides), kind, "unknown")
    with pytest.raises(ManagementFailure, match="permission_denied"):
        reads.list_resources(Reader(**overrides), "unknown", kind, None, 1)


def test_published_indices_hide_foreign_and_unknown_ids_and_scope_cursors(
    database, published_indices
):
    from labelloop_api.management_reads import page_position

    project_id, identities, _ = published_indices
    service, _, _ = database
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    foreign = replace(reads, workspace_id=uuid4())
    other = create(service, key="other-index-project")
    for kind, identity in identities.items():
        with pytest.raises(ManagementFailure, match="not_found"):
            foreign.read(Reader(), kind, identity)
        with pytest.raises(ManagementFailure, match="not_found"):
            reads.read(Reader(), kind, "unknown")
        with pytest.raises(ManagementFailure, match="not_found"):
            foreign.list_resources(Reader(), project_id, kind, "invalid", 1)
        assert reads.list_resources(Reader(), other.id, kind, None, 1).items == []
        scope, _ = page_position(service.workspace_id, project_id, kind, None, 1)
        for target, cursor_kind in [(other.id, kind), (project_id, "asset")]:
            cursor = scope + "." + "0" * 32
            with pytest.raises(ManagementFailure, match="invalid_request"):
                reads.list_resources(Reader(), target, cursor_kind, cursor, 1)
        # The scope tag is not an authority or signature; SQL still scopes ownership.
        other_scope, _ = page_position(service.workspace_id, other.id, kind, None, 1)
        assert (
            reads.list_resources(Reader(), other.id, kind, other_scope + "." + "0" * 32, 1).items
            == []
        )


@pytest.mark.parametrize("limit", [1, 2, 100])
def test_published_variant_pages_follow_internal_uuid_keysets(
    database, compilation, compiler_worker, limit
):
    from labelloop_api.management_workers import compilation_runner

    service, owner, _ = database
    created, _, _, payload = release_inputs(service, owner)
    release = freeze(service, created.id, payload)
    variants = []
    for index, recipe in enumerate(["full-letterbox-320-v1", "full-letterbox-640-v1"]):
        accepted = compile_request(compilation, release.id, recipe=recipe, key=f"index-{index}")
        runner = compilation_runner(
            compiler_worker.store,
            compiler_worker.objects,
            service.workspace_id,
            job_id=accepted.job_id,
        )
        assert runner.run_once("index-compiler")
        variants.append(accepted.resource.id)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    page = reads.list_resources(Reader(), created.id, "variant", None, limit)
    found = list(page.items)
    if limit == 1:
        assert page.next_cursor is not None
        assert page.next_cursor.rsplit(".", 1)[1] == UUID(sorted(variants)[0]).hex
        page = reads.list_resources(Reader(), created.id, "variant", page.next_cursor, limit)
        found.extend(page.items)
    assert page.next_cursor is None
    assert [item.id for item in found] == sorted(variants)


@pytest.mark.parametrize("recipe", ["full-letterbox-320-v1", "full-letterbox-640-v1"])
def test_export_read_is_read_only_and_requires_committed_fenced_publication(
    database, compilation, compiler_worker, recipe
):
    publisher, accepted, lease, training, exported = prepared_model_publication(
        database, compilation, compiler_worker, recipe
    )
    service, owner, role = database
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    model_id = "model-" + accepted.resource.id
    compiler_worker.store.save_stage(lease, "training", training)
    compiler_worker.store.save_stage(lease, "export", exported)
    with pytest.raises(ManagementFailure, match="not_found"):
        reads.read_export(Reader(), model_id)

    def sink(connection, current, artifacts):
        publisher.publish(connection, current, artifacts)
        with pytest.raises(ManagementFailure, match="not_found"):
            reads.read_export(Reader(), model_id)

    compiler_worker.store.complete(lease, ("training", "export"), sink)
    before = model_publication_state(owner, accepted), training_publication_state(owner, accepted)
    readonly = create_engine(
        os.environ["DATABASE_URL"],
        connect_args={"options": f"-c role={role} -c default_transaction_read_only=on"},
    )
    try:
        detail = CanonicalManagementReads(readonly, service.workspace_id).read_export(
            Reader(), model_id
        )
        assert detail == reads.read_export(Actor(), model_id)
    finally:
        readonly.dispose()
    assert detail.model_dump(mode="json") == {
        "model_id": model_id,
        "recipe_id": recipe,
        "weights_sha256": exported["evidence"]["weights_sha256"],
        "artifact_sha256": exported["evidence"]["export_sha256"],
        "format": "onnx",
        "input_hw": exported["evidence"]["static_input_signature"][2:],
        "artifact_id": exported["evidence"]["export_sha256"],
    }
    assert "sha256/" not in detail.model_dump_json()
    assert (
        model_publication_state(owner, accepted),
        training_publication_state(owner, accepted),
    ) == before


@pytest.mark.parametrize(
    "overrides",
    [{"kind": "service"}, {"granted": False}, {"role": "worker_ml"}, {"id": ""}],
)
def test_export_read_denies_untrusted_identity_before_lookup(database, overrides):
    service, _, _ = database
    with pytest.raises(ManagementFailure, match="permission_denied"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_export(
            Reader(**overrides), "unknown"
        )


def test_export_read_hides_foreign_workspace_and_unknown_model(
    database, compilation, compiler_worker
):
    publisher, accepted, lease, training, exported = prepared_model_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "training", training)
    compiler_worker.store.save_stage(lease, "export", exported)
    compiler_worker.store.complete(lease, ("training", "export"), publisher.publish)
    service, _, _ = database
    model_id = "model-" + accepted.resource.id
    for workspace, key in [(uuid4(), model_id), (service.workspace_id, "unknown")]:
        with pytest.raises(ManagementFailure, match="not_found"):
            CanonicalManagementReads(service.engine, workspace).read_export(Reader(), key)


@pytest.mark.parametrize(
    "change",
    [
        "generation",
        "failed",
        "compile_generation",
        "duplicate_model_receipt",
        "duplicate_training_receipt",
        "duplicate_intent",
    ],
)
def test_export_read_requires_unique_current_model_training_and_compiler_authority(
    database, compilation, compiler_worker, change
):
    publisher, accepted, lease, training, exported = prepared_model_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "training", training)
    compiler_worker.store.save_stage(lease, "export", exported)
    compiler_worker.store.complete(lease, ("training", "export"), publisher.publish)
    service, owner, _ = database
    model_id = "model-" + accepted.resource.id
    with owner.begin() as connection:
        if change.startswith("duplicate_"):
            event_type, aggregate = {
                "duplicate_model_receipt": ("management.model_exported", model_id),
                "duplicate_training_receipt": (
                    "management.training_completed",
                    accepted.resource.id,
                ),
                "duplicate_intent": ("management.start_training", lease.input["variant_id"]),
            }[change]
            receipt = (
                connection.execute(
                    select(canonical_event).where(
                        canonical_event.c.event_type == event_type,
                        canonical_event.c.aggregate_id == aggregate,
                    )
                )
                .mappings()
                .one()
            )
            connection.execute(
                insert(canonical_event).values(
                    **{
                        key: value
                        for key, value in receipt.items()
                        if key not in {"id", "created_at"}
                    }
                )
            )
        else:
            assignment = "status='failed'" if change == "failed" else "generation=generation+1"
            connection.execute(
                text(f"UPDATE worker_job SET {assignment} WHERE job_id=:id"),
                {
                    "id": lease.input["compile_job_id"]
                    if change == "compile_generation"
                    else lease.job_id
                },
            )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_export(
            Reader(), model_id
        )


def test_export_read_refuses_inconsistent_saved_stage_digest(
    database, compilation, compiler_worker
):
    from psycopg.types.json import Jsonb

    publisher, accepted, lease, training, exported = prepared_model_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "training", training)
    # Insert inconsistent evidence, never mutate immutable stage history. This is
    # a corrupt persisted-record fixture, not a valid controlled export.
    with compiler_worker.store.connect() as connection:
        connection.execute(
            "INSERT INTO worker_stage_artifact(job_id,stage,sha256,payload,generation) "
            "VALUES (%s,'export',%s,%s,%s)",
            (lease.job_id, "0" * 64, Jsonb(exported), lease.generation),
        )
    compiler_worker.store.complete(lease, ("training", "export"), publisher.publish)
    service, _, _ = database
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_export(
            Reader(), "model-" + accepted.resource.id
        )


@pytest.mark.parametrize("receipt", [False, True])
def test_export_read_refuses_hand_inserted_model_and_borrowed_receipt(
    database, compilation, compiler_worker, receipt
):
    from copy import deepcopy

    from labelloop_api.model_schema import model_release

    publisher, accepted, lease, training, exported = prepared_model_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "training", training)
    compiler_worker.store.save_stage(lease, "export", exported)
    compiler_worker.store.complete(lease, ("training", "export"), publisher.publish)
    service, owner, _ = database
    model_id = "model-" + accepted.resource.id
    forged_id = "model-" + uuid4().hex
    with owner.begin() as connection:
        row = (
            connection.execute(select(model_release).where(model_release.c.release_key == model_id))
            .mappings()
            .one()
        )
        record = deepcopy(row["record"])
        record["release_id"] = forged_id
        connection.execute(
            insert(model_release).values(
                **{
                    key: value
                    for key, value in row.items()
                    if key not in {"id", "release_key", "record"}
                },
                release_key=forged_id,
                record=record,
            )
        )
        if receipt:
            original = (
                connection.execute(
                    select(canonical_event).where(
                        canonical_event.c.event_type == "management.model_exported",
                        canonical_event.c.aggregate_id == model_id,
                    )
                )
                .mappings()
                .one()
            )
            connection.execute(
                insert(canonical_event).values(
                    **{
                        key: value
                        for key, value in original.items()
                        if key not in {"id", "created_at", "aggregate_id"}
                    },
                    aggregate_id=forged_id,
                )
            )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_export(
            Reader(), forged_id
        )


@pytest.mark.parametrize("recipe", ["full-letterbox-320-v1", "full-letterbox-640-v1"])
def test_training_detail_is_read_only_and_exposes_only_pinned_arguments(
    database, compilation, compiler_worker, recipe
):
    worker, _, _, _, accepted, lease, record = prepared_training_publication(
        database, compilation, compiler_worker, recipe
    )
    from labelloop_api.management_train_worker import decode_training

    service, owner, role = database
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    record["result"]["resolved_arguments"]["provider_path"] = "/private/worker/output"
    compiler_worker.store.save_stage(lease, "training", record)

    def sink(connection, current, artifacts):
        worker.publish(connection, current, artifacts)
        with pytest.raises(ManagementFailure, match="not_found"):
            reads.read_training(Reader(), accepted.resource.id)

    compiler_worker.store.complete(lease, ("training",), sink)
    before = training_publication_state(owner, accepted)
    readonly = create_engine(
        os.environ["DATABASE_URL"],
        connect_args={"options": f"-c role={role} -c default_transaction_read_only=on"},
    )
    try:
        detail = CanonicalManagementReads(readonly, service.workspace_id).read_training(
            Reader(), accepted.resource.id
        )
        assert detail == reads.read_training(Actor(), accepted.resource.id)
    finally:
        readonly.dispose()
    result = decode_training(record["result"])
    assert detail.resource == accepted.resource.model_copy(update={"state": "completed"})
    assert detail.recipe_id == recipe
    assert detail.dataset_release_id == result.request.dataset_release_id
    assert detail.variant_id == lease.input["variant_id"]
    assert detail.training_profile_id == lease.input["profile"]["id"]
    assert detail.training_profile_revision == lease.input["profile"]["revision"]
    assert detail.initialization_sha256 == result.request.initialization_sha256
    assert detail.epoch == detail.epochs == result.request.epochs
    assert detail.images == len(record["target_retention"])
    assert detail.requested_arguments == detail.resolved_arguments == result.request.arguments()
    assert detail.checkpoints == list(result.checkpoints)
    assert detail.retention.before == detail.retention.after == 0
    assert detail.retention.dropped_object_ids == detail.mismatches == []
    with owner.connect() as connection:
        completed_at = connection.execute(
            select(canonical_event.c.created_at).where(
                canonical_event.c.event_type == "management.training_completed",
                canonical_event.c.aggregate_id == accepted.resource.id,
            )
        ).scalar_one()
    assert detail.reason is None
    assert detail.updated_at == completed_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    encoded = detail.model_dump_json()
    assert all(key not in encoded for key in ("provider_path", "/private/", "checkpoint_keys"))
    assert training_publication_state(owner, accepted) == before


@pytest.mark.parametrize(
    "overrides",
    [{"kind": "service"}, {"granted": False}, {"role": "worker_ml"}, {"id": ""}],
)
def test_training_detail_denies_untrusted_identity_before_database(database, overrides):
    service, _, _ = database
    with pytest.raises(ManagementFailure, match="permission_denied"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_training(
            Reader(**overrides), "unknown"
        )


def test_training_detail_hides_foreign_workspace_and_unknown_runs(
    database, compilation, compiler_worker
):
    worker, _, _, _, accepted, lease, record = prepared_training_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "training", record)
    compiler_worker.store.complete(lease, ("training",), worker.publish)
    service, _, _ = database
    for workspace, run_id in [(uuid4(), accepted.resource.id), (service.workspace_id, "unknown")]:
        with pytest.raises(ManagementFailure, match="not_found"):
            CanonicalManagementReads(service.engine, workspace).read_training(Reader(), run_id)


@pytest.mark.parametrize(
    "change",
    ["generation", "failed", "compile_generation", "duplicate_receipt", "duplicate_intent"],
)
def test_training_detail_requires_current_unique_fenced_publication(
    database, compilation, compiler_worker, change
):
    worker, _, _, compiled, accepted, lease, record = prepared_training_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "training", record)
    compiler_worker.store.complete(lease, ("training",), worker.publish)
    service, owner, _ = database
    with owner.begin() as connection:
        if change == "duplicate_receipt":
            receipt = (
                connection.execute(
                    select(canonical_event).where(
                        canonical_event.c.event_type == "management.training_completed",
                        canonical_event.c.aggregate_id == accepted.resource.id,
                    )
                )
                .mappings()
                .one()
            )
            connection.execute(
                insert(canonical_event).values(
                    **{
                        key: value
                        for key, value in receipt.items()
                        if key not in {"id", "occurred_at"}
                    }
                )
            )
        elif change == "duplicate_intent":
            intent = (
                connection.execute(
                    select(canonical_event).where(
                        canonical_event.c.event_type == "management.start_training",
                        canonical_event.c.payload["evidence"]["job_id"].as_string()
                        == accepted.job_id,
                    )
                )
                .mappings()
                .one()
            )
            connection.execute(
                insert(canonical_event).values(
                    **{
                        key: value
                        for key, value in intent.items()
                        if key not in {"id", "occurred_at"}
                    }
                )
            )
        else:
            assignment = "status='failed'" if change == "failed" else "generation=generation+1"
            connection.execute(
                text(f"UPDATE worker_job SET {assignment} WHERE job_id=:id"),
                {"id": compiled.job_id if change == "compile_generation" else accepted.job_id},
            )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_training(
            Reader(), accepted.resource.id
        )


@pytest.mark.parametrize("receipt", [False, True])
def test_training_detail_refuses_hand_inserted_run_and_borrowed_receipt(
    database, compilation, compiler_worker, receipt
):
    from copy import deepcopy

    from labelloop_api.model_schema import training_run

    worker, _, _, _, accepted, lease, record = prepared_training_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "training", record)
    compiler_worker.store.complete(lease, ("training",), worker.publish)
    service, owner, _ = database
    forged_id = str(uuid4())
    with owner.begin() as connection:
        run = connection.execute(
            select(training_run).where(training_run.c.run_key == accepted.resource.id)
        ).one()
        forged = deepcopy(run.record)
        forged["request"]["run_id"] = forged_id
        connection.execute(
            insert(training_run).values(
                run_key=forged_id,
                variant_id=run.variant_id,
                input_manifest=run.input_manifest,
                record=forged,
            )
        )
        if receipt:
            original = (
                connection.execute(
                    select(canonical_event).where(
                        canonical_event.c.event_type == "management.training_completed",
                        canonical_event.c.aggregate_id == accepted.resource.id,
                    )
                )
                .mappings()
                .one()
            )
            connection.execute(
                insert(canonical_event).values(
                    **{
                        key: value
                        for key, value in original.items()
                        if key not in {"id", "occurred_at", "aggregate_id"}
                    },
                    aggregate_id=forged_id,
                )
            )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_training(
            Reader(), forged_id
        )


@pytest.mark.parametrize("recipe", ["full-letterbox-320-v1", "full-letterbox-640-v1"])
def test_evaluation_detail_is_read_only_and_never_qualifies(
    database, compilation, compiler_worker, recipe
):
    publisher, accepted, lease, record = prepared_evaluation_publication(
        database, compilation, compiler_worker, recipe
    )
    service, owner, role = database
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    compiler_worker.store.save_stage(lease, "evaluation", record)

    def sink(connection, current, artifacts):
        publisher.publish(connection, current, artifacts)
        with pytest.raises(ManagementFailure, match="not_found"):
            reads.read_evaluation(Reader(), accepted.resource.id)

    compiler_worker.store.complete(lease, ("evaluation",), sink)
    before = evaluation_publication_state(owner, lease)
    readonly = create_engine(
        os.environ["DATABASE_URL"],
        connect_args={"options": f"-c role={role} -c default_transaction_read_only=on"},
    )
    try:
        detail = CanonicalManagementReads(readonly, service.workspace_id).read_evaluation(
            Reader(), accepted.resource.id
        )
        assert detail == reads.read_evaluation(Actor(), accepted.resource.id)
    finally:
        readonly.dispose()
    assert detail.resource == accepted.resource.model_copy(update={"state": "completed"})
    assert detail.candidate_id == lease.input["model_id"]
    assert detail.report.run_id == accepted.resource.id
    assert detail.report.recipe_id == recipe
    from labelloop_api.model_store import decode_report

    assert detail.report == decode_report(record["report"])
    assert detail.qualification == "unqualified" and detail.qualification_id is None
    assert detail.incumbent_id is detail.incumbent_report is detail.minimum_class_support is None
    assert detail.opened_final_test_count == 0
    assert [item.code for item in detail.checks] == ["publication_integrity"]
    assert "weights_key" not in detail.model_dump_json()
    assert "export_key" not in detail.model_dump_json()
    assert evaluation_publication_state(owner, lease) == before


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "service"},
        {"granted": False},
        {"role": "worker_ml"},
        {"id": ""},
    ],
)
def test_evaluation_detail_denies_untrusted_identity_before_database(database, overrides):
    actor = Reader(**overrides)
    service, _, _ = database
    with pytest.raises(ManagementFailure, match="permission_denied"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_evaluation(
            actor, "unknown"
        )


def test_evaluation_detail_hides_foreign_workspace_and_unknown_runs(
    database, compilation, compiler_worker
):
    publisher, accepted, lease, record = prepared_evaluation_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "evaluation", record)
    compiler_worker.store.complete(lease, ("evaluation",), publisher.publish)
    service, _, _ = database
    with pytest.raises(ManagementFailure, match="not_found"):
        CanonicalManagementReads(service.engine, uuid4()).read_evaluation(
            Reader(), accepted.resource.id
        )
    with pytest.raises(ManagementFailure, match="not_found"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_evaluation(
            Reader(), "unknown"
        )


@pytest.mark.parametrize("change", ["generation", "failed", "duplicate_receipt"])
def test_evaluation_detail_requires_current_unique_fenced_publication(
    database, compilation, compiler_worker, change
):
    publisher, accepted, lease, record = prepared_evaluation_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "evaluation", record)
    compiler_worker.store.complete(lease, ("evaluation",), publisher.publish)
    service, owner, _ = database
    with owner.begin() as connection:
        if change == "duplicate_receipt":
            receipt = (
                connection.execute(
                    select(canonical_event).where(
                        canonical_event.c.event_type == "management.evaluation_completed",
                        canonical_event.c.aggregate_id == accepted.resource.id,
                    )
                )
                .mappings()
                .one()
            )
            connection.execute(
                insert(canonical_event).values(
                    **{
                        key: value
                        for key, value in receipt.items()
                        if key not in {"id", "occurred_at"}
                    }
                )
            )
        else:
            assignment = "generation=generation+1" if change == "generation" else "status='failed'"
            connection.execute(
                text(f"UPDATE worker_job SET {assignment} WHERE job_id=:id"),
                {"id": accepted.job_id},
            )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_evaluation(
            Reader(), accepted.resource.id
        )


@pytest.mark.parametrize("receipt", [False, True])
def test_evaluation_detail_does_not_trust_hand_inserted_report_or_borrowed_receipt(
    database, compilation, compiler_worker, receipt
):
    from copy import deepcopy

    from labelloop_api.model_schema import evaluation_run, model_evaluation, model_release

    publisher, accepted, lease, record = prepared_evaluation_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "evaluation", record)
    compiler_worker.store.complete(lease, ("evaluation",), publisher.publish)
    service, owner, _ = database
    forged_id = str(uuid4())
    with owner.begin() as connection:
        model = connection.execute(
            select(model_release).where(model_release.c.release_key == lease.input["model_id"])
        ).one()
        report = deepcopy(record["report"])
        report["run_id"] = forged_id
        row_id = connection.execute(
            insert(evaluation_run).values(
                run_key=forged_id,
                training_run_id=model.training_run_id,
                variant_id=UUID(lease.input["variant_id"]),
                record=report,
            )
        ).inserted_primary_key[0]
        connection.execute(insert(model_evaluation).values(model_id=model.id, evaluation_id=row_id))
        if receipt:
            original = (
                connection.execute(
                    select(canonical_event).where(
                        canonical_event.c.event_type == "management.evaluation_completed",
                        canonical_event.c.aggregate_id == accepted.resource.id,
                    )
                )
                .mappings()
                .one()
            )
            connection.execute(
                insert(canonical_event).values(
                    event_type=original["event_type"],
                    aggregate_id=forged_id,
                    expected_revision=1,
                    actor_kind="system",
                    actor_id=original["actor_id"],
                    payload=original["payload"],
                )
            )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_evaluation(
            Reader(), forged_id
        )


def prepared_evaluation_publication(
    database, compilation, compiler_worker, recipe="full-letterbox-320-v1"
):
    from labelloop_api.management_evaluate_worker import CanonicalLocalEvaluationPublisher
    from labelloop_worker_core.jobs import StageContext

    worker, accepted, lease, _, _ = prepared_evaluation_stage(
        database, compilation, compiler_worker, recipe
    )
    record = worker.evaluate(StageContext(compiler_worker.store, lease, {}))
    return CanonicalLocalEvaluationPublisher(worker.workspace_id), accepted, lease, record


def evaluation_publication_state(owner, lease):
    with owner.connect() as connection:
        rows = connection.execute(
            text("SELECT * FROM evaluation_run WHERE run_key=:id"), {"id": lease.input["run_id"]}
        ).all()
        links = connection.execute(
            text(
                "SELECT l.* FROM model_evaluation l JOIN evaluation_run e ON e.id=l.evaluation_id "
                "WHERE e.run_key=:id"
            ),
            {"id": lease.input["run_id"]},
        ).all()
        events = connection.execute(
            select(canonical_event).where(
                canonical_event.c.event_type == "management.evaluation_completed",
                canonical_event.c.aggregate_id == lease.input["run_id"],
            )
        ).all()
        applications = connection.execute(
            text("SELECT count(*) FROM worker_application WHERE job_id=:id"), {"id": lease.job_id}
        ).scalar_one()
        job = connection.execute(
            text("SELECT status,generation FROM worker_job WHERE job_id=:id"), {"id": lease.job_id}
        ).one()
    return rows, links, events, applications, job


@pytest.mark.parametrize("recipe", ["full-letterbox-320-v1", "full-letterbox-640-v1"])
def test_evaluation_publication_is_atomic_and_does_not_rewrite_model(
    database, compilation, compiler_worker, recipe
):
    from labelloop_api.model_store import load_release

    publisher, accepted, lease, record = prepared_evaluation_publication(
        database, compilation, compiler_worker, recipe
    )
    compiler_worker.store.save_stage(lease, "evaluation", record)
    with database[1].connect() as connection:
        original = connection.execute(
            text("SELECT id,record FROM model_release WHERE release_key=:id"),
            {"id": lease.input["model_id"]},
        ).one()

    def sink(connection, current, artifacts):
        publisher.publish(connection, current, artifacts)
        assert evaluation_publication_state(database[1], lease)[:4] == ([], [], [], 0)

    compiler_worker.store.complete(lease, ("evaluation",), sink)
    rows, links, events, applications, job = evaluation_publication_state(database[1], lease)
    assert len(rows) == len(links) == len(events) == applications == 1
    assert rows[0].record == record["report"] and rows[0].run_key == accepted.resource.id
    assert rows[0].variant_id == UUID(lease.input["variant_id"])
    assert links[0].model_id == original.id
    assert events[0].actor_kind == "system" and events[0].actor_id == lease.worker_id
    assert events[0].payload["requested_by"] == Actor().id
    assert events[0].payload["protocol"] == lease.input["protocol"]
    assert events[0].payload["generation"] == lease.generation
    assert job.status == "succeeded"
    with database[1].connect() as connection:
        assert (
            connection.execute(
                text("SELECT record FROM model_release WHERE id=:id"), {"id": original.id}
            ).scalar_one()
            == original.record
        )
        loaded = load_release(connection, original.id)
        assert loaded is not None and accepted.resource.id in loaded.evaluation_run_ids


@pytest.mark.parametrize("table", ["evaluation_run", "model_evaluation", "canonical_event"])
def test_evaluation_publication_grant_failure_rolls_back_and_resumes(
    database, compilation, compiler_worker, table
):
    import psycopg

    publisher, _, lease, record = prepared_evaluation_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "evaluation", record)
    with database[1].begin() as connection:
        connection.execute(text(f'REVOKE INSERT ON {table} FROM "{database[2]}"'))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        compiler_worker.store.complete(lease, ("evaluation",), publisher.publish)
    assert evaluation_publication_state(database[1], lease)[:4] == ([], [], [], 0)
    with database[1].begin() as connection:
        connection.execute(text(f'GRANT INSERT ON {table} TO "{database[2]}"'))
    compiler_worker.store.complete(lease, ("evaluation",), publisher.publish)
    assert len(evaluation_publication_state(database[1], lease)[0]) == 1


@pytest.mark.parametrize("failure", ["caller", "deadline"])
def test_evaluation_publication_late_failure_rolls_back_application(
    database, compilation, compiler_worker, failure
):
    from labelloop_worker_core.jobs import LeaseLost

    publisher, _, lease, record = prepared_evaluation_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "evaluation", record)

    def sink(connection, current, artifacts):
        publisher.publish(connection, current, artifacts)
        if failure == "caller":
            raise RuntimeError("synthetic caller failure")
        connection.execute(
            "UPDATE worker_job SET lease_until=clock_timestamp()-interval '1 second' "
            "WHERE job_id=%s",
            (lease.job_id,),
        )

    with pytest.raises(RuntimeError if failure == "caller" else LeaseLost):
        compiler_worker.store.complete(lease, ("evaluation",), sink)
    assert evaluation_publication_state(database[1], lease)[:4] == ([], [], [], 0)


@pytest.mark.parametrize("terminal", ["expired", "cancelled", "superseded"])
def test_evaluation_publication_refuses_lost_lease(
    database, compilation, compiler_worker, terminal
):
    from labelloop_worker_core.jobs import LeaseLost

    publisher, _, lease, record = prepared_evaluation_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "evaluation", record)
    if terminal == "cancelled":
        compiler_worker.store.cancel(lease.project_key, lease.job_id)
    else:
        with database[1].begin() as connection:
            connection.execute(
                text(
                    "UPDATE worker_job SET lease_until=clock_timestamp()-interval '1 second' "
                    "WHERE job_id=:id"
                ),
                {"id": lease.job_id},
            )
        if terminal == "superseded":
            assert compiler_worker.store.claim("evaluate_local", "replacement", job_id=lease.job_id)
    with pytest.raises(LeaseLost):
        compiler_worker.store.complete(lease, ("evaluation",), publisher.publish)
    assert evaluation_publication_state(database[1], lease)[:4] == ([], [], [], 0)


@pytest.mark.parametrize(
    "field",
    ["run", "role", "weights", "dataset", "policy", "support", "coverage", "samples", "shape"],
)
def test_evaluation_publication_refuses_mismatched_saved_report(
    database, compilation, compiler_worker, field
):
    publisher, _, lease, record = prepared_evaluation_publication(
        database, compilation, compiler_worker
    )
    report = record["report"]
    if field == "run":
        report["run_id"] = str(uuid4())
    elif field == "role":
        report["role"] = "locked_final_test"
    elif field == "weights":
        report["weights_sha256"] = "0" * 64
    elif field == "dataset":
        report["dataset_release_id"] = "unowned"
    elif field == "policy":
        report["policy"]["conf"] = 0
    elif field == "support":
        report["overall"]["support"] += 1
    elif field == "coverage":
        report["coverage"] = []
    elif field == "samples":
        record["sample_count"] += 1
    else:
        record["tensor_shapes"] = [[1, 3, 640, 640]]
    compiler_worker.store.save_stage(lease, "evaluation", record)
    with pytest.raises(ValueError):
        compiler_worker.store.complete(lease, ("evaluation",), publisher.publish)
    assert evaluation_publication_state(database[1], lease)[:4] == ([], [], [], 0)


def test_evaluation_publication_rechecks_intent_and_runtime_audit_authority(
    database, compilation, compiler_worker
):
    from copy import deepcopy

    publisher, _, lease, record = prepared_evaluation_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "evaluation", record)
    forged = deepcopy(lease.input)
    forged["protocol"]["role"] = "locked_final_test"
    with pytest.raises(ValueError):
        compiler_worker.store.complete(
            replace(lease, input=forged), ("evaluation",), publisher.publish
        )
    assert evaluation_publication_state(database[1], lease)[:4] == ([], [], [], 0)
    with database[1].begin() as connection:
        connection.execute(text(f'GRANT UPDATE ON canonical_event TO "{database[2]}"'))
    with pytest.raises(ValueError, match="must not rewrite"):
        compiler_worker.store.complete(lease, ("evaluation",), publisher.publish)
    assert evaluation_publication_state(database[1], lease)[:4] == ([], [], [], 0)


def test_evaluation_publication_resumes_saved_stage_without_ml_or_objects(
    database, compilation, compiler_worker
):
    from labelloop_worker_core.jobs import JobRunner, Stage

    publisher, _, lease, record = prepared_evaluation_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "evaluation", record)
    with database[1].begin() as connection:
        connection.execute(
            text(
                "UPDATE worker_job SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE job_id=:id"
            ),
            {"id": lease.job_id},
        )

    def unexpected(*args):
        pytest.fail("saved evaluation must not rerun inference or access objects")

    runner = JobRunner(
        compiler_worker.store,
        "evaluate_local",
        (Stage("evaluation", unexpected),),
        publisher.publish,
        job_id=lease.job_id,
    )
    assert runner.run_once("replacement")
    assert not runner.run_once("replacement")
    rows, links, events, applications, job = evaluation_publication_state(database[1], lease)
    assert len(rows) == len(links) == len(events) == applications == 1
    assert job.status == "succeeded" and job.generation == events[0].payload["generation"] == 2


def prepared_evaluation_stage(
    database, compilation, compiler_worker, recipe="full-letterbox-320-v1"
):
    import hashlib
    import json

    from labelloop_api.management_evaluate_worker import CanonicalLocalEvaluationWorker
    from labelloop_core.evaluation import DerivedPredictions
    from labelloop_core.geometry import AffineTransform, Crop

    evaluation, model_id, release_id, _ = prepared_evaluation(
        database, compilation, compiler_worker, recipe
    )
    accepted = evaluation_request(evaluation, model_id, release_id)
    lease = compiler_worker.store.claim(
        "evaluate_local", "fixture-evaluator", job_id=accepted.job_id
    )
    assert lease is not None
    compiler_worker.objects.put(lease.input["weights_key"], b"synthetic weights")
    paths, reads = [], []

    def predictor(weights, root, samples, class_ids, policy, shapes):
        paths.extend([weights, root])
        assert weights.read_bytes() == b"synthetic weights"
        assert class_ids == ("package",)
        expected_images = {root / sample["image_path"] for sample in samples}
        assert expected_images and set(root.rglob("*.jpg")) == expected_images
        manifest_path = root / "manifest.json"
        assert {path for path in root.rglob("*") if path.is_file()} == expected_images | {
            manifest_path
        }
        manifest_bytes = manifest_path.read_bytes()
        assert hashlib.sha256(manifest_bytes).hexdigest() == lease.input["manifest_sha256"]
        assert [
            sample for sample in json.loads(manifest_bytes)["samples"] if sample["split"] == "val"
        ] == samples
        assert not (root / "images/train").exists()
        assert not (root / "images/test").exists()
        assert not (root / "labels").exists()
        derived = []
        for sample in samples:
            assert sample["split"] == "val"
            with Image.open(root / sample["image_path"]) as image:
                assert image.format == "JPEG"
                assert image.size == (policy.imgsz,) * 2
            transform = sample["transform"].copy()
            transform.pop("valid_content_rectangle")
            transform["crop"] = Crop(**transform["crop"])
            for key in ("forward", "inverse"):
                transform[key] = tuple(tuple(row) for row in transform[key])
            derived.append(DerivedPredictions(sample["asset_id"], AffineTransform(**transform), ()))
            shapes.append([1, 3, policy.imgsz, policy.imgsz])
        return tuple(derived)

    def read(key):
        reads.append(key)
        return compiler_worker.objects.read(key)

    from types import SimpleNamespace

    def unexpected(*args):
        pytest.fail("evaluation stage must not upload objects")

    worker = CanonicalLocalEvaluationWorker(
        database[0].engine,
        SimpleNamespace(read=read, put=unexpected),
        database[0].workspace_id,
        predictor,
    )
    return worker, accepted, lease, paths, reads


@pytest.mark.parametrize("recipe", ["full-letterbox-320-v1", "full-letterbox-640-v1"])
def test_evaluation_stage_uses_frozen_truth_and_only_validation_pixels(
    database, compilation, compiler_worker, recipe
):
    from labelloop_api.management_compile_worker import content_key
    from labelloop_worker_core.jobs import StageContext

    worker, accepted, lease, paths, reads = prepared_evaluation_stage(
        database, compilation, compiler_worker, recipe
    )
    context = StageContext(compiler_worker.store, lease, {})
    model, manifest, images = worker.resolve(context)
    assert not reads
    record = worker.evaluate(context)
    report = record["report"]
    assert report["run_id"] == accepted.resource.id
    assert report["dataset_release_id"] == lease.input["dataset_release_id"]
    assert report["weights_sha256"] == model.weights_sha256
    assert report["role"] == "development" and report["policy"] == lease.input["policy"]
    assert report["overall"]["images"] == len(images) == 1
    assert report["overall"]["support"] == 0  # Explicitly synthetic negative-source fixture.
    assert record["sample_count"] == 1 and report["coverage"][0]["covered_fraction"] == 1
    assert paths and all(not path.exists() for path in paths)
    assert set(reads) == {
        lease.input["manifest_key"],
        lease.input["weights_key"],
        *(
            content_key(manifest["file_hashes"][s["image_path"]])
            for s in manifest["samples"]
            if s["split"] == "val"
        ),
    }
    saved = compiler_worker.store.save_stage(lease, "evaluation", record)
    assert saved == record
    # Execution does not yet publish a report/link or claim application/qualification.
    with database[1].connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM evaluation_run WHERE run_key=:id"),
                {"id": accepted.resource.id},
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM worker_application WHERE job_id=:id"),
                {"id": lease.job_id},
            ).scalar_one()
            == 0
        )


@pytest.mark.parametrize(
    "change", ["workspace", "kind", "requester", "variant", "model", "protocol", "policy", "run"]
)
def test_evaluation_stage_refuses_forged_intent_before_io(
    database, compilation, compiler_worker, change
):
    from copy import deepcopy
    from types import SimpleNamespace

    from labelloop_worker_core.jobs import StageContext

    worker, _, lease, _, _ = prepared_evaluation_stage(database, compilation, compiler_worker)
    inputs = deepcopy(lease.input)
    if change in {"workspace", "variant"}:
        inputs[change + "_id"] = str(uuid4())
    elif change == "requester":
        inputs["requested_by"] = "forged"
    elif change == "model":
        inputs["model_id"] = "forged"
    elif change == "protocol":
        inputs["protocol"]["role"] = "locked_final_test"
    elif change == "policy":
        inputs["policy"]["conf"] = 0
    elif change == "run":
        inputs["run_id"] = str(uuid4())
    lease = replace(lease, input=inputs, kind="train_local" if change == "kind" else lease.kind)

    def unexpected(*args):
        pytest.fail("untrusted evaluation must fail before I/O")

    worker = replace(worker, objects=SimpleNamespace(read=unexpected), predictor=unexpected)
    with pytest.raises(ValueError):
        worker.evaluate(StageContext(compiler_worker.store, lease, {}))


@pytest.mark.parametrize("corrupt", ["manifest", "weights", "pixels"])
def test_evaluation_stage_refuses_corrupt_content_before_inference(
    database, compilation, compiler_worker, corrupt
):
    from types import SimpleNamespace

    from labelloop_api.management_compile_worker import content_key
    from labelloop_worker_core.jobs import StageContext

    worker, _, lease, _, _ = prepared_evaluation_stage(database, compilation, compiler_worker)
    context = StageContext(compiler_worker.store, lease, {})
    _, manifest, _ = worker.resolve(context)
    image_path = next(s["image_path"] for s in manifest["samples"] if s["split"] == "val")
    broken = {
        "manifest": lease.input["manifest_key"],
        "weights": lease.input["weights_key"],
        "pixels": content_key(manifest["file_hashes"][image_path]),
    }[corrupt]

    def read(key):
        return b"corrupt" if key == broken else worker.objects.read(key)

    def unexpected(*args):
        pytest.fail("corrupt content must fail before inference")

    with pytest.raises(ValueError, match="digest differs"):
        replace(worker, objects=SimpleNamespace(read=read), predictor=unexpected).evaluate(context)


@pytest.mark.parametrize(
    "failure", ["raises", "weights", "missing", "duplicate", "shape", "transform"]
)
def test_evaluation_stage_rejects_bad_predictions_and_cleans_paths(
    database, compilation, compiler_worker, failure
):
    from labelloop_core.geometry import full_frame_transform
    from labelloop_worker_core.jobs import StageContext

    worker, _, lease, paths, _ = prepared_evaluation_stage(database, compilation, compiler_worker)

    def predictor(weights, root, samples, classes, policy, shapes):
        result = worker.predictor(weights, root, samples, classes, policy, shapes)
        if failure == "raises":
            raise ValueError("synthetic inference failure")
        if failure == "weights":
            weights.write_bytes(b"changed checkpoint")
        elif failure == "missing":
            return ()
        elif failure == "duplicate":
            return result + result
        elif failure == "shape":
            shapes[:] = [[1, 3, 352, 352]]
        else:
            return (replace(result[0], transform=full_frame_transform(9, 7, policy.imgsz)),)
        return result

    with pytest.raises(ValueError):
        replace(worker, predictor=predictor).evaluate(
            StageContext(compiler_worker.store, lease, {})
        )
    assert paths and all(not path.exists() for path in paths)


def prepared_evaluation(database, compilation, compiler_worker, recipe="full-letterbox-320-v1"):
    from uuid import UUID

    from labelloop_api.management_evaluation import (
        ApprovedDevelopmentProtocol,
        CanonicalManagementEvaluation,
    )

    publisher, accepted, lease, trained, exported = prepared_model_publication(
        database, compilation, compiler_worker, recipe
    )
    compiler_worker.store.save_stage(lease, "training", trained)
    compiler_worker.store.save_stage(lease, "export", exported)
    compiler_worker.store.complete(lease, ("training", "export"), publisher.publish)
    protocol = ApprovedDevelopmentProtocol(
        UUID(trained["result"]["request"]["project_id"]),
        "development",
        1,
        UUID(lease.input["variant_id"]),
    )
    return (
        CanonicalManagementEvaluation(database[0], compilation.enqueue, (protocol,)),
        "model-" + accepted.resource.id,
        trained["result"]["request"]["dataset_release_id"],
        lease,
    )


@pytest.fixture
def evaluation_ready(database, compilation, compiler_worker):
    return prepared_evaluation(database, compilation, compiler_worker)


def evaluation_request(
    evaluation,
    model_id,
    release_id,
    *,
    key="evaluate",
    actor=None,
    revision=1,
    protocol="development",
):
    from labelloop_api.management_contracts import EvaluationStart

    actor = actor or Actor()
    payload = EvaluationStart(
        expected_revision=revision, dataset_release_id=release_id, protocol_id=protocol
    )
    context = MutationContext.bind(actor, key, "start_evaluation", model_id, payload)
    return evaluation.start_evaluation(actor, context, payload)


def evaluation_intent_state(owner, model_id):
    with owner.connect() as connection:
        jobs = connection.execute(
            text("SELECT * FROM worker_job WHERE kind='evaluate_local' AND input->>'model_id'=:id"),
            {"id": model_id},
        ).all()
        receipts = connection.execute(
            select(canonical_event).where(
                canonical_event.c.event_type == "management.start_evaluation",
                canonical_event.c.aggregate_id == model_id,
            )
        ).all()
    return jobs, receipts


@pytest.mark.parametrize("recipe", ["full-letterbox-320-v1", "full-letterbox-640-v1"])
def test_evaluation_intent_pins_published_model_and_compiler_in_caller_transaction(
    database, compilation, compiler_worker, recipe
):
    evaluation, model_id, release_id, training_lease = prepared_evaluation(
        database, compilation, compiler_worker, recipe
    )
    service, owner, role = database

    def enqueue(connection, *args, **kwargs):
        assert connection.execute("SELECT current_user").fetchone()[0] == role
        job_id = evaluation.enqueue(connection, *args, **kwargs)
        assert evaluation_intent_state(owner, model_id) == ([], [])
        return job_id

    accepted = evaluation_request(replace(evaluation, enqueue=enqueue), model_id, release_id)
    jobs, receipts = evaluation_intent_state(owner, model_id)
    assert len(jobs) == len(receipts) == 1
    job = jobs[0]
    inputs = job.input
    assert inputs == receipts[0].payload["evidence"]["inputs"]
    assert receipts[0].actor_id == Actor().id and receipts[0].actor_kind == "human"
    assert inputs["workspace_id"] == str(service.workspace_id)
    assert inputs["project_id"] == str(evaluation.protocols[0].project_id)
    assert inputs["model_id"] == model_id and inputs["model_job_id"] == training_lease.job_id
    assert inputs["variant_id"] == training_lease.input["variant_id"]
    assert inputs["compile_job_id"] == training_lease.input["compile_job_id"]
    assert inputs["manifest_key"] == training_lease.input["manifest_key"]
    assert inputs["dataset_release_id"] == release_id
    assert inputs["protocol"] == {
        "id": "development",
        "revision": 1,
        "role": "development",
        "split": "val",
    }
    assert inputs["policy"]["imgsz"] == (320 if "320" in recipe else 640)
    assert inputs["run_id"] == accepted.resource.id and inputs["requested_by"] == Actor().id
    assert len(inputs["model_sha256"]) == len(inputs["manifest_sha256"]) == 64
    assert accepted.resource.kind == "evaluation" and accepted.resource.state == "queued"
    assert job.job_id == accepted.job_id and job.max_attempts == 3 and job.status == "queued"
    lease = compiler_worker.store.claim("evaluate_local", "fixture-evaluator", job_id=job.job_id)
    assert lease is not None and lease.input == inputs
    assert evaluation_request(evaluation, model_id, release_id) == accepted
    assert evaluation_request(replace(evaluation, protocols=()), model_id, release_id) == accepted
    with owner.connect() as connection:
        # Dispatch never rewrites training selection, links a report or qualifies a model.
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM model_evaluation l "
                    "JOIN model_release m ON m.id=l.model_id "
                    "WHERE m.release_key=:id"
                ),
                {"id": model_id},
            ).scalar_one()
            == 1
        )


@pytest.mark.parametrize(
    "grant",
    [
        "INSERT ON worker_job",
        "SELECT ON worker_job",
        "INSERT ON canonical_event",
        "SELECT ON worker_application",
        "SELECT ON model_release",
    ],
)
def test_evaluation_intent_grant_failure_rolls_back_queue_and_audit(
    database, evaluation_ready, grant
):
    from psycopg.errors import InsufficientPrivilege

    evaluation, model_id, release_id, _ = evaluation_ready
    _, owner, role = database
    with owner.begin() as connection:
        connection.execute(text(f'REVOKE {grant} FROM "{role}"'))
    with pytest.raises((DBAPIError, InsufficientPrivilege)):
        evaluation_request(evaluation, model_id, release_id)
    assert evaluation_intent_state(owner, model_id) == ([], [])
    with owner.begin() as connection:
        connection.execute(text(f'GRANT {grant} TO "{role}"'))
    assert evaluation_request(evaluation, model_id, release_id).resource.state == "queued"


def test_evaluation_intent_caller_failure_rolls_back_real_queue(database, evaluation_ready):
    evaluation, model_id, release_id, _ = evaluation_ready

    def enqueue(*args, **kwargs):
        evaluation.enqueue(*args, **kwargs)
        raise RuntimeError("synthetic evaluation caller failure")

    with pytest.raises(RuntimeError, match="synthetic evaluation caller failure"):
        evaluation_request(replace(evaluation, enqueue=enqueue), model_id, release_id)
    assert evaluation_intent_state(database[1], model_id) == ([], [])


@pytest.mark.parametrize("same_key", [True, False])
def test_evaluation_intent_concurrent_retry_and_unique_protocol(
    database, evaluation_ready, same_key
):
    evaluation, model_id, release_id, _ = evaluation_ready
    barrier = Barrier(2)

    def submit(index):
        barrier.wait(timeout=10)
        try:
            return evaluation_request(
                evaluation, model_id, release_id, key="same" if same_key else str(index)
            )
        except ManagementFailure as exc:
            return exc.error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    if same_key:
        assert results[0] == results[1]
    else:
        assert results.count("revision_conflict") == 1
    jobs, receipts = evaluation_intent_state(database[1], model_id)
    assert len(jobs) == len(receipts) == 1


@pytest.mark.parametrize(
    "actor",
    [
        Actor(role="reviewer"),
        Actor(kind="service", role="worker_core"),
        Actor(kind="service", role="worker_ml"),
        Actor(kind="service", role="worker_llm"),
        Actor(kind="service", role="worker_platform"),
    ],
)
def test_evaluation_intent_denies_nonadmin_before_queue(database, evaluation_ready, actor):
    evaluation, model_id, release_id, _ = evaluation_ready
    with pytest.raises(ManagementFailure) as exc:
        evaluation_request(evaluation, model_id, release_id, actor=actor)
    assert exc.value.error.detail == "permission_denied"
    assert evaluation_intent_state(database[1], model_id) == ([], [])


@pytest.mark.parametrize(
    "failure",
    [
        "foreign_workspace",
        "unknown_model",
        "unknown_protocol",
        "foreign_protocol",
        "unknown_variant",
        "wrong_release",
        "stale_revision",
        "forged_context",
        "unpublished_model",
    ],
)
def test_evaluation_intent_refuses_untrusted_or_inaccessible_inputs(
    database, evaluation_ready, failure
):
    from labelloop_api.management_contracts import EvaluationStart
    from labelloop_api.model_schema import model_release

    evaluation, model_id, release_id, _ = evaluation_ready
    _, owner, _ = database
    expected, revision, protocol_id = "not_found", 1, "development"
    if failure == "foreign_workspace":
        evaluation = replace(
            evaluation, mutations=replace(evaluation.mutations, workspace_id=uuid4())
        )
    elif failure == "unknown_model":
        model_id = "missing-model"
    elif failure == "unknown_protocol":
        protocol_id = "missing-protocol"
    elif failure == "foreign_protocol":
        evaluation = replace(
            evaluation, protocols=(replace(evaluation.protocols[0], project_id=uuid4()),)
        )
    elif failure == "unknown_variant":
        evaluation = replace(
            evaluation, protocols=(replace(evaluation.protocols[0], variant_id=uuid4()),)
        )
    elif failure == "wrong_release":
        release_id = "missing-release"
    elif failure == "stale_revision":
        revision, expected = 2, "revision_conflict"
    elif failure == "unpublished_model":
        with owner.begin() as connection:
            row = dict(
                connection.execute(
                    select(model_release).where(model_release.c.release_key == model_id)
                )
                .mappings()
                .one()
            )
            row.pop("id")
            row["release_key"] = "unpublished-" + model_id
            connection.execute(insert(model_release).values(**row))
            model_id = row["release_key"]
        expected = "missing_evidence"
    if failure == "forged_context":
        expected = "invalid_request"
        payload = EvaluationStart(
            expected_revision=1, dataset_release_id=release_id, protocol_id=protocol_id
        )
        context = MutationContext.bind(Actor(), "evaluate", "start_evaluation", model_id, payload)
        with pytest.raises(ManagementFailure) as exc:
            evaluation.start_evaluation(Actor(), replace(context, actor_id="forged"), payload)
    else:
        with pytest.raises(ManagementFailure) as exc:
            evaluation_request(
                evaluation, model_id, release_id, revision=revision, protocol=protocol_id
            )
    assert exc.value.error.detail == expected
    assert evaluation_intent_state(owner, model_id) == ([], [])


def test_evaluation_intent_key_collisions_are_distinct_from_model_revision(
    database, evaluation_ready
):
    evaluation, model_id, release_id, _ = evaluation_ready
    with pytest.raises(ManagementFailure) as exc:
        evaluation_request(evaluation, model_id, release_id, key="create")
    assert exc.value.error.detail == "idempotency_conflict"
    assert evaluation_intent_state(database[1], model_id) == ([], [])
    accepted = evaluation_request(evaluation, model_id, release_id)
    with pytest.raises(ManagementFailure) as exc:
        evaluation_request(evaluation, model_id, "another-release")
    assert exc.value.error.detail == "idempotency_conflict"
    assert evaluation_request(evaluation, model_id, release_id) == accepted
    jobs, receipts = evaluation_intent_state(database[1], model_id)
    assert len(jobs) == len(receipts) == 1


def test_evaluation_intent_refuses_incompatible_compiled_recipe(
    database, compilation, compiler_worker, evaluation_ready
):
    from uuid import UUID

    from labelloop_worker_core.jobs import JobRunner, Stage

    evaluation, model_id, release_id, _ = evaluation_ready
    compiled = compile_request(
        compilation, release_id, key="compile-evaluation-640", recipe="full-letterbox-640-v1"
    )
    assert JobRunner(
        compiler_worker.store,
        "compile_variant",
        (Stage("compilation", compiler_worker.compile),),
        compiler_worker.publish,
        job_id=compiled.job_id,
    ).run_once("fixture-compiler")
    evaluation = replace(
        evaluation,
        protocols=(replace(evaluation.protocols[0], variant_id=UUID(compiled.resource.id)),),
    )
    with pytest.raises(ManagementFailure) as exc:
        evaluation_request(evaluation, model_id, release_id)
    assert exc.value.error.detail == "incompatible_recipe"
    assert evaluation_intent_state(database[1], model_id) == ([], [])


def test_evaluation_protocol_revision_is_a_separate_pinned_intent(database, evaluation_ready):
    evaluation, model_id, release_id, _ = evaluation_ready
    first = evaluation_request(evaluation, model_id, release_id)
    revised = replace(evaluation, protocols=(replace(evaluation.protocols[0], revision=2),))
    second = evaluation_request(revised, model_id, release_id, key="evaluate-v2")
    assert first.job_id != second.job_id and first.resource.id != second.resource.id
    assert evaluation_request(revised, model_id, release_id) == first
    jobs, receipts = evaluation_intent_state(database[1], model_id)
    assert len(jobs) == len(receipts) == 2
    assert {job.input["protocol"]["revision"] for job in jobs} == {1, 2}


def test_evaluation_intent_requires_unique_export_publication_receipt(database, evaluation_ready):
    evaluation, model_id, release_id, _ = evaluation_ready
    with database[1].begin() as connection:
        row = dict(
            connection.execute(
                select(canonical_event).where(
                    canonical_event.c.event_type == "management.model_exported",
                    canonical_event.c.aggregate_id == model_id,
                )
            )
            .mappings()
            .one()
        )
        row.pop("id")
        connection.execute(insert(canonical_event).values(**row))
    with pytest.raises(ManagementFailure) as exc:
        evaluation_request(evaluation, model_id, release_id)
    assert exc.value.error.detail == "missing_evidence"
    assert evaluation_intent_state(database[1], model_id) == ([], [])


@pytest.mark.parametrize("role", ["admin", "reviewer"])
def test_evaluation_intent_binds_real_shared_session_and_csrf(
    database, evaluation_ready, restricted_auth_store, auth_account, role
):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from labelloop_api.auth_schema import principal
    from labelloop_api.main import create_app

    evaluation, model_id, release_id, _ = evaluation_ready
    _, owner, _ = database
    identity, credentials = auth_account
    with owner.begin() as connection:
        connection.execute(principal.update().where(principal.c.id == identity).values(role=role))
    origin = "https://testserver"
    operations = SimpleNamespace(start_evaluation=evaluation.start_evaluation)
    body = {"expected_revision": 1, "dataset_release_id": release_id, "protocol_id": "development"}
    path = f"/models/{model_id}/evaluations"
    with TestClient(create_app(lambda: operations), base_url=origin) as client:
        assert client.post(path, json=body).status_code == 401
        login = client.post("/auth/login", json=credentials, headers={"origin": origin})
        assert login.status_code == 200
        assert (
            client.post(path, json=body, headers={"Idempotency-Key": "evaluate"}).status_code == 403
        )
        assert evaluation_intent_state(owner, model_id) == ([], [])
        headers = {
            "origin": origin,
            "x-csrf-token": login.json()["csrf_token"],
            "Idempotency-Key": "evaluate",
        }
        response = client.post(path, json=body, headers=headers)
        if role == "reviewer":
            assert response.status_code == 403
            assert evaluation_intent_state(owner, model_id) == ([], [])
        else:
            assert response.status_code == 202, response.text
            assert client.post(path, json=body, headers=headers).json() == response.json()
            assert evaluation_intent_state(owner, model_id)[1][0].actor_id == identity
        assert client.post("/auth/logout", headers=headers).status_code == 204
        assert client.post(path, json=body, headers=headers).status_code == 401


def prepared_export_stage(database, compilation, compiler_worker, recipe="full-letterbox-320-v1"):
    from labelloop_api.management_compile_worker import content_key
    from labelloop_api.management_model_worker import CanonicalLocalExportWorker

    publisher, accepted, lease, training, exported = prepared_model_publication(
        database, compilation, compiler_worker, recipe
    )
    compiler_worker.objects.put(
        content_key(training["result"]["selected"]["sha256"]), b"synthetic weights"
    )
    paths = []

    def exporter(**kwargs):
        weights = kwargs["weights"]
        paths.extend([weights, kwargs["evidence"], *kwargs["images"]])
        assert weights.read_bytes() == b"synthetic weights"
        assert kwargs["expected_weights_sha256"] == training["result"]["selected"]["sha256"]
        assert kwargs["policy"].imgsz == (320 if "320" in recipe else 640)
        assert len(kwargs["images"]) == 1
        for image in kwargs["images"]:
            assert image.parts[-3:-1] == ("images", "val")
            assert Image.open(image).size == (kwargs["policy"].imgsz,) * 2
        assert not kwargs["evidence"].exists() and not weights.with_suffix(".onnx").exists()
        weights.with_suffix(".onnx").write_bytes(b"synthetic ONNX; not executable export evidence")
        return exported["evidence"]

    return (
        CanonicalLocalExportWorker(publisher.training, exporter),
        publisher,
        accepted,
        lease,
        training,
        exported,
        paths,
    )


@pytest.mark.parametrize("recipe", ["full-letterbox-320-v1", "full-letterbox-640-v1"])
def test_export_stage_resolves_saved_training_and_publishes_through_queue(
    database, compilation, compiler_worker, recipe
):
    from labelloop_worker_core.jobs import StageContext

    stage, publisher, accepted, lease, training, exported, paths = prepared_export_stage(
        database, compilation, compiler_worker, recipe
    )
    compiler_worker.store.save_stage(lease, "training", training)
    record = stage.export(StageContext(compiler_worker.store, lease, {"training": training}))
    assert record == exported
    assert paths and all(not path.exists() for path in paths)
    assert compiler_worker.objects.read(record["export_key"]) == (
        b"synthetic ONNX; not executable export evidence"
    )
    assert model_publication_state(database[1], accepted) == ([], [], [])
    compiler_worker.store.save_stage(lease, "export", record)
    compiler_worker.store.complete(lease, ("training", "export"), publisher.publish)
    assert len(model_publication_state(database[1], accepted)[0]) == 1


@pytest.mark.parametrize("change", ["request", "keys", "report", "shapes", "retention"])
def test_export_stage_refuses_forged_training_before_object_or_exporter_access(
    database, compilation, compiler_worker, change
):
    from types import SimpleNamespace

    from labelloop_worker_core.jobs import StageContext

    stage, _, _, lease, training, _, _ = prepared_export_stage(
        database, compilation, compiler_worker
    )
    if change == "request":
        training["result"]["request"]["initialization_sha256"] = "0" * 64
    elif change == "keys":
        training["checkpoint_keys"] = {}
    elif change == "report":
        training["reports"][0]["role"] = "locked_final_test"
    elif change == "shapes":
        training["tensor_shapes"]["training"] = [[1, 3, 352, 352]]
    else:
        training["target_retention"] = []

    def unexpected(*args, **kwargs):
        pytest.fail("forged training must fail before I/O")

    stage = replace(
        stage,
        training=replace(stage.training, objects=SimpleNamespace(read=unexpected, put=unexpected)),
        exporter=unexpected,
    )
    with pytest.raises(ValueError):
        stage.export(StageContext(compiler_worker.store, lease, {"training": training}))


@pytest.mark.parametrize("change", ["workspace", "kind", "requester", "variant"])
def test_export_stage_rechecks_owned_intent_before_object_or_exporter_access(
    database, compilation, compiler_worker, change
):
    from copy import deepcopy
    from types import SimpleNamespace

    from labelloop_worker_core.jobs import StageContext

    stage, _, _, lease, training, _, _ = prepared_export_stage(
        database, compilation, compiler_worker
    )
    inputs = deepcopy(lease.input)
    if change == "workspace":
        inputs["workspace_id"] = str(uuid4())
    elif change == "requester":
        inputs["requested_by"] = "forged"
    elif change == "variant":
        inputs["variant_id"] = str(uuid4())
    lease = replace(lease, input=inputs, kind="compile_variant" if change == "kind" else lease.kind)

    def unexpected(*args, **kwargs):
        pytest.fail("forged authority must fail before I/O")

    stage = replace(
        stage,
        training=replace(stage.training, objects=SimpleNamespace(read=unexpected, put=unexpected)),
        exporter=unexpected,
    )
    with pytest.raises(ValueError):
        stage.export(StageContext(compiler_worker.store, lease, {"training": training}))


@pytest.mark.parametrize("corrupt", ["manifest", "checkpoint", "dataset"])
def test_export_stage_refuses_corrupt_bytes_before_exporter(
    database, compilation, compiler_worker, corrupt
):
    from types import SimpleNamespace

    from labelloop_api.management_compile_worker import content_key
    from labelloop_worker_core.jobs import StageContext

    stage, _, _, lease, training, _, _ = prepared_export_stage(
        database, compilation, compiler_worker
    )
    _, manifest, _ = stage.training.resolve(StageContext(compiler_worker.store, lease, {}))
    broken = {
        "manifest": lease.input["manifest_key"],
        "checkpoint": content_key(training["result"]["selected"]["sha256"]),
        "dataset": content_key(manifest["file_hashes"]["class-map.json"]),
    }[corrupt]

    def read(key):
        return b"corrupt" if key == broken else compiler_worker.objects.read(key)

    def unexpected(*args, **kwargs):
        pytest.fail("corrupt bytes must fail before exporter/upload")

    stage = replace(
        stage,
        training=replace(stage.training, objects=SimpleNamespace(read=read, put=unexpected)),
        exporter=unexpected,
    )
    with pytest.raises(ValueError, match="digest differs"):
        stage.export(StageContext(compiler_worker.store, lease, {"training": training}))


@pytest.mark.parametrize("failure", ["raises", "checkpoint", "coverage", "missing"])
def test_export_stage_failure_cleans_paths_without_upload_or_publication(
    database, compilation, compiler_worker, failure
):
    from types import SimpleNamespace

    from labelloop_worker_core.jobs import StageContext

    stage, _, accepted, lease, training, _, paths = prepared_export_stage(
        database, compilation, compiler_worker
    )

    def exporter(**kwargs):
        evidence = stage.exporter(**kwargs).copy()
        if failure == "raises":
            raise ValueError("synthetic export failure")
        if failure == "checkpoint":
            kwargs["weights"].write_bytes(b"mutated checkpoint")
        elif failure == "coverage":
            evidence["sample_count"] = 2
        else:
            kwargs["weights"].with_suffix(".onnx").unlink()
        return evidence

    def unexpected(*args, **kwargs):
        pytest.fail("invalid export must not upload")

    broken = replace(
        stage,
        training=replace(
            stage.training,
            objects=SimpleNamespace(read=compiler_worker.objects.read, put=unexpected),
        ),
        exporter=exporter,
    )
    with pytest.raises(ValueError):
        broken.export(StageContext(compiler_worker.store, lease, {"training": training}))
    assert paths and all(not path.exists() for path in paths)
    assert training_publication_state(database[1], accepted)[:4] == (None, [], [], 0)
    assert model_publication_state(database[1], accepted) == ([], [], [])


def test_export_stage_retry_uses_saved_training_then_atomic_completion(
    database, compilation, compiler_worker
):
    from labelloop_worker_core.jobs import JobRunner, Stage

    stage, publisher, accepted, lease, training, _, _ = prepared_export_stage(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "training", training)
    with database[1].begin() as connection:
        connection.execute(
            text(
                "UPDATE worker_job SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE job_id=:id"
            ),
            {"id": lease.job_id},
        )

    def unexpected(*args, **kwargs):
        pytest.fail("saved training must not execute again")

    calls = []

    def exporter(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("synthetic transient export failure")
        return stage.exporter(**kwargs)

    worker = replace(stage, exporter=exporter)
    runner = JobRunner(
        compiler_worker.store,
        "train_local",
        (Stage("training", unexpected), Stage("export", worker.export)),
        publisher.publish,
        job_id=lease.job_id,
    )
    assert runner.run_once("replacement")
    assert model_publication_state(database[1], accepted) == ([], [], [])
    with database[1].begin() as connection:
        connection.execute(
            text(
                "UPDATE worker_job SET available_at=clock_timestamp()-interval '1 second' "
                "WHERE job_id=:id"
            ),
            {"id": lease.job_id},
        )
    assert runner.run_once("replacement")
    assert not runner.run_once("replacement")
    assert len(calls) == 2
    assert len(model_publication_state(database[1], accepted)[0]) == 1
    assert training_publication_state(database[1], accepted)[4].generation == 3


def prepared_model_publication(
    database, compilation, compiler_worker, recipe="full-letterbox-320-v1"
):
    import hashlib

    from labelloop_api.management_model_worker import CanonicalLocalModelPublisher

    worker, training, created, compiled, accepted, lease, record = prepared_training_publication(
        database, compilation, compiler_worker, recipe
    )
    digest = hashlib.sha256(b"synthetic ONNX; not executable export evidence").hexdigest()
    request = record["result"]["request"]
    exported = {
        "evidence": {
            "weights_sha256": record["result"]["selected"]["sha256"],
            "export_sha256": digest,
            "static_input_signature": [
                1,
                3,
                request["policy"]["imgsz"],
                request["policy"]["imgsz"],
            ],
            "policy": request["policy"],
            "sample_count": 1,
            "detection_counts": [1],
            "coordinate_atol_px": 0.01,
            "confidence_atol": 1e-5,
        },
        "export_key": "sha256/" + digest[:2] + "/" + digest,
        "weights_key": record["checkpoint_keys"][record["result"]["selected"]["sha256"]],
    }
    with database[1].begin() as connection:
        connection.execute(
            text(f'GRANT SELECT, INSERT ON model_release, model_evaluation TO "{database[2]}"')
        )
    return CanonicalLocalModelPublisher(worker), accepted, lease, record, exported


def model_publication_state(owner, accepted):
    with owner.connect() as connection:
        models = connection.execute(
            text("SELECT * FROM model_release WHERE release_key=:id"),
            {"id": "model-" + accepted.resource.id},
        ).all()
        links = connection.execute(
            text(
                "SELECT e.run_key FROM model_evaluation l JOIN model_release m ON m.id=l.model_id "
                "JOIN evaluation_run e ON e.id=l.evaluation_id WHERE m.release_key=:id"
            ),
            {"id": "model-" + accepted.resource.id},
        ).all()
        events = connection.execute(
            select(canonical_event).where(
                canonical_event.c.event_type == "management.model_exported",
                canonical_event.c.aggregate_id == "model-" + accepted.resource.id,
            )
        ).all()
    return models, links, events


@pytest.mark.parametrize("recipe", ["full-letterbox-320-v1", "full-letterbox-640-v1"])
def test_model_publication_commits_training_export_and_audit_once(
    database, compilation, compiler_worker, recipe
):
    publisher, accepted, lease, training, exported = prepared_model_publication(
        database, compilation, compiler_worker, recipe
    )
    compiler_worker.store.save_stage(lease, "training", training)
    compiler_worker.store.save_stage(lease, "export", exported)

    def sink(connection, current, artifacts):
        publisher.publish(connection, current, artifacts)
        assert training_publication_state(database[1], accepted)[:4] == (None, [], [], 0)
        assert model_publication_state(database[1], accepted) == ([], [], [])

    compiler_worker.store.complete(lease, ("training", "export"), sink)
    models, links, events = model_publication_state(database[1], accepted)
    assert len(models) == len(links) == len(events) == 1
    model = models[0]
    assert model.record["project_id"] == lease.project_key
    assert model.record["release_id"] == "model-" + accepted.resource.id
    assert model.record["evaluation_run_ids"] == [
        training["result"]["selected"]["evaluation_run_id"]
    ]
    assert links[0].run_key == model.record["evaluation_run_ids"][0]
    assert model.export_evidence == exported["evidence"]
    assert events[0].actor_kind == "system" and events[0].actor_id == lease.worker_id
    assert events[0].payload["project_id"] == training["result"]["request"]["project_id"]
    assert events[0].payload["export_key"] == exported["export_key"]
    assert events[0].payload["requested_by"] == Actor().id
    assert training_publication_state(database[1], accepted)[4].status == "succeeded"


@pytest.mark.parametrize("grant", ["INSERT ON model_release", "INSERT ON model_evaluation"])
def test_model_publication_grant_failure_rolls_back_training_and_resumes(
    database, compilation, compiler_worker, grant
):
    import psycopg

    publisher, accepted, lease, training, exported = prepared_model_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "training", training)
    compiler_worker.store.save_stage(lease, "export", exported)
    with database[1].begin() as connection:
        connection.execute(text(f'REVOKE {grant} FROM "{database[2]}"'))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        compiler_worker.store.complete(lease, ("training", "export"), publisher.publish)
    assert training_publication_state(database[1], accepted)[:4] == (None, [], [], 0)
    assert model_publication_state(database[1], accepted) == ([], [], [])
    with database[1].begin() as connection:
        connection.execute(text(f'GRANT {grant} TO "{database[2]}"'))
    compiler_worker.store.complete(lease, ("training", "export"), publisher.publish)
    assert len(model_publication_state(database[1], accepted)[0]) == 1


@pytest.mark.parametrize("failure", ["caller", "deadline"])
def test_model_publication_late_failure_rolls_back_whole_application(
    database, compilation, compiler_worker, failure
):
    from labelloop_worker_core.jobs import LeaseLost

    publisher, accepted, lease, training, exported = prepared_model_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "training", training)
    compiler_worker.store.save_stage(lease, "export", exported)

    def sink(connection, current, artifacts):
        publisher.publish(connection, current, artifacts)
        if failure == "caller":
            raise RuntimeError("synthetic caller failure")
        connection.execute(
            "UPDATE worker_job SET lease_until=clock_timestamp()-interval '1 second' "
            "WHERE job_id=%s",
            (lease.job_id,),
        )

    with pytest.raises(RuntimeError if failure == "caller" else LeaseLost):
        compiler_worker.store.complete(lease, ("training", "export"), sink)
    assert training_publication_state(database[1], accepted)[:4] == (None, [], [], 0)
    assert model_publication_state(database[1], accepted) == ([], [], [])


@pytest.mark.parametrize(
    "change",
    ["weights", "policy", "shape", "empty", "counts", "tolerance", "export_key", "weights_key"],
)
def test_model_publication_rejects_forged_export_and_rolls_back_training(
    database, compilation, compiler_worker, change
):
    publisher, accepted, lease, training, exported = prepared_model_publication(
        database, compilation, compiler_worker
    )
    evidence = exported["evidence"]
    if change == "weights":
        evidence["weights_sha256"] = "0" * 64
    elif change == "policy":
        evidence["policy"] = evidence["policy"] | {"tile": True}
    elif change == "shape":
        evidence["static_input_signature"] = [1, 3, 352, 352]
    elif change == "empty":
        evidence["detection_counts"] = [0]
    elif change == "counts":
        evidence["detection_counts"] = [-1]
    elif change == "tolerance":
        evidence["confidence_atol"] = 0.1
    else:
        exported[change] = "sha256/00/" + "0" * 64
    compiler_worker.store.save_stage(lease, "training", training)
    compiler_worker.store.save_stage(lease, "export", exported)
    with pytest.raises(ValueError):
        compiler_worker.store.complete(lease, ("training", "export"), publisher.publish)
    assert training_publication_state(database[1], accepted)[:4] == (None, [], [], 0)
    assert model_publication_state(database[1], accepted) == ([], [], [])


@pytest.mark.parametrize("terminal", ["expired", "cancelled", "superseded"])
def test_model_publication_refuses_lost_lease(database, compilation, compiler_worker, terminal):
    from labelloop_worker_core.jobs import LeaseLost

    publisher, accepted, lease, training, exported = prepared_model_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "training", training)
    compiler_worker.store.save_stage(lease, "export", exported)
    if terminal == "cancelled":
        compiler_worker.store.cancel(lease.project_key, lease.job_id)
    else:
        with database[1].begin() as connection:
            connection.execute(
                text(
                    "UPDATE worker_job SET lease_until=clock_timestamp()-interval '1 second' "
                    "WHERE job_id=:id"
                ),
                {"id": lease.job_id},
            )
        if terminal == "superseded":
            assert compiler_worker.store.claim("train_local", "replacement", job_id=lease.job_id)
    with pytest.raises(LeaseLost):
        compiler_worker.store.complete(lease, ("training", "export"), publisher.publish)
    assert training_publication_state(database[1], accepted)[:4] == (None, [], [], 0)
    assert model_publication_state(database[1], accepted) == ([], [], [])


def test_model_publication_resumes_saved_stages_without_ml_or_object_io(
    database, compilation, compiler_worker
):
    from types import SimpleNamespace

    from labelloop_worker_core.jobs import JobRunner, Stage

    publisher, accepted, lease, training, exported = prepared_model_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "training", training)
    compiler_worker.store.save_stage(lease, "export", exported)
    with database[1].begin() as connection:
        connection.execute(
            text(
                "UPDATE worker_job SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE job_id=:id"
            ),
            {"id": lease.job_id},
        )

    def unexpected(*args, **kwargs):
        pytest.fail("saved stages must not run ML or access objects")

    publisher = replace(
        publisher,
        training=replace(
            publisher.training,
            objects=SimpleNamespace(read=unexpected, put=unexpected),
            backend=unexpected,
        ),
    )
    runner = JobRunner(
        compiler_worker.store,
        "train_local",
        (Stage("training", unexpected), Stage("export", unexpected)),
        publisher.publish,
        job_id=lease.job_id,
    )
    assert runner.run_once("replacement")
    assert not runner.run_once("replacement")
    assert len(model_publication_state(database[1], accepted)[0]) == 1
    assert training_publication_state(database[1], accepted)[4].generation == 2


def prepared_training_publication(
    database, compilation, compiler_worker, recipe="full-letterbox-320-v1"
):
    """Real compiler/queue; explicitly synthetic training artifact, never ML acceptance."""
    import hashlib
    from dataclasses import asdict
    from pathlib import PurePosixPath

    from labelloop_api.management_train_worker import CanonicalLocalTrainingWorker
    from labelloop_core.evaluation import ClassMetrics, EvaluationReport, Metrics
    from labelloop_core.training import Checkpoint, InferencePolicy, TrainingRequest, TrainingResult

    training, created, compiled = prepared_training(
        database,
        compilation,
        compiler_worker,
        recipe,
        training_sample=True,
    )
    accepted = training_request(training, compiled.resource.id)
    lease = compiler_worker.store.claim("train_local", "fixture-ml", job_id=accepted.job_id)
    inputs = lease.input["request"]
    request = TrainingRequest(**(inputs | {"policy": InferencePolicy(**inputs["policy"])}))
    checkpoint = Checkpoint(
        0, hashlib.sha256(b"synthetic weights").hexdigest(), 0.0, "eval-" + uuid4().hex
    )
    result = TrainingResult(
        request, (checkpoint,), checkpoint, request.arguments(), "8.4.204", "fixture-only", False
    )
    metrics = Metrics(
        1,
        0,
        0.0,
        0.0,
        0.0,
        0.0,
        {
            "package": ClassMetrics(False, 0, 0, 0, 0, None, None, None, None),
        },
    )
    report = EvaluationReport(
        checkpoint.evaluation_run_id,
        request.dataset_release_id,
        request.recipe_id,
        checkpoint.sha256,
        request.class_map_sha256,
        "development",
        request.policy,
        metrics,
        {},
        (),
    )
    manifest = publication_state(database[1], compiled)[0]
    record = {
        "result": asdict(result),
        "reports": [asdict(report)],
        "tensor_shapes": {
            key: [[1, 3, request.policy.imgsz, request.policy.imgsz]]
            for key in ("training", "validation", "inference")
        },
        "target_retention": [
            {
                "sample": PurePosixPath(item["image_path"]).name,
                "before": 0,
                "after": 0,
                "before_at_or_below_6px": 0,
                "after_at_or_below_6px": 0,
            }
            for item in manifest["samples"]
            if item["split"] == "train"
        ],
        "checkpoint_keys": {
            checkpoint.sha256: "sha256/" + checkpoint.sha256[:2] + "/" + checkpoint.sha256
        },
    }
    with database[1].begin() as connection:
        connection.execute(
            text(f'GRANT SELECT, INSERT ON training_run, evaluation_run TO "{database[2]}"')
        )

    def unexpected(**kwargs):
        pytest.fail("completion must not construct a backend")

    worker = CanonicalLocalTrainingWorker(
        training.mutations.engine,
        compiler_worker.objects,
        training.mutations.workspace_id,
        unexpected,
    )
    return worker, training, created, compiled, accepted, lease, record


def training_publication_state(owner, accepted):
    with owner.connect() as connection:
        run = (
            connection.execute(
                text("SELECT * FROM training_run WHERE run_key=:id"), {"id": accepted.resource.id}
            )
            .mappings()
            .one_or_none()
        )
        reports = (
            connection.execute(
                text(
                    "SELECT e.record FROM evaluation_run e JOIN training_run t "
                    "ON t.id=e.training_run_id WHERE t.run_key=:id"
                ),
                {"id": accepted.resource.id},
            )
            .scalars()
            .all()
        )
        events = connection.execute(
            select(canonical_event).where(
                canonical_event.c.event_type == "management.training_completed",
                canonical_event.c.aggregate_id == accepted.resource.id,
            )
        ).all()
        applications = connection.execute(
            text("SELECT count(*) FROM worker_application WHERE job_id=:id"),
            {"id": accepted.job_id},
        ).scalar_one()
        job = connection.execute(
            text("SELECT status,generation FROM worker_job WHERE job_id=:id"),
            {"id": accepted.job_id},
        ).one()
    return run, reports, events, applications, job


@pytest.mark.parametrize("recipe", ["full-letterbox-320-v1", "full-letterbox-640-v1"])
def test_training_publication_is_atomic_and_translates_only_verified_project_identity(
    database, compilation, compiler_worker, recipe
):
    import hashlib

    from labelloop_core.json_records import canonical_json

    worker, training, created, compiled, accepted, lease, record = prepared_training_publication(
        database, compilation, compiler_worker, recipe
    )
    record = compiler_worker.store.save_stage(lease, "training", record)

    def sink(connection, current, artifacts):
        worker.publish(connection, current, artifacts)
        assert training_publication_state(database[1], accepted)[:4] == (None, [], [], 0)
        assert connection.execute("SELECT current_user").fetchone()[0] == database[2]

    compiler_worker.store.complete(lease, ("training",), sink)
    run, reports, events, applications, job = training_publication_state(database[1], accepted)
    assert job.status == "succeeded" and applications == 1 and len(events) == len(reports) == 1
    expected = record["result"] | {
        "request": record["result"]["request"] | {"project_id": lease.project_key}
    }
    assert run["record"] == expected
    assert record["result"]["request"]["project_id"] == created.id
    assert run["input_manifest"] == publication_state(database[1], compiled)[0]
    assert reports == record["reports"]
    assert events[0].actor_kind == "system" and events[0].actor_id == lease.worker_id
    evidence = events[0].payload
    assert evidence["project_id"] == created.id and evidence["project_key"] == lease.project_key
    assert evidence["requested_by"] == Actor().id and evidence["generation"] == job.generation
    assert evidence["stage_sha256"] == hashlib.sha256(canonical_json(record)).hexdigest()
    assert training_request(training, compiled.resource.id) == accepted


@pytest.mark.parametrize(
    "grant", ["INSERT ON training_run", "INSERT ON evaluation_run", "INSERT ON canonical_event"]
)
def test_training_publication_missing_grant_rolls_back_and_resumes_saved_stage(
    database, compilation, compiler_worker, grant
):
    import psycopg

    worker, _, _, _, accepted, lease, record = prepared_training_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "training", record)
    with database[1].begin() as connection:
        connection.execute(text(f'REVOKE {grant} FROM "{database[2]}"'))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        compiler_worker.store.complete(lease, ("training",), worker.publish)
    assert training_publication_state(database[1], accepted)[:4] == (None, [], [], 0)
    with database[1].begin() as connection:
        connection.execute(text(f'GRANT {grant} TO "{database[2]}"'))
    compiler_worker.store.complete(lease, ("training",), worker.publish)
    assert training_publication_state(database[1], accepted)[4].status == "succeeded"


@pytest.mark.parametrize("failure", ["caller", "deadline"])
def test_training_late_sink_failure_rolls_back_all_effects(
    database, compilation, compiler_worker, failure
):
    from labelloop_worker_core.jobs import LeaseLost

    worker, _, _, _, accepted, lease, record = prepared_training_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "training", record)

    def sink(connection, current, artifacts):
        worker.publish(connection, current, artifacts)
        if failure == "caller":
            raise RuntimeError("synthetic caller failure")
        connection.execute(
            "UPDATE worker_job SET lease_until=clock_timestamp()-interval '1 second' "
            "WHERE job_id=%s",
            (lease.job_id,),
        )

    with pytest.raises(RuntimeError if failure == "caller" else LeaseLost):
        compiler_worker.store.complete(lease, ("training",), sink)
    assert training_publication_state(database[1], accepted)[:4] == (None, [], [], 0)


@pytest.mark.parametrize("terminal", ["expired", "cancelled", "superseded"])
def test_training_lost_lease_cannot_publish(database, compilation, compiler_worker, terminal):
    from labelloop_worker_core.jobs import LeaseLost

    worker, _, _, _, accepted, lease, record = prepared_training_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "training", record)
    if terminal == "cancelled":
        compiler_worker.store.cancel(lease.project_key, lease.job_id)
    else:
        with database[1].begin() as connection:
            connection.execute(
                text(
                    "UPDATE worker_job SET lease_until=clock_timestamp()-interval '1 second' "
                    "WHERE job_id=:id"
                ),
                {"id": lease.job_id},
            )
        if terminal == "superseded":
            assert compiler_worker.store.claim("train_local", "replacement", job_id=lease.job_id)
    with pytest.raises(LeaseLost):
        compiler_worker.store.complete(lease, ("training",), worker.publish)
    assert training_publication_state(database[1], accepted)[:4] == (None, [], [], 0)


@pytest.mark.parametrize(
    "change", ["request", "arguments", "selected", "report", "shape", "retention", "keys", "epochs"]
)
def test_training_completion_revalidates_saved_artifact(
    database, compilation, compiler_worker, change
):
    worker, _, _, _, accepted, lease, record = prepared_training_publication(
        database, compilation, compiler_worker
    )
    if change == "request":
        record["result"]["request"]["run_id"] = "different"
    elif change == "arguments":
        record["result"]["resolved_arguments"]["tile"] = True
    elif change == "selected":
        record["result"]["selected"]["sha256"] = "0" * 64
    elif change == "report":
        record["reports"][0]["weights_sha256"] = "0" * 64
    elif change == "shape":
        record["tensor_shapes"]["validation"] = [[1, 3, 352, 352]]
    elif change == "retention":
        record["target_retention"][0]["after"] = 1
    elif change == "keys":
        record["checkpoint_keys"] = {}
    else:
        record["result"]["checkpoints"] = []
    compiler_worker.store.save_stage(lease, "training", record)
    with pytest.raises(ValueError):
        compiler_worker.store.complete(lease, ("training",), worker.publish)
    assert training_publication_state(database[1], accepted)[:4] == (None, [], [], 0)


def test_training_saved_artifact_resumes_without_backend_or_object_access(
    database, compilation, compiler_worker
):
    from types import SimpleNamespace

    from labelloop_worker_core.jobs import JobRunner, Stage

    worker, _, _, _, accepted, lease, record = prepared_training_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "training", record)
    with database[1].begin() as connection:
        connection.execute(
            text(
                "UPDATE worker_job SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE job_id=:id"
            ),
            {"id": lease.job_id},
        )

    def unexpected(*args, **kwargs):
        pytest.fail("saved training must not retrain or read/upload objects")

    worker = replace(
        worker, objects=SimpleNamespace(read=unexpected, put=unexpected), backend=unexpected
    )
    runner = JobRunner(
        compiler_worker.store,
        "train_local",
        (Stage("training", unexpected),),
        worker.publish,
        job_id=lease.job_id,
    )
    assert runner.run_once("replacement")
    run, _, events, applications, job = training_publication_state(database[1], accepted)
    assert run is not None and applications == len(events) == 1
    assert job.status == "succeeded" and job.generation == 2
    assert events[0].payload["generation"] == 2
    assert not runner.run_once("replacement")


def test_training_publication_denies_broad_audit_runtime(database, compilation, compiler_worker):
    worker, _, _, _, accepted, lease, record = prepared_training_publication(
        database, compilation, compiler_worker
    )
    compiler_worker.store.save_stage(lease, "training", record)
    with database[1].begin() as connection:
        connection.execute(text(f'GRANT UPDATE ON canonical_event TO "{database[2]}"'))
    with pytest.raises(ValueError, match="must not rewrite"):
        compiler_worker.store.complete(lease, ("training",), worker.publish)
    assert training_publication_state(database[1], accepted)[:4] == (None, [], [], 0)


@pytest.mark.parametrize("recipe", ["full-letterbox-320-v1", "full-letterbox-640-v1"])
def test_training_stage_rehydrates_authorized_inputs_before_backend_execution(
    database, compilation, compiler_worker, recipe
):
    import hashlib
    import json

    from labelloop_api.management_compile_worker import content_key
    from labelloop_api.management_train_worker import CanonicalLocalTrainingWorker
    from labelloop_worker_core.jobs import StageContext

    training, _, compiled = prepared_training(database, compilation, compiler_worker, recipe)
    initialization = b"synthetic approved checkpoint; backend is not a real trainer"
    digest = hashlib.sha256(initialization).hexdigest()
    training = replace(
        training, profiles=(replace(training.profiles[0], initialization_sha256=digest),)
    )
    compiler_worker.objects.put(content_key(digest), initialization)
    accepted = training_request(training, compiled.resource.id)
    lease = compiler_worker.store.claim("train_local", "fixture-ml", job_id=accepted.job_id)
    paths = []

    def backend(**kwargs):
        paths.extend([kwargs["dataset"], kwargs["initialization"], kwargs["output"]])
        assert kwargs["initialization"].read_bytes() == initialization
        manifest = json.loads((kwargs["dataset"] / "manifest.json").read_bytes())
        assert manifest["recipe"]["recipe_id"] == recipe
        assert len(kwargs["validation_images"]) == 1
        image = kwargs["validation_images"][0]
        assert image.revision == 2 and image.objects == ()
        assert image.truth_digest() == manifest["samples"][0]["source_truth_sha256"]
        assert not kwargs["output"].exists()
        raise RuntimeError("synthetic backend reached")

    worker = CanonicalLocalTrainingWorker(
        training.mutations.engine, compiler_worker.objects, training.mutations.workspace_id, backend
    )
    with pytest.raises(RuntimeError, match="synthetic backend reached"):
        worker.train(StageContext(compiler_worker.store, lease, {}))
    assert paths and all(not path.exists() for path in paths)
    with database[1].connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM training_run WHERE run_key=:key"),
                {"key": accepted.resource.id},
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM worker_stage_artifact WHERE job_id=:id"),
                {"id": accepted.job_id},
            ).scalar_one()
            == 0
        )
    assert training_request(training, compiled.resource.id) == accepted


@pytest.mark.parametrize(
    "change", ["requester", "request", "profile", "variant", "workspace", "kind"]
)
def test_training_stage_refuses_forged_inputs_before_object_or_backend_access(
    database, training_ready, compiler_worker, change
):
    from copy import deepcopy
    from types import SimpleNamespace

    from labelloop_api.management_train_worker import CanonicalLocalTrainingWorker
    from labelloop_worker_core.jobs import StageContext

    training, _, compiled = training_ready
    accepted = training_request(training, compiled.resource.id)
    lease = compiler_worker.store.claim("train_local", "fixture-ml", job_id=accepted.job_id)
    inputs = deepcopy(lease.input)
    if change == "requester":
        inputs["requested_by"] = "forged"
    elif change == "request":
        inputs["request"]["epochs"] = 2
    elif change == "profile":
        inputs["profile"]["revision"] = 2
    elif change == "variant":
        inputs["variant_id"] = str(uuid4())
    elif change == "workspace":
        inputs["workspace_id"] = str(uuid4())
    lease = replace(lease, input=inputs, kind="compile_variant" if change == "kind" else lease.kind)

    def unexpected(*args, **kwargs):
        pytest.fail("forged authority must fail before object or backend access")

    worker = CanonicalLocalTrainingWorker(
        training.mutations.engine,
        SimpleNamespace(read=unexpected, put=unexpected),
        training.mutations.workspace_id,
        unexpected,
    )
    with pytest.raises(ValueError):
        worker.train(StageContext(compiler_worker.store, lease, {}))


@pytest.mark.parametrize("corrupt", ["manifest", "initialization", "dataset"])
def test_training_stage_refuses_corrupt_objects_before_backend_access(
    database, training_ready, compiler_worker, corrupt
):
    import hashlib
    from types import SimpleNamespace

    from labelloop_api.management_compile_worker import content_key
    from labelloop_api.management_train_worker import CanonicalLocalTrainingWorker
    from labelloop_worker_core.jobs import StageContext

    training, _, compiled = training_ready
    initialization = b"synthetic initialization"
    digest = hashlib.sha256(initialization).hexdigest()
    training = replace(
        training, profiles=(replace(training.profiles[0], initialization_sha256=digest),)
    )
    compiler_worker.objects.put(content_key(digest), initialization)
    accepted = training_request(training, compiled.resource.id)
    lease = compiler_worker.store.claim("train_local", "fixture-ml", job_id=accepted.job_id)
    manifest = publication_state(database[1], compiled)[0]
    broken = {
        "manifest": lease.input["manifest_key"],
        "initialization": content_key(digest),
        "dataset": content_key(manifest["file_hashes"]["class-map.json"]),
    }[corrupt]

    def read(key):
        return b"corrupt" if key == broken else compiler_worker.objects.read(key)

    def backend(**kwargs):
        pytest.fail("corrupt bytes must fail before backend construction")

    worker = CanonicalLocalTrainingWorker(
        training.mutations.engine,
        SimpleNamespace(read=read, put=compiler_worker.objects.put),
        training.mutations.workspace_id,
        backend,
    )
    with pytest.raises(ValueError, match="digest differs"):
        worker.train(StageContext(compiler_worker.store, lease, {}))


def prepared_training(
    database, compilation, compiler_worker, recipe="full-letterbox-320-v1", *, training_sample=False
):
    from uuid import UUID

    from labelloop_api.management_training import (
        ApprovedTrainingProfile,
        CanonicalManagementTraining,
    )
    from labelloop_worker_core.jobs import JobRunner, Stage

    service, owner, _ = database
    created, _, _, payload = release_inputs(service, owner)
    if training_sample:
        payload = add_training_sample(service, owner, created.id, payload)
    release = freeze(service, created.id, payload)
    accepted = compile_request(compilation, release.id, recipe=recipe)
    assert JobRunner(
        compiler_worker.store,
        "compile_variant",
        (Stage("compilation", compiler_worker.compile),),
        compiler_worker.publish,
        job_id=accepted.job_id,
    ).run_once("fixture-compiler")
    profiles = (ApprovedTrainingProfile(UUID(created.id), "baseline", 1, recipe, "1" * 64),)
    return CanonicalManagementTraining(service, compilation.enqueue, profiles), created, accepted


def add_training_sample(service, owner, project_id, payload):
    output = io.BytesIO()
    Image.new("RGB", (24, 12), "red").save(output, format="PNG")
    asset = ingest(
        service,
        project_id,
        AssetIngest(
            expected_revision=2,
            original_base64=base64.b64encode(output.getvalue()).decode(),
            source_uri="fixture:training",
            source_group="capture-b",
        ),
        key="train-image",
    )
    with owner.begin() as connection:
        source = connection.execute(
            select(source_asset).where(source_asset.c.asset_key == asset.id)
        ).one()
        ontology = connection.execute(
            select(ontology_release).where(ontology_release.c.project_id == project_id)
        ).one()
        connection.execute(
            insert(source_split_assignment).values(
                assignment_version="assignment-1",
                asset_id=source.id,
                group_id="group-2",
                split="train",
                seed=7,
                algorithm="union-find-grouped-stratified-v2",
                thresholds={"phash_max_distance": 8},
                reason="fixture independent training group",
                locked=True,
            )
        )
        connection.execute(
            insert(completeness_assessment).values(
                asset_id=source.id,
                revision=1,
                ontology_hash=completeness_context_hash(
                    ontology.class_map, ontology.classes, "Visible packages"
                ),
                scope_completeness="exhaustive_for_scope",
                actor_kind="human",
                actor_id=Actor().id,
                output_truncated=False,
                unresolved_regions=[],
            )
        )
        append_review(
            connection,
            asset_id=asset.id,
            actor_kind="human",
            actor_id=Actor().id,
            expected_revision=1,
            approval_state="human_accepted",
            review_tier="human_reviewed",
            scope_completeness="exhaustive_for_scope",
            objects=[],
            training_eligible=True,
        )
    return payload.model_copy(
        update={
            "expected_revision": 3,
            "annotation_revisions": payload.annotation_revisions | {asset.id: 2},
        }
    )


@pytest.fixture
def training_ready(database, compilation, compiler_worker):
    return prepared_training(database, compilation, compiler_worker)


def training_request(
    training, variant_id, *, key="train", actor=None, revision=1, profile="baseline"
):
    from labelloop_api.management_contracts import TrainingStart

    actor = actor or Actor()
    payload = TrainingStart(expected_revision=revision, training_profile_id=profile)
    context = MutationContext.bind(actor, key, "start_training", variant_id, payload)
    return training.start_training(actor, context, payload)


def training_receipts(owner, variant_id):
    with owner.connect() as connection:
        return connection.execute(
            select(canonical_event).where(
                canonical_event.c.event_type == "management.start_training",
                canonical_event.c.aggregate_id == variant_id,
            )
        ).all()


def training_jobs(owner, project_id):
    with owner.connect() as connection:
        return connection.execute(
            text("SELECT * FROM worker_job WHERE project_key=:key AND kind='train_local'"),
            {"key": pack_key(owner, project_id)},
        ).all()


@pytest.mark.parametrize("recipe", ["full-letterbox-320-v1", "full-letterbox-640-v1"])
def test_training_intent_pins_real_compilation_and_server_profile_in_caller_transaction(
    database, compilation, compiler_worker, recipe
):
    import hashlib

    from labelloop_compiler.compiler import json_bytes
    from labelloop_core.training import InferencePolicy, TrainingRequest

    training, created, compiled = prepared_training(database, compilation, compiler_worker, recipe)
    service, owner, role = database

    def enqueue(connection, *args, **kwargs):
        assert connection.execute("SELECT current_user").fetchone()[0] == role
        job_id = training.enqueue(connection, *args, **kwargs)
        assert training_jobs(owner, created.id) == []
        assert training_receipts(owner, compiled.resource.id) == []
        return job_id

    accepted = training_request(replace(training, enqueue=enqueue), compiled.resource.id)
    rows = training_jobs(owner, created.id)
    receipts = training_receipts(owner, compiled.resource.id)
    assert len(rows) == len(receipts) == 1
    row = rows[0]
    assert row.job_id == accepted.job_id and row.status == "queued" and row.max_attempts == 3
    assert receipts[0].actor_id == Actor().id and receipts[0].actor_kind == "human"
    assert row.input == receipts[0].payload["evidence"]["inputs"]
    assert row.input["variant_id"] == compiled.resource.id
    assert row.input["compile_job_id"] == compiled.job_id
    assert row.input["workspace_id"] == str(service.workspace_id)
    assert row.input["requested_by"] == Actor().id
    request = row.input["request"].copy()
    request["policy"] = InferencePolicy(**request["policy"])
    request = TrainingRequest(**request)
    assert request.run_id == accepted.resource.id and request.project_id == created.id
    assert request.recipe_id == recipe and request.policy.imgsz == (320 if "320" in recipe else 640)
    manifest = publication_state(owner, compiled)[0]
    assert request.dataset_manifest_sha256 == hashlib.sha256(json_bytes(manifest)).hexdigest()
    assert request.class_map_sha256 == manifest["file_hashes"]["class-map.json"]
    assert request.initialization_sha256 == "1" * 64
    assert row.input["profile"]["arguments"] == request.arguments()
    assert row.input["initialization_key"] == "sha256/11/" + "1" * 64
    lease = compiler_worker.store.claim("train_local", "fixture-ml", job_id=accepted.job_id)
    assert lease is not None and lease.input == row.input
    assert training_request(training, compiled.resource.id) == accepted
    # Profile removal cannot erase or replace an already-authorized retry receipt.
    assert training_request(replace(training, profiles=()), compiled.resource.id) == accepted
    assert len(training_jobs(owner, created.id)) == 1
    assert (
        CanonicalManagementReads(service.engine, service.workspace_id)
        .read(Actor(), "project", created.id)
        .revision
        == 3
    )


@pytest.mark.parametrize(
    "grant", ["INSERT ON worker_job", "SELECT ON worker_job", "INSERT ON canonical_event"]
)
def test_training_intent_grant_failure_rolls_back_queue_and_audit(database, training_ready, grant):
    from psycopg.errors import InsufficientPrivilege

    training, created, compiled = training_ready
    _, owner, role = database
    with owner.begin() as connection:
        connection.execute(text(f'REVOKE {grant} FROM "{role}"'))
    with pytest.raises((DBAPIError, InsufficientPrivilege)):
        training_request(training, compiled.resource.id)
    assert training_jobs(owner, created.id) == []
    assert training_receipts(owner, compiled.resource.id) == []
    with owner.begin() as connection:
        connection.execute(text(f'GRANT {grant} TO "{role}"'))
    assert training_request(training, compiled.resource.id).resource.state == "queued"


def test_training_intent_caller_failure_rolls_back_real_queue(database, training_ready):
    training, created, compiled = training_ready
    _, owner, _ = database

    def enqueue(*args, **kwargs):
        training.enqueue(*args, **kwargs)
        raise RuntimeError("synthetic training caller failure")

    with pytest.raises(RuntimeError, match="synthetic training caller failure"):
        training_request(replace(training, enqueue=enqueue), compiled.resource.id)
    assert training_jobs(owner, created.id) == []
    assert training_receipts(owner, compiled.resource.id) == []


@pytest.mark.parametrize("same_key", [True, False])
def test_training_intent_concurrent_retry_and_unique_profile(database, training_ready, same_key):
    training, created, compiled = training_ready
    _, owner, _ = database
    barrier = Barrier(2)

    def submit(index):
        barrier.wait(timeout=10)
        try:
            return training_request(
                training, compiled.resource.id, key="same" if same_key else str(index)
            )
        except ManagementFailure as exc:
            return exc.error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    if same_key:
        assert results[0] == results[1]
    else:
        assert results.count("revision_conflict") == 1
    assert len(training_jobs(owner, created.id)) == 1
    assert len(training_receipts(owner, compiled.resource.id)) == 1


@pytest.mark.parametrize(
    "actor",
    [
        Actor(role="reviewer"),
        Actor(kind="service", role="worker_core"),
        Actor(kind="service", role="worker_ml"),
        Actor(kind="service", role="worker_llm"),
        Actor(kind="service", role="worker_platform"),
    ],
)
def test_training_intent_denies_nonadmin_before_queue(database, training_ready, actor):
    training, created, compiled = training_ready
    _, owner, _ = database
    with pytest.raises(ManagementFailure) as exc:
        training_request(training, compiled.resource.id, actor=actor)
    assert exc.value.error.detail == "permission_denied"
    assert training_jobs(owner, created.id) == []


@pytest.mark.parametrize(
    "failure",
    [
        "foreign_workspace",
        "unknown_variant",
        "unknown_profile",
        "foreign_profile",
        "wrong_recipe",
        "stale_revision",
        "forged_context",
        "unpublished_variant",
    ],
)
def test_training_intent_refuses_untrusted_or_inaccessible_inputs(
    database, training_ready, failure
):
    from labelloop_api.management_contracts import TrainingStart

    training, created, compiled = training_ready
    _, owner, _ = database
    variant_id = compiled.resource.id
    profile = training.profiles[0]
    expected = "not_found"
    if failure == "foreign_workspace":
        training = replace(training, mutations=replace(training.mutations, workspace_id=uuid4()))
    elif failure == "unknown_variant":
        variant_id = str(uuid4())
    elif failure == "unknown_profile":
        training = replace(training, profiles=())
    elif failure == "foreign_profile":
        training = replace(training, profiles=(replace(profile, project_id=uuid4()),))
    elif failure == "wrong_recipe":
        training = replace(
            training, profiles=(replace(profile, recipe_id="full-letterbox-640-v1"),)
        )
        expected = "incompatible_recipe"
    elif failure == "stale_revision":
        expected = "revision_conflict"
    elif failure == "forged_context":
        expected = "invalid_request"
    elif failure == "unpublished_variant":
        from labelloop_api.management_compilation import CanonicalManagementCompilation

        manifest = publication_state(owner, compiled)[0]
        queued = compile_request(
            CanonicalManagementCompilation(training.mutations, training.enqueue),
            manifest["release_id"],
            key="queued-640",
            recipe="full-letterbox-640-v1",
        )
        variant_id = queued.resource.id
    payload = TrainingStart(
        expected_revision=2 if failure == "stale_revision" else 1, training_profile_id="baseline"
    )
    context = MutationContext.bind(Actor(), "train", "start_training", variant_id, payload)
    if failure == "forged_context":
        context = replace(context, actor_id="forged")
    with pytest.raises(ManagementFailure) as exc:
        training.start_training(Actor(), context, payload)
    assert exc.value.error.detail == expected
    assert training_jobs(owner, created.id) == []
    assert training_receipts(owner, compiled.resource.id) == []


@pytest.mark.parametrize(
    "failure",
    [
        "missing_receipt",
        "duplicate_receipt",
        "compile_not_completed",
        "forged_snapshot_receipt",
        "missing_freeze",
    ],
)
def test_training_requires_fenced_compiler_and_historical_snapshot(
    database, training_ready, failure
):
    training, created, compiled = training_ready
    _, owner, _ = database
    with owner.begin() as connection:
        if failure in {"missing_receipt", "missing_freeze"}:
            connection.execute(
                canonical_event.delete().where(
                    canonical_event.c.event_type
                    == (
                        "management.variant_compiled"
                        if failure == "missing_receipt"
                        else "management.freeze_release"
                    ),
                    canonical_event.c.aggregate_id
                    == (compiled.resource.id if failure == "missing_receipt" else created.id),
                )
            )
        elif failure == "duplicate_receipt":
            receipt = connection.execute(
                select(canonical_event).where(
                    canonical_event.c.event_type == "management.variant_compiled",
                    canonical_event.c.aggregate_id == compiled.resource.id,
                )
            ).one()
            connection.execute(
                insert(canonical_event).values(
                    event_type=receipt.event_type,
                    aggregate_id=receipt.aggregate_id,
                    expected_revision=receipt.expected_revision,
                    payload=receipt.payload,
                    actor_kind=receipt.actor_kind,
                    actor_id=receipt.actor_id,
                )
            )
        elif failure == "compile_not_completed":
            connection.execute(
                text("UPDATE worker_job SET status='failed' WHERE job_id=:id"),
                {"id": compiled.job_id},
            )
        else:
            receipt = connection.execute(
                select(canonical_event).where(
                    canonical_event.c.event_type == "management.variant_compiled",
                    canonical_event.c.aggregate_id == compiled.resource.id,
                )
            ).one()
            connection.execute(
                canonical_event.update()
                .where(canonical_event.c.id == receipt.id)
                .values(payload={**receipt.payload, "canonical_snapshot_sha256": "0" * 64})
            )
    with pytest.raises(ManagementFailure) as exc:
        training_request(training, compiled.resource.id)
    assert exc.value.error.detail == "missing_evidence"
    assert training_jobs(owner, created.id) == []
    assert training_receipts(owner, compiled.resource.id) == []


def test_training_compiler_job_input_is_immutable(database, training_ready):
    training, created, compiled = training_ready
    _, owner, _ = database
    with owner.connect() as connection:
        original = connection.execute(
            text("SELECT input FROM worker_job WHERE job_id=:id"), {"id": compiled.job_id}
        ).scalar_one()
    with pytest.raises(DBAPIError, match="worker stage and application records are immutable"):
        with owner.begin() as connection:
            connection.execute(
                text(
                    "UPDATE worker_job SET input=jsonb_set(input,'{variant_id}','\"forged\"') "
                    "WHERE job_id=:id"
                ),
                {"id": compiled.job_id},
            )
    with owner.connect() as connection:
        assert (
            connection.execute(
                text("SELECT input FROM worker_job WHERE job_id=:id"), {"id": compiled.job_id}
            ).scalar_one()
            == original
        )
    assert training_jobs(owner, created.id) == []
    assert training_receipts(owner, compiled.resource.id) == []
    assert training_request(training, compiled.resource.id).resource.state == "queued"
    assert len(training_jobs(owner, created.id)) == 1
    assert len(training_receipts(owner, compiled.resource.id)) == 1


def test_training_profiles_are_select_only_project_scoped_and_retries_precede_revision(
    database, training_ready
):
    training, created, compiled = training_ready
    _, owner, role = database
    profiles = training.list_training_profiles(Actor(), created.id)
    assert [item.id for item in profiles.profiles] == ["baseline"]
    profile = training.profiles[0]
    other = replace(profile, id="experiment", seed=9)
    training = replace(training, profiles=(profile, other))
    first = training_request(training, compiled.resource.id)
    with pytest.raises(ManagementFailure) as exc:
        training_request(training, compiled.resource.id, revision=2)
    assert exc.value.error.detail == "idempotency_conflict"
    with pytest.raises(ManagementFailure) as exc:
        training_request(training, compiled.resource.id, key="new-key")
    assert exc.value.error.detail == "revision_conflict"
    second = training_request(
        training, compiled.resource.id, key="experiment", profile="experiment"
    )
    third = training_request(
        replace(training, profiles=(replace(profile, revision=2),)),
        compiled.resource.id,
        key="revision-two",
    )
    assert len({first.job_id, second.job_id, third.job_id}) == 3
    with owner.begin() as connection:
        connection.execute(
            text(f'REVOKE INSERT, UPDATE ON ALL TABLES IN SCHEMA public FROM "{role}"')
        )
    reader = type(
        "Reader",
        (),
        {
            "id": "reviewer",
            "kind": "human",
            "role": "reviewer",
            "permits": lambda self, p: p == "review",
        },
    )()
    assert training.list_training_profiles(reader, created.id).profiles[0] == profiles.profiles[0]
    with pytest.raises(ManagementFailure) as exc:
        training.list_training_profiles(reader, str(uuid4()))
    assert exc.value.error.detail == "not_found"
    with pytest.raises(ManagementFailure) as exc:
        training.list_training_profiles(Actor(kind="service"), created.id)
    assert exc.value.error.detail == "permission_denied"


@pytest.mark.parametrize("role", ["admin", "reviewer"])
def test_training_intent_binds_real_shared_session_and_csrf(
    database, training_ready, restricted_auth_store, auth_account, role
):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from labelloop_api.auth_schema import principal
    from labelloop_api.main import create_app

    training, created, compiled = training_ready
    _, owner, _ = database
    identity, credentials = auth_account
    with owner.begin() as connection:
        connection.execute(principal.update().where(principal.c.id == identity).values(role=role))
    origin = "https://testserver"
    operations = SimpleNamespace(
        start_training=training.start_training,
        list_training_profiles=training.list_training_profiles,
    )
    body = {"expected_revision": 1, "training_profile_id": "baseline"}
    path = f"/variants/{compiled.resource.id}/training-runs"
    with TestClient(create_app(lambda: operations), base_url=origin) as client:
        assert client.post(path, json=body).status_code == 401
        login = client.post("/auth/login", json=credentials, headers={"origin": origin})
        assert login.status_code == 200
        assert client.get(f"/projects/{created.id}/training-profiles").status_code == 200
        assert client.post(path, json=body, headers={"Idempotency-Key": "train"}).status_code == 403
        assert training_jobs(owner, created.id) == []
        headers = {
            "origin": origin,
            "x-csrf-token": login.json()["csrf_token"],
            "Idempotency-Key": "train",
        }
        response = client.post(path, json=body, headers=headers)
        if role == "reviewer":
            assert response.status_code == 403
            assert training_jobs(owner, created.id) == []
        else:
            assert response.status_code == 202, response.text
            assert client.post(path, json=body, headers=headers).json() == response.json()
            assert training_receipts(owner, compiled.resource.id)[0].actor_id == identity
        assert client.post("/auth/logout", headers=headers).status_code == 204
        assert client.post(path, json=body, headers=headers).status_code == 401


@pytest.fixture
def compilation(database):
    from labelloop_api.management_compilation import CanonicalManagementCompilation
    from labelloop_worker_core.jobs import JobStore

    service, owner, role = database
    with owner.begin() as connection:
        connection.execute(text(f'GRANT SELECT, INSERT ON worker_job TO "{role}"'))
    return CanonicalManagementCompilation(service, JobStore.enqueue_in_transaction)


@pytest.fixture
def compiler_worker(database, compilation):
    from types import SimpleNamespace

    import psycopg

    from labelloop_api.management_compile_worker import CanonicalCompilationWorker
    from labelloop_worker_core.jobs import JobStore

    service, owner, role = database
    with owner.begin() as connection:
        connection.execute(
            text(
                f"GRANT SELECT, INSERT ON worker_stage_artifact, worker_application, "
                f'preprocessing_recipe, yolo_dataset_variant, derived_sample TO "{role}"'
            )
        )
        connection.execute(text(f'GRANT UPDATE ON worker_job TO "{role}"'))
    store = JobStore(
        lambda: psycopg.connect(
            os.environ["DATABASE_URL"].replace("+psycopg", ""),
            options=f"-c role={role} -c statement_timeout=15000 -c lock_timeout=10000",
        )
    )
    objects = SimpleNamespace(
        read=lambda key: service.store.root.joinpath(*key.split("/")).read_bytes(),
        put=service.store.put,
    )
    return CanonicalCompilationWorker(store, objects, service.workspace_id)


def prepared_compilation(database, compilation, worker):
    from labelloop_worker_core.jobs import StageContext

    service, owner, _ = database
    created, asset, _, payload = release_inputs(service, owner)
    release = freeze(service, created.id, payload)
    accepted = compile_request(compilation, release.id)
    lease = worker.store.claim("compile_variant", "fixture-compiler", job_id=accepted.job_id)
    assert lease is not None
    record = worker.compile(StageContext(worker.store, lease, {}))
    record = worker.store.save_stage(lease, "compilation", record)
    return created, asset, release, accepted, lease, record


def publication_state(owner, accepted):
    with owner.connect() as connection:
        variant = connection.execute(
            text("SELECT file_manifest FROM yolo_dataset_variant WHERE id=:id"),
            {"id": accepted.resource.id},
        ).scalar_one_or_none()
        application = connection.execute(
            text("SELECT count(*) FROM worker_application WHERE job_id=:id"),
            {"id": accepted.job_id},
        ).scalar_one()
        events = connection.execute(
            select(canonical_event).where(
                canonical_event.c.event_type == "management.variant_compiled",
                canonical_event.c.aggregate_id == accepted.resource.id,
            )
        ).all()
        job = connection.execute(
            text("SELECT status,generation FROM worker_job WHERE job_id=:id"),
            {"id": accepted.job_id},
        ).one()
    return variant, application, events, job


@pytest.mark.parametrize("recipe", ["full-letterbox-320-v1", "full-letterbox-640-v1"])
def test_compiler_runner_publishes_real_manifest_lineage_and_audit_atomically(
    database, compilation, compiler_worker, recipe
):
    import hashlib
    import json

    from labelloop_api.management_compile_worker import content_key
    from labelloop_worker_core.jobs import JobRunner, Stage

    service, owner, _ = database
    worker = compiler_worker
    created, asset, source, payload = release_inputs(service, owner)
    release = freeze(service, created.id, payload)
    accepted = compile_request(compilation, release.id, recipe=recipe)

    def sink(connection, lease, artifacts):
        worker.publish(connection, lease, artifacts)
        # Variant, lineage, audit and application stay invisible until complete commits.
        assert publication_state(owner, accepted)[:3] == (None, 0, [])
        assert connection.execute("SELECT current_user").fetchone()[0] == database[2]

    runner = JobRunner(
        worker.store,
        "compile_variant",
        (Stage("compilation", worker.compile),),
        sink,
        job_id=accepted.job_id,
    )
    assert runner.run_once("fixture-compiler")
    manifest, applications, events, job = publication_state(owner, accepted)
    assert job.status == "succeeded" and applications == 1 and len(events) == 1
    assert not runner.run_once("fixture-compiler")
    assert compile_request(compilation, release.id, recipe=recipe) == accepted
    assert manifest["recipe"]["output_size"] == (320 if "320" in recipe else 640)
    assert manifest["samples"][0]["revision"] == 2
    assert manifest["samples"][0]["asset_id"] == asset.id
    assert manifest["samples"][0]["verified_negative"] is True
    evidence = events[0].payload
    assert events[0].actor_kind == "system" and events[0].actor_id == "fixture-compiler"
    assert evidence["requested_by"] == Actor().id and evidence["generation"] == job.generation
    data = worker.objects.read(evidence["manifest_key"])
    assert hashlib.sha256(data).hexdigest() == evidence["manifest_sha256"]
    assert json.loads(data) == manifest
    for sha256 in manifest["file_hashes"].values():
        assert hashlib.sha256(worker.objects.read(content_key(sha256))).hexdigest() == sha256
    with owner.connect() as connection:
        derived = connection.execute(
            text("SELECT * FROM derived_sample WHERE variant_id=:id"), {"id": accepted.resource.id}
        ).one()
        assert derived.source_id == source.id
        assert derived.transform == manifest["samples"][0]["transform"]
        assert derived.image_key == content_key(manifest["samples"][0]["image_sha256"])
        assert derived.crop == derived.transform["crop"]
    assert (
        CanonicalManagementReads(service.engine, service.workspace_id)
        .read(Actor(), "project", created.id)
        .revision
        == 3
    )


def test_compiler_uses_frozen_labels_ontology_and_membership_after_later_changes(
    database, compilation, compiler_worker
):
    from labelloop_worker_core.jobs import JobRunner, Stage

    service, owner, _ = database
    created, asset, _, payload = release_inputs(service, owner)
    release = freeze(service, created.id, payload)
    accepted = compile_request(compilation, release.id)
    with owner.begin() as connection:
        append_review(
            connection,
            asset_id=asset.id,
            actor_kind="human",
            actor_id=Actor().id,
            expected_revision=2,
            approval_state="rejected",
            review_tier="human_reviewed",
            scope_completeness="unreviewed",
            objects=[],
            training_eligible=False,
        )
        ontology = connection.execute(
            select(ontology_release).where(ontology_release.c.project_id == created.id)
        ).one()
        connection.execute(
            insert(ontology_release).values(
                project_id=created.id,
                ontology_key="later-" + uuid4().hex,
                version=2,
                classes=ontology.classes,
                class_map=ontology.class_map,
            )
        )
    ingest(service, created.id, image_payload(revision=3), key="later-image")
    worker = compiler_worker
    JobRunner(
        worker.store,
        "compile_variant",
        (Stage("compilation", worker.compile),),
        worker.publish,
        job_id=accepted.job_id,
    ).run_once("fixture-compiler")
    manifest, applications, _, job = publication_state(owner, accepted)
    assert job.status == "succeeded" and applications == 1
    assert manifest["ontology_version"] == 1
    assert manifest["release_membership"] == {asset.id: "compiled"}
    assert manifest["samples"][0]["revision"] == 2


@pytest.mark.parametrize(
    "grant",
    [
        "INSERT ON preprocessing_recipe",
        "INSERT ON yolo_dataset_variant",
        "INSERT ON derived_sample",
        "INSERT ON canonical_event",
    ],
)
def test_compiler_publication_missing_grant_rolls_back_all_canonical_effects(
    database, compilation, compiler_worker, grant
):
    import psycopg

    _, owner, role = database
    _, _, _, accepted, lease, _ = prepared_compilation(database, compilation, compiler_worker)
    with owner.begin() as connection:
        connection.execute(text(f'REVOKE {grant} FROM "{role}"'))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        compiler_worker.store.complete(lease, ("compilation",), compiler_worker.publish)
    assert publication_state(owner, accepted)[:3] == (None, 0, [])
    with owner.begin() as connection:
        connection.execute(text(f'GRANT {grant} TO "{role}"'))
    compiler_worker.store.complete(lease, ("compilation",), compiler_worker.publish)
    assert publication_state(owner, accepted)[3].status == "succeeded"


@pytest.mark.parametrize("terminal", ["expired", "cancelled", "superseded"])
def test_compiler_stale_or_cancelled_lease_cannot_publish(
    database, compilation, compiler_worker, terminal
):
    from labelloop_worker_core.jobs import LeaseLost

    _, owner, _ = database
    _, _, _, accepted, lease, _ = prepared_compilation(database, compilation, compiler_worker)
    if terminal == "cancelled":
        assert compiler_worker.store.cancel(lease.project_key, lease.job_id)
    else:
        with owner.begin() as connection:
            connection.execute(
                text(
                    "UPDATE worker_job SET lease_until=clock_timestamp()-interval '1 second' "
                    "WHERE job_id=:id"
                ),
                {"id": lease.job_id},
            )
        if terminal == "superseded":
            assert compiler_worker.store.claim(
                "compile_variant", "replacement", job_id=lease.job_id
            )
    with pytest.raises(LeaseLost):
        compiler_worker.store.complete(lease, ("compilation",), compiler_worker.publish)
    assert publication_state(owner, accepted)[:3] == (None, 0, [])


def test_compiler_expired_worker_resumes_saved_artifact_without_recompilation(
    database, compilation, compiler_worker, monkeypatch
):
    from labelloop_worker_core.jobs import JobRunner, Stage

    _, owner, _ = database
    _, _, _, accepted, lease, record = prepared_compilation(database, compilation, compiler_worker)
    with owner.begin() as connection:
        connection.execute(
            text(
                "UPDATE worker_job SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE job_id=:id"
            ),
            {"id": lease.job_id},
        )

    def forbidden(*args, **kwargs):
        raise AssertionError("saved compilation must not recompile or upload")

    monkeypatch.setattr("labelloop_api.management_compile_worker.compile_dataset", forbidden)
    JobRunner(
        compiler_worker.store,
        "compile_variant",
        (Stage("compilation", forbidden),),
        compiler_worker.publish,
        job_id=lease.job_id,
    ).run_once("replacement")
    manifest, applications, events, job = publication_state(owner, accepted)
    assert job.status == "succeeded" and applications == 1 and len(events) == 1
    assert job.generation == 2 and events[0].payload["generation"] == 2
    assert manifest == record["manifest"]


@pytest.mark.parametrize("failure", ["caller", "deadline"])
def test_compiler_late_sink_failure_rolls_back_variant_audit_and_application(
    database, compilation, compiler_worker, failure
):
    from labelloop_worker_core.jobs import LeaseLost

    _, owner, _ = database
    _, _, _, accepted, lease, _ = prepared_compilation(database, compilation, compiler_worker)

    def sink(connection, current, artifacts):
        compiler_worker.publish(connection, current, artifacts)
        if failure == "caller":
            raise RuntimeError("fixture sink failure")
        connection.execute(
            "UPDATE worker_job SET lease_until=clock_timestamp()-interval '1 second' "
            "WHERE job_id=%s",
            (current.job_id,),
        )

    with pytest.raises(RuntimeError if failure == "caller" else LeaseLost):
        compiler_worker.store.complete(lease, ("compilation",), sink)
    assert publication_state(owner, accepted)[:3] == (None, 0, [])


@pytest.mark.parametrize(
    "field", ["workspace_id", "project_id", "release_id", "recipe", "variant_id"]
)
def test_compiler_refuses_forged_lease_inputs_before_reading_objects(
    database, compilation, compiler_worker, field
):
    import copy

    from labelloop_worker_core.jobs import StageContext

    _, _, _, _, lease, _ = prepared_compilation(database, compilation, compiler_worker)
    inputs = copy.deepcopy(lease.input)
    if field == "recipe":
        inputs["recipe"]["output_size"] = 640
    else:
        inputs[field] = str(uuid4())
    with pytest.raises(ValueError):
        compiler_worker.compile(
            StageContext(compiler_worker.store, replace(lease, input=inputs), {})
        )


@pytest.mark.parametrize(
    "field", ["recipe", "revision", "source_sha256", "split", "manifest_sha256"]
)
def test_compiler_publication_refuses_mismatched_stage_lineage(
    database, compilation, compiler_worker, field
):
    import copy
    import hashlib

    from labelloop_compiler.compiler import json_bytes

    _, owner, _ = database
    _, _, _, accepted, lease, record = prepared_compilation(database, compilation, compiler_worker)
    changed = copy.deepcopy(record)
    if field == "recipe":
        changed["manifest"]["recipe"]["output_size"] = 640
    elif field == "manifest_sha256":
        changed["manifest_sha256"] = "0" * 64
    else:
        changed["manifest"]["samples"][0][field] = {
            "revision": 999,
            "source_sha256": "0" * 64,
            "split": "train",
        }[field]
    if field != "manifest_sha256":
        changed["manifest_sha256"] = hashlib.sha256(json_bytes(changed["manifest"])).hexdigest()

    def sink(connection, current, artifacts):
        compiler_worker.publish(connection, current, {"compilation": changed})

    with pytest.raises(ValueError):
        compiler_worker.store.complete(lease, ("compilation",), sink)
    assert publication_state(owner, accepted)[:3] == (None, 0, [])


def test_compiler_storage_failure_does_not_publish_and_retries_real_compiler(
    database, compilation, compiler_worker
):
    from labelloop_worker_core.jobs import JobRunner, Stage

    service, owner, _ = database
    created, _, _, payload = release_inputs(service, owner)
    release = freeze(service, created.id, payload)
    accepted = compile_request(compilation, release.id)
    worker = compiler_worker
    original = worker.objects.put

    def failing(key, data):
        # An immutable orphan is safe; no successful canonical reference exists.
        original(key, data)
        raise OSError("fixture object-store unavailable")

    worker.objects.put = failing
    runner = JobRunner(
        worker.store,
        "compile_variant",
        (Stage("compilation", worker.compile),),
        worker.publish,
        job_id=accepted.job_id,
    )
    assert runner.run_once("fixture-compiler")
    assert publication_state(owner, accepted)[:3] == (None, 0, [])
    assert publication_state(owner, accepted)[3].status == "queued"
    worker.objects.put = original
    with owner.begin() as connection:
        connection.execute(
            text("UPDATE worker_job SET available_at=clock_timestamp() WHERE job_id=:id"),
            {"id": accepted.job_id},
        )
    assert runner.run_once("fixture-compiler")
    assert publication_state(owner, accepted)[3].status == "succeeded"


def test_compiler_publication_refuses_broad_audit_runtime(database, compilation, compiler_worker):
    _, owner, role = database
    _, _, _, accepted, lease, _ = prepared_compilation(database, compilation, compiler_worker)
    with owner.begin() as connection:
        connection.execute(text(f'GRANT UPDATE ON canonical_event TO "{role}"'))
    with pytest.raises(ValueError, match="must not rewrite"):
        compiler_worker.store.complete(lease, ("compilation",), compiler_worker.publish)
    assert publication_state(owner, accepted)[:3] == (None, 0, [])


@pytest.mark.parametrize("recipe", ["full-letterbox-320-v1", "full-letterbox-640-v1"])
def test_compiler_publication_preserves_positive_original_frame_geometry(
    database, compilation, compiler_worker, recipe
):
    from labelloop_api.management_compile_worker import content_key
    from labelloop_worker_core.jobs import JobRunner, Stage

    service, owner, _ = database
    created, asset, source, payload = release_inputs(service, owner, complete=False)
    objects = [{"object_id": "box-1", "class_id": "package", "box": [1, 1, 11, 9]}]
    with owner.begin() as connection:
        append_review(
            connection,
            asset_id=asset.id,
            actor_kind="human",
            actor_id=Actor().id,
            expected_revision=1,
            approval_state="draft",
            review_tier="human_reviewed",
            scope_completeness="unreviewed",
            objects=objects,
        )
        assert (
            connection.execute(
                select(completeness_assessment).where(
                    completeness_assessment.c.asset_id == source.id
                )
            ).first()
            is None
        )
        with pytest.raises(ValueError, match="valid current completeness"):
            append_review(
                connection,
                asset_id=asset.id,
                actor_kind="human",
                actor_id=Actor().id,
                expected_revision=2,
                approval_state="human_accepted",
                review_tier="human_reviewed",
                scope_completeness="exhaustive_for_scope",
                training_eligible=True,
                objects=objects,
            )
        ontology = connection.execute(
            select(ontology_release).where(ontology_release.c.project_id == created.id)
        ).one()
        connection.execute(
            insert(completeness_assessment).values(
                asset_id=source.id,
                revision=2,
                ontology_hash=completeness_context_hash(
                    ontology.class_map, ontology.classes, "Visible packages"
                ),
                scope_completeness="exhaustive_for_scope",
                actor_kind="human",
                actor_id=Actor().id,
                output_truncated=False,
                unresolved_regions=[],
            )
        )
        append_review(
            connection,
            asset_id=asset.id,
            actor_kind="human",
            actor_id=Actor().id,
            expected_revision=2,
            approval_state="human_accepted",
            review_tier="human_reviewed",
            scope_completeness="exhaustive_for_scope",
            training_eligible=True,
            objects=objects,
        )
    payload = payload.model_copy(update={"annotation_revisions": {asset.id: 3}})
    release = freeze(service, created.id, payload)
    accepted = compile_request(compilation, release.id, recipe=recipe)
    worker = compiler_worker
    JobRunner(
        worker.store,
        "compile_variant",
        (Stage("compilation", worker.compile),),
        worker.publish,
        job_id=accepted.job_id,
    ).run_once("fixture-compiler")
    manifest, _, _, job = publication_state(owner, accepted)
    assert job.status == "succeeded"
    sample = manifest["samples"][0]
    assert sample["objects"][0]["source_box"] == [1, 1, 11, 9]
    assert sample["verified_negative"] is False and sample["revision"] == 3
    assert manifest["class_support"]["package"]["val"] == 1
    labels = worker.objects.read(content_key(sample["label_sha256"])).decode()
    assert labels == sample["objects"][0]["yolo_row"] + "\n"
    raster = Image.open(io.BytesIO(worker.objects.read(content_key(sample["image_sha256"]))))
    assert raster.size == (manifest["recipe"]["output_size"],) * 2
    from labelloop_core.geometry import Box

    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    detail = reads.read_recipe_preview(Reader(), release.id, recipe, asset.id)
    assert len(detail.boxes) == 1
    projected = detail.boxes[0]
    assert projected.box == detail.transform.apply(Box(1, 1, 11, 9))
    assert projected.object_id == "box-1" and projected.class_id == "package"
    assert projected.review_tier == "human_reviewed" and projected.clipped is False
    assert projected.flag == "none" and detail.tiny_targets == 0
    with owner.begin() as connection:
        append_review(
            connection,
            asset_id=asset.id,
            actor_kind="human",
            actor_id=Actor().id,
            expected_revision=3,
            approval_state="rejected",
            review_tier="human_reviewed",
            scope_completeness="partial",
            objects=[],
        )
    assert reads.read_recipe_preview(Reader(), release.id, recipe, asset.id) == detail


def test_recipe_preview_is_read_only_and_requires_committed_compiler_publication(
    database, compilation, compiler_worker
):
    from labelloop_compiler.compiler import transform_document

    service, owner, role = database
    _, asset, release, accepted, lease, record = prepared_compilation(
        database, compilation, compiler_worker
    )
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    recipe = "full-letterbox-320-v1"
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        reads.read_recipe_preview(Reader(), release.id, recipe, asset.id)

    def sink(connection, current, artifacts):
        compiler_worker.publish(connection, current, artifacts)
        with pytest.raises(ManagementFailure, match="missing_evidence"):
            reads.read_recipe_preview(Reader(), release.id, recipe, asset.id)

    compiler_worker.store.complete(lease, ("compilation",), sink)
    before = publication_state(owner, accepted)
    readonly = create_engine(
        os.environ["DATABASE_URL"],
        connect_args={"options": f"-c role={role} -c default_transaction_read_only=on"},
    )
    try:
        detail = CanonicalManagementReads(readonly, service.workspace_id).read_recipe_preview(
            Reader(), release.id, recipe, asset.id
        )
    finally:
        readonly.dispose()
    assert detail == reads.read_recipe_preview(Actor(), release.id, recipe, asset.id)
    assert detail.recipe_id == recipe and detail.recipe_revision == 1
    assert detail.boxes == [] and detail.source_asset_id == asset.id
    assert detail.source_width == 24 and detail.source_height == 12
    assert transform_document(detail.transform) == record["manifest"]["samples"][0]["transform"]
    assert detail.manifest_artifact_id == record["manifest_sha256"]
    assert (
        detail.validation_artifact_id == record["manifest"]["file_hashes"]["validation-report.json"]
    )
    assert detail.tiny_targets == detail.below_minimum_targets == detail.clipped_targets == 0
    assert detail.excluded_images == 0
    assert detail.checks[0].passed is True and detail.checks[1].passed is None
    assert "sha256/" not in detail.model_dump_json()
    assert "fixture:" not in detail.model_dump_json()
    assert publication_state(owner, accepted) == before


@pytest.mark.parametrize(
    "overrides", [{"kind": "service"}, {"granted": False}, {"role": "worker_ml"}, {"id": ""}]
)
def test_recipe_preview_denies_untrusted_identity_before_lookup(database, overrides):
    service, _, _ = database
    with pytest.raises(ManagementFailure, match="permission_denied"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_recipe_preview(
            Reader(**overrides), "unknown", "full-letterbox-320-v1", "unknown"
        )


def test_recipe_preview_isolates_release_recipe_and_source(database, compilation, compiler_worker):
    service, _, _ = database
    _, asset, release, _, lease, _ = prepared_compilation(database, compilation, compiler_worker)
    compiler_worker.store.complete(lease, ("compilation",), compiler_worker.publish)
    for workspace, release_id in [(uuid4(), release.id), (service.workspace_id, "unknown")]:
        with pytest.raises(ManagementFailure, match="not_found"):
            CanonicalManagementReads(service.engine, workspace).read_recipe_preview(
                Reader(), release_id, "full-letterbox-320-v1", asset.id
            )
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    for recipe, asset_id in [
        ("full-letterbox-640-v1", asset.id),
        ("full-letterbox-320-v1", "unknown"),
    ]:
        with pytest.raises(ManagementFailure, match="missing_evidence"):
            reads.read_recipe_preview(Reader(), release.id, recipe, asset_id)


@pytest.mark.parametrize("change", ["generation", "failed", "duplicate_receipt"])
def test_recipe_preview_requires_current_unique_compiler_application(
    database, compilation, compiler_worker, change
):
    service, owner, _ = database
    _, asset, release, accepted, lease, _ = prepared_compilation(
        database, compilation, compiler_worker
    )
    compiler_worker.store.complete(lease, ("compilation",), compiler_worker.publish)
    with owner.begin() as connection:
        if change == "duplicate_receipt":
            receipt = (
                connection.execute(
                    select(canonical_event).where(
                        canonical_event.c.event_type == "management.variant_compiled",
                        canonical_event.c.aggregate_id == accepted.resource.id,
                    )
                )
                .mappings()
                .one()
            )
            connection.execute(
                insert(canonical_event).values(
                    **{key: value for key, value in receipt.items() if key != "id"}
                )
            )
        else:
            assignment = "status='failed'" if change == "failed" else "generation=generation+1"
            connection.execute(
                text(f"UPDATE worker_job SET {assignment} WHERE job_id=:id"),
                {"id": accepted.job_id},
            )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_recipe_preview(
            Reader(), release.id, "full-letterbox-320-v1", asset.id
        )


@pytest.mark.parametrize(
    "change", ["transform", "source_hash", "tier", "objects", "test_split", "size"]
)
def test_recipe_preview_refuses_inconsistent_or_final_test_sample(
    database, compilation, compiler_worker, monkeypatch, change
):
    from copy import deepcopy

    from labelloop_api import management_recipe_reads

    service, _, _ = database
    _, asset, release, _, lease, _ = prepared_compilation(database, compilation, compiler_worker)
    compiler_worker.store.complete(lease, ("compilation",), compiler_worker.publish)
    original = management_recipe_reads.verified_variant

    def changed(*args):
        manifest, digest, job_id = original(*args)
        manifest = deepcopy(manifest)
        sample = manifest["samples"][0]
        if change == "transform":
            sample["transform"]["left"] += 1
        elif change == "source_hash":
            sample["source_sha256"] = "0" * 64
        elif change == "tier":
            sample["review_tier"] = "adjudicated"
        elif change == "objects":
            sample["objects"] = [{}]
        elif change == "test_split":
            sample["split"] = "test"
        else:
            sample["image_size"] = [640, 640]
        return manifest, digest, job_id

    # Synthetic projection-boundary corruption; native receipt/application checks run first.
    monkeypatch.setattr(management_recipe_reads, "verified_variant", changed)
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_recipe_preview(
            Reader(), release.id, "full-letterbox-320-v1", asset.id
        )


def test_compiler_rejects_source_bytes_that_differ_from_frozen_hash(
    database, compilation, compiler_worker
):
    from labelloop_compiler.compiler import CompilerFailure
    from labelloop_worker_core.jobs import StageContext

    _, owner, _ = database
    _, _, _, accepted, lease, _ = prepared_compilation(database, compilation, compiler_worker)
    compiler_worker.objects.read = lambda key: b"not-the-source"
    with pytest.raises(CompilerFailure, match="source bytes"):
        compiler_worker.compile(StageContext(compiler_worker.store, lease, {}))
    assert publication_state(owner, accepted)[:3] == (None, 0, [])


def test_compiler_refuses_unattributed_queue_job_for_an_owned_release(
    database, compilation, compiler_worker
):
    from labelloop_worker_core.jobs import StageContext

    _, _, _, _, lease, _ = prepared_compilation(database, compilation, compiler_worker)
    other = compiler_worker.store.enqueue(
        lease.project_key, "unattributed-" + uuid4().hex, "compile_variant", lease.input
    )
    forged = compiler_worker.store.claim("compile_variant", "fixture-compiler", job_id=other)
    with pytest.raises(ValueError, match="authorized receipt"):
        compiler_worker.compile(StageContext(compiler_worker.store, forged, {}))


def compile_request(
    compilation,
    release_id,
    *,
    key="compile",
    actor=None,
    revision=1,
    recipe="full-letterbox-320-v1",
):
    from labelloop_api.management_contracts import VariantCompile

    actor = actor or Actor()
    payload = VariantCompile(expected_revision=revision, recipe_id=recipe)
    context = MutationContext.bind(actor, key, "compile_variant", release_id, payload)
    return compilation.compile_variant(actor, context, payload)


def compile_receipts(owner, release_id):
    with owner.connect() as connection:
        return connection.execute(
            select(canonical_event).where(
                canonical_event.c.event_type == "management.compile_variant",
                canonical_event.c.aggregate_id == release_id,
            )
        ).all()


@pytest.mark.parametrize("recipe", ["full-letterbox-320-v1", "full-letterbox-640-v1"])
def test_compile_intent_uses_actual_caller_transaction_and_shared_queue(
    database, compilation, recipe
):
    from labelloop_worker_core.jobs import JobStore

    service, owner, role = database
    created, _, _, payload = release_inputs(service, owner)
    release = freeze(service, created.id, payload)

    def enqueue(connection, *args, **kwargs):
        assert connection.execute("SELECT current_user").fetchone()[0] == role
        job_id = JobStore.enqueue_in_transaction(connection, *args, **kwargs)
        # A separate connection sees neither intent nor receipt before SQLAlchemy commits.
        with owner.connect() as observer:
            assert (
                observer.execute(
                    text("SELECT job_id FROM worker_job WHERE job_id=:id"), {"id": job_id}
                ).first()
                is None
            )
        assert compile_receipts(owner, release.id) == []
        return job_id

    compilation = replace(compilation, enqueue=enqueue)
    result = compile_request(compilation, release.id, recipe=recipe)
    assert result.resource.kind == "variant" and result.resource.state == "queued"
    assert result.resource.project_id == created.id
    receipts = compile_receipts(owner, release.id)
    assert len(receipts) == 1
    assert receipts[0].actor_id == Actor().id and receipts[0].actor_kind == "human"
    evidence = receipts[0].payload["evidence"]
    assert evidence["job_id"] == result.job_id and evidence["recipe_id"] == recipe
    with owner.connect() as connection:
        row = connection.execute(
            text("SELECT * FROM worker_job WHERE job_id=:id"), {"id": result.job_id}
        ).one()
        assert row.status == "queued" and row.kind == "compile_variant" and row.max_attempts == 3
        assert row.input == evidence["inputs"]
        assert row.input["workspace_id"] == str(service.workspace_id)
        assert row.input["requested_by"] == Actor().id
        assert row.input["release_id"] == release.id
        assert row.input["variant_id"] == result.resource.id
        assert row.input["recipe"]["recipe_id"] == recipe
        assert row.input["recipe"]["output_size"] == (320 if "320" in recipe else 640)
        assert row.input["canonical_snapshot_sha256"]
    # Claim proves compatibility with the real shared queue, not execution of the compiler.
    import psycopg

    queue = JobStore(lambda: psycopg.connect(os.environ["DATABASE_URL"].replace("+psycopg", "")))
    lease = queue.claim("compile_variant", "fixture-worker", job_id=result.job_id)
    assert lease is not None and lease.input == evidence["inputs"]
    assert compile_request(compilation, release.id, recipe=recipe) == result
    assert len(compile_receipts(owner, release.id)) == 1
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    assert reads.read(Actor(), "project", created.id).revision == 3


@pytest.mark.parametrize(
    "grant", ["INSERT ON worker_job", "SELECT ON worker_job", "INSERT ON canonical_event"]
)
def test_compile_intent_grant_failure_rolls_back_queue_and_receipt(database, compilation, grant):
    from psycopg.errors import InsufficientPrivilege

    service, owner, role = database
    created, _, _, payload = release_inputs(service, owner)
    release = freeze(service, created.id, payload)
    with owner.begin() as connection:
        connection.execute(text(f'REVOKE {grant} FROM "{role}"'))
    with pytest.raises((DBAPIError, InsufficientPrivilege)):
        compile_request(compilation, release.id)
    assert compile_receipts(owner, release.id) == []
    with owner.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM worker_job WHERE project_key=:key"),
                {"key": pack_key(owner, created.id)},
            ).scalar_one()
            == 0
        )
    with owner.begin() as connection:
        connection.execute(text(f'GRANT {grant} TO "{role}"'))
    assert compile_request(compilation, release.id).resource.state == "queued"


def pack_key(owner, project_id):
    with owner.connect() as connection:
        return connection.execute(
            select(project.c.project_key).where(project.c.id == project_id)
        ).scalar_one()


def test_compile_intent_caller_exception_rolls_back_real_queue(database, compilation):
    service, owner, _ = database
    created, _, _, payload = release_inputs(service, owner)
    release = freeze(service, created.id, payload)

    def enqueue(*args, **kwargs):
        compilation.enqueue(*args, **kwargs)
        raise RuntimeError("synthetic caller failure")

    with pytest.raises(RuntimeError, match="synthetic caller failure"):
        compile_request(replace(compilation, enqueue=enqueue), release.id)
    assert compile_receipts(owner, release.id) == []
    with owner.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM worker_job WHERE project_key=:key"),
                {"key": pack_key(owner, created.id)},
            ).scalar_one()
            == 0
        )


@pytest.mark.parametrize("same_key", [True, False])
def test_compile_intent_concurrent_retry_and_unique_pair(database, compilation, same_key):
    service, owner, _ = database
    created, _, _, payload = release_inputs(service, owner)
    release = freeze(service, created.id, payload)
    barrier = Barrier(2)

    def submit(index):
        barrier.wait(timeout=10)
        try:
            return compile_request(compilation, release.id, key="same" if same_key else str(index))
        except ManagementFailure as exc:
            return exc.error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    if same_key:
        assert results[0] == results[1]
    else:
        assert results.count("revision_conflict") == 1
    assert len(compile_receipts(owner, release.id)) == 1
    with owner.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM worker_job WHERE project_key=:key"),
                {"key": pack_key(owner, created.id)},
            ).scalar_one()
            == 1
        )


@pytest.mark.parametrize(
    "actor",
    [
        Actor(kind="service", role="worker_core"),
        Actor(kind="service", role="worker_ml"),
        Actor(kind="service", role="worker_llm"),
        Actor(kind="service", role="worker_platform"),
        Actor(role="reviewer"),
    ],
)
def test_compile_intent_refuses_non_admin_before_queue(database, compilation, actor):
    service, owner, _ = database
    created, _, _, payload = release_inputs(service, owner)
    release = freeze(service, created.id, payload)
    with pytest.raises(ManagementFailure) as exc:
        compile_request(compilation, release.id, actor=actor)
    assert exc.value.error.detail == "permission_denied"
    assert compile_receipts(owner, release.id) == []


@pytest.mark.parametrize("target", ["foreign", "unknown"])
def test_compile_intent_authorizes_release_before_replay(database, compilation, target):
    service, owner, _ = database
    created, _, _, payload = release_inputs(service, owner)
    release = freeze(service, created.id, payload)
    compile_request(compilation, release.id)
    other = replace(compilation, mutations=replace(service, workspace_id=uuid4()))
    with pytest.raises(ManagementFailure) as exc:
        compile_request(
            other if target == "foreign" else compilation,
            release.id if target == "foreign" else str(uuid4()),
        )
    assert exc.value.error.detail == "not_found"
    assert len(compile_receipts(owner, release.id)) == 1


def test_compile_intent_revision_retry_collision_and_independent_recipes(database, compilation):
    service, owner, _ = database
    created, _, _, payload = release_inputs(service, owner)
    release = freeze(service, created.id, payload)
    with pytest.raises(ManagementFailure) as exc:
        compile_request(compilation, release.id, revision=2)
    assert exc.value.error.detail == "revision_conflict"
    first = compile_request(compilation, release.id)
    with pytest.raises(ManagementFailure) as exc:
        compile_request(compilation, release.id, recipe="full-letterbox-640-v1")
    assert exc.value.error.detail == "idempotency_conflict"
    second = compile_request(
        compilation, release.id, key="compile-640", recipe="full-letterbox-640-v1"
    )
    assert first.job_id != second.job_id and first.resource.id != second.resource.id
    with pytest.raises(ManagementFailure) as exc:
        compile_request(compilation, release.id, key="another-key")
    assert exc.value.error.detail == "revision_conflict"
    assert len(compile_receipts(owner, release.id)) == 2


@pytest.mark.parametrize("failure", ["missing_receipt", "forged_context"])
def test_compile_intent_requires_canonical_evidence_and_bound_context(
    database, compilation, failure
):
    from labelloop_api.management_contracts import VariantCompile

    service, owner, _ = database
    created, _, _, payload = release_inputs(service, owner)
    release = freeze(service, created.id, payload)
    request = VariantCompile(expected_revision=1, recipe_id="full-letterbox-320-v1")
    context = MutationContext.bind(Actor(), "compile", "compile_variant", release.id, request)
    if failure == "missing_receipt":
        with owner.begin() as connection:
            connection.execute(
                canonical_event.delete().where(
                    canonical_event.c.event_type == "management.freeze_release",
                    canonical_event.c.aggregate_id == created.id,
                )
            )
    else:
        context = replace(context, actor_id="forged")
    with pytest.raises(ManagementFailure) as exc:
        compilation.compile_variant(Actor(), context, request)
    assert exc.value.error.detail == (
        "missing_evidence" if failure == "missing_receipt" else "invalid_request"
    )
    assert compile_receipts(owner, release.id) == []


@pytest.mark.parametrize("role", ["admin", "reviewer"])
def test_compile_intent_binds_real_shared_session_and_csrf(
    database, compilation, restricted_auth_store, auth_account, role
):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from labelloop_api.auth_schema import principal
    from labelloop_api.main import create_app

    service, owner, _ = database
    identity, credentials = auth_account
    created, _, _, payload = release_inputs(service, owner)
    release = freeze(service, created.id, payload)
    with owner.begin() as connection:
        connection.execute(principal.update().where(principal.c.id == identity).values(role=role))
    origin = "https://testserver"
    operations = SimpleNamespace(compile_variant=compilation.compile_variant)
    path = f"/dataset-releases/{release.id}/variants"
    body = {"expected_revision": 1, "recipe_id": "full-letterbox-320-v1"}
    with TestClient(create_app(lambda: operations), base_url=origin) as client:
        assert client.post(path, json=body).status_code == 401
        login = client.post("/auth/login", json=credentials, headers={"origin": origin})
        assert login.status_code == 200, login.text
        assert (
            client.post(path, json=body, headers={"Idempotency-Key": "compile"}).status_code == 403
        )
        assert compile_receipts(owner, release.id) == []
        headers = {
            "origin": origin,
            "x-csrf-token": login.json()["csrf_token"],
            "Idempotency-Key": "compile",
        }
        response = client.post(path, json=body, headers=headers)
        if role == "reviewer":
            assert response.status_code == 403
            assert compile_receipts(owner, release.id) == []
        else:
            assert response.status_code == 202, response.text
            assert response.headers["cache-control"] == "no-store"
            replay = client.post(path, json=body, headers=headers)
            assert replay.status_code == 202 and replay.json() == response.json()
            receipts = compile_receipts(owner, release.id)
            assert len(receipts) == 1 and receipts[0].actor_id == identity
            assert receipts[0].payload["evidence"]["inputs"]["requested_by"] == identity
        assert client.post("/auth/logout", headers=headers).status_code == 204
        assert client.post(path, json=body, headers=headers).status_code == 401


@pytest.fixture
def restricted_auth_store(database, monkeypatch):
    from labelloop_api import auth
    from labelloop_api.auth_store import AuthStore

    _, owner, _ = database
    role = "auth_fixture_" + uuid4().hex
    with owner.begin() as connection:
        connection.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
        connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        connection.execute(text(f'GRANT SELECT ON auth_principal, auth_service_token TO "{role}"'))
        connection.execute(
            text(
                f"GRANT UPDATE (password_hash, failed_attempts, locked_until) "
                f'ON auth_principal TO "{role}"'
            )
        )
        connection.execute(text(f'GRANT SELECT, INSERT, DELETE ON auth_session TO "{role}"'))
        # Expired-session cleanup takes FOR UPDATE locks before deleting its bounded batch.
        connection.execute(text(f'GRANT UPDATE (expires_at) ON auth_session TO "{role}"'))
        connection.execute(text(f'GRANT INSERT ON auth_event TO "{role}"'))
    runtime = create_engine(
        os.environ["DATABASE_URL"],
        connect_args={
            "options": f"-c role={role} -c statement_timeout=15000 -c lock_timeout=10000"
        },
    )
    store = AuthStore(runtime)
    from datetime import UTC, datetime

    monkeypatch.setattr(auth, "auth_store", lambda: store)
    monkeypatch.setattr(auth, "clock", lambda: datetime(2026, 11, 8, tzinfo=UTC))
    monkeypatch.setenv("AUTH_ORIGIN", "https://testserver")
    try:
        yield store, role
    finally:
        runtime.dispose()
        with owner.begin() as connection:
            connection.execute(text(f'DROP OWNED BY "{role}"'))
            connection.execute(text(f'DROP ROLE "{role}"'))


def test_shared_session_canonical_project_create_read_and_replay(
    database, restricted_auth_store, monkeypatch
):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from labelloop_api import auth
    from labelloop_api.auth_schema import principal
    from labelloop_api.auth_store import PASSWORDS
    from labelloop_api.main import create_app

    service, owner, _ = database
    identity, name = str(uuid4()), "fixture-" + uuid4().hex
    password, origin = "fixture-password-only", "https://testserver"
    with owner.begin() as connection:
        connection.execute(
            insert(principal).values(
                id=identity,
                name=name,
                kind="human",
                role="admin",
                password_hash=PASSWORDS.hash(password),
                enabled=True,
                failed_attempts=0,
            )
        )
    store, auth_role = restricted_auth_store
    with store.engine.connect() as connection:
        assert connection.execute(text("SELECT current_user")).scalar_one() == auth_role
    with service.engine.connect() as connection:
        assert connection.execute(text("SELECT current_user")).scalar_one() != auth_role
    monkeypatch.setattr(auth, "clock", lambda: datetime(2026, 11, 8, tzinfo=UTC))
    monkeypatch.setenv("AUTH_ORIGIN", origin)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    # Only these real adapters are bound for this case, never an admission/queue port.
    operations = SimpleNamespace(
        create_project=service.create_project,
        list_projects=reads.list_projects,
    )
    payload = pack().model_dump(mode="json")
    with TestClient(create_app(lambda: operations), base_url=origin) as client:
        assert client.get("/projects").status_code == 401
        login = client.post(
            "/auth/login",
            json={"name": name, "password": password},
            headers={"origin": origin},
        )
        assert login.status_code == 200
        headers = {
            "origin": origin,
            "x-csrf-token": login.json()["csrf_token"],
            "Idempotency-Key": "session-create",
        }
        denied = client.post(
            "/projects", json=payload, headers={"Idempotency-Key": "session-create"}
        )
        assert denied.status_code == 403 and denied.json() == {"detail": "csrf_denied"}
        with owner.connect() as connection:
            assert (
                connection.execute(
                    select(project.c.id).where(project.c.project_key == payload["project_key"])
                ).first()
                is None
            )
        created = client.post("/projects", json=payload, headers=headers)
        assert created.status_code == 201, created.text
        replay = client.post("/projects", json=payload, headers=headers)
        assert replay.status_code == 201 and replay.json() == created.json()
        conflict = client.post(
            "/projects", json={**payload, "scope": "Changed scope"}, headers=headers
        )
        assert conflict.status_code == 409 and conflict.json() == {"detail": "idempotency_conflict"}
        page = client.get("/projects")
        assert page.status_code == 200 and page.json()["items"] == [created.json()]
        with owner.connect() as connection:
            receipts = (
                connection.execute(
                    select(canonical_event).where(
                        canonical_event.c.aggregate_id == created.json()["id"]
                    )
                )
                .mappings()
                .all()
            )
        assert len(receipts) == 1
        assert receipts[0]["actor_kind"] == "human" and receipts[0]["actor_id"] == identity
        assert counts(owner, created.json()["id"]) == (0, 0, 1)
        assert client.post("/auth/logout", headers=headers).status_code == 204
        assert client.get("/projects").status_code == 401


@pytest.fixture
def auth_account(database):
    from labelloop_api.auth_schema import principal
    from labelloop_api.auth_store import PASSWORDS

    _, owner, _ = database
    identity, name = str(uuid4()), "fixture-" + uuid4().hex
    password = "synthetic-password-only"
    with owner.begin() as connection:
        connection.execute(
            insert(principal).values(
                id=identity,
                name=name,
                kind="human",
                role="admin",
                password_hash=PASSWORDS.hash(password),
                enabled=True,
                failed_attempts=2,
            )
        )
    return identity, {"name": name, "password": password}


@pytest.fixture
def auth_management_client(database, restricted_auth_store):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from labelloop_api.main import create_app

    service, _, _ = database
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    operations = SimpleNamespace(
        create_project=service.create_project,
        list_projects=reads.list_projects,
        read_reference_lifecycle=reads.read_reference_lifecycle,
    )
    with TestClient(create_app(lambda: operations), base_url="https://testserver") as client:
        yield client


@pytest.mark.parametrize("role", ["admin", "reviewer"])
def test_reference_lifecycle_composes_with_restricted_shared_sessions(
    database, auth_account, auth_management_client, role
):
    from labelloop_api.auth_schema import principal

    service, owner, _ = database
    identity, credentials = auth_account
    created, _, _ = revalidation_inputs(service, owner)
    path = f"/projects/{created.id}/references/lifecycle"
    assert auth_management_client.get(path).status_code == 401
    with owner.begin() as connection:
        connection.execute(principal.update().where(principal.c.id == identity).values(role=role))
    login = auth_management_client.post(
        "/auth/login", json=credentials, headers={"origin": "https://testserver"}
    )
    assert login.status_code == 200, login.text
    before = counts(owner, created.id)
    response = auth_management_client.get(path)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["project_revision"] == 5
    assert response.json()["references"][0]["last_approval_revision"] == 4
    assert response.json()["references"][0]["eligibility"] == "not_assessed"
    assert (
        auth_management_client.get(f"/projects/{uuid4()}/references/lifecycle").status_code == 404
    )
    logout = auth_management_client.post(
        "/auth/logout",
        headers={"origin": "https://testserver", "x-csrf-token": login.json()["csrf_token"]},
    )
    assert logout.status_code == 204
    assert auth_management_client.get(path).status_code == 401
    assert counts(owner, created.id) == before


def auth_snapshot(owner, identity):
    from labelloop_api.auth_schema import auth_event, principal, session

    with owner.connect() as connection:
        return (
            connection.execute(select(principal).where(principal.c.id == identity)).all(),
            connection.execute(
                select(session)
                .where(session.c.principal_id == identity)
                .order_by(session.c.token_hash)
            ).all(),
            connection.execute(
                select(auth_event)
                .where(auth_event.c.principal_id == identity)
                .order_by(auth_event.c.id)
            ).all(),
        )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE auth_principal SET role = 'admin'",
        "UPDATE auth_principal SET enabled = true",
        "UPDATE auth_principal SET kind = 'human'",
        "DELETE FROM auth_principal",
        "INSERT INTO auth_principal DEFAULT VALUES",
        "INSERT INTO auth_service_token DEFAULT VALUES",
        "UPDATE auth_service_token SET expires_at = now()",
        "DELETE FROM auth_service_token",
        "UPDATE auth_session SET principal_id = 'foreign'",
        "UPDATE auth_session SET csrf_hash = 'forged'",
        "UPDATE auth_event SET action = 'logout'",
        "DELETE FROM auth_event",
        "TRUNCATE auth_event",
        "SELECT * FROM canonical_event",
        "INSERT INTO canonical_event DEFAULT VALUES",
    ],
)
def test_auth_runtime_role_cannot_provision_or_rewrite_authority(restricted_auth_store, statement):
    store, role = restricted_auth_store
    with store.engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
        ).one() == (role, False, False, False, False)
    with pytest.raises(DBAPIError) as error, store.engine.begin() as connection:
        connection.execute(text(statement))
    assert error.value.orig.sqlstate == "42501"


@pytest.mark.parametrize(
    "privilege, table",
    [
        ("INSERT", "auth_event"),
        ("INSERT", "auth_session"),
        ("DELETE", "auth_session"),
        ("UPDATE (failed_attempts)", "auth_principal"),
        ("UPDATE (expires_at)", "auth_session"),
    ],
)
def test_auth_runtime_login_grant_failure_rolls_back(
    database, restricted_auth_store, auth_account, auth_management_client, privilege, table
):
    from datetime import timedelta

    from labelloop_api import auth
    from labelloop_api.auth_schema import session
    from labelloop_api.auth_store import digest

    _, owner, _ = database
    _, role = restricted_auth_store
    identity, credentials = auth_account
    with owner.begin() as connection:
        connection.execute(
            insert(session).values(
                token_hash=digest(uuid4().hex),
                principal_id=identity,
                csrf_hash=digest(uuid4().hex),
                expires_at=auth.clock() - timedelta(seconds=1),
            )
        )
        connection.execute(text(f'REVOKE {privilege} ON {table} FROM "{role}"'))
    before = auth_snapshot(owner, identity)
    response = auth_management_client.post(
        "/auth/login", json=credentials, headers={"origin": "https://testserver"}
    )
    assert response.status_code == 503 and response.json() == {
        "detail": "authentication_unavailable"
    }
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert auth_snapshot(owner, identity) == before
    assert auth_management_client.get("/projects").status_code == 401


@pytest.mark.parametrize("privilege, table", [("INSERT", "auth_event"), ("DELETE", "auth_session")])
def test_auth_runtime_logout_grant_failure_preserves_session(
    database, restricted_auth_store, auth_account, auth_management_client, privilege, table
):
    _, owner, _ = database
    _, role = restricted_auth_store
    identity, credentials = auth_account
    login = auth_management_client.post(
        "/auth/login", json=credentials, headers={"origin": "https://testserver"}
    )
    assert login.status_code == 200, login.text
    before = auth_snapshot(owner, identity)
    with owner.begin() as connection:
        connection.execute(text(f'REVOKE {privilege} ON {table} FROM "{role}"'))
    response = auth_management_client.post(
        "/auth/logout",
        headers={"origin": "https://testserver", "x-csrf-token": login.json()["csrf_token"]},
    )
    assert response.status_code == 503 and response.json() == {
        "detail": "authentication_unavailable"
    }
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert auth_snapshot(owner, identity) == before
    assert auth_management_client.get("/auth/me").json()["id"] == identity


def test_auth_runtime_csrf_audit_failure_cannot_reach_canonical_mutation(
    database, restricted_auth_store, auth_account, auth_management_client
):
    _, owner, _ = database
    _, role = restricted_auth_store
    identity, credentials = auth_account
    login = auth_management_client.post(
        "/auth/login", json=credentials, headers={"origin": "https://testserver"}
    )
    assert login.status_code == 200, login.text
    before = auth_snapshot(owner, identity)
    with owner.begin() as connection:
        connection.execute(text(f'REVOKE INSERT ON auth_event FROM "{role}"'))
    payload = pack().model_dump(mode="json")
    response = auth_management_client.post(
        "/projects", json=payload, headers={"Idempotency-Key": "audit-denied"}
    )
    assert response.status_code == 503 and response.json() == {
        "detail": "authentication_unavailable"
    }
    assert auth_snapshot(owner, identity) == before
    with owner.connect() as connection:
        assert (
            connection.execute(
                select(project.c.id).where(project.c.project_key == payload["project_key"])
            ).first()
            is None
        )


def pack():
    return ProjectCreate(
        project_key="fixture-" + uuid4().hex,
        scope="Visible packages",
        domain="industrial",
        annotation_guide="Visible extents only",
        source_permitted_use="Generated fixture",
        classes=[{"stable_id": "package", "name": "Package", "definition": "A visible package"}],
    )


def create(service, payload=None, actor=None, key="create"):
    actor = actor or Actor()
    payload = payload or pack()
    context = MutationContext.bind(actor, key, "create_project", "projects", payload)
    return service.create_project(actor, context, payload)


def image_payload(revision=1):
    output = io.BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (12, 24), "white").save(output, format="JPEG", exif=exif)
    return AssetIngest(
        expected_revision=revision,
        original_base64=base64.b64encode(output.getvalue()).decode(),
        source_uri="fixture:generated",
        source_group="capture-a",
    )


def ingest(service, project_id, payload=None, actor=None, key="ingest"):
    actor = actor or Actor()
    payload = payload or image_payload()
    context = MutationContext.bind(actor, key, "ingest_asset", project_id, payload)
    return service.ingest_asset(actor, context, payload)


def counts(owner, project_id):
    with owner.connect() as connection:
        assets = (
            connection.execute(
                select(source_asset.c.id).where(source_asset.c.project_id == project_id)
            )
            .scalars()
            .all()
        )
        return (
            len(assets),
            connection.execute(
                select(func.count())
                .select_from(annotation_revision)
                .where(annotation_revision.c.asset_id.in_(assets))
            ).scalar_one(),
            connection.execute(
                select(func.count())
                .select_from(canonical_event)
                .where(canonical_event.c.aggregate_id == project_id)
            ).scalar_one(),
        )


@dataclass(frozen=True)
class Reader:
    id: str = "fixture-reviewer"
    kind: str = "human"
    role: str = "reviewer"
    granted: bool = True

    def permits(self, permission):
        return self.granted and permission == "review"


def test_canonical_reads_project_asset_release_and_current_revisions(database):
    service, owner, role = database
    created, asset, _, payload = release_inputs(service, owner)
    released = freeze(service, created.id, payload)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    before = counts(owner, created.id)
    # Reads need no runtime INSERT or row-lock privilege.
    with owner.begin() as connection:
        connection.execute(
            text(f'REVOKE INSERT, UPDATE ON ALL TABLES IN SCHEMA public FROM "{role}"')
        )
    for actor in (Actor(), Reader()):
        assert reads.read(actor, "project", created.id).revision == 3
        summary = reads.read(actor, "asset", asset.id)
        assert summary.revision == 2
        assert summary.state == "draft"  # review approval is not a published dataset
        assert set(summary.model_dump()) == {"id", "project_id", "kind", "revision", "state"}
        assert reads.read(actor, "dataset-release", released.id) == released
        page = reads.list_resources(actor, created.id, "dataset-release", None, 100)
        assert page.items == [released]
        assert page.next_cursor is None
    assert counts(owner, created.id) == before


def test_canonical_read_pages_are_bounded_and_scoped_keysets(database):
    service, owner, _ = database
    created = [create(service, key=f"create-{index}") for index in range(3)]
    target = created[0].id
    assets = [
        ingest(service, target, image_payload(index + 1), key=f"asset-{index}")
        for index in range(3)
    ]
    other_asset = ingest(service, created[1].id, key="other-project-asset")
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    actor = Reader()
    first = reads.list_projects(actor, None, 2)
    second = reads.list_projects(actor, first.next_cursor, 2)
    assert first.next_cursor is not None and len(first.next_cursor) <= 128
    assert second.next_cursor is None
    assert [item.id for item in first.items + second.items] == sorted(item.id for item in created)
    assert len(first.items) == 2
    one = reads.list_resources(actor, target, "asset", None, 1)
    two = reads.list_resources(actor, target, "asset", one.next_cursor, 1)
    three = reads.list_resources(actor, target, "asset", two.next_cursor, 1)
    # The public asset key and internal UUID are independently generated.
    # Keyset order is the internal UUID, not lexical order of the public key.
    with owner.connect() as connection:
        ordered = connection.execute(
            select(source_asset.c.id, source_asset.c.asset_key)
            .where(source_asset.c.project_id == target)
            .order_by(source_asset.c.id)
        ).all()
    expected = [row.asset_key for row in ordered]
    assert set(expected) == {item.id for item in assets}
    assert other_asset.id not in expected
    assert [item.id for item in one.items + two.items + three.items] == expected
    assert one.next_cursor.rsplit(".", 1)[1] == ordered[0].id.hex
    assert two.next_cursor.rsplit(".", 1)[1] == ordered[1].id.hex
    assert three.next_cursor is None
    for limit in (2, 3, 100):
        page = reads.list_resources(actor, target, "asset", None, limit)
        found = list(page.items)
        if page.next_cursor is not None:
            page = reads.list_resources(actor, target, "asset", page.next_cursor, limit)
            found.extend(page.items)
        assert page.next_cursor is None
        assert [item.id for item in found] == expected
    for call in (
        lambda: reads.list_resources(actor, target, "asset", first.next_cursor, 1),
        lambda: reads.list_resources(actor, created[1].id, "asset", one.next_cursor, 1),
        lambda: reads.list_resources(actor, target, "dataset-release", one.next_cursor, 1),
        lambda: replace(reads, workspace_id=uuid4()).list_projects(actor, first.next_cursor, 1),
    ):
        with pytest.raises(ManagementFailure, match="invalid_request"):
            call()


@pytest.mark.parametrize("limit", [1, 2, 3, 100])
def test_canonical_release_pages_keep_frozen_revisions_after_later_review(database, limit):
    service, owner, _ = database
    created, asset, _, payload = release_inputs(service, owner)
    releases = [
        freeze(
            service,
            created.id,
            payload.model_copy(update={"expected_revision": 2 + index}),
            key=f"freeze-{index}",
        )
        for index in range(3)
    ]
    with owner.begin() as connection:
        append_review(
            connection,
            asset_id=asset.id,
            actor_kind="human",
            actor_id=Actor().id,
            expected_revision=2,
            approval_state="human_accepted",
            review_tier="human_reviewed",
            scope_completeness="exhaustive_for_scope",
            objects=[],
            training_eligible=True,
        )
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    before = counts(owner, created.id)
    cursor = None
    found = []
    for _ in range(3):
        page = reads.list_resources(Reader(), created.id, "dataset-release", cursor, limit)
        assert 1 <= len(page.items) <= limit
        found.extend(page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert cursor is None
    assert found == sorted(releases, key=lambda item: item.id)
    assert reads.read(Reader(), "asset", asset.id).revision == 3
    assert all(item.revision == 1 and item.state == "frozen" for item in found)
    assert counts(owner, created.id) == before


@pytest.mark.parametrize("cursor", ["bad", "a" * 129, "a" * 32 + "." + "z" * 32])
def test_canonical_read_refuses_invalid_cursors(database, cursor):
    service, _, _ = database
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    with pytest.raises(ManagementFailure, match="invalid_request"):
        reads.list_projects(Reader(), cursor, 1)


@pytest.mark.parametrize("limit", [0, 101, True, 1.0])
def test_canonical_read_refuses_unbounded_or_coerced_limits(database, limit):
    service, _, _ = database
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    with pytest.raises(ManagementFailure, match="invalid_request"):
        reads.list_projects(Reader(), None, limit)


@pytest.mark.parametrize(
    "actor",
    [
        Reader(kind="service", role="admin"),
        Reader(kind="model"),
        Reader(id=""),
        Reader(role="other"),
        Reader(granted=False),
    ],
)
def test_canonical_read_denies_non_human_or_ungranted_actors(database, actor):
    service, _, _ = database
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    with pytest.raises(ManagementFailure, match="permission_denied"):
        reads.list_projects(actor, None, 10)
    with pytest.raises(ManagementFailure, match="permission_denied"):
        reads.read(actor, "project", "unknown")
    with pytest.raises(ManagementFailure, match="permission_denied"):
        reads.list_resources(actor, "unknown", "asset", None, 10)


def test_canonical_read_hides_foreign_legacy_and_unknown_resources(database):
    service, owner, _ = database
    created, asset, _, payload = release_inputs(service, owner)
    released = freeze(service, created.id, payload)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    foreign = replace(reads, workspace_id=uuid4())
    for kind, identity in (
        ("project", created.id),
        ("asset", asset.id),
        ("dataset-release", released.id),
    ):
        with pytest.raises(ManagementFailure, match="not_found"):
            foreign.read(Reader(), kind, identity)
        with pytest.raises(ManagementFailure, match="not_found"):
            reads.read(Reader(), kind, "unknown")
    assert foreign.list_projects(Reader(), None, 100).items == []
    with pytest.raises(ManagementFailure, match="not_found"):
        foreign.list_resources(Reader(), created.id, "model", "invalid", 10)
    with owner.begin() as connection:
        connection.execute(text(f'GRANT SELECT ON model_release TO "{database[2]}"'))
    assert reads.list_resources(Reader(), created.id, "model", None, 10).items == []
    legacy_id = uuid4()
    with owner.begin() as connection:
        connection.execute(
            insert(project).values(
                id=legacy_id,
                project_key="legacy-" + uuid4().hex,
                workspace_id=service.workspace_id,
                task="detect",
                scope="legacy",
                domain="industrial",
            )
        )
    with pytest.raises(ManagementFailure, match="not_found"):
        reads.read(Reader(), "project", str(legacy_id))
    assert [item.id for item in reads.list_projects(Reader(), None, 100).items] == [created.id]


def scope_payload(revision=1):
    return ScopeDraft(
        expected_revision=revision,
        name="Package inspection",
        scope="Visible packages on the production line",
        domain="industrial",
        representation="scene_object",
        intended_use="Fixture detection",
        specificity="Package category",
        source_permitted_use="Generated fixture only",
    )


def save_scope(service, project_id, payload=None, actor=None, key="scope"):
    actor = actor or Actor()
    payload = payload or scope_payload()
    context = MutationContext.bind(actor, key, "save_scope_draft", project_id, payload)
    return service.save_scope_draft(actor, context, payload)


def approve_step(service, project_id, payload, actor=None, key="approve"):
    actor = actor or Actor()
    context = MutationContext.bind(actor, key, "approve_setup_step", project_id, payload)
    return service.approve_setup_step(actor, context, payload)


def step_approval(service, project_id, step="scope"):
    detail = CanonicalManagementReads(service.engine, service.workspace_id).read_project(
        Reader(), project_id
    )
    return SetupStepApprove(
        expected_revision=detail.resource.revision,
        step=step,
        draft_sha256=detail.setup_draft_sha256[step],
        reason="Reviewed fixture intent",
    )


@pytest.mark.parametrize("step", ["scope", "guide"])
def test_setup_approval_is_attributed_pinned_idempotent_draft_intent(database, step):
    service, owner, _ = database
    created = create(service)
    (save_scope if step == "scope" else save_guide)(service, created.id)
    payload = step_approval(service, created.id, step)
    result = approve_step(service, created.id, payload)
    assert result.revision == 3 and result.state == "draft"
    assert approve_step(service, created.id, payload) == result
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    detail = reads.read_project(Reader(), created.id)
    assert detail.approved_steps == [step] and detail.published_release_id is None
    assert detail.classes[0].approval is None
    assert detail.setup_draft_sha256[step] == payload.draft_sha256
    with pytest.raises(ManagementFailure, match="idempotency_conflict"):
        approve_step(service, created.id, payload.model_copy(update={"reason": "Changed"}))
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        approve_step(service, created.id, payload, key="stale")
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        approve_step(service, created.id, step_approval(service, created.id, step), key="again")
    with owner.connect() as connection:
        receipt = connection.execute(
            select(canonical_event).where(
                canonical_event.c.aggregate_id == created.id,
                canonical_event.c.event_type == "management.approve_setup_step",
            )
        ).one()
        assert receipt.actor_id == Actor().id and receipt.actor_kind == "human"
        assert receipt.payload["evidence"] == payload.model_dump(exclude={"expected_revision"})
    assert counts(owner, created.id) == (0, 0, 3)


def test_setup_approvals_compose_without_publishing_or_changing_draft_pins(database):
    service, _, _ = database
    created = create(service)
    save_scope(service, created.id)
    save_guide(service, created.id, guide_payload(2))
    scope = step_approval(service, created.id)
    approve_step(service, created.id, scope)
    guide = step_approval(service, created.id, "guide")
    approve_step(service, created.id, guide, key="approve-guide")
    detail = CanonicalManagementReads(service.engine, service.workspace_id).read_project(
        Reader(), created.id
    )
    assert detail.approved_steps == ["guide", "scope"]
    assert detail.resource.revision == 5 and detail.published_release_id is None


@pytest.mark.parametrize("same_key", [True, False])
def test_parallel_setup_approvals_share_project_revision_and_retry_locks(database, same_key):
    service, owner, _ = database
    created = create(service)
    save_scope(service, created.id)
    payload = step_approval(service, created.id)
    barrier = Barrier(2, timeout=10)

    def submit(key):
        barrier.wait()
        try:
            return approve_step(
                service, created.id, payload, key="same" if same_key else key
            ).revision
        except ManagementFailure as error:
            return error.error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ("left", "right")))
    assert outcomes.count(3) == (2 if same_key else 1)
    assert outcomes.count("revision_conflict") == (0 if same_key else 1)
    assert counts(owner, created.id) == (0, 0, 3)


@pytest.mark.parametrize("step", ["scope", "guide"])
def test_setup_approval_requires_saved_draft_and_exact_digest(database, step):
    service, owner, _ = database
    created = create(service)
    payload = SetupStepApprove(
        expected_revision=1, step=step, draft_sha256="a" * 64, reason="Fixture"
    )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        approve_step(service, created.id, payload)
    (save_scope if step == "scope" else save_guide)(service, created.id)
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        approve_step(service, created.id, payload.model_copy(update={"expected_revision": 2}))
    assert counts(owner, created.id) == (0, 0, 2)


@pytest.mark.parametrize("actor", [Actor(kind="service"), Actor(role="reviewer"), Actor(id="")])
def test_setup_approval_refuses_nonadmin_principals(database, actor):
    service, owner, _ = database
    created = create(service)
    save_scope(service, created.id)
    with pytest.raises(ManagementFailure, match="permission_denied"):
        approve_step(service, created.id, step_approval(service, created.id), actor=actor)
    assert counts(owner, created.id) == (0, 0, 2)


def test_setup_approval_replay_checks_workspace_target_and_actor_binding(database):
    service, owner, _ = database
    created = create(service)
    save_scope(service, created.id)
    payload = step_approval(service, created.id)
    approve_step(service, created.id, payload)
    with pytest.raises(ManagementFailure, match="not_found"):
        approve_step(replace(service, workspace_id=uuid4()), created.id, payload)
    with pytest.raises(ManagementFailure, match="not_found"):
        approve_step(service, "not-a-uuid", payload)
    context = MutationContext.bind(Actor(), "approve", "approve_setup_step", created.id, payload)
    with pytest.raises(ManagementFailure, match="invalid_request"):
        service.approve_setup_step(Actor(), replace(context, actor_id="forged"), payload)
    assert counts(owner, created.id) == (0, 0, 3)


@pytest.mark.parametrize("table", ["canonical_event", "project"])
def test_setup_approval_is_atomic_when_runtime_grant_is_missing(database, table):
    service, owner, role = database
    created = create(service)
    save_scope(service, created.id)
    payload = step_approval(service, created.id)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    before = reads.read_project(Reader(), created.id)
    privilege = "INSERT" if table == "canonical_event" else "UPDATE (scope, domain, access_policy)"
    with owner.begin() as connection:
        connection.execute(text(f'REVOKE {privilege} ON {table} FROM "{role}"'))
    with pytest.raises(DBAPIError):
        approve_step(service, created.id, payload)
    assert reads.read_project(Reader(), created.id) == before
    assert counts(owner, created.id) == (0, 0, 2)
    with owner.begin() as connection:
        connection.execute(text(f'GRANT {privilege} ON {table} TO "{role}"'))
    assert approve_step(service, created.id, payload).revision == 3


@pytest.mark.parametrize("step", ["scope", "guide"])
def test_setup_edits_clear_approvals_even_when_draft_bytes_are_unchanged(database, step):
    service, owner, _ = database
    created = create(service)
    saver = save_scope if step == "scope" else save_guide
    factory = scope_payload if step == "scope" else guide_payload
    saver(service, created.id)
    approve_step(service, created.id, step_approval(service, created.id, step))
    with owner.connect() as connection:
        prior = connection.execute(
            select(project.c.access_policy).where(project.c.id == created.id)
        ).scalar_one()["management_setup_approvals"]
    saver(service, created.id, factory(3), key="edit")
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    assert reads.read_project(Reader(), created.id).approved_steps == []
    # Restoring old policy cannot resurrect an approval invalidated by a save.
    with owner.begin() as connection:
        row = connection.execute(select(project).where(project.c.id == created.id)).one()
        policy = {**row.access_policy, "management_setup_approvals": prior}
        connection.execute(
            project.update().where(project.c.id == row.id).values(access_policy=policy)
        )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        reads.read_project(Reader(), created.id)


@pytest.mark.parametrize("tamper", ["receipt", "digest", "step", "draft"])
def test_setup_approval_projection_requires_current_matching_receipt(database, tamper):
    service, owner, _ = database
    created = create(service)
    save_scope(service, created.id)
    approve_step(service, created.id, step_approval(service, created.id))
    with owner.begin() as connection:
        row = connection.execute(select(project).where(project.c.id == created.id)).one()
        policy = dict(row.access_policy)
        if tamper == "receipt":
            policy["management_setup_approvals"]["scope"]["receipt_revision"] = 100
        elif tamper == "digest":
            policy["management_setup_approvals"]["scope"]["draft_sha256"] = "a" * 64
        elif tamper == "step":
            policy["management_setup_approvals"]["publish"] = {}
        else:
            policy["management_scope_draft"]["name"] = "Unattributed"
        connection.execute(
            project.update().where(project.c.id == row.id).values(access_policy=policy)
        )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_project(
            Reader(), created.id
        )


def test_scope_save_reconciles_replay_and_projects_only_attributed_draft(database):
    service, owner, _ = database
    created = create(service)
    payload = scope_payload()
    result = save_scope(service, created.id, payload)
    assert result.revision == 2 and result.state == "draft"
    assert save_scope(service, created.id, payload) == result
    with pytest.raises(ManagementFailure, match="idempotency_conflict"):
        save_scope(service, created.id, payload.model_copy(update={"name": "Changed"}))
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        save_scope(service, created.id, key="stale")
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    detail = reads.read_project(Reader(), created.id)
    assert detail.resource == result
    assert detail.name == payload.name
    assert detail.scope == payload.scope
    assert detail.representation == payload.representation
    assert detail.intended_use == payload.intended_use
    assert detail.specificity == payload.specificity
    assert detail.source_permitted_use == payload.source_permitted_use
    assert detail.approved_steps == []
    assert detail.published_release_id is None
    assert detail.classes[0].approval is None
    assert detail.guide is None and detail.references is None
    with owner.connect() as connection:
        receipt = connection.execute(
            select(canonical_event).where(
                canonical_event.c.aggregate_id == created.id,
                canonical_event.c.event_type == "management.save_scope_draft",
            )
        ).one()
        assert receipt.actor_kind == "human" and receipt.actor_id == Actor().id
        assert receipt.expected_revision == 1
        assert receipt.payload["evidence"]["scope_draft"] == payload.model_dump(
            exclude={"expected_revision"}
        )
        assert receipt.payload["evidence"]["invalidated_steps"] == [
            "scope",
            "classes",
            "evidence",
            "guide",
            "sources",
            "publish",
        ]
    assert counts(owner, created.id) == (0, 0, 2)
    next_payload = payload.model_copy(update={"expected_revision": 2, "name": "Next name"})
    assert save_scope(service, created.id, next_payload, key="next").revision == 3
    assert save_scope(service, created.id, payload) == result
    assert reads.read_project(Reader(), created.id).name == "Next name"


@pytest.mark.parametrize("same_key", [True, False])
def test_parallel_scope_saves_share_canonical_revision_and_retry_locks(database, same_key):
    service, owner, _ = database
    created = create(service)
    barrier = Barrier(2, timeout=10)

    def submit(key):
        barrier.wait()
        try:
            return save_scope(service, created.id, key="same" if same_key else key).revision
        except ManagementFailure as error:
            return error.error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ("left", "right")))
    assert outcomes.count(2) == (2 if same_key else 1)
    assert outcomes.count("revision_conflict") == (0 if same_key else 1)
    assert counts(owner, created.id) == (0, 0, 2)


def test_scope_save_audit_failure_rolls_back_policy_and_allows_same_key_retry(database):
    service, owner, role = database
    created = create(service)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    before = reads.read_project(Reader(), created.id)
    with owner.begin() as connection:
        connection.execute(text(f'REVOKE INSERT ON canonical_event FROM "{role}"'))
    with pytest.raises(DBAPIError):
        save_scope(service, created.id)
    assert reads.read_project(Reader(), created.id) == before
    assert counts(owner, created.id) == (0, 0, 1)
    with owner.begin() as connection:
        connection.execute(text(f'GRANT INSERT ON canonical_event TO "{role}"'))
    assert save_scope(service, created.id).revision == 2


@pytest.mark.parametrize("actor", [Actor(kind="service"), Actor(role="reviewer"), Actor(id="")])
def test_scope_save_denies_nonadmin_principals(database, actor):
    service, owner, _ = database
    created = create(service)
    with pytest.raises(ManagementFailure, match="permission_denied"):
        save_scope(service, created.id, actor=actor)
    assert counts(owner, created.id) == (0, 0, 1)


def test_scope_save_checks_workspace_and_context_even_for_replays(database):
    service, owner, _ = database
    created = create(service)
    save_scope(service, created.id)
    with pytest.raises(ManagementFailure, match="not_found"):
        save_scope(replace(service, workspace_id=uuid4()), created.id)
    with pytest.raises(ManagementFailure, match="not_found"):
        save_scope(service, "not-a-uuid")
    payload = scope_payload()
    context = MutationContext.bind(Actor(), "scope", "save_scope_draft", created.id, payload)
    with pytest.raises(ManagementFailure, match="invalid_request"):
        service.save_scope_draft(Actor(), replace(context, actor_id="forged"), payload)
    assert counts(owner, created.id) == (0, 0, 2)


@pytest.mark.parametrize("same_scope", [True, False])
def test_scope_save_invalidates_qualification_and_refuses_new_freeze(database, same_scope):
    service, owner, _ = database
    created, _, _, payload = release_inputs(service, owner)
    frozen = freeze(service, created.id, payload)
    with owner.begin() as connection:
        row = connection.execute(select(project).where(project.c.id == created.id)).one()
        policy = {**row.access_policy, "auto_accept_qualification": {"qualified": True}}
        policy["management_setup_approvals"] = {"scope": "previous-approval"}
        connection.execute(
            project.update().where(project.c.id == row.id).values(access_policy=policy)
        )
    # Release freeze consumed revision 3; the edit invalidates any old approvals.
    draft = scope_payload(3)
    if same_scope:
        draft = draft.model_copy(update={"scope": row.scope})
    save_scope(service, created.id, draft)
    with owner.connect() as connection:
        policy = connection.execute(
            select(project.c.access_policy).where(project.c.id == created.id)
        ).scalar_one()
        assert "auto_accept_qualification" not in policy
        assert policy["management_setup_approvals"] == {}
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    assert reads.read(Reader(), "dataset-release", frozen.id) == frozen
    assert reads.read_project(Reader(), created.id).approved_steps == []
    plan = reads.read_freeze_plan(Reader(), created.id)
    assert plan.payload is None and not plan.checks[0].passed
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        freeze(service, created.id, payload.model_copy(update={"expected_revision": 4}), key="new")


@pytest.mark.parametrize("tamper", ["no_receipt", "name", "scope", "removed", "approval"])
def test_scope_projection_requires_matching_canonical_draft_receipt(database, tamper):
    service, owner, _ = database
    created = create(service)
    if tamper != "no_receipt":
        save_scope(service, created.id)
    with owner.begin() as connection:
        row = connection.execute(select(project).where(project.c.id == created.id)).one()
        policy = dict(row.access_policy)
        draft = scope_payload().model_dump(exclude={"expected_revision"})
        policy["management_scope_draft"] = draft
        if tamper == "name":
            draft["name"] = "Unattributed name"
        if tamper == "removed":
            del policy["management_scope_draft"]
        if tamper == "approval":
            policy["management_setup_approvals"] = {"scope": "forged"}
        connection.execute(
            project.update()
            .where(project.c.id == row.id)
            .values(
                access_policy=policy, scope="Unattributed scope" if tamper == "scope" else row.scope
            )
        )
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        reads.read_project(Reader(), created.id)


def catalog_payload(service, project_id, revision=1):
    detail = CanonicalManagementReads(service.engine, service.workspace_id).read_project(
        Reader(), project_id
    )
    return ClassCatalogDraft(
        expected_revision=revision,
        base_ontology_id=detail.versions["ontology_id"],
        classes=[
            {
                "stable_id": "package",
                "name": "Parcel",
                "definition": "A visible package",
                "aliases": ["Package"],
                "parent_id": None,
                "positive_examples": ["A sealed cardboard parcel"],
                "exclusions": ["Printed depictions"],
                "mirror_safe": False,
            }
        ],
    )


def save_catalog(service, project_id, payload, actor=None, key="catalog"):
    actor = actor or Actor()
    context = MutationContext.bind(actor, key, "save_class_catalog_draft", project_id, payload)
    return service.save_class_catalog_draft(actor, context, payload)


def test_catalog_save_is_attributed_replayable_and_separate_from_canonical_classes(database):
    service, owner, _ = database
    created = create(service)
    payload = catalog_payload(service, created.id)
    result = save_catalog(service, created.id, payload)
    assert result.revision == 2 and result.state == "draft"
    assert save_catalog(service, created.id, payload) == result
    changed = payload.model_copy(
        update={"classes": [payload.classes[0].model_copy(update={"name": "Box"})]}
    )
    with pytest.raises(ManagementFailure, match="idempotency_conflict"):
        save_catalog(service, created.id, changed)
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        save_catalog(service, created.id, payload, key="stale")
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    detail = reads.read_project(Reader(), created.id)
    assert detail.class_catalog_draft.model_dump() == payload.model_dump(
        exclude={"expected_revision"}
    )
    assert detail.classes[0].name == "Package" and detail.classes[0].approval is None
    assert detail.approved_steps == [] and detail.references is None
    assert detail.published_release_id is None
    with owner.connect() as connection:
        ontologies = connection.execute(
            select(ontology_release).where(ontology_release.c.project_id == created.id)
        ).all()
        assert len(ontologies) == 1 and ontologies[0].class_map == {"package": 0}
        receipt = connection.execute(
            select(canonical_event).where(
                canonical_event.c.aggregate_id == created.id,
                canonical_event.c.event_type == "management.save_class_catalog_draft",
            )
        ).one()
        assert receipt.actor_id == Actor().id and receipt.actor_kind == "human"
        assert (
            receipt.payload["evidence"]["class_catalog_draft"]
            == detail.class_catalog_draft.model_dump()
        )
        assert "publish" in receipt.payload["evidence"]["invalidated_steps"]
    assert (
        save_catalog(
            service, created.id, changed.model_copy(update={"expected_revision": 2}), key="next"
        ).revision
        == 3
    )
    assert save_catalog(service, created.id, payload) == result
    assert reads.read_project(Reader(), created.id).class_catalog_draft.classes[0].name == "Box"
    assert counts(owner, created.id) == (0, 0, 3)


@pytest.mark.parametrize("same_key", [True, False])
def test_parallel_catalog_saves_share_project_revision_and_retry_locks(database, same_key):
    service, owner, _ = database
    created = create(service)
    payload = catalog_payload(service, created.id)
    barrier = Barrier(2, timeout=10)

    def submit(key):
        barrier.wait()
        try:
            return save_catalog(
                service, created.id, payload, key="same" if same_key else key
            ).revision
        except ManagementFailure as error:
            return error.error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ("left", "right")))
    assert outcomes.count(2) == (2 if same_key else 1)
    assert outcomes.count("revision_conflict") == (0 if same_key else 1)
    assert counts(owner, created.id) == (0, 0, 2)


def test_catalog_scope_and_guide_preserve_each_other_and_share_revision_order(database):
    service, owner, _ = database
    created = create(service)
    payload = catalog_payload(service, created.id)
    save_scope(service, created.id)
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        save_catalog(service, created.id, payload)
    save_catalog(service, created.id, payload.model_copy(update={"expected_revision": 2}))
    save_guide(service, created.id, guide_payload(3))
    save_scope(service, created.id, scope_payload(4), key="next-scope")
    detail = CanonicalManagementReads(service.engine, service.workspace_id).read_project(
        Reader(), created.id
    )
    assert detail.resource.revision == 5
    assert detail.class_catalog_draft.classes[0].name == "Parcel"
    assert detail.name == scope_payload().name and detail.guide.min_target_px == 4.0
    assert detail.approved_steps == [] and counts(owner, created.id) == (0, 0, 5)


def test_catalog_audit_failure_rolls_back_draft_and_retry_succeeds(database):
    service, owner, role = database
    created = create(service)
    payload = catalog_payload(service, created.id)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    before = reads.read_project(Reader(), created.id)
    with owner.begin() as connection:
        connection.execute(text(f'REVOKE INSERT ON canonical_event FROM "{role}"'))
    with pytest.raises(DBAPIError):
        save_catalog(service, created.id, payload)
    assert reads.read_project(Reader(), created.id) == before
    with owner.begin() as connection:
        connection.execute(text(f'GRANT INSERT ON canonical_event TO "{role}"'))
    assert save_catalog(service, created.id, payload).revision == 2


@pytest.mark.parametrize("actor", [Actor(kind="service"), Actor(role="reviewer"), Actor(id="")])
def test_catalog_denies_nonadmin_principals(database, actor):
    service, owner, _ = database
    created = create(service)
    with pytest.raises(ManagementFailure, match="permission_denied"):
        save_catalog(service, created.id, catalog_payload(service, created.id), actor=actor)
    assert counts(owner, created.id) == (0, 0, 1)


def test_catalog_checks_workspace_and_context_before_replay(database):
    service, owner, _ = database
    created = create(service)
    payload = catalog_payload(service, created.id)
    save_catalog(service, created.id, payload)
    with pytest.raises(ManagementFailure, match="not_found"):
        save_catalog(replace(service, workspace_id=uuid4()), created.id, payload)
    with pytest.raises(ManagementFailure, match="not_found"):
        save_catalog(service, "not-a-uuid", payload)
    context = MutationContext.bind(
        Actor(), "catalog", "save_class_catalog_draft", created.id, payload
    )
    with pytest.raises(ManagementFailure, match="invalid_request"):
        service.save_class_catalog_draft(Actor(), replace(context, actor_id="forged"), payload)
    assert counts(owner, created.id) == (0, 0, 2)


@pytest.mark.parametrize("base", ["unknown", "foreign", "obsolete"])
def test_catalog_requires_current_owned_ontology_pin(database, base):
    service, owner, _ = database
    created = create(service)
    payload = catalog_payload(service, created.id)
    if base == "foreign":
        other = create(service, key="other")
        payload = payload.model_copy(
            update={"base_ontology_id": catalog_payload(service, other.id).base_ontology_id}
        )
    elif base == "obsolete":
        with owner.begin() as connection:
            connection.execute(
                insert(ontology_release).values(
                    ontology_key=str(uuid4()),
                    project_id=created.id,
                    version=2,
                    classes=[{"stable_id": "package", "name": "Package", "definition": "Package"}],
                    class_map={"package": 0},
                )
            )
    else:
        payload = payload.model_copy(update={"base_ontology_id": "unknown"})
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        save_catalog(service, created.id, payload)
    assert counts(owner, created.id) == (0, 0, 1)


def test_catalog_invalidates_qualification_and_new_freeze_not_historical_release(database):
    service, owner, _ = database
    created, _, _, freeze_payload = release_inputs(service, owner)
    frozen = freeze(service, created.id, freeze_payload)
    payload = catalog_payload(service, created.id, 3)
    with owner.begin() as connection:
        row = connection.execute(select(project).where(project.c.id == created.id)).one()
        policy = {
            **row.access_policy,
            "auto_accept_qualification": {"qualified": True},
            "management_setup_approvals": {"classes": "old"},
        }
        connection.execute(
            project.update().where(project.c.id == row.id).values(access_policy=policy)
        )
    save_catalog(service, created.id, payload)
    with owner.connect() as connection:
        policy = connection.execute(
            select(project.c.access_policy).where(project.c.id == created.id)
        ).scalar_one()
        assert (
            "auto_accept_qualification" not in policy and policy["management_setup_approvals"] == {}
        )
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    assert reads.read_release(Reader(), frozen.id).resource == frozen
    assert reads.read_freeze_plan(Reader(), created.id).payload is None
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        freeze(
            service,
            created.id,
            freeze_payload.model_copy(update={"expected_revision": 4}),
            key="new",
        )


@pytest.mark.parametrize("tamper", ["no_receipt", "class", "removed", "approval", "ontology"])
def test_catalog_projection_requires_matching_attributed_current_draft(database, tamper):
    service, owner, _ = database
    created = create(service)
    payload = catalog_payload(service, created.id)
    if tamper != "no_receipt":
        save_catalog(service, created.id, payload)
    with owner.begin() as connection:
        row = connection.execute(select(project).where(project.c.id == created.id)).one()
        policy = dict(row.access_policy)
        policy["management_class_catalog_draft"] = payload.model_dump(exclude={"expected_revision"})
        if tamper == "class":
            policy["management_class_catalog_draft"]["classes"][0]["name"] = "Unattributed"
        if tamper == "removed":
            del policy["management_class_catalog_draft"]
        if tamper == "approval":
            policy["management_setup_approvals"] = {"classes": "forged"}
        if tamper == "ontology":
            connection.execute(
                insert(ontology_release).values(
                    ontology_key=str(uuid4()),
                    project_id=created.id,
                    version=2,
                    classes=[{"stable_id": "package", "name": "Package", "definition": "Package"}],
                    class_map={"package": 0},
                )
            )
        connection.execute(
            project.update().where(project.c.id == row.id).values(access_policy=policy)
        )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_project(
            Reader(), created.id
        )


def publication_payload(service, project_id):
    detail = CanonicalManagementReads(service.engine, service.workspace_id).read_project(
        Reader(), project_id
    )
    return ClassCatalogPublish(
        expected_revision=detail.resource.revision,
        base_ontology_id=detail.versions["ontology_id"],
        draft_sha256=detail.setup_draft_sha256["classes"],
        reason="Reviewed new catalog version",
    )


def publish_catalog(service, project_id, payload, actor=None, key="publish-catalog"):
    actor = actor or Actor()
    context = MutationContext.bind(actor, key, "publish_class_catalog", project_id, payload)
    return service.publish_class_catalog(actor, context, payload)


def ontology_rows(owner, project_id):
    with owner.connect() as connection:
        return connection.execute(
            select(ontology_release)
            .where(ontology_release.c.project_id == project_id)
            .order_by(ontology_release.c.version)
        ).all()


def test_catalog_publication_preserves_ids_indices_and_history_not_project_publication(database):
    service, owner, _ = database
    created, _, _, freeze_payload = release_inputs(service, owner)
    frozen = freeze(service, created.id, freeze_payload)
    draft = catalog_payload(service, created.id, 3)
    # Input ordering cannot remap an existing class; new indices are server-owned.
    added = draft.classes[0].model_copy(update={"stable_id": "carton", "name": "Carton"})
    draft = draft.model_copy(update={"classes": [added, draft.classes[0]]})
    original = ontology_rows(owner, created.id)[0]
    save_catalog(service, created.id, draft)
    payload = publication_payload(service, created.id)
    result = publish_catalog(service, created.id, payload)
    assert result.revision == 5 and result.state == "draft"
    assert publish_catalog(service, created.id, payload) == result
    with pytest.raises(ManagementFailure, match="idempotency_conflict"):
        publish_catalog(service, created.id, payload.model_copy(update={"reason": "Changed"}))
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        publish_catalog(service, created.id, payload, key="stale")
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    detail = reads.read_project(Reader(), created.id)
    assert detail.class_catalog_draft is None
    assert detail.setup_draft_sha256["classes"] == payload.draft_sha256
    assert detail.classes[0].approval is None
    assert detail.published_release_id is None and detail.versions["ontology"] == "2"
    assert {item.stable_id: item.name for item in detail.classes} == {
        "package": "Parcel",
        "carton": "Carton",
    }
    old, new = ontology_rows(owner, created.id)
    assert old == original and new.class_map == {"package": 0, "carton": 1}
    assert new.ontology_key == detail.versions["ontology_id"] != old.ontology_key
    assert reads.read_release(Reader(), frozen.id).resource == frozen
    assert reads.read_freeze_plan(Reader(), created.id).payload is None
    with owner.connect() as connection:
        receipt = connection.execute(
            select(canonical_event).where(
                canonical_event.c.aggregate_id == created.id,
                canonical_event.c.event_type == "management.publish_class_catalog",
            )
        ).one()
        assert receipt.actor_id == Actor().id and receipt.actor_kind == "human"
        assert receipt.payload["evidence"]["class_catalog"] == draft.model_dump(
            exclude={"expected_revision"}
        )
    # Another edit must explicitly pin the just-published ontology.
    next_draft = catalog_payload(service, created.id, 5).model_copy(
        update={"classes": draft.classes}
    )
    save_catalog(service, created.id, next_draft, key="next-catalog")
    assert publish_catalog(service, created.id, payload) == result
    publish_catalog(service, created.id, publication_payload(service, created.id), key="next-pub")
    assert len(ontology_rows(owner, created.id)) == 3


@pytest.mark.parametrize("same_key", [True, False])
def test_parallel_catalog_publication_is_one_version_and_receipt(database, same_key):
    service, owner, _ = database
    created = create(service)
    save_catalog(service, created.id, catalog_payload(service, created.id))
    payload = publication_payload(service, created.id)
    barrier = Barrier(2, timeout=10)

    def submit(key):
        barrier.wait()
        try:
            return publish_catalog(
                service, created.id, payload, key="same" if same_key else key
            ).revision
        except ManagementFailure as error:
            return error.error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ("left", "right")))
    assert results.count(3) == (2 if same_key else 1)
    assert results.count("revision_conflict") == (0 if same_key else 1)
    assert len(ontology_rows(owner, created.id)) == 2 and counts(owner, created.id) == (0, 0, 3)


@pytest.mark.parametrize(
    "field,value", [("base_ontology_id", "foreign"), ("draft_sha256", "f" * 64)]
)
def test_catalog_publication_requires_exact_current_ontology_and_draft(database, field, value):
    service, owner, _ = database
    created = create(service)
    save_catalog(service, created.id, catalog_payload(service, created.id))
    payload = publication_payload(service, created.id).model_copy(update={field: value})
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        publish_catalog(service, created.id, payload)
    assert len(ontology_rows(owner, created.id)) == 1


def test_catalog_publication_refuses_removed_identity_and_missing_draft(database):
    service, owner, _ = database
    created = create(service)
    payload = ClassCatalogPublish(
        expected_revision=1,
        base_ontology_id=ontology_rows(owner, created.id)[0].ontology_key,
        draft_sha256="a" * 64,
        reason="No draft",
    )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        publish_catalog(service, created.id, payload)
    draft = catalog_payload(service, created.id)
    draft = draft.model_copy(
        update={"classes": [draft.classes[0].model_copy(update={"stable_id": "replacement"})]}
    )
    save_catalog(service, created.id, draft)
    with pytest.raises(ManagementFailure, match="unsupported_classes"):
        publish_catalog(service, created.id, publication_payload(service, created.id))
    assert len(ontology_rows(owner, created.id)) == 1


@pytest.mark.parametrize("actor", [Actor(kind="service"), Actor(role="reviewer"), Actor(id="")])
def test_catalog_publication_refuses_nonadmin_principals(database, actor):
    service, owner, _ = database
    created = create(service)
    save_catalog(service, created.id, catalog_payload(service, created.id))
    with pytest.raises(ManagementFailure, match="permission_denied"):
        publish_catalog(service, created.id, publication_payload(service, created.id), actor=actor)
    assert len(ontology_rows(owner, created.id)) == 1


def test_catalog_publication_checks_owned_target_and_context_before_replay(database):
    service, owner, _ = database
    created = create(service)
    save_catalog(service, created.id, catalog_payload(service, created.id))
    payload = publication_payload(service, created.id)
    publish_catalog(service, created.id, payload)
    with pytest.raises(ManagementFailure, match="not_found"):
        publish_catalog(replace(service, workspace_id=uuid4()), created.id, payload)
    with pytest.raises(ManagementFailure, match="not_found"):
        publish_catalog(service, "not-a-uuid", payload)
    context = MutationContext.bind(
        Actor(), "publish-catalog", "publish_class_catalog", created.id, payload
    )
    with pytest.raises(ManagementFailure, match="invalid_request"):
        service.publish_class_catalog(Actor(), replace(context, actor_id="forged"), payload)
    assert counts(owner, created.id) == (0, 0, 3)


@pytest.mark.parametrize("table", ["ontology_release", "canonical_event", "project"])
def test_catalog_publication_is_atomic_under_runtime_grant_failure(database, table):
    service, owner, role = database
    created = create(service)
    save_catalog(service, created.id, catalog_payload(service, created.id))
    payload = publication_payload(service, created.id)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    before = reads.read_project(Reader(), created.id)
    privilege = "UPDATE (access_policy)" if table == "project" else "INSERT"
    with owner.begin() as connection:
        connection.execute(text(f'REVOKE {privilege} ON {table} FROM "{role}"'))
    with pytest.raises(DBAPIError):
        publish_catalog(service, created.id, payload)
    assert reads.read_project(Reader(), created.id) == before
    assert len(ontology_rows(owner, created.id)) == 1
    with owner.begin() as connection:
        connection.execute(text(f'GRANT {privilege} ON {table} TO "{role}"'))
    assert publish_catalog(service, created.id, payload).revision == 3


@pytest.mark.parametrize("accepted", [True, False])
def test_catalog_publication_requires_reference_retirement_without_revalidating_it(
    database, accepted
):
    service, owner, _ = database
    from labelloop_api.management_setup import draft_sha256

    created, _, acceptance = acceptance_inputs(service, owner)
    if accepted:
        accept(service, created.id, acceptance)
    revision = 4 if accepted else 3
    draft = catalog_payload(service, created.id, revision)
    save_catalog(service, created.id, draft)
    payload = ClassCatalogPublish(
        expected_revision=revision + 1,
        base_ontology_id=draft.base_ontology_id,
        draft_sha256=draft_sha256(draft.model_dump(exclude={"expected_revision"})),
        reason="Reviewed catalog",
    )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        publish_catalog(service, created.id, payload)
    withdraw(
        service,
        created.id,
        ReferenceWithdraw(
            expected_revision=revision + 1,
            reference_id=acceptance.reference_id,
            reason="Retire before ontology edit",
        ),
    )
    publish_catalog(service, created.id, publication_payload(service, created.id))
    detail = CanonicalManagementReads(service.engine, service.workspace_id).read_project(
        Reader(), created.id
    )
    assert detail.references[0].approval == "withdrawn"
    assert detail.versions["ontology"] == "2"


@pytest.mark.parametrize("tamper", ["marker", "missing_marker", "ontology", "restored_draft"])
def test_catalog_publication_read_requires_matching_receipt_and_canonical_version(database, tamper):
    service, owner, _ = database
    created = create(service)
    draft = catalog_payload(service, created.id)
    save_catalog(service, created.id, draft)
    publish_catalog(service, created.id, publication_payload(service, created.id))
    with owner.begin() as connection:
        row = connection.execute(select(project).where(project.c.id == created.id)).one()
        policy = dict(row.access_policy)
        if tamper == "marker":
            policy["management_class_catalog_publication"]["receipt_revision"] = 999
        elif tamper == "missing_marker":
            del policy["management_class_catalog_publication"]
        elif tamper == "restored_draft":
            policy["management_class_catalog_draft"] = draft.model_dump(
                exclude={"expected_revision"}
            )
        else:
            current = ontology_rows(owner, created.id)[-1]
            connection.execute(
                insert(ontology_release).values(
                    ontology_key=str(uuid4()),
                    project_id=created.id,
                    version=3,
                    classes=current.classes,
                    class_map=current.class_map,
                )
            )
        connection.execute(
            project.update().where(project.c.id == created.id).values(access_policy=policy)
        )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_project(
            Reader(), created.id
        )


def test_catalog_publication_races_draft_save_under_one_project_lock(database):
    service, owner, _ = database
    created = create(service)
    draft = catalog_payload(service, created.id)
    save_catalog(service, created.id, draft)
    payload = publication_payload(service, created.id)
    changed = draft.model_copy(update={"expected_revision": 2})
    barrier = Barrier(2, timeout=10)

    def submit(publishing):
        barrier.wait()
        try:
            if publishing:
                return publish_catalog(service, created.id, payload).revision
            return save_catalog(service, created.id, changed, key="racing-save").revision
        except ManagementFailure as error:
            return error.error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, (True, False)))
    assert sorted(map(str, results)) == ["3", "revision_conflict"]
    assert counts(owner, created.id) == (0, 0, 3)
    assert len(ontology_rows(owner, created.id)) in (1, 2)


def test_catalog_publication_clears_approval_and_qualification_without_editing_assessments(
    database,
):
    service, owner, _ = database
    created, _, _, _ = release_inputs(service, owner)
    save_scope(service, created.id, scope_payload(2))
    save_catalog(service, created.id, catalog_payload(service, created.id, 3))
    approve_step(service, created.id, step_approval(service, created.id, "scope"))
    payload = publication_payload(service, created.id)
    with owner.begin() as connection:
        row = connection.execute(select(project).where(project.c.id == created.id)).one()
        policy = {**row.access_policy, "auto_accept_qualification": {"id": "old"}}
        connection.execute(
            project.update().where(project.c.id == created.id).values(access_policy=policy)
        )
        assessments = connection.execute(select(completeness_assessment)).all()
    publish_catalog(service, created.id, payload)
    with owner.connect() as connection:
        policy = connection.execute(
            select(project.c.access_policy).where(project.c.id == created.id)
        ).scalar_one()
        assert (
            policy["management_setup_approvals"] == {} and "auto_accept_qualification" not in policy
        )
        assert connection.execute(select(completeness_assessment)).all() == assessments
    assert (
        CanonicalManagementReads(service.engine, service.workspace_id)
        .read_project(Reader(), created.id)
        .approved_steps
        == []
    )


def test_catalog_publication_projection_is_select_only(database):
    service, owner, role = database
    created = create(service)
    save_catalog(service, created.id, catalog_payload(service, created.id))
    publish_catalog(service, created.id, publication_payload(service, created.id))
    with owner.begin() as connection:
        connection.execute(
            text(f'REVOKE INSERT, UPDATE ON ALL TABLES IN SCHEMA public FROM "{role}"')
        )
    detail = CanonicalManagementReads(service.engine, service.workspace_id).read_project(
        Reader(), created.id
    )
    assert detail.versions["ontology"] == "2" and detail.resource.state == "draft"
    assert counts(owner, created.id) == (0, 0, 3)


def published_catalog_inputs(service):
    created = create(service)
    draft = catalog_payload(service, created.id)
    save_catalog(service, created.id, draft)
    publish_catalog(service, created.id, publication_payload(service, created.id))
    return created, draft, step_approval(service, created.id, "classes")


def test_class_approval_binds_published_definitions_not_prose_support_or_project_release(database):
    service, owner, _ = database
    created, draft, payload = published_catalog_inputs(service)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    assert reads.read_project(Reader(), created.id).classes[0].approval is None
    result = approve_step(service, created.id, payload)
    assert result.revision == 4 and result.state == "draft"
    assert approve_step(service, created.id, payload) == result
    detail = reads.read_project(Reader(), created.id)
    assert detail.approved_steps == ["classes"] and detail.published_release_id is None
    assert detail.class_catalog_draft is None and detail.classes[0].approval == "admin_approved"
    assert detail.classes[0].mirror_safe == draft.classes[0].mirror_safe
    assert detail.classes[0].exclusions == draft.classes[0].exclusions
    assert draft.classes[0].positive_examples and detail.classes[0].positive_examples is None
    assert detail.setup_draft_sha256["classes"] == payload.draft_sha256
    assert reads.read_freeze_plan(Reader(), created.id).payload is None
    with pytest.raises(ManagementFailure, match="idempotency_conflict"):
        approve_step(service, created.id, payload.model_copy(update={"reason": "Changed"}))
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        approve_step(service, created.id, payload, key="stale")
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        approve_step(
            service, created.id, step_approval(service, created.id, "classes"), key="again"
        )
    with owner.connect() as connection:
        receipt = connection.execute(
            select(canonical_event).where(
                canonical_event.c.aggregate_id == created.id,
                canonical_event.c.event_type == "management.approve_setup_step",
            )
        ).one()
        assert receipt.actor_id == Actor().id and receipt.actor_kind == "human"
        assert receipt.payload["evidence"] == payload.model_dump(exclude={"expected_revision"})
    assert counts(owner, created.id) == (0, 0, 4)


@pytest.mark.parametrize("saved", [True, False])
def test_class_approval_requires_publication_not_bootstrap_or_draft(database, saved):
    service, owner, _ = database
    created = create(service)
    if saved:
        save_catalog(service, created.id, catalog_payload(service, created.id))
    payload = SetupStepApprove(
        expected_revision=2 if saved else 1,
        step="classes",
        draft_sha256="a" * 64,
        reason="Fixture",
    )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        approve_step(service, created.id, payload)
    assert counts(owner, created.id) == (0, 0, 2 if saved else 1)


@pytest.mark.parametrize("same_key", [True, False])
def test_parallel_class_approvals_share_project_revision_and_retry_locks(database, same_key):
    service, owner, _ = database
    created, _, payload = published_catalog_inputs(service)
    barrier = Barrier(2, timeout=10)

    def submit(key):
        barrier.wait()
        try:
            return approve_step(
                service, created.id, payload, key="same" if same_key else key
            ).revision
        except ManagementFailure as error:
            return error.error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ("left", "right")))
    assert outcomes.count(4) == (2 if same_key else 1)
    assert outcomes.count("revision_conflict") == (0 if same_key else 1)
    assert counts(owner, created.id) == (0, 0, 4)


@pytest.mark.parametrize("actor", [Actor(kind="service"), Actor(role="reviewer"), Actor(id="")])
def test_class_approval_refuses_nonadmin_principals(database, actor):
    service, owner, _ = database
    created, _, payload = published_catalog_inputs(service)
    with pytest.raises(ManagementFailure, match="permission_denied"):
        approve_step(service, created.id, payload, actor=actor)
    assert counts(owner, created.id) == (0, 0, 3)


@pytest.mark.parametrize("table", ["canonical_event", "project"])
def test_class_approval_grant_failure_is_atomic_and_retryable(database, table):
    service, owner, role = database
    created, _, payload = published_catalog_inputs(service)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    before = reads.read_project(Reader(), created.id)
    privilege = "INSERT" if table == "canonical_event" else "UPDATE (scope, domain, access_policy)"
    with owner.begin() as connection:
        connection.execute(text(f'REVOKE {privilege} ON {table} FROM "{role}"'))
    with pytest.raises(DBAPIError):
        approve_step(service, created.id, payload)
    assert reads.read_project(Reader(), created.id) == before
    assert counts(owner, created.id) == (0, 0, 3)
    with owner.begin() as connection:
        connection.execute(text(f'GRANT {privilege} ON {table} TO "{role}"'))
    assert approve_step(service, created.id, payload).revision == 4


@pytest.mark.parametrize("edit", ["scope", "guide", "catalog", "sources"])
def test_setup_save_invalidates_class_approval_even_if_old_policy_is_restored(database, edit):
    service, owner, _ = database
    created, _, payload = published_catalog_inputs(service)
    approve_step(service, created.id, payload)
    with owner.connect() as connection:
        prior = connection.execute(
            select(project.c.access_policy).where(project.c.id == created.id)
        ).scalar_one()["management_setup_approvals"]
    if edit == "scope":
        save_scope(service, created.id, scope_payload(4))
    elif edit == "guide":
        save_guide(service, created.id, guide_payload(4))
    elif edit == "catalog":
        save_catalog(service, created.id, catalog_payload(service, created.id, 4), key="next")
    else:
        save_sources(service, created.id, source_policy_payload(4))
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    detail = reads.read_project(Reader(), created.id)
    assert detail.approved_steps == [] and detail.classes[0].approval is None
    with owner.begin() as connection:
        row = connection.execute(select(project).where(project.c.id == created.id)).one()
        connection.execute(
            project.update()
            .where(project.c.id == row.id)
            .values(access_policy={**row.access_policy, "management_setup_approvals": prior})
        )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        reads.read_project(Reader(), created.id)


def test_class_approval_enforces_digest_owned_target_and_context_before_replay(database):
    service, owner, _ = database
    created, _, payload = published_catalog_inputs(service)
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        approve_step(service, created.id, payload.model_copy(update={"draft_sha256": "a" * 64}))
    approve_step(service, created.id, payload)
    with pytest.raises(ManagementFailure, match="not_found"):
        approve_step(replace(service, workspace_id=uuid4()), created.id, payload)
    with pytest.raises(ManagementFailure, match="not_found"):
        approve_step(service, "not-a-uuid", payload)
    context = MutationContext.bind(Actor(), "approve", "approve_setup_step", created.id, payload)
    with pytest.raises(ManagementFailure, match="invalid_request"):
        service.approve_setup_step(Actor(), replace(context, actor_id="forged"), payload)
    assert counts(owner, created.id) == (0, 0, 4)


@pytest.mark.parametrize("remove_policy", [True, False])
def test_class_approval_refuses_new_unpublished_draft_even_if_its_policy_is_removed(
    database, remove_policy
):
    service, owner, _ = database
    created, _, previous = published_catalog_inputs(service)
    save_catalog(service, created.id, catalog_payload(service, created.id, 3), key="next")
    if remove_policy:
        with owner.begin() as connection:
            row = connection.execute(select(project).where(project.c.id == created.id)).one()
            policy = dict(row.access_policy)
            policy.pop("management_class_catalog_draft")
            connection.execute(
                project.update().where(project.c.id == row.id).values(access_policy=policy)
            )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        approve_step(service, created.id, previous.model_copy(update={"expected_revision": 4}))
    assert counts(owner, created.id) == (0, 0, 4)


def test_class_approval_races_new_catalog_draft_under_shared_project_lock(database):
    service, owner, _ = database
    created, _, payload = published_catalog_inputs(service)
    draft = catalog_payload(service, created.id, 3)
    barrier = Barrier(2, timeout=10)

    def submit(operation):
        barrier.wait()
        try:
            result = (
                approve_step(service, created.id, payload)
                if operation == "approve"
                else save_catalog(service, created.id, draft, key="next")
            )
            return result.revision
        except ManagementFailure as error:
            return error.error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ("approve", "save")))
    assert sorted(outcomes, key=str) == [4, "revision_conflict"]
    assert counts(owner, created.id) == (0, 0, 4)


def test_class_scope_guide_approvals_compose_and_projection_needs_only_select(database):
    service, owner, role = database
    created, _, _ = published_catalog_inputs(service)
    save_scope(service, created.id, scope_payload(3))
    save_guide(service, created.id, guide_payload(4))
    for step in ("scope", "guide"):
        with pytest.raises(ManagementFailure, match="idempotency_conflict"):
            approve_step(service, created.id, step_approval(service, created.id, step), key=step)
    assert counts(owner, created.id) == (0, 0, 5)
    for step in ("scope", "classes", "guide"):
        payload = step_approval(service, created.id, step)
        result = approve_step(service, created.id, payload, key=f"approve-{step}")
        assert approve_step(service, created.id, payload, key=f"approve-{step}") == result
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    before = reads.read_project(Reader(), created.id)
    assert before.approved_steps == ["classes", "guide", "scope"]
    with owner.begin() as connection:
        connection.execute(
            text(f'REVOKE INSERT, UPDATE ON ALL TABLES IN SCHEMA public FROM "{role}"')
        )
    assert reads.read_project(Reader(), created.id) == before
    assert counts(owner, created.id) == (0, 0, 8)


def reference_inputs(service, owner, **kwargs):
    created, asset, source, frozen = release_inputs(service, owner, **kwargs)
    payload = ReferenceEnroll(
        expected_revision=2,
        base_ontology_id=frozen.ontology_id,
        assignment_version=frozen.assignment_version,
        asset_id=asset.id,
        class_id="package",
        source="Generated fixture",
        permitted_use="CC0 fixture",
        view="Side",
        lighting="Diffuse",
        scale="Full frame",
        validity_scope="Visible packages",
        confused_with=[],
    )
    return created, asset, source, payload


def enroll(service, project_id, payload, actor=None, key="reference"):
    actor = actor or Actor()
    context = MutationContext.bind(actor, key, "enroll_reference", project_id, payload)
    return service.enroll_reference(actor, context, payload)


def reference_count(owner, project_id):
    with owner.connect() as connection:
        return connection.execute(
            select(func.count())
            .select_from(reference_example)
            .join(source_asset)
            .where(source_asset.c.project_id == project_id)
        ).scalar_one()


def test_reference_enrollment_is_proposed_attributed_and_replayable(database):
    service, owner, _ = database
    created, asset, _, payload = reference_inputs(service, owner, split="train")
    with owner.begin() as connection:
        row = connection.execute(select(project).where(project.c.id == created.id)).one()
        connection.execute(
            project.update()
            .where(project.c.id == row.id)
            .values(
                access_policy={
                    **row.access_policy,
                    "auto_accept_qualification": {"qualified": True},
                    "management_setup_approvals": {"evidence": "old"},
                }
            )
        )
    saved = enroll(service, created.id, payload)
    assert saved.revision == 3 and enroll(service, created.id, payload) == saved
    with pytest.raises(ManagementFailure, match="idempotency_conflict"):
        enroll(service, created.id, payload.model_copy(update={"view": "Top"}))
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        enroll(service, created.id, payload, key="stale")
    detail = CanonicalManagementReads(service.engine, service.workspace_id).read_project(
        Reader(), created.id
    )
    assert len(detail.references) == 1
    ref = detail.references[0]
    assert ref.asset_id == asset.id and ref.approval == "proposed"
    assert ref.source == payload.source and detail.approved_steps == []
    assert detail.published_release_id is None
    with owner.connect() as connection:
        row = connection.execute(
            select(reference_example).where(reference_example.c.id == ref.id)
        ).one()
        assert not row.approved and row.crop is None and row.embedding_model is None
        receipt = connection.execute(
            select(canonical_event).where(
                canonical_event.c.aggregate_id == created.id,
                canonical_event.c.event_type == "management.enroll_reference",
            )
        ).one()
        assert receipt.actor_id == Actor().id and receipt.actor_kind == "human"
        assert receipt.payload["evidence"]["reference"] == ref.model_dump()
        policy = connection.execute(
            select(project.c.access_policy).where(project.c.id == created.id)
        ).scalar_one()
        assert (
            "auto_accept_qualification" not in policy and policy["management_setup_approvals"] == {}
        )
    assert reference_count(owner, created.id) == 1
    assert counts(owner, created.id)[2] == 3


@pytest.mark.parametrize("split,locked", [("val", True), ("test", True), ("train", False)])
def test_reference_enrollment_refuses_holdouts_and_unlocked_sources(database, split, locked):
    service, owner, _ = database
    created, _, _, payload = reference_inputs(service, owner, split=split, locked=locked)
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        enroll(service, created.id, payload)
    assert reference_count(owner, created.id) == 0


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("base_ontology_id", "foreign", "revision_conflict"),
        ("assignment_version", "foreign", "missing_evidence"),
        ("class_id", "unknown", "unsupported_classes"),
        ("confused_with", ["unknown"], "unsupported_classes"),
        ("asset_id", "unknown", "not_found"),
    ],
)
def test_reference_enrollment_validates_authoritative_pins(database, field, value, code):
    service, owner, _ = database
    created, _, _, payload = reference_inputs(service, owner, split="train")
    with pytest.raises(ManagementFailure, match=code):
        enroll(service, created.id, payload.model_copy(update={field: value}))
    assert reference_count(owner, created.id) == 0


@pytest.mark.parametrize("same_key", [True, False])
def test_reference_enrollment_concurrency_uses_project_and_retry_locks(database, same_key):
    service, owner, _ = database
    created, _, _, payload = reference_inputs(service, owner, split="train")
    barrier = Barrier(2, timeout=10)

    def submit(key):
        barrier.wait()
        try:
            return enroll(service, created.id, payload, key="same" if same_key else key).revision
        except ManagementFailure as error:
            return error.error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ("left", "right")))
    assert outcomes.count(3) == (2 if same_key else 1)
    assert outcomes.count("revision_conflict") == (0 if same_key else 1)
    assert reference_count(owner, created.id) == 1


def test_reference_event_failure_rolls_back_and_retry_succeeds(database):
    service, owner, role = database
    created, _, _, payload = reference_inputs(service, owner, split="train")
    with owner.begin() as connection:
        connection.execute(text(f'REVOKE INSERT ON canonical_event FROM "{role}"'))
    with pytest.raises(DBAPIError):
        enroll(service, created.id, payload)
    assert reference_count(owner, created.id) == 0
    assert counts(owner, created.id)[2] == 2
    with owner.begin() as connection:
        connection.execute(text(f'GRANT INSERT ON canonical_event TO "{role}"'))
    assert enroll(service, created.id, payload).revision == 3


@pytest.mark.parametrize("actor", [Actor(kind="service"), Actor(role="reviewer"), Actor(id="")])
def test_reference_enrollment_denies_nonadmin(database, actor):
    service, owner, _ = database
    created, _, _, payload = reference_inputs(service, owner, split="train")
    with pytest.raises(ManagementFailure, match="permission_denied"):
        enroll(service, created.id, payload, actor=actor)
    assert reference_count(owner, created.id) == 0


def test_reference_enrollment_checks_context_workspace_and_asset_ownership(database):
    service, owner, _ = database
    created, asset, _, payload = reference_inputs(service, owner, split="train")
    enroll(service, created.id, payload)
    with pytest.raises(ManagementFailure, match="not_found"):
        enroll(replace(service, workspace_id=uuid4()), created.id, payload)
    context = MutationContext.bind(Actor(), "reference", "enroll_reference", created.id, payload)
    with pytest.raises(ManagementFailure, match="invalid_request"):
        service.enroll_reference(Actor(), replace(context, actor_id="forged"), payload)
    other = create(service, key="other")
    with pytest.raises(ManagementFailure, match="not_found"):
        enroll(
            service,
            other.id,
            payload.model_copy(
                update={
                    "expected_revision": 1,
                    "asset_id": asset.id,
                    "base_ontology_id": catalog_payload(service, other.id).base_ontology_id,
                }
            ),
            key="other-ref",
        )


@pytest.mark.parametrize("tamper", ["approved", "provenance", "removed"])
def test_reference_projection_requires_matching_attributed_row(database, tamper):
    service, owner, _ = database
    created, _, _, payload = reference_inputs(service, owner, split="train")
    enroll(service, created.id, payload)
    with owner.begin() as connection:
        ids = select(source_asset.c.id).where(source_asset.c.project_id == created.id)
        if tamper == "removed":
            connection.execute(
                reference_example.delete().where(reference_example.c.asset_id.in_(ids))
            )
        else:
            connection.execute(
                reference_example.update()
                .where(reference_example.c.asset_id.in_(ids))
                .values(
                    **(
                        {"approved": True}
                        if tamper == "approved"
                        else {"evidence_provenance": "{}"}
                    )
                )
            )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_project(
            Reader(), created.id
        )


def test_reference_enrollment_excludes_reassigned_historical_holdouts(database):
    service, owner, _ = database
    created, _, source, payload = reference_inputs(service, owner, split="val")
    with owner.begin() as connection:
        connection.execute(
            insert(source_split_assignment).values(
                assignment_version="assignment-2",
                asset_id=source.id,
                group_id="group-1",
                split="train",
                seed=7,
                algorithm="union-find-grouped-stratified-v2",
                thresholds={"phash_max_distance": 8},
                reason="fixture reassignment",
                locked=True,
            )
        )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        enroll(
            service, created.id, payload.model_copy(update={"assignment_version": "assignment-2"})
        )
    assert reference_count(owner, created.id) == 0


def acceptance_inputs(service, owner):
    created, _, source, enrollment = reference_inputs(service, owner, split="train")
    enroll(service, created.id, enrollment)
    detail = CanonicalManagementReads(service.engine, service.workspace_id).read_project(
        Reader(), created.id
    )
    payload = ReferenceAccept(
        expected_revision=3,
        reference_id=detail.references[0].id,
        base_ontology_id=enrollment.base_ontology_id,
        assignment_version=enrollment.assignment_version,
        reason="Verified provenance, permitted use and visible class",
    )
    return created, source, payload


def accept(service, project_id, payload, actor=None, key="accept-reference"):
    actor = actor or Actor()
    context = MutationContext.bind(actor, key, "accept_reference", project_id, payload)
    return service.accept_reference(actor, context, payload)


def test_reference_acceptance_is_attributed_atomic_and_replayable(database):
    service, owner, _ = database
    created, _, payload = acceptance_inputs(service, owner)
    result = accept(service, created.id, payload)
    assert result.revision == 4 and result.state == "draft"
    assert accept(service, created.id, payload) == result
    with pytest.raises(ManagementFailure, match="idempotency_conflict"):
        accept(service, created.id, payload.model_copy(update={"reason": "different"}))
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        accept(service, created.id, payload, key="stale")
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        accept(
            service, created.id, payload.model_copy(update={"expected_revision": 4}), key="again"
        )
    detail = CanonicalManagementReads(service.engine, service.workspace_id).read_project(
        Reader(), created.id
    )
    assert detail.references[0].approval == "admin_approved"
    assert detail.approved_steps == [] and detail.published_release_id is None
    with owner.connect() as connection:
        reference = connection.execute(
            select(reference_example).where(reference_example.c.id == payload.reference_id)
        ).one()
        assert reference.approved and reference.crop is None and reference.embedding_model is None
        receipt = connection.execute(
            select(canonical_event).where(
                canonical_event.c.aggregate_id == created.id,
                canonical_event.c.event_type == "management.accept_reference",
            )
        ).one()
        assert receipt.actor_kind == "human" and receipt.actor_id == Actor().id
        assert receipt.payload["evidence"]["reason"] == payload.reason
        assert receipt.payload["evidence"]["reference"] == detail.references[0].model_dump()
    assert counts(owner, created.id)[2] == 4


@pytest.mark.parametrize("same_key", [True, False])
def test_reference_acceptance_serializes_revision_and_replay(database, same_key):
    service, owner, _ = database
    created, _, payload = acceptance_inputs(service, owner)
    barrier = Barrier(2, timeout=10)

    def submit(key):
        barrier.wait()
        try:
            return accept(service, created.id, payload, key="same" if same_key else key).revision
        except ManagementFailure as error:
            return error.error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ("left", "right")))
    assert outcomes.count(4) == (2 if same_key else 1)
    assert outcomes.count("revision_conflict") == (0 if same_key else 1)
    assert counts(owner, created.id)[2] == 4


@pytest.mark.parametrize("actor", [Actor(kind="service"), Actor(role="reviewer"), Actor(id="")])
def test_reference_acceptance_denies_nonadmin(database, actor):
    service, owner, _ = database
    created, _, payload = acceptance_inputs(service, owner)
    with pytest.raises(ManagementFailure, match="permission_denied"):
        accept(service, created.id, payload, actor=actor)
    assert counts(owner, created.id)[2] == 3


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("reference_id", "missing", "not_found"),
        ("base_ontology_id", "other", "revision_conflict"),
        ("assignment_version", "other", "revision_conflict"),
    ],
)
def test_reference_acceptance_checks_enrollment_pins(database, field, value, code):
    service, owner, _ = database
    created, _, payload = acceptance_inputs(service, owner)
    with pytest.raises(ManagementFailure, match=code):
        accept(service, created.id, payload.model_copy(update={field: value}))
    assert counts(owner, created.id)[2] == 3


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("change", ["test", "unlocked", "new-version", "pending", "ontology"])
def test_reference_acceptance_rechecks_current_source_evidence(database, when, change):
    service, owner, _ = database
    created, source, payload = acceptance_inputs(service, owner)
    if when == "after":
        accept(service, created.id, payload)
    with owner.begin() as connection:
        if change in {"test", "unlocked", "new-version"}:
            # Locked history cannot be edited; publish changed evidence as a new
            # assignment and require the old enrollment pin to fail closed.
            connection.execute(
                insert(source_split_assignment).values(
                    assignment_version="assignment-2",
                    asset_id=source.id,
                    group_id="group-1",
                    split="test" if change == "test" else "train",
                    seed=7,
                    algorithm="union-find-grouped-stratified-v2",
                    thresholds={"phash_max_distance": 8},
                    reason="New assignment",
                    locked=change != "unlocked",
                )
            )
        elif change == "pending":
            connection.execute(
                insert(borderline_review).values(
                    left_asset_id=source.id,
                    right_asset_id=source.id,
                    metric="phash",
                    value="7",
                    algorithm_version="fixture-v1",
                    review_state="pending",
                )
            )
        else:
            connection.execute(
                insert(ontology_release).values(
                    ontology_key=str(uuid4()),
                    project_id=created.id,
                    version=2,
                    classes=[{"stable_id": "package", "name": "Package", "definition": "Package"}],
                    class_map={"package": 0},
                )
            )
    with pytest.raises(ManagementFailure, match="missing_evidence|revision_conflict"):
        if when == "before":
            accept(service, created.id, payload)
        else:
            CanonicalManagementReads(service.engine, service.workspace_id).read_project(
                Reader(), created.id
            )
    assert counts(owner, created.id)[2] == (3 if when == "before" else 4)


@pytest.mark.parametrize("failure", ["event-insert", "approval-update"])
def test_reference_acceptance_rolls_back_on_runtime_grant_failure(database, failure):
    service, owner, role = database
    created, _, payload = acceptance_inputs(service, owner)
    privilege = (
        "INSERT ON canonical_event"
        if failure == "event-insert"
        else "UPDATE (approved) ON reference_example"
    )
    with owner.begin() as connection:
        connection.execute(text(f'REVOKE {privilege} FROM "{role}"'))
    with pytest.raises(DBAPIError):
        accept(service, created.id, payload)
    with owner.connect() as connection:
        assert not connection.execute(
            select(reference_example.c.approved).where(
                reference_example.c.id == payload.reference_id
            )
        ).scalar_one()
    assert counts(owner, created.id)[2] == 3
    with owner.begin() as connection:
        connection.execute(text(f'GRANT {privilege} TO "{role}"'))
    assert accept(service, created.id, payload).revision == 4


@pytest.mark.parametrize("field", ["scope", "domain", "annotation_guide", "source_permitted_use"])
def test_reference_acceptance_is_bound_to_setup_context(database, field):
    service, owner, _ = database
    created, _, payload = acceptance_inputs(service, owner)
    accept(service, created.id, payload)
    with owner.begin() as connection:
        row = connection.execute(select(project).where(project.c.id == created.id)).one()
        values = (
            {field: "Changed scope"}
            if field in {"scope", "domain"}
            else {"access_policy": {**row.access_policy, field: "Changed policy"}}
        )
        connection.execute(project.update().where(project.c.id == created.id).values(**values))
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_project(
            Reader(), created.id
        )


def test_accepted_reference_projection_requires_unchanged_persisted_approval(database):
    service, owner, _ = database
    created, _, payload = acceptance_inputs(service, owner)
    accept(service, created.id, payload)
    with owner.begin() as connection:
        connection.execute(
            reference_example.update()
            .where(reference_example.c.id == payload.reference_id)
            .values(approved=False)
        )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_project(
            Reader(), created.id
        )


def test_reference_acceptance_enforces_workspace_context_and_project_ownership(database):
    service, owner, _ = database
    created, _, payload = acceptance_inputs(service, owner)
    with pytest.raises(ManagementFailure, match="not_found"):
        accept(replace(service, workspace_id=uuid4()), created.id, payload)
    context = MutationContext.bind(Actor(), "accept", "accept_reference", created.id, payload)
    with pytest.raises(ManagementFailure, match="invalid_request"):
        service.accept_reference(Actor(), replace(context, actor_id="forged"), payload)
    other = create(service, key="other")
    with pytest.raises(ManagementFailure, match="not_found"):
        accept(
            service, other.id, payload.model_copy(update={"expected_revision": 1}), key="other-ref"
        )
    assert counts(owner, created.id)[2] == 3


def revalidate(service, project_id, payload, actor=None, key="revalidate-reference"):
    actor = actor or Actor()
    context = MutationContext.bind(actor, key, "revalidate_reference", project_id, payload)
    return service.revalidate_reference(actor, context, payload)


def revalidation_inputs(service, owner):
    created, source, approval = acceptance_inputs(service, owner)
    accept(service, created.id, approval)
    save_scope(service, created.id, scope_payload(4))
    payload = ReferenceRevalidate(
        **approval.model_dump(exclude={"expected_revision"}),
        expected_revision=5,
        previous_approval_revision=4,
    )
    return created, source, payload


@pytest.mark.parametrize("state", ["proposed", "accepted", "renewed", "retired", "withdrawn"])
def test_reference_lifecycle_exposes_only_historical_recovery_pins(database, state):
    service, owner, _ = database
    created, source, approval = acceptance_inputs(service, owner)
    revision = 3
    if state in {"accepted", "renewed", "withdrawn"}:
        accept(service, created.id, approval)
        revision = 4
    if state == "renewed":
        save_scope(service, created.id, scope_payload(4))
        revalidate(
            service,
            created.id,
            ReferenceRevalidate(
                **approval.model_dump(exclude={"expected_revision"}),
                expected_revision=5,
                previous_approval_revision=4,
            ),
        )
        revision = 6
    if state in {"retired", "withdrawn"}:
        withdraw(service, created.id, withdrawal_payload(approval, revision))
        revision += 1
    before = counts(owner, created.id)
    detail = CanonicalManagementReads(
        service.engine, service.workspace_id
    ).read_reference_lifecycle(Reader(), created.id)
    assert detail.project_id == created.id and detail.project_revision == revision
    assert len(detail.references) == 1
    reference = detail.references[0]
    assert reference.model_dump() == {
        "id": approval.reference_id,
        "asset_id": source.asset_key,
        "class_id": "package",
        "state": "accepted"
        if state in {"accepted", "renewed"}
        else ("withdrawn" if state in {"retired", "withdrawn"} else "proposed"),
        "ontology_id": approval.base_ontology_id,
        "assignment_version": approval.assignment_version,
        "last_approval_revision": 6
        if state == "renewed"
        else (4 if state in {"accepted", "withdrawn"} else None),
        "eligibility": "not_assessed",
    }
    assert counts(owner, created.id) == before


def test_reference_lifecycle_recovers_latest_revision_for_explicit_renewal(database):
    service, owner, _ = database
    created, _, payload = revalidation_inputs(service, owner)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        reads.read_project(Reader(), created.id)
    detail = reads.read_reference_lifecycle(Reader(), created.id)
    reference = detail.references[0]
    result = revalidate(
        service,
        created.id,
        payload.model_copy(
            update={
                "expected_revision": detail.project_revision,
                "previous_approval_revision": reference.last_approval_revision,
            }
        ),
    )
    assert result.revision == 6
    save_guide(service, created.id, guide_payload(6))
    recovery = reads.read_reference_lifecycle(Reader(), created.id)
    assert recovery.project_revision == 7
    assert recovery.references[0].last_approval_revision == 6
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        reads.read_project(Reader(), created.id)
    assert reads.read_freeze_plan(Reader(), created.id).payload is None


@pytest.mark.parametrize(
    "actor",
    [
        Actor(kind="service"),
        Actor(role="reviewer"),
        Actor(role="none"),
        Actor(id=""),
    ],
)
def test_reference_lifecycle_denies_untrusted_readers(database, actor):
    service, owner, _ = database
    created, _, _ = acceptance_inputs(service, owner)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    with pytest.raises(ManagementFailure, match="permission_denied"):
        reads.read_reference_lifecycle(actor, created.id)


@pytest.mark.parametrize(
    "field,value",
    [
        ("approved", False),
        ("evidence_provenance", "{}"),
        ("class_id", "unattributed"),
        ("embedding_model", "unattributed"),
    ],
)
def test_reference_lifecycle_refuses_corrupt_history_not_just_current_staleness(
    database, field, value
):
    service, owner, _ = database
    created, _, payload = revalidation_inputs(service, owner)
    with owner.begin() as connection:
        connection.execute(
            reference_example.update()
            .where(reference_example.c.id == payload.reference_id)
            .values(**{field: value})
        )
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        reads.read_reference_lifecycle(Reader(), created.id)


def test_reference_lifecycle_is_select_only_and_scoped_to_managed_workspace(database):
    service, owner, role = database
    created, _, _ = revalidation_inputs(service, owner)
    foreign = replace(service, workspace_id=uuid4())
    other = create(foreign, key="foreign")
    with owner.begin() as connection:
        legacy = connection.execute(
            insert(project)
            .values(
                project_key=str(uuid4()),
                workspace_id=service.workspace_id,
                task="detect",
                scope="Legacy",
                domain="package",
                access_policy={},
            )
            .returning(project.c.id)
        ).scalar_one()
        connection.execute(
            text(f'REVOKE INSERT, UPDATE ON ALL TABLES IN SCHEMA public FROM "{role}"')
        )
    before = counts(owner, created.id)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    assert (
        reads.read_reference_lifecycle(Reader(), created.id).references[0].eligibility
        == "not_assessed"
    )
    for identifier in (other.id, str(legacy), str(uuid4()), "not-a-uuid"):
        with pytest.raises(ManagementFailure, match="not_found"):
            reads.read_reference_lifecycle(Reader(), identifier)
    foreign_reads = CanonicalManagementReads(service.engine, foreign.workspace_id)
    with pytest.raises(ManagementFailure, match="not_found"):
        foreign_reads.read_reference_lifecycle(Reader(), created.id)
    assert counts(owner, created.id) == before


def test_reference_lifecycle_empty_bank_is_explicit_not_missing_permission(database):
    service, _, _ = database
    created = create(service)
    detail = CanonicalManagementReads(
        service.engine, service.workspace_id
    ).read_reference_lifecycle(Reader(), created.id)
    assert detail.model_dump() == {
        "project_id": created.id,
        "project_revision": 1,
        "references": [],
    }


def test_reference_lifecycle_snapshot_does_not_lock_or_mix_concurrent_withdrawal(database):
    service, owner, _ = database
    created, _, approval = acceptance_inputs(service, owner)
    accept(service, created.id, approval)
    writes = []

    def retire(connection, cursor, statement, parameters, context, executemany):
        if "FROM reference_example JOIN source_asset" in statement and not writes:
            writes.append(True)
            withdraw(service, created.id, withdrawal_payload(approval, 4))

    event.listen(service.engine, "after_cursor_execute", retire)
    try:
        reads = CanonicalManagementReads(service.engine, service.workspace_id)
        snapshot = reads.read_reference_lifecycle(Reader(), created.id)
    finally:
        event.remove(service.engine, "after_cursor_execute", retire)
    assert writes == [True]
    assert snapshot.project_revision == 4 and snapshot.references[0].state == "accepted"
    current = reads.read_reference_lifecycle(Reader(), created.id)
    assert current.project_revision == 5 and current.references[0].state == "withdrawn"
    assert current.references[0].last_approval_revision == 4


def test_reference_revalidation_preserves_history_and_binds_current_context(database):
    service, owner, _ = database
    created, _, payload = revalidation_inputs(service, owner)
    with owner.connect() as connection:
        before = connection.execute(
            select(reference_example).where(reference_example.c.id == payload.reference_id)
        ).one()
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        reads.read_project(Reader(), created.id)
    result = revalidate(service, created.id, payload)
    assert result.revision == 6 and result.state == "draft"
    assert revalidate(service, created.id, payload) == result
    with pytest.raises(ManagementFailure, match="idempotency_conflict"):
        revalidate(service, created.id, payload.model_copy(update={"reason": "Changed"}))
    detail = reads.read_project(Reader(), created.id)
    assert detail.references[0].approval == "admin_approved"
    assert detail.approved_steps == [] and detail.published_release_id is None
    with owner.connect() as connection:
        after = connection.execute(
            select(reference_example).where(reference_example.c.id == payload.reference_id)
        ).one()
        assert after == before
        receipt = connection.execute(
            select(canonical_event).where(
                canonical_event.c.aggregate_id == created.id,
                canonical_event.c.event_type == "management.revalidate_reference",
            )
        ).one()
        assert receipt.actor_kind == "human" and receipt.actor_id == Actor().id
        assert receipt.payload["evidence"]["previous_approval_revision"] == 4
        assert receipt.payload["evidence"]["reason"] == payload.reason
        assert receipt.payload["evidence"]["reference"] == detail.references[0].model_dump()
    assert counts(owner, created.id)[2] == 6


@pytest.mark.parametrize("same_key", [True, False])
def test_reference_revalidation_serializes_retries_and_revision(database, same_key):
    service, owner, _ = database
    created, _, payload = revalidation_inputs(service, owner)
    barrier = Barrier(2, timeout=10)

    def submit(key):
        barrier.wait()
        try:
            return revalidate(
                service, created.id, payload, key="same" if same_key else key
            ).revision
        except ManagementFailure as error:
            return error.error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ("left", "right")))
    assert outcomes.count(6) == (2 if same_key else 1)
    assert outcomes.count("revision_conflict") == (0 if same_key else 1)
    assert counts(owner, created.id)[2] == 6


@pytest.mark.parametrize("actor", [Actor(kind="service"), Actor(role="reviewer"), Actor(id="")])
def test_reference_revalidation_refuses_nonadmin(database, actor):
    service, owner, _ = database
    created, _, payload = revalidation_inputs(service, owner)
    with pytest.raises(ManagementFailure, match="permission_denied"):
        revalidate(service, created.id, payload, actor=actor)
    assert counts(owner, created.id)[2] == 5


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("previous_approval_revision", 3, "revision_conflict"),
        ("base_ontology_id", "missing", "revision_conflict"),
        ("assignment_version", "missing", "missing_evidence"),
        ("reference_id", "missing", "not_found"),
    ],
)
def test_reference_revalidation_checks_explicit_pins(database, field, value, code):
    service, owner, _ = database
    created, _, payload = revalidation_inputs(service, owner)
    with pytest.raises(ManagementFailure, match=code):
        revalidate(service, created.id, payload.model_copy(update={field: value}))
    assert counts(owner, created.id)[2] == 5


@pytest.mark.parametrize("state", ["proposed", "withdrawn", "unchanged"])
def test_reference_revalidation_never_reactivates_or_repeats_unchanged_approval(database, state):
    service, owner, _ = database
    created, _, approval = acceptance_inputs(service, owner)
    revision = 3
    if state != "proposed":
        accept(service, created.id, approval)
        revision = 4
    if state == "withdrawn":
        withdraw(service, created.id, withdrawal_payload(approval))
        revision = 5
    payload = ReferenceRevalidate(
        **approval.model_dump(exclude={"expected_revision"}),
        expected_revision=revision,
        previous_approval_revision=4,
    )
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        revalidate(service, created.id, payload)
    assert counts(owner, created.id)[2] == revision


@pytest.mark.parametrize("change", ["train", "test", "unlocked", "pending", "class-draft"])
def test_reference_revalidation_rechecks_current_grouping_and_identity(database, change):
    service, owner, _ = database
    created, source, payload = revalidation_inputs(service, owner)
    if change == "class-draft":
        save_catalog(
            service,
            created.id,
            ClassCatalogDraft(
                expected_revision=5,
                base_ontology_id=payload.base_ontology_id,
                classes=[
                    {
                        "stable_id": "package",
                        "name": "Parcel",
                        "definition": "Visible parcel",
                        "aliases": [],
                        "parent_id": None,
                        "positive_examples": [],
                        "exclusions": [],
                        "mirror_safe": False,
                    }
                ],
            ),
        )
        payload = payload.model_copy(update={"expected_revision": 6})
    elif change == "pending":
        with owner.begin() as connection:
            connection.execute(
                insert(borderline_review).values(
                    left_asset_id=source.id,
                    right_asset_id=source.id,
                    metric="phash",
                    value="7",
                    algorithm_version="fixture-v1",
                    review_state="pending",
                )
            )
    else:
        with owner.begin() as connection:
            connection.execute(
                insert(source_split_assignment).values(
                    assignment_version="assignment-2",
                    asset_id=source.id,
                    group_id="group-1",
                    split="test" if change == "test" else "train",
                    seed=7,
                    algorithm="union-find-grouped-stratified-v2",
                    thresholds={"phash_max_distance": 8},
                    reason="New assignment",
                    locked=change != "unlocked",
                )
            )
        payload = payload.model_copy(update={"assignment_version": "assignment-2"})
    if change == "train":
        assert revalidate(service, created.id, payload).revision == 6
        assert (
            CanonicalManagementReads(service.engine, service.workspace_id)
            .read_project(Reader(), created.id)
            .references[0]
            .approval
            == "admin_approved"
        )
    else:
        with pytest.raises(ManagementFailure, match="missing_evidence"):
            revalidate(service, created.id, payload)
        assert counts(owner, created.id)[2] == payload.expected_revision


@pytest.mark.parametrize(
    "privilege", ["INSERT ON canonical_event", "UPDATE (access_policy) ON project"]
)
def test_reference_revalidation_rolls_back_atomic_receipt_and_invalidation(database, privilege):
    service, owner, role = database
    created, _, payload = revalidation_inputs(service, owner)
    with owner.begin() as connection:
        connection.execute(text(f'REVOKE {privilege} FROM "{role}"'))
    with pytest.raises(DBAPIError):
        revalidate(service, created.id, payload)
    assert counts(owner, created.id)[2] == 5
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_project(
            Reader(), created.id
        )
    with owner.begin() as connection:
        connection.execute(text(f'GRANT {privilege} TO "{role}"'))
    assert revalidate(service, created.id, payload).revision == 6


def test_reference_revalidation_chain_and_withdrawal_preserve_read_only_history(database):
    service, owner, role = database
    created, _, payload = revalidation_inputs(service, owner)
    revalidate(service, created.id, payload)
    save_guide(service, created.id, guide_payload(6))
    second = payload.model_copy(update={"expected_revision": 7, "previous_approval_revision": 6})
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        revalidate(
            service,
            created.id,
            second.model_copy(update={"previous_approval_revision": 4}),
            key="old-pin",
        )
    assert revalidate(service, created.id, second, key="renew-again").revision == 8
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    with owner.begin() as connection:
        connection.execute(text(f'REVOKE INSERT ON canonical_event FROM "{role}"'))
        connection.execute(
            text(f'REVOKE UPDATE (scope, domain, access_policy) ON project FROM "{role}"')
        )
        connection.execute(text(f'REVOKE UPDATE (approved) ON reference_example FROM "{role}"'))
    assert reads.read_project(Reader(), created.id).references[0].approval == "admin_approved"
    with owner.begin() as connection:
        connection.execute(text(f'GRANT INSERT ON canonical_event TO "{role}"'))
        connection.execute(text(f'GRANT UPDATE (access_policy) ON project TO "{role}"'))
        connection.execute(text(f'GRANT UPDATE (approved) ON reference_example TO "{role}"'))
    withdraw(service, created.id, withdrawal_payload(payload, 8))
    detail = CanonicalManagementReads(service.engine, service.workspace_id).read_project(
        Reader(), created.id
    )
    assert detail.references[0].approval == "withdrawn"
    assert counts(owner, created.id)[2] == 9


def test_reference_revalidation_enforces_workspace_ownership_and_context(database):
    service, owner, _ = database
    created, _, payload = revalidation_inputs(service, owner)
    with pytest.raises(ManagementFailure, match="not_found"):
        revalidate(replace(service, workspace_id=uuid4()), created.id, payload)
    other = create(service, key="other")
    with pytest.raises(ManagementFailure, match="not_found"):
        revalidate(
            service,
            other.id,
            payload.model_copy(update={"expected_revision": 1}),
            key="foreign-ref",
        )
    context = MutationContext.bind(Actor(), "renew", "revalidate_reference", created.id, payload)
    with pytest.raises(ManagementFailure, match="invalid_request"):
        service.revalidate_reference(Actor(), replace(context, actor_id="forged"), payload)
    assert counts(owner, created.id)[2] == 5


def test_reference_revalidation_cannot_launder_a_historical_holdout(database):
    service, owner, _ = database
    created, source, payload = revalidation_inputs(service, owner)
    for version, split in (("assignment-2", "test"), ("assignment-3", "train")):
        with owner.begin() as connection:
            connection.execute(
                insert(source_split_assignment).values(
                    assignment_version=version,
                    asset_id=source.id,
                    group_id="group-1",
                    split=split,
                    seed=7,
                    algorithm="union-find-grouped-stratified-v2",
                    thresholds={"phash_max_distance": 8},
                    reason="Changed assignment",
                    locked=True,
                )
            )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        revalidate(
            service, created.id, payload.model_copy(update={"assignment_version": "assignment-3"})
        )
    assert counts(owner, created.id)[2] == 5


@pytest.mark.parametrize("change", ["scope", "guide", "sources"])
def test_reference_revalidation_never_falls_back_to_earlier_approval(database, change):
    service, owner, _ = database
    created, _, payload = revalidation_inputs(service, owner)
    revalidate(service, created.id, payload)
    if change == "scope":
        save_scope(
            service,
            created.id,
            scope_payload(6).model_copy(update={"scope": "New target scope"}),
            key="scope-again",
        )
    elif change == "guide":
        save_guide(service, created.id, guide_payload(6))
    else:
        save_sources(service, created.id, source_policy_payload(6))
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_project(
            Reader(), created.id
        )
    assert counts(owner, created.id)[2] == 7


def test_reference_revalidation_and_withdrawal_share_the_project_lock(database):
    service, owner, _ = database
    created, _, payload = revalidation_inputs(service, owner)
    barrier = Barrier(2, timeout=10)

    def submit(operation):
        barrier.wait()
        try:
            result = (
                revalidate(service, created.id, payload)
                if operation == "renew"
                else withdraw(service, created.id, withdrawal_payload(payload, 5))
            )
            return result.revision
        except ManagementFailure as error:
            return error.error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ("renew", "withdraw")))
    assert outcomes.count(6) == outcomes.count("revision_conflict") == 1
    detail = CanonicalManagementReads(service.engine, service.workspace_id).read_project(
        Reader(), created.id
    )
    assert detail.references[0].approval in {"admin_approved", "withdrawn"}
    assert counts(owner, created.id)[2] == 6


@pytest.mark.parametrize("field,value", [("approved", False), ("evidence_provenance", "{}")])
def test_reference_revalidation_refuses_persisted_history_mismatch(database, field, value):
    service, owner, _ = database
    created, _, payload = revalidation_inputs(service, owner)
    with owner.begin() as connection:
        connection.execute(
            reference_example.update()
            .where(reference_example.c.id == payload.reference_id)
            .values(**{field: value})
        )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        revalidate(service, created.id, payload)
    assert counts(owner, created.id)[2] == 5


def withdraw(service, project_id, payload, actor=None, key="withdraw-reference"):
    actor = actor or Actor()
    context = MutationContext.bind(actor, key, "withdraw_reference", project_id, payload)
    return service.withdraw_reference(actor, context, payload)


def withdrawal_payload(acceptance, revision=4):
    return ReferenceWithdraw(
        expected_revision=revision,
        reference_id=acceptance.reference_id,
        reason="No longer valid for the current scope",
    )


@pytest.mark.parametrize("accepted", [True, False])
def test_reference_withdrawal_preserves_history_and_revokes_approval(database, accepted):
    service, owner, _ = database
    created, _, approval = acceptance_inputs(service, owner)
    if accepted:
        accept(service, created.id, approval)
    payload = withdrawal_payload(approval, 4 if accepted else 3)
    with owner.connect() as connection:
        provenance = connection.execute(
            select(reference_example.c.evidence_provenance).where(
                reference_example.c.id == payload.reference_id
            )
        ).scalar_one()
    result = withdraw(service, created.id, payload)
    assert result.revision == payload.expected_revision + 1 and result.state == "draft"
    assert withdraw(service, created.id, payload) == result
    with pytest.raises(ManagementFailure, match="idempotency_conflict"):
        withdraw(service, created.id, payload.model_copy(update={"reason": "Different"}))
    for revision in (payload.expected_revision, result.revision):
        with pytest.raises(ManagementFailure, match="revision_conflict"):
            withdraw(
                service,
                created.id,
                payload.model_copy(update={"expected_revision": revision}),
                key=f"again-{revision}",
            )
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    detail = reads.read_project(Reader(), created.id)
    assert detail.references[0].approval == "withdrawn"
    assert detail.approved_steps == [] and detail.published_release_id is None
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        accept(
            service,
            created.id,
            approval.model_copy(update={"expected_revision": result.revision}),
            key="reactivate",
        )
    with owner.connect() as connection:
        row = connection.execute(
            select(reference_example).where(reference_example.c.id == payload.reference_id)
        ).one()
        assert not row.approved and row.evidence_provenance == provenance
        receipt = connection.execute(
            select(canonical_event).where(
                canonical_event.c.aggregate_id == created.id,
                canonical_event.c.event_type == "management.withdraw_reference",
            )
        ).one()
        assert receipt.actor_kind == "human" and receipt.actor_id == Actor().id
        assert receipt.payload["evidence"]["reference"] == detail.references[0].model_dump()
        assert receipt.payload["evidence"]["reason"] == payload.reason
    assert reference_count(owner, created.id) == 1
    assert counts(owner, created.id)[2] == result.revision


@pytest.mark.parametrize("change", ["scope", "guide", "sources", "ontology", "holdout", "pending"])
def test_withdrawal_retires_stale_approval_without_renewing_eligibility(database, change):
    service, owner, _ = database
    created, source, approval = acceptance_inputs(service, owner)
    accept(service, created.id, approval)
    revision = 4
    if change == "scope":
        save_scope(service, created.id, scope_payload(revision))
        revision += 1
    elif change == "guide":
        save_guide(service, created.id, guide_payload(revision))
        revision += 1
    elif change == "sources":
        save_sources(service, created.id, source_policy_payload(revision))
        revision += 1
    else:
        with owner.begin() as connection:
            if change == "ontology":
                connection.execute(
                    insert(ontology_release).values(
                        ontology_key=str(uuid4()),
                        project_id=created.id,
                        version=2,
                        classes=[
                            {"stable_id": "package", "name": "Package", "definition": "Package"}
                        ],
                        class_map={"package": 0},
                    )
                )
            elif change == "holdout":
                connection.execute(
                    insert(source_split_assignment).values(
                        assignment_version="assignment-2",
                        asset_id=source.id,
                        group_id="group-1",
                        split="test",
                        seed=7,
                        algorithm="union-find-grouped-stratified-v2",
                        thresholds={"phash_max_distance": 8},
                        reason="New holdout",
                        locked=True,
                    )
                )
            else:
                connection.execute(
                    insert(borderline_review).values(
                        left_asset_id=source.id,
                        right_asset_id=source.id,
                        metric="phash",
                        value="7",
                        algorithm_version="fixture-v1",
                        review_state="pending",
                    )
                )
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    with pytest.raises(ManagementFailure, match="missing_evidence|revision_conflict"):
        reads.read_project(Reader(), created.id)
    recovery = reads.read_reference_lifecycle(Reader(), created.id)
    assert recovery.project_revision == revision
    assert recovery.references[0].id == approval.reference_id
    assert recovery.references[0].last_approval_revision == 4
    assert recovery.references[0].state == "accepted"
    assert recovery.references[0].eligibility == "not_assessed"
    assert (
        withdraw(
            service,
            created.id,
            ReferenceWithdraw(
                expected_revision=recovery.project_revision,
                reference_id=recovery.references[0].id,
                reason="Retire stale evidence using history-only pins",
            ),
        ).revision
        == revision + 1
    )
    assert reads.read_project(Reader(), created.id).references[0].approval == "withdrawn"
    assert reads.read_freeze_plan(Reader(), created.id).payload is None


@pytest.mark.parametrize("same_key", [True, False])
def test_reference_withdrawal_serializes_project_revision_and_retry(database, same_key):
    service, owner, _ = database
    created, _, approval = acceptance_inputs(service, owner)
    accept(service, created.id, approval)
    payload = withdrawal_payload(approval)
    barrier = Barrier(2, timeout=10)

    def submit(key):
        barrier.wait()
        try:
            return withdraw(service, created.id, payload, key="same" if same_key else key).revision
        except ManagementFailure as error:
            return error.error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ("left", "right")))
    assert outcomes.count(5) == (2 if same_key else 1)
    assert outcomes.count("revision_conflict") == (0 if same_key else 1)
    assert counts(owner, created.id)[2] == 5


@pytest.mark.parametrize("actor", [Actor(kind="service"), Actor(role="reviewer"), Actor(id="")])
def test_reference_withdrawal_refuses_nonadmin(database, actor):
    service, owner, _ = database
    created, _, approval = acceptance_inputs(service, owner)
    with pytest.raises(ManagementFailure, match="permission_denied"):
        withdraw(service, created.id, withdrawal_payload(approval, 3), actor=actor)
    assert counts(owner, created.id)[2] == 3


def test_reference_withdrawal_enforces_scope_and_context_before_replay(database):
    service, owner, _ = database
    created, _, approval = acceptance_inputs(service, owner)
    payload = withdrawal_payload(approval, 3)
    withdraw(service, created.id, payload)
    with pytest.raises(ManagementFailure, match="not_found"):
        withdraw(replace(service, workspace_id=uuid4()), created.id, payload)
    other = create(service, key="other")
    other_payload = payload.model_copy(update={"expected_revision": 1})
    with pytest.raises(ManagementFailure, match="idempotency_conflict"):
        withdraw(service, other.id, other_payload, key="other")
    with pytest.raises(ManagementFailure, match="not_found"):
        withdraw(service, other.id, other_payload, key="other-reference")
    assert counts(owner, other.id)[2] == 1
    context = MutationContext.bind(Actor(), "withdraw", "withdraw_reference", created.id, payload)
    with pytest.raises(ManagementFailure, match="invalid_request"):
        service.withdraw_reference(Actor(), replace(context, actor_id="forged"), payload)
    with pytest.raises(ManagementFailure, match="not_found"):
        withdraw(
            service,
            created.id,
            payload.model_copy(update={"reference_id": "missing", "expected_revision": 4}),
            key="missing",
        )
    assert counts(owner, created.id)[2] == 4


@pytest.mark.parametrize(
    "privilege",
    [
        "INSERT ON canonical_event",
        "UPDATE (approved) ON reference_example",
        "UPDATE (access_policy) ON project",
    ],
)
def test_reference_withdrawal_is_atomic_on_runtime_grant_failure(database, privilege):
    service, owner, role = database
    created, _, approval = acceptance_inputs(service, owner)
    accept(service, created.id, approval)
    payload = withdrawal_payload(approval)
    before = CanonicalManagementReads(service.engine, service.workspace_id).read_project(
        Reader(), created.id
    )
    with owner.begin() as connection:
        connection.execute(text(f'REVOKE {privilege} FROM "{role}"'))
    with pytest.raises(DBAPIError):
        withdraw(service, created.id, payload)
    assert (
        CanonicalManagementReads(service.engine, service.workspace_id).read_project(
            Reader(), created.id
        )
        == before
    )
    with owner.begin() as connection:
        connection.execute(text(f'GRANT {privilege} TO "{role}"'))
    assert withdraw(service, created.id, payload).revision == 5


@pytest.mark.parametrize("tamper", ["approval", "provenance", "forged-withdrawal"])
def test_withdrawal_requires_intact_rows_and_receipt_history(database, tamper):
    service, owner, _ = database
    created, _, approval = acceptance_inputs(service, owner)
    accept(service, created.id, approval)
    payload = withdrawal_payload(approval)
    if tamper == "forged-withdrawal":
        withdraw(service, created.id, payload)
    with owner.begin() as connection:
        connection.execute(
            reference_example.update()
            .where(reference_example.c.id == payload.reference_id)
            .values(
                **(
                    {"evidence_provenance": "{}"}
                    if tamper == "provenance"
                    else {"approved": tamper == "forged-withdrawal"}
                )
            )
        )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_project(
            Reader(), created.id
        )
    if tamper != "forged-withdrawal":
        with pytest.raises(ManagementFailure, match="missing_evidence"):
            withdraw(service, created.id, payload)


def test_withdrawal_clears_setup_approvals_and_qualification(database):
    service, owner, _ = database
    created, _, approval = acceptance_inputs(service, owner)
    save_scope(service, created.id, scope_payload(3))
    approve_step(service, created.id, step_approval(service, created.id))
    with owner.begin() as connection:
        row = connection.execute(select(project).where(project.c.id == created.id)).one()
        connection.execute(
            project.update()
            .where(project.c.id == created.id)
            .values(
                access_policy={**row.access_policy, "auto_accept_qualification": {"id": "obsolete"}}
            )
        )
    withdraw(service, created.id, withdrawal_payload(approval, 5))
    with owner.connect() as connection:
        policy = connection.execute(
            select(project.c.access_policy).where(project.c.id == created.id)
        ).scalar_one()
        assert (
            policy["management_setup_approvals"] == {} and "auto_accept_qualification" not in policy
        )


def test_acceptance_and_withdrawal_cannot_both_win_the_same_revision(database):
    service, owner, _ = database
    created, _, approval = acceptance_inputs(service, owner)
    barrier = Barrier(2, timeout=10)

    def submit(operation):
        barrier.wait()
        try:
            result = (
                accept(service, created.id, approval)
                if operation == "accept"
                else withdraw(service, created.id, withdrawal_payload(approval, 3))
            )
            return result.revision
        except ManagementFailure as error:
            return error.error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ("accept", "withdraw")))
    assert sorted(str(item) for item in outcomes) == ["4", "revision_conflict"]
    detail = CanonicalManagementReads(service.engine, service.workspace_id).read_project(
        Reader(), created.id
    )
    if detail.references[0].approval == "admin_approved":
        withdraw(service, created.id, withdrawal_payload(approval), key="retire-winner")
    else:
        assert detail.references[0].approval == "withdrawn"
    assert counts(owner, created.id)[2] in {4, 5}


def test_withdrawn_reference_reads_are_select_only_and_do_not_enable_freeze(database):
    service, owner, role = database
    created, _, approval = acceptance_inputs(service, owner)
    accept(service, created.id, approval)
    withdraw(service, created.id, withdrawal_payload(approval))
    before = counts(owner, created.id)
    with owner.begin() as connection:
        connection.execute(
            text(f'REVOKE INSERT, UPDATE ON ALL TABLES IN SCHEMA public FROM "{role}"')
        )
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    assert reads.read_project(Reader(), created.id).references[0].approval == "withdrawn"
    assert reads.read_freeze_plan(Reader(), created.id).payload is None
    assert counts(owner, created.id) == before


def source_policy_payload(revision=1):
    return SourcePolicyDraft(
        expected_revision=revision,
        metadata_keys=["session", "sequence", "subject"],
        collection_notes="Independent capture sessions and subjects",
    )


def save_sources(service, project_id, payload=None, actor=None, key="sources"):
    actor = actor or Actor()
    payload = payload or source_policy_payload()
    context = MutationContext.bind(actor, key, "save_source_policy_draft", project_id, payload)
    return service.save_source_policy_draft(actor, context, payload)


def test_source_policy_is_attributed_replayable_and_not_effective_grouping(database):
    service, owner, _ = database
    created = create(service)
    payload = source_policy_payload()
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    assert reads.read_project(Reader(), created.id).source_policy_draft is None
    saved = save_sources(service, created.id, payload)
    assert saved.revision == 2 and saved.state == "draft"
    assert save_sources(service, created.id, payload) == saved
    changed = payload.model_copy(update={"metadata_keys": ["subject"]})
    with pytest.raises(ManagementFailure, match="idempotency_conflict"):
        save_sources(service, created.id, changed)
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        save_sources(service, created.id, payload, key="stale")
    detail = reads.read_project(Reader(), created.id)
    assert detail.source_policy_draft.model_dump() == payload.model_dump(
        exclude={"expected_revision"}
    )
    assert detail.sources.metadata_keys is None and detail.sources.assignment_version is None
    assert detail.sources.images == 0 and detail.sources.groups is None
    assert detail.approved_steps == [] and detail.references is None
    assert detail.published_release_id is None
    with owner.connect() as connection:
        receipt = connection.execute(
            select(canonical_event).where(
                canonical_event.c.aggregate_id == created.id,
                canonical_event.c.event_type == "management.save_source_policy_draft",
            )
        ).one()
        assert receipt.actor_id == Actor().id and receipt.actor_kind == "human"
        assert (
            receipt.payload["evidence"]["source_policy_draft"]
            == detail.source_policy_draft.model_dump()
        )
        assert "publish" in receipt.payload["evidence"]["invalidated_steps"]
    save_sources(
        service, created.id, changed.model_copy(update={"expected_revision": 2}), key="next"
    )
    assert save_sources(service, created.id, payload) == saved
    assert reads.read_project(Reader(), created.id).source_policy_draft.metadata_keys == ["subject"]
    assert counts(owner, created.id) == (0, 0, 3)


@pytest.mark.parametrize("same_key", [True, False])
def test_source_policy_parallel_saves_share_revision_and_retry_locks(database, same_key):
    service, owner, _ = database
    created = create(service)
    barrier = Barrier(2, timeout=10)

    def submit(key):
        barrier.wait()
        try:
            return save_sources(service, created.id, key="same" if same_key else key).revision
        except ManagementFailure as error:
            return error.error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ("left", "right")))
    assert outcomes.count(2) == (2 if same_key else 1)
    assert outcomes.count("revision_conflict") == (0 if same_key else 1)
    assert counts(owner, created.id) == (0, 0, 2)


def test_source_policy_preserves_other_drafts_and_shared_revision_order(database):
    service, owner, _ = database
    created = create(service)
    catalog = catalog_payload(service, created.id, 2)
    save_scope(service, created.id)
    save_catalog(service, created.id, catalog)
    save_guide(service, created.id, guide_payload(3))
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        save_sources(service, created.id)
    save_sources(service, created.id, source_policy_payload(4))
    save_scope(service, created.id, scope_payload(5), key="next-scope")
    detail = CanonicalManagementReads(service.engine, service.workspace_id).read_project(
        Reader(), created.id
    )
    assert detail.resource.revision == 6 and detail.approved_steps == []
    assert detail.class_catalog_draft.classes[0].name == "Parcel"
    assert detail.name == scope_payload().name and detail.guide.min_target_px == 4.0
    assert detail.source_policy_draft.metadata_keys == ["session", "sequence", "subject"]
    assert counts(owner, created.id) == (0, 0, 6)


def test_source_policy_audit_failure_rolls_back_and_retry_succeeds(database):
    service, owner, role = database
    created = create(service)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    before = reads.read_project(Reader(), created.id)
    with owner.begin() as connection:
        connection.execute(text(f'REVOKE INSERT ON canonical_event FROM "{role}"'))
    with pytest.raises(DBAPIError):
        save_sources(service, created.id)
    assert reads.read_project(Reader(), created.id) == before
    with owner.begin() as connection:
        connection.execute(text(f'GRANT INSERT ON canonical_event TO "{role}"'))
    assert save_sources(service, created.id).revision == 2


@pytest.mark.parametrize("actor", [Actor(kind="service"), Actor(role="reviewer"), Actor(id="")])
def test_source_policy_denies_nonadmin_principals(database, actor):
    service, owner, _ = database
    created = create(service)
    with pytest.raises(ManagementFailure, match="permission_denied"):
        save_sources(service, created.id, actor=actor)
    assert counts(owner, created.id) == (0, 0, 1)


def test_source_policy_checks_workspace_and_context_before_replay(database):
    service, owner, _ = database
    created = create(service)
    payload = source_policy_payload()
    save_sources(service, created.id, payload)
    with pytest.raises(ManagementFailure, match="not_found"):
        save_sources(replace(service, workspace_id=uuid4()), created.id, payload)
    with pytest.raises(ManagementFailure, match="not_found"):
        save_sources(service, "not-a-uuid", payload)
    context = MutationContext.bind(
        Actor(), "sources", "save_source_policy_draft", created.id, payload
    )
    with pytest.raises(ManagementFailure, match="invalid_request"):
        service.save_source_policy_draft(Actor(), replace(context, actor_id="forged"), payload)
    assert counts(owner, created.id) == (0, 0, 2)


def test_source_policy_invalidates_acceptance_not_historical_grouping_or_release(database):
    service, owner, _ = database
    created, _, _, freeze_payload = release_inputs(service, owner)
    frozen = freeze(service, created.id, freeze_payload)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    before = reads.read_project(Reader(), created.id).sources
    with owner.begin() as connection:
        row = connection.execute(select(project).where(project.c.id == created.id)).one()
        policy = {
            **row.access_policy,
            "auto_accept_qualification": {"qualified": True},
            "management_setup_approvals": {"sources": "old"},
        }
        connection.execute(
            project.update().where(project.c.id == row.id).values(access_policy=policy)
        )
    save_sources(service, created.id, source_policy_payload(3))
    with owner.connect() as connection:
        policy = connection.execute(
            select(project.c.access_policy).where(project.c.id == created.id)
        ).scalar_one()
        assert (
            "auto_accept_qualification" not in policy and policy["management_setup_approvals"] == {}
        )
    detail = reads.read_project(Reader(), created.id)
    assert detail.sources == before and detail.sources.metadata_keys is None
    assert reads.read_release(Reader(), frozen.id).resource == frozen
    assert reads.read_freeze_plan(Reader(), created.id).payload is None
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        freeze(
            service,
            created.id,
            freeze_payload.model_copy(update={"expected_revision": 4}),
            key="new",
        )


@pytest.mark.parametrize("tamper", ["no_receipt", "notes", "removed", "approval"])
def test_source_policy_projection_requires_matching_attributed_draft(database, tamper):
    service, owner, _ = database
    created = create(service)
    payload = source_policy_payload()
    if tamper != "no_receipt":
        save_sources(service, created.id, payload)
    with owner.begin() as connection:
        row = connection.execute(select(project).where(project.c.id == created.id)).one()
        policy = dict(row.access_policy)
        policy["management_source_policy_draft"] = payload.model_dump(exclude={"expected_revision"})
        if tamper == "notes":
            policy["management_source_policy_draft"]["collection_notes"] = "Unattributed"
        if tamper == "removed":
            del policy["management_source_policy_draft"]
        if tamper == "approval":
            policy["management_setup_approvals"] = {"sources": "forged"}
        connection.execute(
            project.update().where(project.c.id == row.id).values(access_policy=policy)
        )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_project(
            Reader(), created.id
        )


def guide_payload(revision=1):
    return GuideDraft(
        expected_revision=revision,
        annotation_guide="Visible package extents, no hidden-extent inference",
        extent="visible",
        truncation="Clip at image edges",
        occlusion="Label visible extent only",
        reflections="Out of scope",
        min_target_px=4.0,
        exhaustive_for_scope=True,
        background="Explicitly out-of-scope objects only",
    )


def save_guide(service, project_id, payload=None, actor=None, key="guide"):
    actor = actor or Actor()
    payload = payload or guide_payload()
    context = MutationContext.bind(actor, key, "save_guide_draft", project_id, payload)
    return service.save_guide_draft(actor, context, payload)


def test_guide_save_replays_and_projects_attributed_policy_without_approval(database):
    service, owner, _ = database
    created = create(service)
    payload = guide_payload()
    result = save_guide(service, created.id, payload)
    assert result.revision == 2 and result.state == "draft"
    assert save_guide(service, created.id, payload) == result
    with pytest.raises(ManagementFailure, match="idempotency_conflict"):
        save_guide(service, created.id, payload.model_copy(update={"min_target_px": 8.0}))
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        save_guide(service, created.id, key="stale")
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    detail = reads.read_project(Reader(), created.id)
    assert detail.resource == result
    assert detail.annotation_guide == payload.annotation_guide
    assert detail.guide.model_dump() == payload.model_dump(
        exclude={"expected_revision", "annotation_guide"}
    )
    assert detail.approved_steps == [] and detail.published_release_id is None
    assert detail.name is None and detail.references is None
    with owner.connect() as connection:
        receipt = connection.execute(
            select(canonical_event).where(
                canonical_event.c.aggregate_id == created.id,
                canonical_event.c.event_type == "management.save_guide_draft",
            )
        ).one()
        assert receipt.actor_kind == "human" and receipt.actor_id == Actor().id
        assert receipt.payload["evidence"]["guide_draft"] == payload.model_dump(
            exclude={"expected_revision"}
        )
        assert "publish" in receipt.payload["evidence"]["invalidated_steps"]
    assert counts(owner, created.id) == (0, 0, 2)
    next_payload = guide_payload(2).model_copy(update={"min_target_px": 8.0})
    assert save_guide(service, created.id, next_payload, key="next").revision == 3
    assert save_guide(service, created.id, payload) == result
    assert reads.read_project(Reader(), created.id).guide.min_target_px == 8.0


@pytest.mark.parametrize("same_key", [True, False])
def test_parallel_guide_saves_use_shared_aggregate_and_retry_locks(database, same_key):
    service, owner, _ = database
    created = create(service)
    barrier = Barrier(2, timeout=10)

    def submit(key):
        barrier.wait()
        try:
            return save_guide(service, created.id, key="same" if same_key else key).revision
        except ManagementFailure as error:
            return error.error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ("left", "right")))
    assert outcomes.count(2) == (2 if same_key else 1)
    assert outcomes.count("revision_conflict") == (0 if same_key else 1)
    assert counts(owner, created.id) == (0, 0, 2)


def test_guide_and_scope_saves_preserve_both_drafts_in_shared_revision_order(database):
    service, owner, _ = database
    created = create(service)
    save_scope(service, created.id)
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        save_guide(service, created.id)
    save_guide(service, created.id, guide_payload(2))
    save_scope(service, created.id, scope_payload(3), key="scope-next")
    detail = CanonicalManagementReads(service.engine, service.workspace_id).read_project(
        Reader(), created.id
    )
    assert detail.resource.revision == 4
    assert detail.name == scope_payload().name
    assert detail.guide.min_target_px == guide_payload().min_target_px
    assert detail.approved_steps == []
    assert counts(owner, created.id) == (0, 0, 4)


def test_guide_save_audit_failure_rolls_back_and_same_key_retry_succeeds(database):
    service, owner, role = database
    created = create(service)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    before = reads.read_project(Reader(), created.id)
    with owner.begin() as connection:
        connection.execute(text(f'REVOKE INSERT ON canonical_event FROM "{role}"'))
    with pytest.raises(DBAPIError):
        save_guide(service, created.id)
    assert reads.read_project(Reader(), created.id) == before
    with owner.begin() as connection:
        connection.execute(text(f'GRANT INSERT ON canonical_event TO "{role}"'))
    assert save_guide(service, created.id).revision == 2


@pytest.mark.parametrize("actor", [Actor(kind="service"), Actor(role="reviewer"), Actor(id="")])
def test_guide_save_denies_nonadmin_principals(database, actor):
    service, owner, _ = database
    created = create(service)
    with pytest.raises(ManagementFailure, match="permission_denied"):
        save_guide(service, created.id, actor=actor)
    assert counts(owner, created.id) == (0, 0, 1)


def test_guide_save_checks_workspace_and_context_on_replay(database):
    service, owner, _ = database
    created = create(service)
    save_guide(service, created.id)
    with pytest.raises(ManagementFailure, match="not_found"):
        save_guide(replace(service, workspace_id=uuid4()), created.id)
    with pytest.raises(ManagementFailure, match="not_found"):
        save_guide(service, "not-a-uuid")
    payload = guide_payload()
    context = MutationContext.bind(Actor(), "guide", "save_guide_draft", created.id, payload)
    with pytest.raises(ManagementFailure, match="invalid_request"):
        service.save_guide_draft(Actor(), replace(context, actor_id="forged"), payload)
    assert counts(owner, created.id) == (0, 0, 2)


@pytest.mark.parametrize("same_text", [True, False])
def test_guide_save_invalidates_auto_accept_and_freeze_but_not_historical_release(
    database, same_text
):
    service, owner, _ = database
    created, _, _, payload = release_inputs(service, owner)
    frozen = freeze(service, created.id, payload)
    with owner.begin() as connection:
        row = connection.execute(select(project).where(project.c.id == created.id)).one()
        policy = {**row.access_policy, "auto_accept_qualification": {"qualified": True}}
        policy["management_setup_approvals"] = {"guide": "old-approval"}
        connection.execute(
            project.update().where(project.c.id == row.id).values(access_policy=policy)
        )
    draft = guide_payload(3)
    if same_text:
        draft = draft.model_copy(update={"annotation_guide": row.access_policy["annotation_guide"]})
    save_guide(service, created.id, draft)
    with owner.connect() as connection:
        policy = connection.execute(
            select(project.c.access_policy).where(project.c.id == created.id)
        ).scalar_one()
        assert "auto_accept_qualification" not in policy
        assert policy["management_setup_approvals"] == {}
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    assert reads.read(Reader(), "dataset-release", frozen.id) == frozen
    assert reads.read_project(Reader(), created.id).approved_steps == []
    assert reads.read_freeze_plan(Reader(), created.id).payload is None
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        freeze(service, created.id, payload.model_copy(update={"expected_revision": 4}), key="new")


@pytest.mark.parametrize("tamper", ["no_receipt", "policy", "text", "removed", "approval"])
def test_guide_projection_requires_matching_attributed_draft(database, tamper):
    service, owner, _ = database
    created = create(service)
    if tamper != "no_receipt":
        save_guide(service, created.id)
    with owner.begin() as connection:
        row = connection.execute(select(project).where(project.c.id == created.id)).one()
        policy = dict(row.access_policy)
        draft = guide_payload().model_dump(exclude={"expected_revision"})
        policy["management_guide_draft"] = draft
        if tamper == "policy":
            draft["min_target_px"] = 8.0
        if tamper == "text":
            policy["annotation_guide"] = "Unattributed guide"
        if tamper == "removed":
            del policy["management_guide_draft"]
        if tamper == "approval":
            policy["management_setup_approvals"] = {"guide": "forged"}
        connection.execute(
            project.update().where(project.c.id == row.id).values(access_policy=policy)
        )
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        reads.read_project(Reader(), created.id)


def test_create_replay_and_conflict_preserve_single_canonical_ontology(database):
    service, owner, _ = database
    payload = pack()
    result = create(service, payload)
    assert create(service, payload) == result
    with owner.connect() as connection:
        row = connection.execute(select(project).where(project.c.id == result.id)).one()
        assert str(row.workspace_id) == str(service.workspace_id)
        assert row.access_policy["autonomous_spend"] is False
        assert row.access_policy["external_llm_providers_allowed"] == []
        ontologies = connection.execute(
            select(ontology_release).where(ontology_release.c.project_id == result.id)
        ).all()
        assert len(ontologies) == 1
        assert ontologies[0].class_map == {"package": 0}
    assert counts(owner, result.id) == (0, 0, 1)
    with pytest.raises(ManagementFailure, match="idempotency_conflict"):
        create(service, payload.model_copy(update={"scope": "Changed"}))
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        create(service, payload, actor=Actor(id="another-admin"))
    # The same retry key under another authenticated actor is not a replay.
    other = create(service, pack(), actor=Actor(id="another-admin"))
    assert other.id != result.id


def test_parallel_identical_ingest_replays_once_and_normalizes_source(database):
    service, owner, _ = database
    created = create(service)
    barrier = Barrier(2, timeout=10)

    def submit(_):
        barrier.wait()
        return ingest(service, created.id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert results[0] == results[1]
    assert counts(owner, created.id) == (1, 1, 2)
    with owner.connect() as connection:
        row = connection.execute(
            select(source_asset).where(source_asset.c.asset_key == results[0].id)
        ).one()
        assert (row.width, row.height, row.exif_orientation) == (24, 12, 6)
        stored = service.store.root / row.object_key
        assert stored.is_file()
        event = connection.execute(
            select(canonical_event).where(
                canonical_event.c.aggregate_id == created.id,
                canonical_event.c.event_type == "management.ingest_asset",
            )
        ).one()
        assert event.actor_id == Actor().id
        assert event.payload["aggregate_revision"] == 2
        assert event.payload["evidence"]["source_group"] == "capture-a"
        assert event.payload["evidence"]["imaging_toolchain"]["pillow"]
    # Replays precede the stale expected-project-revision check, but changed
    # request bodies never replay, and a fresh key must use the new revision.
    assert ingest(service, created.id) == results[0]
    with pytest.raises(ManagementFailure, match="idempotency_conflict"):
        ingest(service, created.id, image_payload(2))
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        ingest(service, created.id, key="stale")
    ingest(service, created.id, image_payload(2), key="next")
    assert counts(owner, created.id) == (2, 2, 3)


def test_parallel_different_keys_cannot_both_commit_the_same_revision(database):
    service, owner, _ = database
    created = create(service)
    barrier = Barrier(2, timeout=10)

    def submit(key):
        barrier.wait()
        try:
            return ingest(service, created.id, key=key).kind
        except ManagementFailure as error:
            return error.error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ("left", "right")))
    assert sorted(outcomes) == ["asset", "revision_conflict"]
    assert counts(owner, created.id) == (1, 1, 2)


def test_event_failure_rolls_back_project_and_ontology(database):
    service, owner, role = database
    payload = pack()
    with owner.begin() as connection:
        connection.execute(text(f'REVOKE INSERT ON canonical_event FROM "{role}"'))
    try:
        with pytest.raises(DBAPIError):
            create(service, payload)
        with owner.connect() as connection:
            assert (
                connection.execute(
                    select(project.c.id).where(project.c.project_key == payload.project_key)
                ).scalar_one_or_none()
                is None
            )
    finally:
        with owner.begin() as connection:
            connection.execute(text(f'GRANT INSERT ON canonical_event TO "{role}"'))
    result = create(service, payload)
    assert counts(owner, result.id) == (0, 0, 1)


def test_storage_failure_or_bad_image_commits_no_revision_and_allows_retry(database):
    service, owner, _ = database
    created = create(service)

    class BrokenStore:
        def put(self, key, data):
            raise OSError("fixture storage unavailable")

    with pytest.raises(OSError):
        ingest(replace(service, store=BrokenStore()), created.id)
    assert counts(owner, created.id) == (0, 0, 1)
    invalid = AssetIngest(expected_revision=1, original_base64="YmFk", source_group="capture-a")
    with pytest.raises(ManagementFailure, match="invalid_request"):
        ingest(service, created.id, invalid)
    assert counts(owner, created.id) == (0, 0, 1)
    ingest(service, created.id)
    assert counts(owner, created.id) == (1, 1, 2)


def test_runtime_event_mutation_is_denied_and_owner_connection_fails_closed(database):
    service, owner, _ = database
    created = create(service)
    for statement in (
        "UPDATE canonical_event SET actor_id = 'changed' WHERE aggregate_id = :id",
        "DELETE FROM canonical_event WHERE aggregate_id = :id",
        "TRUNCATE canonical_event",
    ):
        with pytest.raises(DBAPIError), service.engine.begin() as connection:
            connection.execute(text(statement), {"id": created.id})
    with pytest.raises(ManagementFailure, match="management_unavailable"):
        create(replace(service, engine=owner), key="unsafe-role")
    assert counts(owner, created.id) == (0, 0, 1)


def test_repeatable_snapshot_engine_is_refused_before_any_write(database):
    service, owner, _ = database
    engine = service.engine.execution_options(isolation_level="REPEATABLE READ")
    payload = pack()
    with pytest.raises(ManagementFailure, match="management_unavailable"):
        create(replace(service, engine=engine), payload)
    with owner.connect() as connection:
        assert (
            connection.execute(
                select(project.c.id).where(project.c.project_key == payload.project_key)
            ).scalar_one_or_none()
            is None
        )


def test_resource_scope_and_context_are_checked_before_writes_or_replays(database):
    service, owner, _ = database
    created = create(service)
    accepted = ingest(service, created.id)
    with pytest.raises(ManagementFailure, match="not_found"):
        ingest(replace(service, workspace_id=uuid4()), created.id)
    with pytest.raises(ManagementFailure, match="not_found"):
        ingest(service, "not-a-uuid", key="invalid-target")
    for actor in (Actor(kind="service"), Actor(role="reviewer")):
        with pytest.raises(ManagementFailure, match="permission_denied"):
            ingest(service, created.id, actor=actor)
    payload = image_payload()
    context = MutationContext.bind(Actor(), "ingest", "ingest_asset", created.id, payload)
    with pytest.raises(ManagementFailure, match="invalid_request"):
        service.ingest_asset(Actor(), replace(context, actor_id="forged"), payload)
    assert ingest(service, created.id) == accepted
    assert counts(owner, created.id) == (1, 1, 2)


def test_project_detail_select_only_preserves_bootstrap_facts_not_setup_approvals(database):
    service, owner, role = database
    payload = pack()
    created = create(service, payload)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    before = counts(owner, created.id)
    with owner.begin() as connection:
        connection.execute(
            text(f'REVOKE INSERT, UPDATE ON ALL TABLES IN SCHEMA public FROM "{role}"')
        )
    for actor in (Actor(), Reader()):
        detail = reads.read_project(actor, created.id)
        assert detail.resource == created
        assert (detail.scope, detail.domain) == (payload.scope, payload.domain)
        assert detail.annotation_guide == payload.annotation_guide
        assert detail.source_permitted_use == payload.source_permitted_use
        assert detail.external_provider_ids == []
        assert detail.name is detail.representation is detail.intended_use is None
        assert detail.specificity is detail.guide is detail.references is None
        assert detail.approved_steps is detail.published_release_id is None
        assert detail.sources.images == 0
        assert detail.sources.groups is detail.sources.split_counts is None
        assert detail.sources.metadata_keys is detail.sources.assignment_version is None
        assert detail.sources.pending_pairs == []
        assert detail.versions["ontology"] == "1"
        assert detail.saved_at.endswith("Z")
        item = detail.classes[0]
        assert (item.stable_id, item.name, item.definition) == (
            "package",
            "Package",
            "A visible package",
        )
        assert item.aliases == [] and item.parent_id is None
        assert (
            item.approval is item.mirror_safe is item.positive_examples is item.exclusions is None
        )
    assert counts(owner, created.id) == before


@pytest.mark.parametrize(
    "actor", [Reader(kind="service"), Reader(role="worker"), Reader(granted=False)]
)
def test_project_detail_denies_untrusted_principals(database, actor):
    service, _, _ = database
    with pytest.raises(ManagementFailure, match="permission_denied"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_project(
            actor, str(uuid4())
        )


def test_project_detail_hides_foreign_legacy_and_unknown_projects(database):
    service, owner, _ = database
    created = create(service)
    with owner.begin() as connection:
        legacy = connection.execute(
            insert(project)
            .values(
                project_key="legacy-" + uuid4().hex,
                workspace_id=service.workspace_id,
                task="detect",
                scope="Legacy",
                domain="industrial",
            )
            .returning(project.c.id)
        ).scalar_one()
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    foreign = CanonicalManagementReads(service.engine, uuid4())
    for reader, target in [(foreign, created.id), (reads, str(legacy)), (reads, "missing")]:
        with pytest.raises(ManagementFailure, match="not_found"):
            reader.read_project(Reader(), target)


def test_project_detail_uses_current_ontology_assignment_counts_and_pending_pairs(database):
    service, owner, _ = database
    created, asset, source, _ = release_inputs(service, owner)
    second = ingest(service, created.id, image_payload(2), key="second")
    with owner.begin() as connection:
        second_id = connection.execute(
            select(source_asset.c.id).where(source_asset.c.asset_key == second.id)
        ).scalar_one()
        connection.execute(
            insert(ontology_release).values(
                ontology_key="ontology-2",
                project_id=created.id,
                version=2,
                classes=[
                    {
                        "stable_id": "package",
                        "name": "Renamed",
                        "definition": "A package",
                        "aliases": ["Parcel"],
                        "parent_id": None,
                    }
                ],
                class_map={"package": 0},
            )
        )
        connection.execute(
            insert(source_split_assignment).values(
                assignment_version="assignment-1",
                asset_id=second_id,
                group_id="group-1",
                split="val",
                seed=7,
                algorithm="union-find-grouped-stratified-v2",
                thresholds={"phash_max_distance": 8},
                reason="fixture grouping",
                locked=True,
            )
        )
        pair_id = connection.execute(
            insert(borderline_review)
            .values(
                left_asset_id=source.id,
                right_asset_id=second_id,
                metric="phash",
                value="9",
                review_state="pending",
                algorithm_version="fixture",
            )
            .returning(borderline_review.c.id)
        ).scalar_one()
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    detail = reads.read_project(Reader(), created.id)
    assert detail.resource.revision == 3
    assert detail.classes[0].name == "Renamed" and detail.classes[0].aliases == ["Parcel"]
    assert detail.versions["ontology"] == "2"
    assert detail.sources.images == 2 and detail.sources.groups == 1
    assert detail.sources.split_counts == {"train": 0, "val": 2, "test": 0}
    assert detail.sources.assignment_version == "assignment-1"
    assert detail.sources.pending_pairs[0].model_dump() == {
        "id": str(pair_id),
        "left_asset_id": asset.id,
        "right_asset_id": second.id,
    }
    # Pending grouping is not hidden by otherwise materialized split counts;
    # this summary never asserts that freezing or publication can pass.
    assert reads.read_freeze_plan(Reader(), created.id).payload is None
    assert "source_uri" not in detail.model_dump_json()
    assert "object_key" not in detail.model_dump_json()


@pytest.mark.parametrize("mode", ["missing", "unlocked", "mixed", "tie"])
def test_project_detail_does_not_invent_a_current_split_publication(database, mode):
    service, owner, _ = database
    created, _, source, _ = release_inputs(
        service, owner, locked=mode not in {"missing", "unlocked"}
    )
    with owner.begin() as connection:
        if mode == "missing":
            connection.execute(
                source_split_assignment.delete().where(
                    source_split_assignment.c.asset_id == source.id
                )
            )
        if mode in {"mixed", "tie"}:
            if mode == "mixed":
                ingest(service, created.id, image_payload(2), key="second")
            else:
                stamp = connection.execute(
                    select(source_split_assignment.c.created_at).where(
                        source_split_assignment.c.asset_id == source.id
                    )
                ).scalar_one()
                connection.execute(
                    insert(source_split_assignment).values(
                        assignment_version="assignment-tie",
                        asset_id=source.id,
                        group_id="group-1",
                        split="train",
                        seed=7,
                        algorithm="fixture",
                        thresholds={},
                        reason="fixture",
                        locked=True,
                        created_at=stamp,
                    )
                )
    detail = CanonicalManagementReads(service.engine, service.workspace_id).read_project(
        Reader(), created.id
    )
    assert detail.sources.groups is detail.sources.split_counts is None
    assert detail.sources.assignment_version is None
    assert "assignment" not in detail.versions


@pytest.mark.parametrize("missing", ["policy", "class_map"])
def test_project_detail_missing_evidence_fails_closed(database, missing):
    service, owner, _ = database
    created = create(service)
    with owner.begin() as connection:
        if missing == "policy":
            connection.execute(
                project.update()
                .where(project.c.id == created.id)
                .values(access_policy={"internal": "never-reflect-raw-policy"})
            )
        else:
            connection.execute(
                insert(ontology_release).values(
                    ontology_key="invalid-map",
                    project_id=created.id,
                    version=2,
                    classes=[{"stable_id": "package", "name": "Package", "definition": "Package"}],
                    class_map={"other": 0},
                )
            )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_project(
            Reader(), created.id
        )


def test_project_detail_snapshot_does_not_lock_writers_or_mix_later_ingest(database):
    service, _, _ = database
    created = create(service)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    changed = False

    def concurrent_ingest(connection, cursor, statement, parameters, context, executemany):
        nonlocal changed
        if changed or "FROM ontology_release" not in statement:
            return
        changed = True
        ingest(service, created.id)

    event.listen(service.engine, "after_cursor_execute", concurrent_ingest)
    try:
        detail = reads.read_project(Reader(), created.id)
    finally:
        event.remove(service.engine, "after_cursor_execute", concurrent_ingest)
    assert changed and detail.resource.revision == 1 and detail.sources.images == 0
    later = reads.read_project(Reader(), created.id)
    assert later.resource.revision == 2 and later.sources.images == 1


def test_project_detail_ignores_unattributed_approval_and_provider_payload_fields(database):
    service, owner, _ = database
    created = create(service)
    with owner.begin() as connection:
        row = connection.execute(select(project).where(project.c.id == created.id)).one()
        policy = {
            **row.access_policy,
            "approved_steps": ["publish"],
            "classes": [{"approval": "admin_approved"}],
            "provider_payload": {"key": "never-reflect-raw-policy"},
        }
        connection.execute(
            project.update().where(project.c.id == created.id).values(access_policy=policy)
        )
    detail = CanonicalManagementReads(service.engine, service.workspace_id).read_project(
        Reader(), created.id
    )
    assert detail.approved_steps is detail.classes[0].approval is None
    assert "never-reflect-raw-policy" not in detail.model_dump_json()


def test_project_detail_cross_project_pending_relation_fails_without_disclosing_endpoint(database):
    service, owner, _ = database
    first = create(service)
    second = create(service, key="other-project")
    assets = [ingest(service, target.id, key="ingest-" + target.id) for target in (first, second)]
    with owner.begin() as connection:
        ids = [
            connection.execute(
                select(source_asset.c.id).where(source_asset.c.asset_key == asset.id)
            ).scalar_one()
            for asset in assets
        ]
        connection.execute(
            insert(borderline_review).values(
                left_asset_id=ids[0],
                right_asset_id=ids[1],
                metric="phash",
                value="9",
                review_state="pending",
                algorithm_version="fixture",
            )
        )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        CanonicalManagementReads(service.engine, service.workspace_id).read_project(
            Reader(), first.id
        )


def release_inputs(service, owner, *, complete=True, split="val", locked=True):
    created = create(service)
    asset = ingest(service, created.id)
    with owner.begin() as connection:
        source = connection.execute(
            select(source_asset).where(source_asset.c.asset_key == asset.id)
        ).one()
        ontology = connection.execute(
            select(ontology_release).where(ontology_release.c.project_id == created.id)
        ).one()
        connection.execute(
            insert(source_split_assignment).values(
                assignment_version="assignment-1",
                asset_id=source.id,
                group_id="group-1",
                split=split,
                seed=7,
                algorithm="union-find-grouped-stratified-v2",
                thresholds={"phash_max_distance": 8},
                reason="fixture grouping",
                locked=locked,
            )
        )
        if complete:
            connection.execute(
                insert(completeness_assessment).values(
                    asset_id=source.id,
                    revision=1,
                    ontology_hash=completeness_context_hash(
                        ontology.class_map, ontology.classes, "Visible packages"
                    ),
                    scope_completeness="exhaustive_for_scope",
                    actor_kind="human",
                    actor_id=Actor().id,
                    output_truncated=False,
                    unresolved_regions=[],
                )
            )
            append_review(
                connection,
                asset_id=asset.id,
                actor_kind="human",
                actor_id=Actor().id,
                expected_revision=1,
                approval_state="human_accepted",
                review_tier="human_reviewed",
                scope_completeness="exhaustive_for_scope",
                objects=[],
                training_eligible=True,
            )
    payload = ReleaseFreeze(
        expected_revision=2,
        ontology_id=ontology.ontology_key,
        assignment_version="assignment-1",
        annotation_revisions={asset.id: 2 if complete else 1},
    )
    return created, asset, source, payload


def freeze(service, project_id, payload, key="freeze", actor=None):
    actor = actor or Actor()
    context = MutationContext.bind(actor, key, "freeze_release", project_id, payload)
    return service.freeze_release(actor, context, payload)


def release_count(owner, project_id):
    with owner.connect() as connection:
        return connection.execute(
            select(func.count())
            .select_from(canonical_dataset_release)
            .where(canonical_dataset_release.c.project_id == project_id)
        ).scalar_one()


def test_freeze_plan_is_select_only_and_supplies_a_real_freeze_payload(database):
    service, owner, role = database
    created, asset, _, _ = release_inputs(service, owner)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    before = counts(owner, created.id)
    with owner.begin() as connection:
        connection.execute(
            text(f'REVOKE INSERT, UPDATE ON ALL TABLES IN SCHEMA public FROM "{role}"')
        )
    for actor in (Actor(), Reader()):
        plan = reads.read_freeze_plan(actor, created.id)
        assert plan.payload is not None
        assert plan.payload.expected_revision == 2
        assert plan.payload.annotation_revisions == {asset.id: 2}
        assert plan.exclusions == []
        assert plan.checks[0].passed is True
        assert "Not model qualification" in plan.checks[0].reason
        assert set(plan.model_dump()) == {"project_id", "payload", "checks", "exclusions"}
    assert counts(owner, created.id) == before
    assert release_count(owner, created.id) == 0
    # Restore only the fixture's known grants, then consume the actual plan.
    with owner.begin() as connection:
        connection.execute(
            text(f'GRANT INSERT ON canonical_event, canonical_dataset_release TO "{role}"')
        )
        connection.execute(
            text(f'GRANT UPDATE (scope, domain, access_policy) ON project TO "{role}"')
        )
        connection.execute(text(f'GRANT UPDATE (source_uri) ON source_asset TO "{role}"'))
    assert freeze(service, created.id, plan.payload).state == "frozen"


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({"complete": False}, "incomplete_labels"),
        ({"split": "train"}, "incomplete_labels"),
        ({"locked": False}, "missing_evidence"),
    ],
)
def test_freeze_plan_reports_unready_without_inventing_membership(database, options, reason):
    service, owner, _ = database
    created, _, _, _ = release_inputs(service, owner, **options)
    plan = CanonicalManagementReads(service.engine, service.workspace_id).read_freeze_plan(
        Reader(), created.id
    )
    assert plan.payload is None
    assert plan.checks[0].passed is False
    assert plan.checks[0].reason == reason
    assert release_count(owner, created.id) == 0


@pytest.mark.parametrize(
    "actor", [Reader(kind="service"), Reader(role="worker"), Reader(granted=False), Reader(id="")]
)
def test_freeze_plan_denies_untrusted_principals(database, actor):
    service, _, _ = database
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    with pytest.raises(ManagementFailure, match="permission_denied"):
        reads.read_freeze_plan(actor, str(uuid4()))


def test_freeze_plan_ownership_and_legacy_isolation(database):
    service, owner, _ = database
    created = create(service)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    with owner.begin() as connection:
        legacy = connection.execute(
            insert(project)
            .values(
                project_key="legacy-" + uuid4().hex,
                workspace_id=service.workspace_id,
                task="detect",
                scope="legacy",
                domain="fixture",
                access_policy={},
            )
            .returning(project.c.id)
        ).scalar_one()
    for reader, target in (
        (replace(reads, workspace_id=uuid4()), created.id),
        (reads, str(uuid4())),
        (reads, "not-a-uuid"),
        (reads, str(legacy)),
    ):
        with pytest.raises(ManagementFailure, match="not_found"):
            reader.read_freeze_plan(Reader(), target)
    empty = reads.read_freeze_plan(Reader(), created.id)
    assert empty.payload is None and empty.checks[0].reason == "missing_evidence"


def test_freeze_plan_excludes_unreviewed_sources_but_validates_all_grouping(database):
    service, owner, _ = database
    created, asset, _, _ = release_inputs(service, owner)
    other = ingest(service, created.id, image_payload(2), key="unreviewed")
    with owner.begin() as connection:
        source_id = connection.execute(
            select(source_asset.c.id).where(source_asset.c.asset_key == other.id)
        ).scalar_one()
        connection.execute(
            insert(source_split_assignment).values(
                assignment_version="assignment-1",
                asset_id=source_id,
                group_id="group-1",
                split="val",
                seed=7,
                algorithm="union-find-grouped-stratified-v2",
                thresholds={"phash_max_distance": 8},
                reason="fixture grouping",
                locked=True,
            )
        )
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    plan = reads.read_freeze_plan(Reader(), created.id)
    assert plan.payload is not None
    assert plan.payload.annotation_revisions == {asset.id: 2}
    assert plan.exclusions[0].asset_ids == [other.id]
    assert plan.exclusions[0].reason == "incomplete_labels"
    with owner.begin() as connection:
        connection.execute(
            insert(borderline_review).values(
                left_asset_id=source_id,
                right_asset_id=source_id,
                metric="phash",
                value="9",
                review_state="pending",
                algorithm_version="fixture",
            )
        )
    denied = reads.read_freeze_plan(Reader(), created.id)
    assert denied.payload is None and denied.checks[0].reason == "missing_evidence"


def test_freeze_plan_uses_one_snapshot_and_post_rechecks_later_review(database):
    service, owner, _ = database
    created, asset, _, _ = release_inputs(service, owner)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    changed = False

    def concurrent_review(connection, cursor, statement, parameters, context, executemany):
        nonlocal changed
        if changed or "FROM source_asset" not in statement:
            return
        changed = True
        # The read snapshot is already open. This writer must neither block nor
        # enter the reader's partially prepared result. It uses a separate engine.
        with owner.begin() as writer:
            append_review(
                writer,
                asset_id=asset.id,
                actor_kind="human",
                actor_id=Actor().id,
                expected_revision=2,
                approval_state="rejected",
                review_tier="human_reviewed",
                scope_completeness="partial",
                objects=[],
                training_eligible=False,
            )

    event.listen(service.engine, "after_cursor_execute", concurrent_review)
    try:
        plan = reads.read_freeze_plan(Reader(), created.id)
    finally:
        event.remove(service.engine, "after_cursor_execute", concurrent_review)
    assert changed and plan.payload is not None
    assert plan.payload.annotation_revisions == {asset.id: 2}
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        freeze(service, created.id, plan.payload)
    current = reads.read_freeze_plan(Reader(), created.id)
    assert current.payload is None and current.checks[0].reason == "incomplete_labels"
    assert release_count(owner, created.id) == 0


def test_release_detail_uses_frozen_members_and_select_only_role(database):
    service, owner, role = database
    created, asset, _, payload = release_inputs(service, owner)
    released = freeze(service, created.id, payload)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    before = reads.read_release(Reader(), released.id)
    with owner.begin() as connection:
        append_review(
            connection,
            asset_id=asset.id,
            actor_kind="human",
            actor_id=Actor().id,
            expected_revision=2,
            approval_state="rejected",
            review_tier="human_reviewed",
            scope_completeness="partial",
            objects=[],
            training_eligible=False,
        )
        connection.execute(
            text(f'REVOKE INSERT, UPDATE ON ALL TABLES IN SCHEMA public FROM "{role}"')
        )
    counts_before = counts(owner, created.id)
    for actor in (Actor(), Reader()):
        detail = reads.read_release(actor, released.id)
        assert detail == before
        assert detail.resource == released
        assert detail.members[0].asset_id == asset.id
        assert detail.members[0].annotation_revision == 2
        assert detail.members[0].scope_completeness == "exhaustive_for_scope"
        assert detail.members[0].review_tier == "human_reviewed"
        assert detail.previous_release_id is None
        assert detail.changed_asset_ids == [asset.id]
        assert detail.acceptance_policy_id == "canonical-training-eligibility-v1"
        assert detail.frozen_at and detail.frozen_by == Actor().id
        assert detail.manifest_sha256 is None and detail.manifest_artifact_id is None
        assert detail.classes[0].minimum_support is None
        assert detail.checks[1].passed is None
        serialized = detail.model_dump_json()
        for forbidden in ("source_uri", "object_key", "fingerprint", "idempotency_key"):
            assert forbidden not in serialized
    assert counts(owner, created.id) == counts_before


@pytest.mark.parametrize(
    "actor", [Reader(kind="service"), Reader(role="unknown"), Reader(granted=False), Reader(id="")]
)
def test_release_detail_refuses_untrusted_principals_before_lookup(database, actor):
    service, _, _ = database
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    with pytest.raises(ManagementFailure, match="permission_denied"):
        reads.read_release(actor, str(uuid4()))


def test_release_detail_isolates_foreign_unknown_and_legacy_rows(database):
    service, owner, _ = database
    created, _, _, payload = release_inputs(service, owner)
    released = freeze(service, created.id, payload)
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    for reader, target in (
        (replace(reads, workspace_id=uuid4()), released.id),
        (reads, str(uuid4())),
        (reads, "not-a-uuid"),
        (reads, created.id),
    ):
        with pytest.raises(ManagementFailure, match="not_found"):
            reader.read_release(Reader(), target)


@pytest.mark.parametrize("missing", ["receipt", "policy", "hash", "member"])
def test_release_detail_refuses_missing_recorded_evidence(database, missing):
    service, owner, _ = database
    created, asset, _, payload = release_inputs(service, owner)
    released = freeze(service, created.id, payload)
    copied_id = str(uuid4())
    with owner.begin() as connection:
        original = (
            connection.execute(
                select(canonical_dataset_release).where(
                    canonical_dataset_release.c.id == released.id
                )
            )
            .mappings()
            .one()
        )
        values = dict(original, id=copied_id, release_key=copied_id)
        if missing == "member":
            values["sample_revisions"] = {asset.id: 900}
        connection.execute(insert(canonical_dataset_release).values(**values))
        if missing != "receipt":
            evidence = {
                "acceptance_policy_id": "canonical-training-eligibility-v1",
                "canonical_snapshot_sha256": original["hashes"]["canonical_snapshot_sha256"],
            }
            if missing == "policy":
                evidence.pop("acceptance_policy_id")
            if missing == "hash":
                evidence["canonical_snapshot_sha256"] = "0" * 64
            connection.execute(
                insert(canonical_event).values(
                    aggregate_id=created.id,
                    actor_kind="human",
                    actor_id=Actor().id,
                    event_type="management.freeze_release",
                    expected_revision=3,
                    payload={
                        "workspace_id": str(service.workspace_id),
                        "result": released.model_copy(update={"id": copied_id}).model_dump(),
                        "evidence": evidence,
                    },
                )
            )
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        reads.read_release(Reader(), copied_id)


def test_release_detail_computes_prior_release_and_class_delta_from_pins(database):
    service, owner, _ = database
    created, asset, source, payload = release_inputs(service, owner)
    first = freeze(service, created.id, payload)
    objects = [{"object_id": "parcel", "class_id": "package", "box": [1, 2, 8, 10]}]
    with owner.begin() as connection:
        append_review(
            connection,
            asset_id=asset.id,
            actor_kind="human",
            actor_id=Actor().id,
            expected_revision=2,
            approval_state="proposed",
            review_tier="human_reviewed",
            scope_completeness="partial",
            objects=objects,
        )
        # The changed object set invalidated completeness at revision 3. The
        # fixture's independent human inspection now establishes it for that set.
        connection.execute(
            completeness_assessment.update()
            .where(
                completeness_assessment.c.asset_id == source.id,
                completeness_assessment.c.revision == 3,
            )
            .values(scope_completeness="exhaustive_for_scope")
        )
        append_review(
            connection,
            asset_id=asset.id,
            actor_kind="human",
            actor_id=Actor().id,
            expected_revision=3,
            approval_state="human_accepted",
            review_tier="human_reviewed",
            scope_completeness="exhaustive_for_scope",
            objects=objects,
            training_eligible=True,
        )
    second = freeze(
        service,
        created.id,
        payload.model_copy(update={"expected_revision": 3, "annotation_revisions": {asset.id: 4}}),
        key="second-release",
    )
    reads = CanonicalManagementReads(service.engine, service.workspace_id)
    detail = reads.read_release(Reader(), second.id)
    assert detail.previous_release_id == first.id
    assert detail.members[0].annotation_revision == 4
    assert detail.members[0].objects == 1
    assert detail.classes[0].val == 1 and detail.classes[0].delta == 1
    assert detail.classes[0].train == detail.classes[0].test == 0
    assert detail.human_reviewed_objects == 1 and detail.auto_accepted_objects == 0
    assert detail.changed_asset_ids == [asset.id]
    assert reads.read_release(Reader(), first.id).members[0].objects == 0


def test_parallel_freeze_replays_one_immutable_release_and_audit(database):
    service, owner, _ = database
    created, asset, _, payload = release_inputs(service, owner)
    barrier = Barrier(2, timeout=10)

    def submit(_):
        barrier.wait()
        return freeze(service, created.id, payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert results[0] == results[1]
    assert results[0].state == "frozen"
    assert release_count(owner, created.id) == 1
    assert counts(owner, created.id) == (1, 2, 3)
    with owner.connect() as connection:
        row = connection.execute(
            select(canonical_dataset_release).where(canonical_dataset_release.c.id == results[0].id)
        ).one()
        assert row.sample_revisions == {asset.id: 2}
        assert row.excluded_asset_ids == {}
        assert len(row.hashes["canonical_snapshot_sha256"]) == 64
        event = connection.execute(
            select(canonical_event).where(
                canonical_event.c.aggregate_id == created.id,
                canonical_event.c.event_type == "management.freeze_release",
            )
        ).one()
        assert event.payload["evidence"] == {
            **row.hashes,
            "acceptance_policy_id": "canonical-training-eligibility-v1",
        }
        assert event.actor_id == Actor().id
    for statement in (
        "UPDATE canonical_dataset_release SET frozen = false WHERE id = :id",
        "DELETE FROM canonical_dataset_release WHERE id = :id",
    ):
        with pytest.raises(DBAPIError), owner.begin() as connection:
            connection.execute(text(statement), {"id": results[0].id})
    assert freeze(service, created.id, payload) == results[0]
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        freeze(service, created.id, payload, key="stale")
    with pytest.raises(ManagementFailure, match="idempotency_conflict"):
        freeze(service, created.id, payload.model_copy(update={"expected_revision": 3}))
    with pytest.raises(ManagementFailure, match="not_found"):
        freeze(replace(service, workspace_id=uuid4()), created.id, payload)
    for actor in (Actor(role="reviewer"), Actor(kind="service")):
        with pytest.raises(ManagementFailure, match="permission_denied"):
            freeze(service, created.id, payload, actor=actor)


@pytest.mark.parametrize(
    ("options", "code"),
    [
        ({"complete": False}, "incomplete_labels"),
        ({"split": "train"}, "incomplete_labels"),
        ({"locked": False}, "missing_evidence"),
    ],
)
def test_freeze_refuses_unreviewed_negative_empty_validation_and_unlocked_split(
    database, options, code
):
    service, owner, _ = database
    created, _, _, payload = release_inputs(service, owner, **options)
    with pytest.raises(ManagementFailure, match=code):
        freeze(service, created.id, payload)
    assert release_count(owner, created.id) == 0
    assert counts(owner, created.id)[2] == 2


def test_freeze_rechecks_annotation_and_completeness_context(database):
    service, owner, _ = database
    created, asset, _, payload = release_inputs(service, owner)
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        freeze(
            service, created.id, payload.model_copy(update={"annotation_revisions": {asset.id: 1}})
        )
    with owner.begin() as connection:
        connection.execute(
            project.update().where(project.c.id == created.id).values(scope="Changed")
        )
    with pytest.raises(ManagementFailure, match="incomplete_labels"):
        freeze(service, created.id, payload)
    assert release_count(owner, created.id) == 0


def test_freeze_refuses_pending_group_review_and_cross_group_relation(database):
    service, owner, _ = database
    created, asset, source, payload = release_inputs(service, owner)
    # A pending pair is not proof of independence, even for an excluded endpoint.
    with owner.begin() as connection:
        pair_id = connection.execute(
            insert(borderline_review)
            .values(
                left_asset_id=source.id,
                right_asset_id=source.id,
                metric="phash",
                value="9",
                algorithm_version="fixture",
            )
            .returning(borderline_review.c.id)
        ).scalar_one()
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        freeze(service, created.id, payload)
    with owner.begin() as connection:
        connection.execute(borderline_review.delete().where(borderline_review.c.id == pair_id))
    other = ingest(service, created.id, image_payload(2), key="another-source")
    with owner.begin() as connection:
        other_id = connection.execute(
            select(source_asset.c.id).where(source_asset.c.asset_key == other.id)
        ).scalar_one()
        connection.execute(
            insert(source_split_assignment).values(
                assignment_version="assignment-1",
                asset_id=other_id,
                group_id="group-2",
                split="train",
                seed=7,
                algorithm="union-find-grouped-stratified-v2",
                thresholds={"phash_max_distance": 8},
                reason="fixture",
                locked=True,
            )
        )
        connection.execute(
            insert(similarity_relation).values(
                left_asset_id=source.id,
                right_asset_id=other_id,
                reason="exact_hash",
                evidence="fixture",
                algorithm_version="fixture",
            )
        )
    with pytest.raises(ManagementFailure, match="missing_evidence"):
        freeze(service, created.id, payload.model_copy(update={"expected_revision": 3}))
    assert release_count(owner, created.id) == 0
    assert asset.id in payload.annotation_revisions


def test_freeze_event_failure_rolls_back_release_and_retry_commits(database):
    service, owner, role = database
    created, _, _, payload = release_inputs(service, owner)
    with owner.begin() as connection:
        connection.execute(text(f'REVOKE INSERT ON canonical_event FROM "{role}"'))
    try:
        with pytest.raises(DBAPIError):
            freeze(service, created.id, payload)
        assert release_count(owner, created.id) == 0
    finally:
        with owner.begin() as connection:
            connection.execute(text(f'GRANT INSERT ON canonical_event TO "{role}"'))
    result = freeze(service, created.id, payload)
    assert result.kind == "dataset-release"
    assert release_count(owner, created.id) == 1


def test_freeze_pins_exclusions_and_refuses_obsolete_assignment(database):
    service, owner, _ = database
    created, asset, source, payload = release_inputs(service, owner)
    extra = ingest(service, created.id, image_payload(2), key="excluded")
    payload = payload.model_copy(update={"expected_revision": 3})
    with owner.begin() as connection:
        extra_id = connection.execute(
            select(source_asset.c.id).where(source_asset.c.asset_key == extra.id)
        ).scalar_one()
        original = connection.execute(
            select(source_split_assignment).where(source_split_assignment.c.asset_id == source.id)
        ).one()
        connection.execute(
            insert(source_split_assignment).values(
                assignment_version="assignment-1",
                asset_id=extra_id,
                group_id="group-1",
                split="val",
                seed=7,
                algorithm=original.algorithm,
                thresholds=original.thresholds,
                reason="fixture",
                locked=True,
            )
        )
    result = freeze(service, created.id, payload)
    detail = CanonicalManagementReads(service.engine, service.workspace_id).read_release(
        Reader(), result.id
    )
    assert [member.asset_id for member in detail.members] == [asset.id]
    assert detail.exclusions[0].asset_ids == [extra.id]
    assert detail.exclusions[0].reason == "not_selected"
    with owner.begin() as connection:
        row = connection.execute(
            select(canonical_dataset_release).where(canonical_dataset_release.c.id == result.id)
        ).one()
        assert row.sample_revisions == {asset.id: 2}
        assert row.excluded_asset_ids == {extra.id: "not_selected"}
        connection.execute(
            insert(source_split_assignment).values(
                assignment_version="assignment-2",
                asset_id=source.id,
                group_id="group-1",
                split="val",
                seed=7,
                algorithm=original.algorithm,
                thresholds=original.thresholds,
                reason="new publication",
                locked=True,
            )
        )
    # A historical retry is a receipt, not a new freeze under today's policy.
    assert freeze(service, created.id, payload) == result
    with pytest.raises(ManagementFailure, match="revision_conflict"):
        freeze(
            service, created.id, payload.model_copy(update={"expected_revision": 4}), key="old-plan"
        )


def test_parallel_freeze_different_keys_share_project_revision_guard(database):
    service, owner, _ = database
    created, _, _, payload = release_inputs(service, owner)
    barrier = Barrier(2, timeout=10)

    def submit(key):
        barrier.wait()
        try:
            return freeze(service, created.id, payload, key=key).kind
        except ManagementFailure as error:
            return error.error.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ("left", "right")))
    assert sorted(outcomes) == ["dataset-release", "revision_conflict"]
    assert release_count(owner, created.id) == 1


def test_review_writer_and_freeze_share_source_row_synchronization(database):
    service, owner, _ = database
    created, asset, source, payload = release_inputs(service, owner)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with owner.begin() as connection:
            connection.execute(
                select(source_asset).where(source_asset.c.id == source.id).with_for_update()
            )
            pending = pool.submit(freeze, service, created.id, payload)
            append_review(
                connection,
                asset_id=asset.id,
                actor_kind="human",
                actor_id=Actor().id,
                expected_revision=2,
                approval_state="human_accepted",
                review_tier="human_reviewed",
                scope_completeness="exhaustive_for_scope",
                objects=[],
                training_eligible=True,
            )
        with pytest.raises(ManagementFailure, match="revision_conflict"):
            pending.result(timeout=15)
    assert release_count(owner, created.id) == 0
