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
from sqlalchemy import create_engine, event, func, insert, select, text
from sqlalchemy.exc import DBAPIError

from labelloop_api.management_canonical import CanonicalManagementMutations
from labelloop_api.management_contracts import (
    AssetIngest,
    GuideDraft,
    ProjectCreate,
    ReleaseFreeze,
    ScopeDraft,
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
                f"completeness_assessment, source_split_assignment, similarity_relation, "
                f'borderline_review TO "{role}"'
            )
        )
        connection.execute(
            text(f'GRANT UPDATE (scope, domain, access_policy) ON project TO "{role}"')
        )
        connection.execute(text(f'GRANT UPDATE (source_uri) ON source_asset TO "{role}"'))
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
    with pytest.raises(ManagementFailure, match="management_unavailable"):
        reads.list_resources(Reader(), created.id, "model", None, 10)
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
