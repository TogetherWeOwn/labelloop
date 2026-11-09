"""Committed review/promotion history through real HTTP on disposable PostgreSQL."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from registry_http_smoke import authenticated_client, login, qualification_journals, snapshot

from labelloop_api import budget_schema as budgets
from labelloop_api import model_schema as models
from labelloop_api import schema as db
from labelloop_api.auth_schema import principal
from labelloop_api.auth_store import Principal
from labelloop_api.budget_store import BudgetStore
from labelloop_core.budget import BudgetPolicy


def exercise_audit_reads(engine, project_key):
    # Persist a real policy event before reconnecting through the audit read. A
    # zero-limit fixture policy authorizes no paid operation or provider call.
    ledger = BudgetStore(engine)
    with engine.connect() as connection:
        account = (
            connection.execute(
                sa.select(principal)
                .where(principal.c.kind == "human", principal.c.role == "admin")
                .order_by(principal.c.id)
                .limit(1)
            )
            .mappings()
            .one()
        )
        revision = connection.execute(
            sa.select(budgets.head.c.revision).where(budgets.head.c.project_id == project_key)
        ).scalar_one_or_none()
    ledger.set_policy(
        project_key,
        actor=Principal(account["id"], account["name"], account["kind"], account["role"]),
        expected_revision=revision or 0,
        policy=BudgetPolicy(0, 0, 0),
    )
    # Synthetic journal input tests PostgreSQL projection/recovery, not emission
    # by a real paid attempt. The zero-limit policy and every ledger row stay put.
    refusal_id = str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            sa.insert(budgets.event).values(
                id=refusal_id,
                project_id=project_key,
                operation_id=None,
                principal_id=account["id"],
                principal_kind=account["kind"],
                principal_role=account["role"],
                principal_version="synthetic-audit-fixture",
                policy_revision=(revision or 0) + 1,
                action="approval_exhausted",
                record={"private": "must-not-leak"},
                occurred_at=datetime.now(UTC),
            )
        )
    budget_before = ledger.snapshot(project_key)

    def records():
        with engine.connect() as connection:
            identity = connection.execute(
                sa.select(db.project.c.id).where(db.project.c.project_key == project_key)
            ).scalar_one()
            reviews = (
                connection.execute(
                    sa.select(db.review_event)
                    .join(db.source_asset)
                    .where(db.source_asset.c.project_id == identity)
                    .order_by(db.review_event.c.id)
                )
                .mappings()
                .all()
            )
            promotions = (
                connection.execute(
                    sa.select(models.promotion_event).where(
                        models.promotion_event.c.project_id == identity
                    )
                )
                .mappings()
                .all()
            )
            budget_events = (
                connection.execute(
                    sa.select(budgets.event)
                    .where(budgets.event.c.project_id == project_key)
                    .order_by(budgets.event.c.id)
                )
                .mappings()
                .all()
            )
            return reviews, promotions, budget_events

    before = records()
    assert before[0] and before[1]
    registry = snapshot(engine, project_key)
    journals = qualification_journals(engine)
    expected = {
        (kind, str(row["id"])): row
        for kind, rows in zip(("Review", "Promotion", "Budget"), before, strict=True)
        for row in rows
    }
    with engine.connect() as connection:
        directory = {
            row["id"]: row
            for row in connection.execute(
                sa.select(principal.c.id, principal.c.name, principal.c.kind).where(
                    principal.c.id.in_(
                        {row["actor_id"] for row in (*before[0], *before[1])}
                        | {row["principal_id"] for row in before[2]}
                    )
                )
            ).mappings()
        }
    path = f"/projects/{project_key}/audit-log"
    with authenticated_client(engine) as (client, identities, workers):
        for suffix in ("", "/export"):
            assert client.get(path + suffix).status_code == 401
            for token in workers.values():
                assert (
                    client.get(
                        path + suffix, headers={"authorization": "Bearer " + token}
                    ).status_code
                    == 403
                )
        login(client, identities["admin"][1])
        events = []
        after = None
        for _ in range(len(expected) + 1):
            response = client.get(path, params={"limit": 1} | ({"after": after} if after else {}))
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            page = response.json()
            assert page["total"] == len(expected)
            events.extend(page["events"])
            after = page["next_after"]
            if after is None:
                break
        else:
            raise AssertionError("audit pagination did not terminate")
        assert len(events) == len(expected)
        assert {(event["kind"], event["id"]) for event in events} == set(expected)
        refusal = next(event for event in events if event["id"] == refusal_id)
        assert refusal["kind"] == "Budget" and refusal["refused"] is True
        for event in events:
            saved = expected[(event["kind"], event["id"])]
            assert (
                event["actor_id"]
                == saved["principal_id" if event["kind"] == "Budget" else "actor_id"]
            )
            assert event["actor_kind"] == (
                saved["principal_kind"]
                if event["kind"] == "Budget"
                else saved["actor_kind"]
                if event["kind"] == "Review"
                else "unknown"
            )
            if event["kind"] == "Budget":
                with engine.connect() as connection:
                    project_id = connection.execute(
                        sa.select(db.project.c.id).where(db.project.c.project_key == project_key)
                    ).scalar_one()
                assert event["record_id"] == str(project_id)
                assert event["revision"] == saved["policy_revision"]
                refused = True if saved["action"] == "approval_exhausted" else None
                assert event["refused"] is refused
                assert event["sentence"] == (
                    "Refused a review request: approval exhausted."
                    if refused
                    else "Recorded a budget event."
                )
            else:
                assert event["refused"] is None
            account = directory.get(event["actor_id"])
            compatible = account is not None and (
                event["actor_kind"] == "unknown"
                or (event["actor_kind"] == "human" and account["kind"] == "human")
                or (
                    event["actor_kind"] in ("model", "system", "service")
                    and account["kind"] == "service"
                )
            )
            assert event["actor_name"] == (account["name"] if compatible else None)
            assert set(event) == {
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
        named_promotion = next(
            event for event in events if event["kind"] == "Promotion" and event["actor_name"]
        )
        export = client.get(path + "/export")
        assert export.status_code == 200
        assert [json.loads(line) for line in export.text.splitlines()] == events
        assert "must-not-leak" not in export.text
        assert "synthetic-audit-fixture" not in export.text
        # Filter complete committed history before counts/cursors, and preserve
        # exact page/export equality on the real PostgreSQL UUID representation.
        for params, matching in (
            (
                {
                    "kind": ["Review", "Budget"],
                    "actor_kind": ["human", "model", "system", "service"],
                },
                [event for event in events if event["kind"] in ("Review", "Budget")],
            ),
            (
                {
                    "kind": ["Budget", "Review"],
                    "actor_kind": ["service", "system", "model", "human"],
                },
                [event for event in events if event["kind"] in ("Review", "Budget")],
            ),
            (
                {"kind": ["Budget", "Budget"], "actor_kind": ["human", "human"]},
                [
                    event
                    for event in events
                    if event["kind"] == "Budget" and event["actor_kind"] == "human"
                ],
            ),
            ({"search": events[0]["id"]}, [events[0]]),
            (
                {"search": events[0]["record_id"]},
                [event for event in events if event["record_id"] == events[0]["record_id"]],
            ),
            *(
                (
                    {"actor_kind": actor_kind},
                    [event for event in events if event["actor_kind"] == actor_kind],
                )
                for actor_kind in ("human", "model", "system", "service", "unknown")
            ),
            (
                {"search": events[0]["actor_id"].upper(), "actor_kind": events[0]["actor_kind"]},
                [
                    event
                    for event in events
                    if events[0]["actor_id"].lower() in event["actor_id"].lower()
                    and event["actor_kind"] == events[0]["actor_kind"]
                ],
            ),
            (
                {"search": named_promotion["actor_name"].upper(), "actor_kind": "unknown"},
                [
                    event
                    for event in events
                    if event["actor_kind"] == "unknown"
                    and event["actor_name"] == named_promotion["actor_name"]
                ],
            ),
            ({"search": identities["admin"][1]}, []),
            ({"search": "%"}, [event for event in events if "%" in event["actor_id"]]),
        ):
            response = client.get(path, params=params | {"limit": 1})
            assert response.status_code == 200
            filtered = response.json()
            assert filtered["total"] == len(matching)
            assert filtered["events"] == matching[:1]
            filtered_export = client.get(path + "/export", params=params)
            assert filtered_export.status_code == 200
            assert [json.loads(line) for line in filtered_export.text.splitlines()] == matching
            if len(matching) > 1:
                next_page = client.get(
                    path, params=params | {"limit": 1, "after": filtered["next_after"]}
                ).json()
                assert next_page["total"] == len(matching)
                assert next_page["events"] == matching[1:2]
        excluded = next(event for event in events if event["kind"] == "Promotion")
        assert (
            client.get(
                path,
                params={"kind": ["Review", "Budget"], "after": f"Promotion/{excluded['id']}"},
            ).status_code
            == 409
        )
        for suffix in ("", "/export"):
            for invalid in (
                {"kind": ["Review", "Connection", "Budget"]},
                {"kind": ["Review"] * 4},
                {"actor_kind": ["human", "person", "service"]},
                {"actor_kind": ["human"] * 6},
            ):
                response = client.get(path + suffix, params=invalid)
                assert response.status_code == 422
                assert response.json() == {"detail": "invalid_request"}
        assert client.get("/projects/missing-audit-project/audit-log").status_code == 404

        # A new authenticated principal gets a new authorized query, not the
        # administrator's count, cursor or export result.
        client.cookies.clear()
        login(client, identities["reviewer"][1])
        visible = client.get(path).json()
        assert visible["total"] == len(before[0])
        assert all(event["kind"] == "Review" for event in visible["events"])
        combined = {
            "kind": ["Review", "Promotion", "Budget"],
            "actor_kind": ["human", "model", "system", "service", "unknown"],
        }
        selected = client.get(path, params=combined)
        assert selected.status_code == 200
        assert selected.json() == visible
        exported = client.get(path + "/export", params=combined)
        assert exported.status_code == 200
        assert [json.loads(line) for line in exported.text.splitlines()] == [
            event for event in events if event["kind"] == "Review"
        ]
        for hidden_kind in ("Promotion", "Budget"):
            assert client.get(path, params={"kind": hidden_kind}).json()["total"] == 0
            assert client.get(path + "/export", params={"kind": hidden_kind}).text == ""
            hidden = next(event for event in events if event["kind"] == hidden_kind)
            assert (
                client.get(path, params={"after": f"{hidden_kind}/{hidden['id']}"}).status_code
                == 409
            )
            for search in (hidden["id"], hidden["actor_id"], hidden["actor_name"]):
                if search is None:
                    continue
                params = {"kind": hidden_kind, "search": search}
                assert client.get(path, params=params).json()["total"] == 0
                assert client.get(path + "/export", params=params).text == ""
        promotion = next(event for event in events if event["kind"] == "Promotion")
        denied_cursor = client.get(path, params={"after": f"Promotion/{promotion['id']}"})
        assert denied_cursor.status_code == 409
        assert denied_cursor.json() == {"detail": "audit_state_rejected"}
        for search in (
            promotion["id"],
            promotion["record_id"],
            promotion["actor_id"],
            named_promotion["actor_name"],
        ):
            params = {"search": search, "actor_kind": "unknown"}
            response = client.get(path, params=params)
            assert response.status_code == 200
            assert response.json() == {
                "project_id": project_key,
                "events": [],
                "total": 0,
                "next_after": None,
            }
            assert client.get(path + "/export", params=params).text == ""
            hidden_cursor = client.get(
                path, params=params | {"after": f"Promotion/{promotion['id']}"}
            )
            assert hidden_cursor.status_code == 409
            assert hidden_cursor.json() == {"detail": "audit_state_rejected"}
    assert ledger.snapshot(project_key) == budget_before
    assert records() == before
    assert snapshot(engine, project_key) == registry
    assert qualification_journals(engine) == journals
    return True
