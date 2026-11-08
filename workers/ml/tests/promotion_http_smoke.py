"""Real HTTP promotion over the sole-final fixture's disposable PostgreSQL savepoint.

The fixture is transactional, not a fresh-connection durability claim. Only connection
configuration is adapted: production sessions, CSRF, service, stores and gates run.
"""

from contextlib import contextmanager

import sqlalchemy as sa
from registry_http_smoke import (
    authenticated_client,
    login,
    qualification_journals,
    snapshot,
)

from labelloop_api.model_store import json_record
from labelloop_core.registry import RegistryState


class FixtureEngine:
    """Borrow the outer fixture connection without committing or closing its history."""

    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def connect(self):
        yield self.connection

    @contextmanager
    def begin(self):
        with self.connection.begin_nested():
            yield self.connection


def exercise_http_promotion(connection, candidate, before, arguments):
    engine = FixtureEngine(connection)
    path = f"/projects/{candidate.project_id}/promotions"
    payload = {
        key: value for key, value in arguments.items() if key not in {"actor_id", "project_key"}
    }
    payload["action"] = "promote"
    original = snapshot(engine, candidate.project_id)
    journals = qualification_journals(engine)
    with connection.begin_nested() as fixture:
        with authenticated_client(engine) as (client, identities, workers):
            assert client.post(path, json=payload).status_code == 401
            reviewer = login(client, identities["reviewer"][1])
            assert client.post(path, headers=reviewer, json=payload).status_code == 403
            client.cookies.clear()
            for token in workers.values():
                assert (
                    client.post(
                        path, headers={"authorization": "Bearer " + token}, json=payload
                    ).status_code
                    == 403
                )
            headers = login(client, identities["admin"][1])
            for denied in (
                {},
                headers | {"origin": "https://untrusted.invalid"},
                headers | {"x-csrf-token": "wrong"},
            ):
                assert client.post(path, headers=denied, json=payload).status_code == 403
            for extra in (
                {"actor_id": "forged"},
                {"qualification": {}},
                {"evaluation_key": "chosen"},
                {"require_fixture_policy": False},
            ):
                response = client.post(path, headers=headers, json=payload | extra)
                assert response.status_code == 422
                assert response.json() == {"detail": "invalid_request"}
            for target, extra, status in (
                (path, {"expected_revision": before.revision - 1}, 409),
                ("/projects/missing-http-project/promotions", {}, 404),
                (path, {"recipe_key": "missing-http-recipe", "expected_revision": 0}, 404),
                (path, {"model_key": "missing-http-model"}, 404),
                (path, {"annotation_release_key": "missing-http-population"}, 409),
                (path, {"canary_release_key": "missing-http-canary"}, 409),
            ):
                response = client.post(target, headers=headers, json=payload | extra)
                assert response.status_code == status
                assert response.json() == {
                    "detail": "registry_scope_not_found"
                    if status == 404
                    else "registry_change_rejected"
                }
                assert response.headers["cache-control"] == "no-store"
            assert snapshot(engine, candidate.project_id) == original
            assert qualification_journals(engine) == journals

            injected = []

            def fail_event(_conn, _cursor, statement, _parameters, _context, _many):
                if statement.startswith("INSERT INTO promotion_event"):
                    injected.append(True)
                    raise sa.exc.SQLAlchemyError("private fixture promotion event failure")

            sa.event.listen(connection, "before_cursor_execute", fail_event)
            try:
                response = client.post(path, headers=headers, json=payload)
            finally:
                sa.event.remove(connection, "before_cursor_execute", fail_event)
            assert injected == [True]
            assert response.status_code == 503
            assert response.json() == {"detail": "registry_unavailable"}
            assert snapshot(engine, candidate.project_id) == original
            assert qualification_journals(engine) == journals

            response = client.post(path, headers=headers, json=payload)
            expected = RegistryState(
                candidate.project_id,
                candidate.recipe_id,
                before.revision + 1,
                candidate,
                before.champion,
            )
            assert response.status_code == 200
            assert response.json() == json_record(expected)
            assert response.headers["cache-control"] == "no-store"
            after = snapshot(engine, candidate.project_id)
            index = 0 if candidate.policy.imgsz == 320 else 1
            assert after[0][index] == expected
            assert after[0][1 - index] == original[0][1 - index]
            assert len(after[1]) == len(original[1]) + 1
            added = next(row for row in after[1] if row not in original[1])
            assert added["actor_id"] == identities["admin"][0]
            assert added["action"] == "promote"
            assert added["record"]["after"] == json_record(expected)
            assert qualification_journals(engine) == journals
            for change in ({}, {"expected_revision": expected.revision}):
                replay = client.post(path, headers=headers, json=payload | change)
                assert replay.status_code == 409
                assert replay.json() == {"detail": "registry_change_rejected"}
                assert snapshot(engine, candidate.project_id) == after
        fixture.rollback()
    assert snapshot(engine, candidate.project_id) == original
    assert qualification_journals(engine) == journals
