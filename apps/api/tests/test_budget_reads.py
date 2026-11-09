"""Real session/read transactions; PostgreSQL evidence runs only in Actions."""

from dataclasses import asdict, replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
import test_budget_store as budget_tests
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from test_auth import NOW, ORIGIN, login, worker_token
from test_auth import password_hash as password_hash
from test_auth import store as store
from test_budget_store import ADMIN, LLM, claim, request, reserve

from labelloop_api import budget_reads
from labelloop_api import budget_schema as db
from labelloop_api.budget_store import DispatchIntent, digest
from labelloop_api.main import app
from labelloop_api.schema import project
from labelloop_core.budget import BudgetPolicy, LedgerEntry, SpendApproval

PATH = "/projects/budget-fixture/costs"
postgres_budget = budget_tests.store


def entry(key="one", **kwargs):
    req = request(key)
    return LedgerEntry(req, SpendApproval(ADMIN.id, req.digest), **kwargs)


def persist(connection, value, *, key=None, intent=budget_tests.INTENT, review_pause=None):
    connection.execute(
        sa.insert(db.operation).values(
            project_id="budget-fixture",
            operation_id=key or value.request.operation_id,
            dry_run_digest=digest(
                {"request": asdict(value.request), "intent": asdict(intent), "policy_revision": 1}
            ),
            policy_revision=1,
            entry=asdict(value),
            intent=asdict(intent),
            evidence={"private": "must-not-leak"},
            review_pause=review_pause,
        )
    )


@pytest.fixture
def http(monkeypatch, store):
    with store.engine.begin() as connection:
        project.create(connection)
        db.head.create(connection)
        db.operation.create(connection)
        db.event.create(connection)
        for key in ("budget-fixture", "foreign", "unconfigured"):
            connection.execute(
                sa.insert(project).values(
                    id=uuid4(),
                    project_key=key,
                    workspace_id=uuid4(),
                    task="detect",
                    scope="fixture",
                    domain="licence-clean",
                    created_at=NOW,
                )
            )
        for key in ("budget-fixture", "foreign"):
            connection.execute(
                sa.insert(db.head).values(
                    project_id=key,
                    revision=1,
                    policy={"limits": asdict(BudgetPolicy(100, 100, 100))},
                )
            )
    monkeypatch.setattr(budget_reads, "auth_store", lambda: store)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client


@pytest.mark.parametrize(
    ("state", "actual", "floor", "exposure"),
    [
        ("reserved", None, 0, 40),
        ("dispatched", None, 0, 40),
        ("unconfirmed", None, 55, 55),
        ("settled", 0, 0, 0),
        ("settled", 30, 0, 30),
        ("cancelled", None, 0, 0),
        ("insufficient_credits", None, 0, 40),
    ],
)
@pytest.mark.parametrize("path", [PATH, PATH + "/export"])
def test_projection_preserves_actual_exposure_and_redacts_private_records(
    http, store, state, actual, floor, exposure, path
):
    with store.engine.begin() as connection:
        persist(connection, entry(state=state, actual=actual, exposure_floor=floor))
        connection.execute(
            sa.update(db.head)
            .where(db.head.c.project_id == "budget-fixture")
            .values(
                pause_reason="insufficient_credits" if state == "insufficient_credits" else None
            )
        )
    assert login(http, "admin").status_code == 200
    response = http.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    page = response.json()
    assert page["currency"] == "USD" and page["unit"] == "micro_usd"
    assert page["policy_revision"] == 1
    assert page["next_after"] is None
    assert page["operations"] == [
        {
            "operation_id": "one",
            "iteration_id": "iteration",
            "month": "2026-11",
            "price_version": "fixture-v1",
            "estimate": 40,
            "actual": actual,
            "exposure": exposure,
            "state": state,
            "approved_by": ADMIN.id,
            "reserved_autonomously": False,
            "reserved_at": None,
            "kind": "LLM review",
            "annotation_job_id": None,
            "review_pause": None,
        }
    ]
    assert "must-not-leak" not in response.text
    summary = http.get(SUMMARY, params=SUMMARY_QUERY)
    assert summary.status_code == 200
    totals = summary.json()["monthly"]
    assert (
        totals
        == summary.json()["iteration"]
        == {
            "operation_count": 1,
            "actual": actual if actual is not None else 0,
            "confirmed_estimate": 40 if actual is not None else 0,
            "reserved": exposure if actual is None and state != "unconfirmed" else 0,
            "unconfirmed": exposure if state == "unconfirmed" else 0,
            "exposure": exposure,
            "delta": actual - 40 if actual is not None else 0,
        }
    )
    assert summary.json()["pause_reason"] == page["pause_reason"]


def reservation_event(connection, value, **changes):
    row = {
        "id": str(uuid4()),
        "project_id": "budget-fixture",
        "operation_id": value.request.operation_id,
        "principal_id": ADMIN.id,
        "principal_kind": "human",
        "principal_role": "admin",
        "principal_version": "a" * 64,
        "policy_revision": 1,
        "action": "reserve",
        "record": asdict(value),
        "occurred_at": NOW,
    }
    connection.execute(sa.insert(db.event).values(row | changes))


def test_reservation_time_uses_exact_original_event_not_later_activity(http, store):
    original = entry()
    with store.engine.begin() as connection:
        persist(connection, replace(original, state="settled", actual=0))
        reservation_event(connection, original)
        reservation_event(connection, original, action="settle", record={"private": "excluded"})
        reservation_event(
            connection, original, project_id="foreign", record={"private": "excluded"}
        )
        reservation_event(
            connection, original, operation_id="other", record={"private": "excluded"}
        )
    login(http, "admin")
    response = http.get(PATH)
    assert response.status_code == 200
    assert response.json()["operations"][0]["reserved_at"] == NOW.isoformat().replace("+00:00", "Z")
    assert response.json()["operations"][0]["actual"] == 0
    assert "private" not in response.text


@pytest.mark.parametrize(
    "mismatch", ["duplicate", "request", "approval", "autonomous", "state", "root"]
)
def test_reservation_time_rejects_ambiguous_or_substituted_event(http, store, mismatch):
    original = entry()
    recorded = original
    if mismatch == "request":
        req = replace(original.request, iteration_id="substituted")
        recorded = LedgerEntry(req, SpendApproval(ADMIN.id, req.digest))
    elif mismatch == "approval":
        recorded = replace(original, approval=SpendApproval("other", original.request.digest))
    elif mismatch == "autonomous":
        recorded = replace(original, reserved_autonomously=True)
    elif mismatch == "state":
        recorded = replace(original, state="dispatched")
    with store.engine.begin() as connection:
        persist(connection, original)
        reservation_event(
            connection, recorded, record={} if mismatch == "root" else asdict(recorded)
        )
        if mismatch == "duplicate":
            persist(connection, entry("b"))
            reservation_event(connection, original)
    login(http, "admin")
    response = http.get(PATH)
    assert response.status_code == 409
    assert response.json() == {"detail": "cost_state_rejected"}
    assert "substituted" not in response.text


def test_reservation_lookup_is_bounded_to_returned_page(http, store):
    with store.engine.begin() as connection:
        persist(connection, entry("a"))
        persist(connection, entry("b"))
        reservation_event(connection, entry("a"))
        reservation_event(connection, entry("b"), record={})
    login(http, "admin")
    first = http.get(PATH, params={"limit": 1})
    assert first.status_code == 200
    assert first.json()["operations"][0]["reserved_at"] is not None
    assert first.json()["next_after"] == "a"
    assert http.get(PATH, params={"limit": 1, "after": "a"}).status_code == 409


@pytest.mark.parametrize(
    ("intent", "kind", "job"),
    [
        (budget_tests.INTENT, "LLM review", None),
        (budget_tests.BATCH_INTENT, "LLM review", "annotation-job"),
        (DispatchIntent("platform", "fixture"), None, None),
    ],
)
def test_operation_metadata_uses_only_bound_dispatch_intent(http, store, intent, kind, job):
    with store.engine.begin() as connection:
        persist(connection, entry(), intent=intent)
    login(http, "admin")
    response = http.get(PATH)
    assert response.status_code == 200
    row = response.json()["operations"][0]
    assert row["kind"] == kind
    assert row["annotation_job_id"] == job
    for private in ("provider_id", "model_id", "release_id", "image_ids", "max_input_tokens"):
        assert private not in response.text
    assert "recipe_id" not in row


@pytest.mark.parametrize("pause", [None, "approval_exhausted"])
@pytest.mark.parametrize("state", ["reserved", "dispatched", "unconfirmed", "settled"])
def test_review_pause_is_separate_from_ledger_state_project_pause_and_totals(
    http, store, pause, state
):
    value = entry(state=state, actual=0 if state == "settled" else None)
    with store.engine.begin() as connection:
        persist(connection, value, intent=budget_tests.BATCH_INTENT, review_pause=pause)
    login(http, "admin")
    response = http.get(PATH)
    assert response.status_code == 200
    row = response.json()["operations"][0]
    assert row["review_pause"] == pause
    assert row["kind"] == "LLM review" and row["annotation_job_id"] == "annotation-job"
    assert row["state"] == state
    assert row["actual"] == value.actual and row["exposure"] == value.exposure
    assert response.json()["pause_reason"] is None
    summary = http.get(SUMMARY, params=SUMMARY_QUERY).json()
    assert summary["pause_reason"] is None
    assert summary["monthly"]["exposure"] == value.exposure
    assert summary["monthly"]["operation_count"] == 1


@pytest.mark.parametrize("intent", [budget_tests.INTENT, DispatchIntent("platform", "fixture")])
def test_review_pause_without_bound_batch_fails_closed(http, store, intent):
    with store.engine.begin() as connection:
        persist(connection, entry(), intent=intent, review_pause="approval_exhausted")
    login(http, "admin")
    response = http.get(PATH)
    assert response.status_code == 409
    assert response.json() == {"detail": "cost_state_rejected"}


@pytest.mark.parametrize(
    "change", ["intent", "job", "digest", "policy", "request", "root", "batch_root"]
)
def test_operation_metadata_rejects_substituted_binding(http, store, change):
    intent = budget_tests.BATCH_INTENT
    with store.engine.begin() as connection:
        persist(connection, entry(), intent=intent)
        changes = {
            "intent": {"intent": asdict(replace(intent, stage="platform", review_batch=None))},
            "job": {
                "intent": asdict(
                    replace(
                        intent,
                        review_batch=replace(budget_tests.BATCH, annotation_job_id="substituted"),
                    )
                )
            },
            "digest": {"dry_run_digest": "b" * 64},
            "policy": {"policy_revision": 2},
            "request": {
                "entry": asdict(
                    LedgerEntry(
                        req := replace(request(), iteration_id="substituted"),
                        SpendApproval(ADMIN.id, req.digest),
                    )
                )
            },
            "root": {"intent": {"private": "excluded"}},
            "batch_root": {"intent": asdict(intent) | {"review_batch": 1}},
        }
        connection.execute(sa.update(db.operation).values(**changes[change]))
    login(http, "admin")
    response = http.get(PATH)
    assert response.status_code == 409
    assert response.json() == {"detail": "cost_state_rejected"}


def test_autonomous_reservation_does_not_invent_human_approval(http, store):
    value = LedgerEntry(request(), reserved_autonomously=True)
    with store.engine.begin() as connection:
        persist(connection, value)
    login(http, "admin")
    response = http.get(PATH)
    assert response.status_code == 200
    row = response.json()["operations"][0]
    assert row["approved_by"] is None
    assert row["reserved_autonomously"] is True


def test_openapi_publishes_typed_read_contract():
    contract = app.openapi()
    response = contract["paths"]["/projects/{project_key}/costs"]["get"]["responses"]["200"]
    assert response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/BudgetPage"
    }
    assert "policy_revision" in contract["components"]["schemas"]["BudgetPage"]["required"]
    operation = contract["components"]["schemas"]["CostOperation"]
    assert {"reserved_at", "kind", "annotation_job_id", "review_pause"} <= set(
        operation["required"]
    )
    assert operation["properties"]["review_pause"]["anyOf"] == [
        {"type": "string", "const": "approval_exhausted"},
        {"type": "null"},
    ]
    assert operation["properties"]["kind"]["anyOf"] == [
        {"type": "string", "const": "LLM review"},
        {"type": "null"},
    ]
    assert operation["properties"]["reserved_at"]["anyOf"] == [
        {"type": "string", "format": "date-time"},
        {"type": "null"},
    ]


def test_bounded_keyset_pages_and_project_isolation(http, store):
    with store.engine.begin() as connection:
        for key in ("c", "a", "b"):
            persist(connection, entry(key))
        foreign = replace(request("foreign-operation"), project_id="foreign")
        value = LedgerEntry(foreign, SpendApproval(ADMIN.id, foreign.digest))
        connection.execute(
            sa.insert(db.operation).values(
                project_id="foreign",
                operation_id="foreign-operation",
                dry_run_digest="a" * 64,
                policy_revision=1,
                entry=asdict(value),
                intent={},
            )
        )
    login(http, "admin")
    first = http.get(PATH, params={"limit": 2}).json()
    assert [row["operation_id"] for row in first["operations"]] == ["a", "b"]
    assert first["next_after"] == "b"
    second = http.get(PATH, params={"limit": 2, "after": first["next_after"]}).json()
    assert [row["operation_id"] for row in second["operations"]] == ["c"]
    assert second["next_after"] is None
    assert http.get(PATH, params={"after": "z"}).json()["operations"] == []
    assert first["policy"] == asdict(BudgetPolicy(100, 100, 100))


def test_cursor_accepts_full_stored_identity_contract(http, store):
    key = "long operation " + "x" * 241
    assert len(key) == 256
    with store.engine.begin() as connection:
        persist(connection, entry(key))
        persist(connection, entry("z"))
    login(http, "admin")
    page = http.get(PATH, params={"limit": 1}).json()
    assert page["next_after"] == key
    assert http.get(PATH, params={"after": key}).json()["operations"][0]["operation_id"] == "z"


@pytest.mark.parametrize(
    "name", [None, "reviewer", "worker-core", "worker-ml", "worker-llm", "worker-platform"]
)
@pytest.mark.parametrize("path", [PATH, PATH + "/export"])
def test_denial_precedes_any_cost_query(http, store, monkeypatch, name, path):
    def forbidden():
        raise AssertionError("unauthorized cost read")

    monkeypatch.setattr(budget_reads, "auth_store", forbidden)
    headers = {}
    if name and name.startswith("worker-"):
        headers["authorization"] = "Bearer " + worker_token(store, name)
    elif name:
        login(http, name)
    response = http.get(path, headers=headers)
    assert response.status_code == (401 if name is None else 403)
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 101}, {"after": " "}, {"after": "x" * 257}]
)
def test_query_bounds(http, params):
    login(http, "admin")
    response = http.get(PATH, params=params)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}


@pytest.mark.parametrize("key", ["missing", "unconfigured"])
def test_absent_budget_is_not_invented_as_empty(http, key):
    login(http, "admin")
    response = http.get(f"/projects/{key}/costs")
    assert response.status_code == 404
    assert response.json() == {"detail": "cost_scope_not_found"}


@pytest.mark.parametrize(
    "change", [{"actual": 3}, {"request": {}}, {"state": "private-invalid-state"}]
)
def test_malformed_entry_fails_closed(http, store, change):
    with store.engine.begin() as connection:
        persist(connection, entry())
        connection.execute(sa.update(db.operation).values(entry=asdict(entry()) | change))
    login(http, "admin")
    response = http.get(PATH)
    assert response.status_code == 409
    assert response.json() == {"detail": "cost_state_rejected"}


def test_substituted_operation_identity_rejects(http, store):
    with store.engine.begin() as connection:
        persist(connection, entry(), key="different")
    login(http, "admin")
    assert http.get(PATH).status_code == 409


@pytest.mark.parametrize("error", [ValueError("private"), SQLAlchemyError("private")])
def test_projection_failure_rolls_back_and_redacts(http, store, monkeypatch, error):
    def fail(connection, **kwargs):
        persist(connection, entry("must-roll-back"))
        raise error

    monkeypatch.setattr(budget_reads, "load_budget_page", fail)
    login(http, "admin")
    response = http.get(PATH)
    assert response.status_code == (409 if isinstance(error, ValueError) else 503)
    assert "private" not in response.text
    with store.engine.connect() as connection:
        assert (
            connection.execute(sa.select(sa.func.count()).select_from(db.operation)).scalar_one()
            == 0
        )


EXPORT = PATH + "/export"


def test_export_contains_complete_project_not_only_first_page(http, store):
    with store.engine.begin() as connection:
        for index in range(101):
            persist(connection, entry(f"operation-{index:03}"))
        before = connection.execute(sa.select(db.operation)).mappings().all()
    login(http, "admin")
    page = http.get(PATH).json()
    assert len(page["operations"]) == 50 and page["next_after"] is not None
    response = http.get(EXPORT)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    exported = response.json()
    assert exported["next_after"] is None
    assert [row["operation_id"] for row in exported["operations"]] == [
        f"operation-{index:03}" for index in range(101)
    ]
    assert exported["operations"][:50] == page["operations"]
    assert "must-not-leak" not in response.text
    with store.engine.connect() as connection:
        assert connection.execute(sa.select(db.operation)).mappings().all() == before
    assert http.get("/projects/foreign/costs/export").json()["operations"] == []


@pytest.mark.parametrize("count", [0, 2, 3])
def test_export_bound_returns_all_or_refuses(http, store, monkeypatch, count):
    monkeypatch.setattr(budget_reads, "SUMMARY_MAX_OPERATIONS", 2)
    with store.engine.begin() as connection:
        for index in range(count):
            persist(connection, entry(f"operation-{index}"))
    login(http, "admin")
    response = http.get(EXPORT)
    assert response.headers["cache-control"] == "no-store"
    if count <= 2:
        assert response.status_code == 200
        assert len(response.json()["operations"]) == count
        assert response.json()["next_after"] is None
    else:
        assert response.status_code == 409
        assert response.json() == {"detail": "cost_state_rejected"}
        assert "operation-" not in response.text


@pytest.mark.parametrize("key", ["missing", "unconfigured"])
def test_export_requires_existing_configured_project(http, key):
    login(http, "admin")
    response = http.get(f"/projects/{key}/costs/export")
    assert response.status_code == 404
    assert response.json() == {"detail": "cost_scope_not_found"}


def test_export_rejects_invalid_later_operation_without_partial_body(http, store):
    with store.engine.begin() as connection:
        for index in range(51):
            persist(connection, entry(f"operation-{index:03}"))
        connection.execute(
            sa.update(db.operation)
            .where(db.operation.c.operation_id == "operation-050")
            .values(dry_run_digest="0" * 64)
        )
    login(http, "admin")
    assert http.get(PATH).status_code == 200
    response = http.get(EXPORT)
    assert response.status_code == 409
    assert response.json() == {"detail": "cost_state_rejected"}


@pytest.mark.parametrize("error", [ValueError("private"), SQLAlchemyError("private")])
def test_export_failure_rolls_back_and_redacts(http, store, monkeypatch, error):
    def fail(connection, **kwargs):
        persist(connection, entry("must-roll-back"))
        raise error

    monkeypatch.setattr(budget_reads, "load_budget_export", fail)
    login(http, "admin")
    response = http.get(EXPORT)
    assert response.status_code == (409 if isinstance(error, ValueError) else 503)
    assert "private" not in response.text
    assert response.headers["cache-control"] == "no-store"
    with store.engine.connect() as connection:
        assert not connection.execute(sa.select(db.operation)).all()


def test_export_openapi_cannot_advertise_partial_cursor():
    contract = app.openapi()
    route = contract["paths"]["/projects/{project_key}/costs/export"]["get"]
    assert route["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/BudgetExport"
    }
    assert (
        contract["components"]["schemas"]["BudgetExport"]["properties"]["next_after"]["type"]
        == "null"
    )


SUMMARY = PATH + "/summary"
SUMMARY_QUERY = {"month": "2026-11", "iteration_id": "iteration"}


def test_summary_is_complete_across_pages_and_retains_child_exposure(http, store):
    values = [
        entry("a", state="settled", actual=0),
        entry("b", state="settled", actual=30),
        entry("c", state="unconfirmed", exposure_floor=75),
        entry("d", state="dispatched", exposure_floor=60),
        entry("e", state="cancelled"),
    ]
    # Iteration totals span months; month totals span iterations. Neither is a page sum.
    older = replace(request("f"), month="2026-10")
    other = replace(request("g"), iteration_id="other", estimate=65)
    values.extend(LedgerEntry(req, SpendApproval(ADMIN.id, req.digest)) for req in (older, other))
    with store.engine.begin() as connection:
        for value in values:
            persist(connection, value)
    login(http, "admin")
    assert len(http.get(PATH, params={"limit": 1}).json()["operations"]) == 1
    response = http.get(SUMMARY, params=SUMMARY_QUERY)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["month"] == "2026-11" and data["iteration_id"] == "iteration"
    assert data["iteration"] == {
        "operation_count": 6,
        "actual": 30,
        "confirmed_estimate": 80,
        "reserved": 100,
        "unconfirmed": 75,
        "exposure": 205,
        "delta": -50,
    }
    assert data["monthly"] == data["iteration"] | {"reserved": 125, "exposure": 230}
    assert data["policy_revision"] == 1
    assert data["policy"] == asdict(BudgetPolicy(100, 100, 100))
    assert "must-not-leak" not in response.text
    assert "prices" not in data and "operations" not in data
    unmatched = http.get(SUMMARY, params={"month": "2026-12", "iteration_id": "missing"}).json()
    assert unmatched["monthly"] == unmatched["iteration"] == {key: 0 for key in data["monthly"]}


@pytest.mark.parametrize(
    "name", [None, "reviewer", "worker-core", "worker-ml", "worker-llm", "worker-platform"]
)
def test_summary_denial_precedes_any_ledger_read(http, store, monkeypatch, name):
    def forbidden():
        raise AssertionError("unauthorized summary read")

    monkeypatch.setattr(budget_reads, "auth_store", forbidden)
    headers = {}
    if name and name.startswith("worker-"):
        headers["authorization"] = "Bearer " + worker_token(store, name)
    elif name:
        login(http, name)
    response = http.get(SUMMARY, params=SUMMARY_QUERY, headers=headers)
    assert response.status_code == (401 if name is None else 403)
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "query",
    [
        {},
        {"month": "2026-11"},
        {"iteration_id": "iteration"},
        SUMMARY_QUERY | {"month": "0000-01"},
        SUMMARY_QUERY | {"month": "2026-13"},
        SUMMARY_QUERY | {"month": "2026-1"},
        SUMMARY_QUERY | {"iteration_id": " "},
        SUMMARY_QUERY | {"iteration_id": "x" * 257},
    ],
)
def test_summary_query_requires_exact_scopes(http, query):
    login(http, "admin")
    response = http.get(SUMMARY, params=query)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}


@pytest.mark.parametrize("key", ["missing", "unconfigured"])
def test_summary_does_not_manufacture_missing_budget(http, key):
    login(http, "admin")
    response = http.get(f"/projects/{key}/costs/summary", params=SUMMARY_QUERY)
    assert response.status_code == 404
    assert response.json() == {"detail": "cost_scope_not_found"}


def test_summary_is_project_scoped_and_rejects_off_month_substitution(http, store):
    foreign = replace(request("foreign-operation"), project_id="foreign")
    with store.engine.begin() as connection:
        connection.execute(
            sa.insert(db.operation).values(
                project_id="foreign",
                operation_id=foreign.operation_id,
                dry_run_digest="a" * 64,
                policy_revision=1,
                entry=asdict(LedgerEntry(foreign, reserved_autonomously=True)),
                intent={},
            )
        )
    login(http, "admin")
    response = http.get(SUMMARY, params=SUMMARY_QUERY)
    assert response.status_code == 200
    assert response.json()["monthly"]["operation_count"] == 0
    with store.engine.begin() as connection:
        persist(connection, entry(), key="substituted")
    # A cursor/month/iteration must not hide malformed ledger members.
    response = http.get(SUMMARY, params={"month": "2026-12", "iteration_id": "missing"})
    assert response.status_code == 409
    assert response.json() == {"detail": "cost_state_rejected"}


def test_summary_exhaustion_refuses_instead_of_returning_partial_totals(http, store, monkeypatch):
    monkeypatch.setattr(budget_reads, "SUMMARY_MAX_OPERATIONS", 1)
    with store.engine.begin() as connection:
        persist(connection, entry("a"))
        persist(connection, entry("b"))
    login(http, "admin")
    response = http.get(SUMMARY, params=SUMMARY_QUERY)
    assert response.status_code == 409
    assert response.json() == {"detail": "cost_state_rejected"}


@pytest.mark.parametrize("error", [ValueError("private"), SQLAlchemyError("private")])
def test_summary_failure_rolls_back_and_redacts(http, store, monkeypatch, error):
    def fail(connection, **kwargs):
        persist(connection, entry("must-roll-back"))
        raise error

    monkeypatch.setattr(budget_reads, "load_budget_summary", fail)
    login(http, "admin")
    response = http.get(SUMMARY, params=SUMMARY_QUERY)
    assert response.status_code == (409 if isinstance(error, ValueError) else 503)
    assert "private" not in response.text
    with store.engine.connect() as connection:
        assert (
            connection.execute(sa.select(sa.func.count()).select_from(db.operation)).scalar_one()
            == 0
        )


def test_summary_openapi_has_typed_scopes_and_totals():
    contract = app.openapi()
    route = contract["paths"]["/projects/{project_key}/costs/summary"]["get"]
    assert route["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/BudgetSummary"
    }
    assert {item["name"] for item in route["parameters"] if item["required"]} == {
        "project_key",
        "month",
        "iteration_id",
    }


def test_postgres_committed_ledger_http_recovery(monkeypatch, store, postgres_budget):
    # Real auth sessions and real disposable PostgreSQL ledger; neither is bypassed.
    reserve(postgres_budget)
    claim(postgres_budget)
    postgres_budget.outcome("budget-fixture", "one", actor=LLM, status="unconfirmed")
    monkeypatch.setattr(
        budget_reads, "auth_store", lambda: SimpleNamespace(engine=postgres_budget.engine)
    )
    with TestClient(app, base_url=ORIGIN) as client:
        login(client, "admin")
        page = client.get(PATH)
        assert page.status_code == 200
        assert page.json()["operations"][0]["actual"] is None
        assert page.json()["operations"][0]["exposure"] == 40
        assert page.json()["operations"][0]["kind"] == "LLM review"
        assert page.json()["operations"][0]["annotation_job_id"] is None
        export = client.get(PATH + "/export")
        assert export.status_code == 200
        assert export.headers["cache-control"] == "no-store"
        assert export.json() == page.json()
        with postgres_budget.engine.connect() as connection:
            reserved_at = connection.execute(
                sa.select(db.event.c.occurred_at).where(
                    db.event.c.project_id == "budget-fixture",
                    db.event.c.operation_id == "one",
                    db.event.c.action == "reserve",
                )
            ).scalar_one()
        assert page.json()["operations"][0]["reserved_at"] == reserved_at.isoformat().replace(
            "+00:00", "Z"
        )
        summary = client.get(SUMMARY, params=SUMMARY_QUERY)
        assert summary.status_code == 200
        assert (
            summary.json()["monthly"]
            == summary.json()["iteration"]
            == {
                "operation_count": 1,
                "actual": 0,
                "confirmed_estimate": 0,
                "reserved": 0,
                "unconfirmed": 40,
                "exposure": 40,
                "delta": 0,
            }
        )
    before = postgres_budget.snapshot("budget-fixture")
    with TestClient(app, base_url=ORIGIN) as client:
        login(client, "admin")
        recovered = client.get(PATH)
        assert recovered.json() == page.json()
        assert client.get(PATH + "/export").json() == export.json()
        recovered_summary = client.get(SUMMARY, params=SUMMARY_QUERY)
        assert recovered_summary.json() == summary.json()
    assert postgres_budget.snapshot("budget-fixture") == before
    assert before.entry("one").state == "unconfirmed"


@pytest.mark.parametrize("batch", [False, True])
def test_postgres_bound_operation_metadata_recovers_after_policy_change(
    monkeypatch, store, postgres_budget, batch
):
    if batch:
        budget_tests.configure_review(postgres_budget)
        dry = budget_tests.reserve_review(postgres_budget)
    else:
        req = request()
        dry = postgres_budget.prepare(req, DispatchIntent("platform", "fixture"), actor=ADMIN)
        postgres_budget.reserve("budget-fixture", "one", dry.digest, actor=ADMIN)
    # The operation retains its reservation revision, never today's policy digest.
    postgres_budget.set_policy(
        "budget-fixture",
        actor=ADMIN,
        expected_revision=dry.policy_revision,
        policy=BudgetPolicy(100, 100, 100),
        pixel_providers=("fixture",),
    )
    monkeypatch.setattr(
        budget_reads, "auth_store", lambda: SimpleNamespace(engine=postgres_budget.engine)
    )
    with postgres_budget.engine.connect() as connection:
        before = connection.execute(sa.select(db.operation)).mappings().all()
        events = connection.execute(sa.select(db.event).order_by(db.event.c.id)).mappings().all()
    first = None
    for _ in range(2):
        with TestClient(app, base_url=ORIGIN) as client:
            login(client, "admin")
            response = client.get(PATH)
            assert response.status_code == 200
            value = response.json()
            row = value["operations"][0]
            assert row["kind"] == ("LLM review" if batch else None)
            assert row["annotation_job_id"] == ("annotation-job" if batch else None)
            assert row["actual"] is None
            assert row["exposure"] == 40
            assert value["policy_revision"] == dry.policy_revision + 1
            assert "provider_id" not in response.text
            assert "image_ids" not in response.text
            if first is not None:
                assert value == first
            first = value
    with postgres_budget.engine.connect() as connection:
        assert connection.execute(sa.select(db.operation)).mappings().all() == before
        assert (
            connection.execute(sa.select(db.event).order_by(db.event.c.id)).mappings().all()
            == events
        )


@pytest.mark.parametrize("export", [False, True])
def test_postgres_summary_retains_writer_lock_until_outer_commit(postgres_budget, export):
    reserve(postgres_budget)
    with postgres_budget.engine.begin() as connection:
        if export:
            result = budget_reads.load_budget_export(connection, project_key="budget-fixture")
            assert result.operations[0].exposure == 40
            assert result.next_after is None
        else:
            summary = budget_reads.load_budget_summary(
                connection, project_key="budget-fixture", **SUMMARY_QUERY
            )
            assert summary.monthly.exposure == 40
        # A separate writer connection cannot take the head before this read commits.
        with postgres_budget.engine.begin() as writer:
            writer.execute(sa.text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(sa.exc.OperationalError) as blocked:
                writer.execute(
                    sa.select(db.head)
                    .where(db.head.c.project_id == "budget-fixture")
                    .with_for_update()
                )
            assert blocked.value.orig.sqlstate == "55P03"
            writer.rollback()
    with postgres_budget.engine.begin() as writer:
        assert writer.execute(
            sa.select(db.head)
            .where(db.head.c.project_id == "budget-fixture")
            .with_for_update(nowait=True)
        ).one()
