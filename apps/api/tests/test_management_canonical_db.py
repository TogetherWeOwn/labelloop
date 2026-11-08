"""Real canonical transactions, only against a disposable Actions Postgres service.

These are not session/CSRF, queue, setup-publication or project-to-export tests.
"""

import base64
import io
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from threading import Barrier
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError

from labelloop_api.management_canonical import CanonicalManagementMutations
from labelloop_api.management_contracts import AssetIngest, ProjectCreate
from labelloop_api.management_ports import ManagementFailure, MutationContext
from labelloop_api.schema import (
    annotation_revision,
    canonical_event,
    ontology_release,
    project,
    source_asset,
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
                f'annotation_revision, canonical_event TO "{role}"'
            )
        )
        connection.execute(text(f'GRANT UPDATE (scope) ON project TO "{role}"'))
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
