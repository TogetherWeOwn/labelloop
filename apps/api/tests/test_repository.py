"""Offline storage and transaction boundaries; integration is the Postgres dev stack."""

import io
from unittest.mock import MagicMock

import pytest
from PIL import Image
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from labelloop_api.database_url import database_url, quoted_database_url
from labelloop_api.repository import ingest_canonical_asset
from labelloop_api.revision_store import append_review
from labelloop_api.schema import annotation_revision, review_event, source_asset


def test_content_hash_is_database_and_object_store_identity() -> None:
    for column in (source_asset.c.sha256, source_asset.c.object_key):
        assert column.compile(dialect=postgresql.dialect())


def test_invalid_ingest_touches_neither_database_nor_minio() -> None:
    connection = MagicMock()
    store = MagicMock()
    with pytest.raises(ValueError, match="decoded"):
        ingest_canonical_asset(
            connection,
            store,
            b"not an image",
            project_id="project",
            asset_id="asset",
            source_uri="fixture",
        )
    store.put.assert_not_called()
    connection.execute.assert_not_called()


def test_duplicate_asset_id_never_silently_appends_alias() -> None:
    output = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(output, format="JPEG")
    connection = MagicMock()
    connection.execute.return_value.scalar_one_or_none.return_value = "existing"
    store = MagicMock()
    with pytest.raises(ValueError, match="already assigned"):
        ingest_canonical_asset(
            connection,
            store,
            output.getvalue(),
            project_id="project",
            asset_id="asset",
            source_uri="fixture",
        )
    store.put.assert_called_once()
    assert connection.execute.call_count == 1


def test_sql_defaults_are_sql_not_uuid_literals() -> None:
    assert source_asset.c.id.server_default is not None
    ddl = str(CreateTable(source_asset).compile(dialect=postgresql.dialect()))
    assert "DEFAULT gen_random_uuid()" in ddl
    assert "DEFAULT 'gen_random_uuid()'" not in ddl


def test_review_intent_precedes_append_and_nonhuman_eligibility_is_denied() -> None:
    connection = MagicMock()
    asset_uuid = object()
    with pytest.raises(ValueError, match="eligibility"):
        append_review(
            connection,
            asset_id="asset",
            actor_kind="model",
            actor_id="worker",
            expected_revision=1,
            approval_state="auto_accepted",
            review_tier="machine_verified_no_human_review",
            scope_completeness="exhaustive_for_scope",
            objects=[],
            training_eligible=True,
        )
    connection.execute.assert_not_called()

    def execute(statement, parameters=None):
        result = MagicMock()
        sql = str(statement)
        if "SELECT revision, actor_kind" in sql:
            result.mappings.return_value.one.return_value = {
                "revision": 1,
                "actor_kind": "system",
                "approval_state": "draft",
                "review_tier": "unreviewed",
                "objects": [],
            }
        elif "SELECT ontology_release.class_map" in sql:
            result.mappings.return_value.one.return_value = {
                "class_map": {"item": 0},
                "access_policy": {},
                "assessed_completeness": "exhaustive_for_scope",
                "output_truncated": False,
                "unresolved_regions": [],
            }
        else:
            result.one.return_value = (200, 150)
        return result

    connection = MagicMock()
    connection.execute.side_effect = execute
    with pytest.MonkeyPatch.context() as monkeypatch:
        from labelloop_api import revision_store

        monkeypatch.setattr(revision_store, "source_uuid", lambda _conn, _key: asset_uuid)
        revision = append_review(
            connection,
            asset_id="asset",
            actor_kind="human",
            actor_id="reviewer",
            expected_revision=1,
            approval_state="human_accepted",
            review_tier="adjudicated",
            scope_completeness="exhaustive_for_scope",
            objects=[],
            training_eligible=True,
        )
    assert revision == 2
    tables = [
        call.args[0].table.name
        for call in connection.execute.call_args_list
        if hasattr(call.args[0], "table") and hasattr(call.args[0].table, "name")
    ]
    assert tables.index(review_event.name) < tables.index(annotation_revision.name)


def review_connection(
    objects=None,
    actor_kind="human",
    approval_state="human_accepted",
    review_tier="adjudicated",
    qualification=None,
):
    connection = MagicMock()
    current_objects = [] if objects is None else objects

    def execute(statement, parameters=None):
        result = MagicMock()
        sql = str(statement)
        if "SELECT revision, actor_kind" in sql:
            result.mappings.return_value.one.return_value = {
                "revision": 1,
                "actor_kind": actor_kind,
                "approval_state": approval_state,
                "review_tier": review_tier,
                "objects": current_objects,
            }
        elif "SELECT ontology_release.class_map" in sql:
            result.mappings.return_value.one.return_value = {
                "class_map": {"item": 0},
                "access_policy": qualification or {},
                "assessed_completeness": "exhaustive_for_scope",
                "output_truncated": False,
                "unresolved_regions": [],
            }
        else:
            result.one.return_value = (100, 100)
        return result

    connection.execute.side_effect = execute
    return connection


def test_review_rejects_invalid_class_and_geometry_before_writing():
    common = {
        "asset_id": "asset",
        "actor_kind": "human",
        "actor_id": "reviewer",
        "expected_revision": 1,
        "approval_state": "human_accepted",
        "review_tier": "adjudicated",
        "scope_completeness": "exhaustive_for_scope",
        "training_eligible": True,
    }
    with pytest.MonkeyPatch.context() as monkeypatch:
        from labelloop_api import revision_store

        monkeypatch.setattr(revision_store, "source_uuid", lambda _conn, _key: "uuid")
        for item in (
            {"object_id": "one", "class_id": "unknown", "box": [0, 0, 5, 5]},
            {"object_id": "one", "class_id": "item", "box": [-1, 0, 5, 5]},
        ):
            with pytest.raises(ValueError, match="class|non-negative"):
                append_review(review_connection([item]), objects=[item], **common)


def test_changed_objects_need_new_completeness_decision():
    item = {"object_id": "one", "class_id": "item", "box": [0, 0, 5, 5]}
    with pytest.MonkeyPatch.context() as monkeypatch:
        from labelloop_api import revision_store

        monkeypatch.setattr(revision_store, "source_uuid", lambda _conn, _key: "uuid")
        with pytest.raises(ValueError, match="new authoritative completeness"):
            append_review(
                review_connection([]),
                asset_id="asset",
                actor_kind="human",
                actor_id="reviewer",
                expected_revision=1,
                approval_state="human_accepted",
                review_tier="adjudicated",
                scope_completeness="exhaustive_for_scope",
                objects=[item],
                training_eligible=True,
            )


def test_system_policy_auto_acceptance_is_not_categorically_forbidden():
    with pytest.MonkeyPatch.context() as monkeypatch:
        from labelloop_api import revision_store

        monkeypatch.setattr(revision_store, "source_uuid", lambda _conn, _key: "uuid")
        assert (
            append_review(
                review_connection(
                    [],
                    actor_kind="system",
                    approval_state="proposed",
                    review_tier="machine_verified_no_human_review",
                    qualification={"auto_accept_qualification": {"id": "qualified-policy"}},
                ),
                asset_id="asset",
                actor_kind="system",
                actor_id="qualified-policy",
                expected_revision=1,
                approval_state="auto_accepted",
                review_tier="machine_verified_no_human_review",
                scope_completeness="exhaustive_for_scope",
                objects=[],
                training_eligible=True,
            )
            == 2
        )


def test_database_url_encodes_password_punctuation(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "local/dev@password")
    url = database_url()
    assert url.endswith(":***@postgres:5432/labelloop")
    assert "password" not in url and "dev" not in url
    engine = __import__("sqlalchemy").create_engine(quoted_database_url())
    assert engine.url.password == "local/dev@password"
    engine.dispose()
