"""Committed review/promotion history through real HTTP on disposable PostgreSQL."""

import json

import sqlalchemy as sa
from registry_http_smoke import authenticated_client, login, qualification_journals, snapshot

from labelloop_api import model_schema as models
from labelloop_api import schema as db


def exercise_audit_reads(engine, project_key):
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
            return reviews, promotions

    before = records()
    assert before[0] and before[1]
    registry = snapshot(engine, project_key)
    journals = qualification_journals(engine)
    expected = {
        (kind, str(row["id"])): row
        for kind, rows in zip(("Review", "Promotion"), before, strict=True)
        for row in rows
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
        for event in events:
            saved = expected[(event["kind"], event["id"])]
            assert event["actor_id"] == saved["actor_id"]
            assert event["actor_kind"] == (
                saved["actor_kind"] if event["kind"] == "Review" else "unknown"
            )
            assert set(event) == {
                "id",
                "at",
                "kind",
                "actor_id",
                "actor_kind",
                "record_id",
                "revision",
                "sentence",
            }
        export = client.get(path + "/export")
        assert export.status_code == 200
        assert [json.loads(line) for line in export.text.splitlines()] == events
        assert client.get("/projects/missing-audit-project/audit-log").status_code == 404

        # A new authenticated principal gets a new authorized query, not the
        # administrator's count, cursor or export result.
        client.cookies.clear()
        login(client, identities["reviewer"][1])
        visible = client.get(path).json()
        assert visible["total"] == len(before[0])
        assert all(event["kind"] == "Review" for event in visible["events"])
        assert client.get(path, params={"kind": "Promotion"}).json()["total"] == 0
        assert client.get(path + "/export", params={"kind": "Promotion"}).text == ""
        promotion = next(event for event in events if event["kind"] == "Promotion")
        denied_cursor = client.get(path, params={"after": f"Promotion/{promotion['id']}"})
        assert denied_cursor.status_code == 409
        assert denied_cursor.json() == {"detail": "audit_state_rejected"}
    assert records() == before
    assert snapshot(engine, project_key) == registry
    assert qualification_journals(engine) == journals
    return True
