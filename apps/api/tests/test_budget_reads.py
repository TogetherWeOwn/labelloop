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
from labelloop_api.main import app
from labelloop_api.schema import project
from labelloop_core.budget import BudgetPolicy, LedgerEntry, SpendApproval

PATH = "/projects/budget-fixture/costs"
postgres_budget = budget_tests.store


def entry(key="one", **kwargs):
    req = request(key)
    return LedgerEntry(req, SpendApproval(ADMIN.id, req.digest), **kwargs)


def persist(connection, value, *, key=None):
    connection.execute(
        sa.insert(db.operation).values(
            project_id="budget-fixture",
            operation_id=key or value.request.operation_id,
            dry_run_digest="a" * 64,
            policy_revision=1,
            entry=asdict(value),
            intent={"private": "must-not-leak"},
            evidence={"private": "must-not-leak"},
        )
    )


@pytest.fixture
def http(monkeypatch, store):
    with store.engine.begin() as connection:
        project.create(connection)
        db.head.create(connection)
        db.operation.create(connection)
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
def test_projection_preserves_actual_exposure_and_redacts_private_records(
    http, store, state, actual, floor, exposure
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
    response = http.get(PATH)
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
        }
    ]
    assert "must-not-leak" not in response.text


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
def test_denial_precedes_any_cost_query(http, store, monkeypatch, name):
    def forbidden():
        raise AssertionError("unauthorized cost read")

    monkeypatch.setattr(budget_reads, "auth_store", forbidden)
    headers = {}
    if name and name.startswith("worker-"):
        headers["authorization"] = "Bearer " + worker_token(store, name)
    elif name:
        login(http, name)
    response = http.get(PATH, headers=headers)
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
    before = postgres_budget.snapshot("budget-fixture")
    with TestClient(app, base_url=ORIGIN) as client:
        login(client, "admin")
        recovered = client.get(PATH)
        assert recovered.json() == page.json()
    assert postgres_budget.snapshot("budget-fixture") == before
    assert before.entry("one").state == "unconfirmed"
