"""Real session/SQL audit projections; no paid calls or production resources."""

import json
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from test_auth import NOW, ORIGIN, login, worker_token
from test_auth import password_hash as password_hash
from test_auth import store as store

from labelloop_api import audit_reads
from labelloop_api import budget_schema as budgets
from labelloop_api import model_schema as models
from labelloop_api import schema as db
from labelloop_api.auth_schema import principal
from labelloop_api.main import app

PATH = "/projects/audit-fixture/audit-log"
PROJECT = UUID("00000000-0000-0000-0000-000000000001")
FOREIGN = UUID("00000000-0000-0000-0000-000000000002")
ASSET = UUID("00000000-0000-0000-0000-000000000003")
OTHER_ASSET = UUID("00000000-0000-0000-0000-000000000004")


def review(connection, number, *, asset=ASSET, at=NOW, **changes):
    identity = UUID(int=number)
    connection.execute(
        sa.insert(db.review_event).values(
            dict(
                id=identity,
                asset_id=asset,
                actor_kind="system",
                actor_id="recorded-actor",
                decision="private-decision",
                expected_revision=1,
                correction={"private": "must-not-leak"},
                reason="must-not-leak",
                created_at=at,
            )
            | changes
        )
    )
    return identity


def promotion(connection, number, *, project=PROJECT, at=NOW):
    identity = UUID(int=number)
    connection.execute(
        sa.insert(models.promotion_event).values(
            id=identity,
            project_id=project,
            recipe_id=uuid4(),
            revision=number,
            actor_id="historical-actor",
            action="promote",
            record={"private": "must-not-leak"},
            created_at=at,
        )
    )
    return identity


def budget(connection, number, *, project="audit-fixture", actor="budget-actor", kind="human"):
    if (
        connection.execute(
            sa.select(budgets.head.c.project_id).where(budgets.head.c.project_id == project)
        ).first()
        is None
    ):
        connection.execute(
            sa.insert(budgets.head).values(project_id=project, revision=1, policy={})
        )
    identity = str(UUID(int=number))
    connection.execute(
        sa.insert(budgets.event).values(
            id=identity,
            project_id=project,
            operation_id="private-operation",
            principal_id=actor,
            principal_kind=kind,
            principal_role="private-role",
            principal_version="private-version",
            policy_revision=1,
            action="private-action",
            record={"private": "must-not-leak"},
            occurred_at=NOW,
        )
    )
    return identity


@pytest.fixture
def http(store, monkeypatch):
    with store.engine.begin() as connection:
        # Keep UUIDs as text in SQLite: its UUID type has numeric affinity and
        # corrupts digit-only or exponent-shaped hex. Never mutate shared tables.
        metadata = sa.MetaData()
        sa.Table(
            "source_asset",
            metadata,
            sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
            sa.Column("project_id", sa.Uuid(native_uuid=False), nullable=False),
        )
        sa.Table(
            "preprocessing_recipe",
            metadata,
            sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        )
        for table in (db.project, db.review_event, models.promotion_event):
            copied = table.to_metadata(metadata)
            for column in copied.c:
                if isinstance(column.type, sa.Uuid):
                    column.type = sa.Uuid(native_uuid=False)
        # SQLite supplies only the source identity columns used by the read. The
        # production PostgreSQL smoke retains the complete canonical schema.
        budgets.head.to_metadata(metadata)
        budgets.event.to_metadata(metadata)
        metadata.create_all(connection)
        for identity, key in ((PROJECT, "audit-fixture"), (FOREIGN, "foreign")):
            connection.execute(
                sa.insert(db.project).values(
                    id=identity,
                    project_key=key,
                    workspace_id=uuid4(),
                    task="detect",
                    scope="fixture",
                    domain="licence-clean",
                    created_at=NOW,
                )
            )
        connection.execute(
            sa.insert(db.source_asset).values(
                [
                    {"id": ASSET, "project_id": PROJECT},
                    {"id": OTHER_ASSET, "project_id": FOREIGN},
                ]
            )
        )
        review(connection, 10)
        review(connection, 20)
        promotion(connection, 30)
        review(connection, 40, asset=OTHER_ASSET)
        promotion(connection, 50, project=FOREIGN)
    monkeypatch.setattr(audit_reads, "auth_store", lambda: store)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client


def test_sqlite_fixture_preserves_numeric_and_exponent_shaped_uuid_values(http, store):
    with store.engine.begin() as connection:
        for number in (0xE0, 0xE1):
            review(connection, number)
        assert set(connection.execute(sa.select(db.project.c.id)).scalars()) == {PROJECT, FOREIGN}
        assert set(
            connection.execute(sa.select(db.review_event.c.id, db.review_event.c.asset_id)).all()
        ) == {
            (UUID(int=number), asset)
            for number, asset in (
                (10, ASSET),
                (20, ASSET),
                (40, OTHER_ASSET),
                (0xE0, ASSET),
                (0xE1, ASSET),
            )
        }
    for table in (db.project, db.review_event, models.promotion_event):
        assert table.c.id.type.native_uuid


def test_admin_metadata_projection_is_redacted_and_does_not_infer_promotion_actor(http):
    login(http, "admin")
    response = http.get(PATH)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    page = response.json()
    assert page["total"] == 3 and page["next_after"] is None
    assert [event["id"] for event in page["events"]] == [str(UUID(int=n)) for n in (20, 10, 30)]
    assert page["events"][-1]["actor_kind"] == "unknown"
    assert page["events"][0] == {
        "id": str(UUID(int=20)),
        "at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": "Review",
        "actor_id": "recorded-actor",
        "actor_kind": "system",
        "actor_name": None,
        "record_id": str(ASSET),
        "revision": 1,
        "sentence": "Recorded a review decision.",
        "refused": None,
    }
    assert "must-not-leak" not in response.text and "private-decision" not in response.text


def test_reviewer_restriction_applies_before_filters_counts_paging_and_export(http):
    login(http)
    page = http.get(PATH, params={"limit": 1}).json()
    assert page["total"] == 2 and len(page["events"]) == 1
    assert page["next_after"] == f"Review/{UUID(int=20)}"
    assert http.get(PATH, params={"after": page["next_after"]}).json()["events"][0]["id"] == str(
        UUID(int=10)
    )
    empty = http.get(PATH, params={"kind": "Promotion"}).json()
    assert empty["total"] == 0 and empty["events"] == [] and empty["next_after"] is None
    exported = http.get(PATH + "/export")
    assert exported.status_code == 200
    assert [json.loads(line)["kind"] for line in exported.text.splitlines()] == ["Review", "Review"]
    assert http.get(PATH + "/export", params={"kind": "Promotion"}).text == ""
    assert "must-not-leak" not in exported.text


@pytest.mark.parametrize(
    ("recorded_kind", "account", "resolved"),
    [
        ("human", "reviewer", True),
        ("human", "worker-core", False),
        ("model", "worker-core", True),
        ("system", "worker-core", True),
        ("model", "reviewer", False),
        ("system", "reviewer", False),
    ],
)
def test_current_principal_name_requires_exact_id_and_compatible_kind(
    http, store, recorded_kind, account, resolved
):
    with store.engine.begin() as connection:
        identity = connection.execute(
            sa.select(principal.c.id).where(principal.c.name == account)
        ).scalar_one()
        review(connection, 60, actor_kind=recorded_kind, actor_id=identity)
        # A legacy actor string equal to a current name is not an identity link.
        review(connection, 70, actor_kind=recorded_kind, actor_id=account)
    login(http, "admin")
    events = {event["id"]: event for event in http.get(PATH).json()["events"]}
    linked = events[str(UUID(int=60))]
    assert linked["actor_id"] == identity and linked["actor_kind"] == recorded_kind
    assert linked["actor_name"] == (account if resolved else None)
    assert events[str(UUID(int=70))]["actor_name"] is None


def test_current_name_search_pages_and_exports_only_visible_event_identities(http, store):
    with store.engine.begin() as connection:
        identity = connection.execute(
            sa.select(principal.c.id).where(principal.c.name == "worker-core")
        ).scalar_one()
        connection.execute(
            sa.update(principal).where(principal.c.id == identity).values(name="named%_/worker")
        )
        for number in (60, 70):
            review(connection, number, actor_id=identity)
        review(connection, 80, actor_kind="human", actor_id=identity)
        # Names from hidden sources and accounts without visible events stay hidden.
        review(connection, 90, asset=OTHER_ASSET, actor_id=identity)
    login(http)
    params = {"search": "NAMED%_/WORKER", "actor_kind": "system"}
    first = http.get(PATH, params=params | {"limit": 1}).json()
    assert first["total"] == 2
    assert first["events"][0]["id"] == str(UUID(int=70))
    second = http.get(PATH, params=params | {"limit": 1, "after": first["next_after"]}).json()
    assert second["total"] == 2 and second["next_after"] is None
    assert second["events"][0]["id"] == str(UUID(int=60))
    exported = http.get(PATH + "/export", params=params)
    assert [json.loads(line) for line in exported.text.splitlines()] == first["events"] + second[
        "events"
    ]
    for search in ("named%_/not-worker", "worker-ml", "admin"):
        page = http.get(PATH, params={"search": search}).json()
        assert page["total"] == 0 and page["events"] == []
        assert http.get(PATH + "/export", params={"search": search}).text == ""
    assert http.get(PATH, params=params | {"after": f"Review/{UUID(int=80)}"}).status_code == 409


def test_promotion_name_is_current_metadata_not_inferred_actor_kind(http, store):
    with store.engine.begin() as connection:
        identity = connection.execute(
            sa.select(principal.c.id).where(principal.c.name == "worker-core")
        ).scalar_one()
        connection.execute(
            sa.update(models.promotion_event)
            .where(models.promotion_event.c.id == UUID(int=30))
            .values(actor_id=identity)
        )
    login(http, "admin")
    original = http.get(PATH, params={"kind": "Promotion"}).json()["events"][0]
    assert original["actor_name"] == "worker-core" and original["actor_kind"] == "unknown"
    with store.engine.begin() as connection:
        connection.execute(
            sa.update(principal)
            .where(principal.c.id == identity)
            .values(name="renamed-worker", enabled=False)
        )
    current = http.get(PATH, params={"search": "renamed-worker"}).json()["events"][0]
    assert current == original | {"actor_name": "renamed-worker"}
    assert http.get(PATH, params={"search": "worker-core"}).json()["total"] == 0
    client_events = http.get(PATH + "/export", params={"search": "renamed-worker"})
    assert [json.loads(line) for line in client_events.text.splitlines()] == [current]
    http.cookies.clear()
    login(http)
    assert http.get(PATH, params={"search": "renamed-worker"}).json()["total"] == 0
    assert http.get(PATH + "/export", params={"search": "renamed-worker"}).text == ""
    assert http.get(PATH, params={"after": f"Promotion/{UUID(int=30)}"}).status_code == 409


def test_stable_keyset_ordering_and_filtered_export_equality(http, store):
    with store.engine.begin() as connection:
        review(connection, 60, at=NOW + timedelta(seconds=1))
    login(http, "admin")
    events = []
    after = None
    while True:
        page = http.get(PATH, params={"limit": 1} | ({"after": after} if after else {})).json()
        assert page["total"] == 4
        events.extend(page["events"])
        after = page["next_after"]
        if after is None:
            break
    assert [event["id"] for event in events] == [str(UUID(int=n)) for n in (60, 20, 10, 30)]
    params = {"kind": "Review", "since": (NOW + timedelta(seconds=1)).isoformat()}
    page = http.get(PATH, params=params).json()
    response = http.get(PATH + "/export", params=params)
    assert [json.loads(line) for line in response.text.splitlines()] == page["events"]
    assert len(page["events"]) == 1
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("name", ["admin", "reviewer"])
@pytest.mark.parametrize(
    ("search", "numbers"),
    [
        ("RECORDED", [20, 10]),
        (str(UUID(int=20)), [20]),
        (UUID(int=20).hex, [20]),
        (str(ASSET), [20, 10]),
        ("must-not-leak", []),
        ("private-decision", []),
        (str(UUID(int=40)), []),
        (str(OTHER_ASSET), []),
    ],
)
def test_metadata_search_is_scoped_and_matches_export(http, name, search, numbers):
    login(http, name)
    params = {"search": search}
    response = http.get(PATH, params=params)
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == len(numbers)
    assert [event["id"] for event in page["events"]] == [str(UUID(int=n)) for n in numbers]
    exported = http.get(PATH + "/export", params=params)
    assert exported.status_code == 200
    assert [json.loads(line) for line in exported.text.splitlines()] == page["events"]


@pytest.mark.parametrize("search", ["%", "_", "/", "%' OR 1=1 --"])
def test_actor_search_treats_sql_patterns_as_literal_text(http, store, search):
    with store.engine.begin() as connection:
        review(connection, 60, actor_id="literal" + search + "actor")
    login(http, "admin")
    page = http.get(PATH, params={"search": search}).json()
    assert page["total"] == 1
    assert [event["id"] for event in page["events"]] == [str(UUID(int=60))]
    exported = http.get(PATH + "/export", params={"search": search})
    assert [json.loads(line) for line in exported.text.splitlines()] == page["events"]


@pytest.mark.parametrize("actor_kind", ["human", "model", "system", "unknown"])
def test_recorded_actor_kind_is_not_inferred_from_identity(http, store, actor_kind):
    with store.engine.begin() as connection:
        review(connection, 60, actor_kind="human", actor_id="recorded-actor")
        review(connection, 70, actor_kind="model", actor_id="recorded-actor")
    login(http, "admin")
    params = {"actor_kind": actor_kind}
    page = http.get(PATH, params=params).json()
    expected = {"human": [60], "model": [70], "system": [20, 10], "unknown": [30]}[actor_kind]
    assert page["total"] == len(expected)
    assert [event["id"] for event in page["events"]] == [str(UUID(int=n)) for n in expected]
    exported = http.get(PATH + "/export", params=params)
    assert [json.loads(line) for line in exported.text.splitlines()] == page["events"]


@pytest.mark.parametrize("search", ["historical-actor", str(UUID(int=30))])
def test_search_and_actor_filters_never_reveal_hidden_promotions(http, search):
    login(http)
    params = {"actor_kind": "unknown", "search": search}
    page = http.get(PATH, params=params).json()
    assert page == {"project_id": "audit-fixture", "events": [], "total": 0, "next_after": None}
    assert http.get(PATH + "/export", params=params).text == ""


def test_filtered_cursor_count_and_export_share_the_search_population(http):
    login(http, "admin")
    params = {"search": "RECORDED", "actor_kind": "system", "limit": 1}
    page = http.get(PATH, params=params).json()
    assert page["total"] == 2
    assert page["next_after"] == f"Review/{UUID(int=20)}"
    last = http.get(PATH, params=params | {"after": page["next_after"]}).json()
    assert last["total"] == 2 and last["next_after"] is None
    exported = http.get(PATH + "/export", params={"search": "RECORDED", "actor_kind": "system"})
    assert [json.loads(line) for line in exported.text.splitlines()] == page["events"] + last[
        "events"
    ]
    for changed in ({"search": "historical"}, {"actor_kind": "human"}):
        response = http.get(PATH, params=params | {"after": page["next_after"]} | changed)
        assert response.status_code == 409
        assert response.json() == {"detail": "audit_state_rejected"}


@pytest.mark.parametrize(
    "after", ["invalid", "Review/not-a-uuid", f"Review/{UUID(int=40)}", f"Promotion/{UUID(int=30)}"]
)
def test_cursor_must_resolve_inside_authorized_filtered_query(http, after):
    login(http)
    response = http.get(PATH, params={"after": after})
    assert response.status_code == 409
    assert response.json() == {"detail": "audit_state_rejected"}


@pytest.mark.parametrize("account", ["admin", "reviewer"])
@pytest.mark.parametrize("reverse", [False, True])
def test_set_filters_share_authorized_counts_pages_and_export(http, store, account, reverse):
    with store.engine.begin() as connection:
        budget(connection, 60)
        review(connection, 70, actor_kind="human")
    login(http, account)
    kinds = ["Review", "Budget"]
    actors = ["system", "human"]
    if reverse:
        kinds.reverse()
        actors.reverse()
    params = {"kind": kinds, "actor_kind": actors}
    expected = [70, 20, 10, 60] if account == "admin" else [70, 20, 10]
    events = []
    after = None
    for number in expected:
        response = http.get(
            PATH, params=params | {"limit": 1} | ({"after": after} if after else {})
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        page = response.json()
        assert page["total"] == len(expected)
        assert [event["id"] for event in page["events"]] == [str(UUID(int=number))]
        events.extend(page["events"])
        after = page["next_after"]
    assert after is None
    exported = http.get(PATH + "/export", params=params)
    assert exported.status_code == 200
    assert [json.loads(line) for line in exported.text.splitlines()] == events
    assert "must-not-leak" not in exported.text
    assert "private" not in exported.text
    for excluded in (f"Promotion/{UUID(int=30)}", f"Review/{UUID(int=40)}"):
        assert http.get(PATH, params=params | {"after": excluded}).status_code == 409
    if account == "reviewer":
        assert (
            http.get(PATH, params=params | {"after": f"Budget/{UUID(int=60)}"}).status_code == 409
        )
    narrowed = http.get(
        PATH,
        params={
            "kind": ["Review", "Budget"],
            "actor_kind": ["human"],
            "after": f"Review/{UUID(int=20)}",
        },
    )
    assert narrowed.status_code == 409
    with store.engine.connect() as connection:
        assert (
            connection.execute(sa.select(sa.func.count()).select_from(db.review_event)).scalar_one()
            == 4
        )
        assert (
            connection.execute(sa.select(sa.func.count()).select_from(budgets.event)).scalar_one()
            == 1
        )


@pytest.mark.parametrize("suffix", ["", "/export"])
def test_duplicate_set_members_do_not_duplicate_events(http, suffix):
    login(http, "admin")
    expected = http.get(PATH + suffix, params={"kind": "Review", "actor_kind": "system"})
    response = http.get(
        PATH + suffix,
        params={"kind": ["Review", "Review"], "actor_kind": ["system", "system"]},
    )
    assert response.status_code == expected.status_code == 200
    assert response.content == expected.content


@pytest.mark.parametrize("suffix", ["", "/export"])
@pytest.mark.parametrize(
    "params",
    [
        {"kind": ["Review", "Connection", "Budget"]},
        {"kind": ["Review"] * 4},
        {"actor_kind": ["human", "person", "service"]},
        {"actor_kind": ["human"] * 6},
    ],
)
def test_every_set_member_and_collection_bound_is_validated(http, suffix, params):
    login(http, "admin")
    response = http.get(PATH + suffix, params=params)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}


def test_cursor_filtered_out_is_not_reused(http):
    login(http, "admin")
    response = http.get(PATH, params={"kind": "Review", "after": f"Promotion/{UUID(int=30)}"})
    assert response.status_code == 409


@pytest.mark.parametrize("suffix", ["", "/export"])
@pytest.mark.parametrize(
    "name", [None, "worker-core", "worker-ml", "worker-llm", "worker-platform"]
)
def test_denied_before_audit_database_access(http, store, monkeypatch, name, suffix):
    def forbidden():
        raise AssertionError("unauthorized audit read")

    monkeypatch.setattr(audit_reads, "auth_store", forbidden)
    headers = {"authorization": "Bearer " + worker_token(store, name)} if name else {}
    response = http.get(PATH + suffix, headers=headers)
    assert response.status_code == (403 if name else 401)


@pytest.mark.parametrize("suffix", ["", "/export"])
def test_expired_session_cannot_read(http, monkeypatch, suffix):
    from labelloop_api import auth

    login(http, "admin")
    monkeypatch.setattr(auth, "clock", lambda: NOW + timedelta(days=1))
    assert http.get(PATH + suffix).status_code == 401


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"kind": "Connection"},
        {"after": "x" * 65},
        {"since": "not-a-date"},
        {"actor_kind": "person"},
        {"search": ""},
        {"search": "x" * 129},
    ],
)
def test_query_bounds(http, params):
    login(http)
    response = http.get(PATH, params=params)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}


@pytest.mark.parametrize(
    "params", [{"actor_kind": "person"}, {"search": ""}, {"search": "x" * 129}]
)
def test_export_filter_bounds(http, params):
    login(http)
    response = http.get(PATH + "/export", params=params)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}


@pytest.mark.parametrize(
    "params",
    [
        {"since": "2026-11-07T00:00:00"},
        {"since": "2026-11-08T00:00:00Z", "through": "2026-11-07T00:00:00Z"},
    ],
)
def test_ambiguous_or_inverted_dates_reject(http, params):
    login(http)
    assert http.get(PATH, params=params).status_code == 409
    assert http.get(PATH + "/export", params=params).status_code == 409


def test_missing_project_is_not_a_fabricated_empty_history(http):
    login(http)
    assert http.get("/projects/missing/audit-log").status_code == 404


@pytest.mark.parametrize("error", [ValueError("must-not-leak"), SQLAlchemyError("must-not-leak")])
@pytest.mark.parametrize("suffix", ["", "/export"])
def test_outer_failure_rolls_back_and_redacts(http, store, monkeypatch, error, suffix):
    def fail(connection, **kwargs):
        review(connection, 70)
        raise error

    monkeypatch.setattr(audit_reads, "load_audit_page", fail)
    login(http)
    response = http.get(PATH + suffix)
    assert response.status_code == (409 if isinstance(error, ValueError) else 503)
    assert "must-not-leak" not in response.text
    with store.engine.connect() as connection:
        assert (
            connection.execute(sa.select(sa.func.count()).select_from(db.review_event)).scalar_one()
            == 3
        )


def test_export_refuses_overflow_instead_of_silent_truncation(http, store):
    with store.engine.begin() as connection:
        for number in range(100, 1099):
            review(connection, number)
    login(http)
    response = http.get(PATH + "/export")
    assert response.status_code == 409
    assert response.json() == {"detail": "audit_export_requires_narrower_range"}
    assert "content-disposition" not in response.headers


@pytest.mark.parametrize("account,recorded_kind", [("admin", "human"), ("worker-llm", "service")])
def test_budget_metadata_uses_recorded_principal_kind_and_redacts_payloads(
    http, store, account, recorded_kind
):
    with store.engine.begin() as connection:
        actor = connection.execute(
            sa.select(principal.c.id).where(principal.c.name == account)
        ).scalar_one()
        identity = budget(connection, 60, actor=actor, kind=recorded_kind)
        budget(connection, 70, project="foreign", actor=actor, kind=recorded_kind)
        before = connection.execute(sa.select(budgets.event)).all()
    login(http, "admin")
    params = {"kind": "Budget", "actor_kind": recorded_kind, "search": account.upper()}
    response = http.get(PATH, params=params)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    page = response.json()
    assert page["total"] == 1 and page["next_after"] is None
    assert page["events"] == [
        {
            "id": identity,
            "at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "kind": "Budget",
            "actor_id": actor,
            "actor_kind": recorded_kind,
            "actor_name": account,
            "record_id": str(PROJECT),
            "revision": 1,
            "sentence": "Recorded a budget event.",
            "refused": None,
        }
    ]
    for search in (identity, UUID(identity).hex, str(PROJECT)):
        assert (
            http.get(PATH, params={"kind": "Budget", "search": search}).json()["events"]
            == page["events"]
        )
    exported = http.get(PATH + "/export", params=params)
    assert exported.status_code == 200
    assert [json.loads(line) for line in exported.text.splitlines()] == page["events"]
    for private in ("must-not-leak", "private-operation", "private-action", "private-role"):
        assert private not in response.text and private not in exported.text
        assert http.get(PATH, params={"search": private}).json()["total"] == 0
    with store.engine.connect() as connection:
        assert connection.execute(sa.select(budgets.event)).all() == before


@pytest.mark.parametrize("suffix", ["", "/export"])
def test_reviewer_cannot_enumerate_budget_events_or_principal_names(http, store, suffix):
    with store.engine.begin() as connection:
        actor = connection.execute(
            sa.select(principal.c.id).where(principal.c.name == "admin")
        ).scalar_one()
        identity = budget(connection, 60, actor=actor)
    login(http)
    for params in ({"kind": "Budget"}, {"search": "admin"}, {"search": identity}):
        response = http.get(PATH + suffix, params=params)
        assert response.status_code == 200
        if suffix:
            assert response.text == ""
        else:
            assert response.json() == {
                "project_id": "audit-fixture",
                "events": [],
                "total": 0,
                "next_after": None,
            }
    assert http.get(PATH, params={"after": f"Budget/{identity}"}).status_code == 409
    assert http.get(PATH).json()["total"] == 2


def test_mixed_storage_uuid_ordering_and_kind_disambiguated_cursors(http, store):
    with store.engine.begin() as connection:
        # An equal UUID in different journals must not collapse or skip either row.
        budget(connection, 20)
        budget(connection, 0xE0)
    login(http, "admin")
    events = []
    after = None
    for _ in range(6):
        response = http.get(PATH, params={"limit": 1} | ({"after": after} if after else {}))
        assert response.status_code == 200
        page = response.json()
        assert page["total"] == 5
        events.extend(page["events"])
        after = page["next_after"]
        if after is None:
            break
    else:
        raise AssertionError("mixed audit cursor did not terminate")
    assert [(event["kind"], event["id"]) for event in events] == [
        (kind, str(UUID(int=number)))
        for kind, number in (
            ("Review", 20),
            ("Review", 10),
            ("Promotion", 30),
            ("Budget", 0xE0),
            ("Budget", 20),
        )
    ]
    exported = http.get(PATH + "/export")
    assert [json.loads(line) for line in exported.text.splitlines()] == events
    assert (
        http.get(PATH, params={"kind": "Budget", "after": f"Review/{UUID(int=20)}"}).status_code
        == 409
    )
    assert http.get(PATH, params={"after": f"Budget/{UUID(int=999)}"}).status_code == 409


@pytest.mark.parametrize("field,value", [("id", "must-not-leak"), ("principal_kind", "invalid")])
def test_malformed_budget_metadata_fails_closed(http, store, field, value):
    with store.engine.begin() as connection:
        identity = budget(connection, 60)
        connection.execute(
            sa.update(budgets.event).where(budgets.event.c.id == identity).values({field: value})
        )
    login(http, "admin")
    for suffix in ("", "/export"):
        response = http.get(PATH + suffix)
        assert response.status_code == 409
        assert response.json() == {"detail": "audit_state_rejected"}


@pytest.mark.parametrize(
    "action,refused",
    [
        ("approval_exhausted", True),
        ("policy", None),
        ("review_request_claim", None),
        ("insufficient_credits", None),
        ("budget_exceeded", None),
        ("private-action", None),
    ],
)
def test_refusal_requires_exact_durable_budget_action(http, store, action, refused):
    with store.engine.begin() as connection:
        identity = budget(connection, 60)
        connection.execute(
            sa.update(budgets.event)
            .where(budgets.event.c.id == identity)
            .values(
                action=action,
                record={"refused": True, "reason": "approval_exhausted", "private": "hidden"},
            )
        )
        review(connection, 70, decision="approval_exhausted", reason="approval_exhausted")
        before = connection.execute(sa.select(budgets.event)).all()
    login(http, "admin")
    response = http.get(PATH, params={"kind": "Budget", "limit": 1})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    page = response.json()
    assert page["total"] == 1 and page["next_after"] is None
    event = page["events"][0]
    assert event["id"] == identity and event["refused"] is refused
    assert event["sentence"] == (
        "Refused a review request: approval exhausted." if refused else "Recorded a budget event."
    )
    exported = http.get(PATH + "/export", params={"kind": "Budget"})
    assert exported.status_code == 200
    assert [json.loads(line) for line in exported.text.splitlines()] == page["events"]
    assert "hidden" not in exported.text and "private-operation" not in exported.text
    assert http.get(PATH, params={"search": "approval_exhausted"}).json()["total"] == 0
    others = http.get(PATH, params={"kind": "Review"}).json()["events"]
    assert all(other["refused"] is None for other in others)
    with store.engine.connect() as connection:
        assert connection.execute(sa.select(budgets.event)).all() == before
    http.cookies.clear()
    login(http)
    assert http.get(PATH, params={"kind": "Budget"}).json()["total"] == 0
    assert http.get(PATH + "/export", params={"kind": "Budget"}).text == ""
    assert http.get(PATH, params={"after": f"Budget/{identity}"}).status_code == 409


def test_openapi_has_typed_metadata_only_contract():
    contract = app.openapi()
    schema = contract["paths"]["/projects/{project_key}/audit-log"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/AuditMetadataPage"}
    properties = contract["components"]["schemas"]["AuditMetadata"]["properties"]
    assert set(properties) == {
        "id",
        "at",
        "kind",
        "actor_id",
        "actor_kind",
        "actor_name",
        "record_id",
        "revision",
        "sentence",
        "refused",
    }
    assert properties["refused"]["anyOf"] == [{"type": "boolean"}, {"type": "null"}]
    for suffix in ("", "/export"):
        operation = contract["paths"]["/projects/{project_key}/audit-log" + suffix]["get"]
        assert "requestBody" not in operation
        parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
        for key, maximum in (("kind", 3), ("actor_kind", 5)):
            parameter = parameters[key]
            assert parameter["in"] == "query" and parameter["required"] is False
            array = next(
                value for value in parameter["schema"]["anyOf"] if value["type"] == "array"
            )
            assert array["minItems"] == 1 and array["maxItems"] == maximum
            assert array["items"]["type"] == "string"
