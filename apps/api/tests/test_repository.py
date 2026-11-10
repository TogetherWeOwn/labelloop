"""Offline storage and transaction boundaries; integration is the Postgres dev stack."""

import io
import runpy
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from labelloop_api.database_url import database_url, quoted_database_url
from labelloop_api.repository import ingest_canonical_asset
from labelloop_api.revision_store import append_review, completeness_context_hash
from labelloop_api.schema import annotation_revision, derived_sample, review_event, source_asset


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
                "classes": [],
                "scope": "test",
                "assessed_ontology_hash": completeness_context_hash({"item": 0}, [], "test"),
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
    context_changes=None,
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
                "classes": [],
                "scope": "test",
                "assessed_ontology_hash": completeness_context_hash({"item": 0}, [], "test"),
                "access_policy": qualification or {},
                "assessed_completeness": "exhaustive_for_scope",
                "output_truncated": False,
                "unresolved_regions": [],
                **(context_changes or {}),
            }
        else:
            result.one.return_value = (100, 100)
            result.scalar_one.return_value = "project-key"
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


def test_access_policy_id_alone_cannot_authorize_system_auto_acceptance(monkeypatch):
    from labelloop_api import revision_store

    monkeypatch.setattr(revision_store, "source_uuid", lambda _conn, _key: "uuid")
    connection = review_connection(
        [],
        actor_kind="system",
        approval_state="proposed",
        review_tier="machine_verified_no_human_review",
        qualification={"auto_accept_qualification": {"id": "qualified-policy"}},
    )
    with pytest.raises(ValueError, match="current qualification"):
        append_review(
            connection,
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
    assert not any(hasattr(call.args[0], "table") for call in connection.execute.call_args_list)


def test_system_policy_auto_acceptance_is_not_categorically_forbidden(monkeypatch):
    from labelloop_api import revision_store
    from labelloop_core.qualification_data import ScopeCell

    guard = MagicMock()
    monkeypatch.setattr(revision_store, "require_automatic_acceptance", guard)
    snapshot = MagicMock(return_value={"checked-policy": "snapshot"})
    monkeypatch.setattr(revision_store, "automatic_acceptance_record", snapshot)
    monkeypatch.setattr(revision_store, "lock_project", MagicMock())
    automatic = revision_store.AutomaticReview(
        "recipe", "final-evaluation", (ScopeCell("item", "source", "ordinary"),), MagicMock()
    )
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
                automatic=automatic,
            )
            == 2
        )
    snapshot.assert_called_once()
    assert snapshot.call_args.kwargs["qualification"] is automatic.qualification
    assert snapshot.call_args.kwargs["scope"] == automatic.scope
    assert snapshot.call_args.kwargs["evaluation_key"] == automatic.evaluation_key
    assert snapshot.call_args.kwargs["revision"]["revision"] == 2
    guard.assert_called_once()
    assert guard.call_args.kwargs == {
        "project_key": "project-key",
        "recipe_key": automatic.recipe_key,
        "scope": automatic.scope[0],
        "evaluation_key": automatic.evaluation_key,
        "qualification": automatic.qualification,
    }


@pytest.mark.parametrize(
    "objects", [[], [{"object_id": "one", "class_id": "item", "box": [0, 0, 5, 5]}]]
)
@pytest.mark.parametrize(
    "failure", ["paused scope", "adverse audit", "stale final test", "foreign project"]
)
def test_automatic_revision_calls_current_guard_before_any_write(monkeypatch, objects, failure):
    from labelloop_api import revision_store
    from labelloop_core.qualification_data import ScopeCell

    monkeypatch.setattr(revision_store, "source_uuid", lambda _conn, _key: "uuid")
    events = []
    monkeypatch.setattr(revision_store, "lock_project", lambda *_: events.append("project-lock"))
    guard = MagicMock(side_effect=ValueError(failure))
    monkeypatch.setattr(revision_store, "require_automatic_acceptance", guard)
    connection = review_connection(
        objects, actor_kind="model", approval_state="proposed", review_tier="unreviewed"
    )
    execute = connection.execute.side_effect

    def observed(statement, parameters=None):
        if "SELECT id FROM source_asset" in str(statement):
            events.append("asset-lock")
        return execute(statement, parameters)

    connection.execute.side_effect = observed
    automatic = revision_store.AutomaticReview(
        "recipe", "final-evaluation", (ScopeCell("item", "source", "ordinary"),), MagicMock()
    )
    with pytest.raises(ValueError, match=failure):
        append_review(
            connection,
            asset_id="asset",
            actor_kind="system",
            actor_id="service-principal",
            expected_revision=1,
            approval_state="auto_accepted",
            review_tier="machine_verified_no_human_review",
            scope_completeness="exhaustive_for_scope",
            objects=objects,
            training_eligible=False,
            automatic=automatic,
        )
    assert events == ["project-lock", "asset-lock"]
    assert guard.call_args.kwargs["project_key"] == "project-key"
    assert not any(
        getattr(call.args[0], "is_insert", False) for call in connection.execute.call_args_list
    )


@pytest.mark.parametrize("classes", [(), ("other",), ("item", "other")])
def test_negative_automatic_revision_cannot_omit_or_expand_ontology_scope(monkeypatch, classes):
    from labelloop_api import revision_store
    from labelloop_core.qualification_data import ScopeCell

    monkeypatch.setattr(revision_store, "source_uuid", lambda _conn, _key: "uuid")
    monkeypatch.setattr(revision_store, "lock_project", MagicMock())
    guard = MagicMock()
    monkeypatch.setattr(revision_store, "require_automatic_acceptance", guard)
    with pytest.raises(ValueError, match="scope"):
        automatic = revision_store.AutomaticReview(
            "recipe",
            "final-evaluation",
            tuple(ScopeCell(key, "source", "ordinary") for key in classes),
            MagicMock(),
        )
        append_review(
            review_connection(
                [], actor_kind="system", approval_state="proposed", review_tier="unreviewed"
            ),
            asset_id="asset",
            actor_kind="system",
            actor_id="service-principal",
            expected_revision=1,
            approval_state="auto_accepted",
            review_tier="machine_verified_no_human_review",
            scope_completeness="exhaustive_for_scope",
            objects=[],
            automatic=automatic,
        )
    guard.assert_not_called()


@pytest.mark.parametrize(
    "context_changes",
    [
        {"class_map": {"item": 0, "new-class": 1}},
        {"classes": [{"stable_id": "item", "definition": "changed scope"}]},
        {"scope": "new scope"},
        {"assessed_ontology_hash": None},
    ],
)
def test_obsolete_ontology_or_scope_cannot_qualify_a_negative(monkeypatch, context_changes):
    from labelloop_api import revision_store

    monkeypatch.setattr(revision_store, "source_uuid", lambda _conn, _key: "uuid")
    connection = review_connection(context_changes=context_changes)
    with pytest.raises(ValueError, match="valid current completeness"):
        append_review(
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
    assert not any(hasattr(call.args[0], "table") for call in connection.execute.call_args_list)


@pytest.mark.parametrize("truncated", [False, True])
def test_model_cannot_upgrade_partial_completeness(monkeypatch, truncated):
    from labelloop_api import revision_store

    monkeypatch.setattr(revision_store, "source_uuid", lambda _conn, _key: "uuid")
    connection = review_connection(
        actor_kind="model",
        approval_state="proposed",
        review_tier="unreviewed",
        context_changes={"assessed_completeness": "partial", "output_truncated": truncated},
    )
    with pytest.raises(ValueError, match="valid current completeness"):
        append_review(
            connection,
            asset_id="asset",
            actor_kind="model",
            actor_id="proposal",
            expected_revision=1,
            approval_state="proposed",
            review_tier="unreviewed",
            scope_completeness="exhaustive_for_scope",
            objects=[],
        )
    assert not any(hasattr(call.args[0], "table") for call in connection.execute.call_args_list)


@pytest.mark.parametrize("changed", [False, True])
def test_append_preserves_assessment_instead_of_caller_claim(monkeypatch, changed):
    from labelloop_api import revision_store

    monkeypatch.setattr(revision_store, "source_uuid", lambda _conn, _key: "uuid")
    connection = review_connection(
        actor_kind="model",
        approval_state="proposed",
        review_tier="unreviewed",
        context_changes={"assessed_completeness": "partial", "output_truncated": True},
    )
    append_review(
        connection,
        asset_id="asset",
        actor_kind="model",
        actor_id="proposal",
        expected_revision=1,
        approval_state="proposed",
        review_tier="unreviewed",
        scope_completeness="unreviewed",
        objects=[{"object_id": "one", "class_id": "item", "box": [0, 0, 5, 5]}] if changed else [],
    )
    copies = [
        call
        for call in connection.execute.call_args_list
        if "INSERT INTO completeness_assessment" in str(call.args[0])
    ]
    assert len(copies) == 1
    assert copies[0].args[1]["next_completeness"] == ("unreviewed" if changed else "partial")
    assert ":actor_kind" not in str(copies[0].args[0])
    assert ":actor_id" not in str(copies[0].args[0])


def test_derived_sample_retains_variant_foreign_key():
    assert {key.target_fullname for key in derived_sample.c.variant_id.foreign_keys} == {
        "yolo_dataset_variant.id"
    }


def test_database_url_encodes_password_punctuation(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "local/dev@password")
    url = database_url()
    assert url.endswith(":***@postgres:5432/labelloop")
    assert "password" not in url and "dev" not in url
    engine = __import__("sqlalchemy").create_engine(quoted_database_url())
    assert engine.url.password == "local/dev@password"
    engine.dispose()


def test_alembic_receives_percent_encoded_url_without_interpolation(monkeypatch):
    import sqlalchemy
    from alembic import context
    from alembic.config import Config

    monkeypatch.setenv("POSTGRES_PASSWORD", "local/dev@password%")
    url = quoted_database_url()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(context, "config", Config(), raising=False)
    for operation in ("configure", "begin_transaction", "run_migrations"):
        monkeypatch.setattr(context, operation, MagicMock())
    engine_factory = MagicMock()
    monkeypatch.setattr(sqlalchemy, "engine_from_config", engine_factory)
    runpy.run_path(str(Path(__file__).parents[1] / "migrations" / "env.py"))
    assert engine_factory.call_args.args[0]["sqlalchemy.url"] == url
