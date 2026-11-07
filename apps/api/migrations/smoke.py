"""Isolated migration smoke checks that never change canonical application records."""

import importlib.util
import os
from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import create_engine, insert, text

from labelloop_api.revision_store import append_review
from labelloop_api.schema import (
    annotation_revision,
    completeness_assessment,
    project,
    review_event,
    source_asset,
)


def rejects(expected: type[Exception], message: str, operation: Callable[[], object]) -> None:
    try:
        operation()
    except expected as error:
        assert message in str(error), str(error)
    else:
        raise AssertionError(f"{message} was missing from the rejection")


def initial_state(connection: sa.Connection) -> None:
    project_id = connection.execute(
        insert(project).values(
            project_key="migration-smoke",
            workspace_id="00000000-0000-0000-0000-000000000001",
            task="test",
            scope="test",
            domain="test",
        )
    ).inserted_primary_key[0]
    asset_id = connection.execute(
        insert(source_asset).values(
            asset_key="migration-smoke-asset",
            project_id=project_id,
            sha256="0" * 64,
            object_key="sha256/00/" + "0" * 64,
            width=1,
            height=1,
            exif_orientation=1,
            source_uri="migration-test",
        )
    ).inserted_primary_key[0]
    connection.execute(
        insert(annotation_revision).values(
            asset_id=asset_id,
            revision=1,
            actor_kind="human",
            actor_id="migration-test",
            objects=[],
            approval_state="human_accepted",
            review_tier="adjudicated",
            scope_completeness="exhaustive_for_scope",
            training_eligible=True,
            gold_eligible=True,
        )
    )


def append(connection: sa.Connection, *, expected: int, eligible: bool = False) -> None:
    append_review(
        connection,
        asset_id="migration-smoke-asset",
        actor_kind="model",
        actor_id="migration-test",
        expected_revision=expected,
        approval_state="auto_accepted",
        review_tier="machine_verified_no_human_review",
        scope_completeness="exhaustive_for_scope",
        objects=[],
        training_eligible=eligible,
    )


def install_isolated_guards(connection: sa.Connection) -> None:
    connection.execute(text("CREATE SCHEMA migration_smoke"))
    connection.execute(text("SET search_path TO migration_smoke"))
    schema = sa.MetaData()
    for original in (
        project,
        source_asset,
        annotation_revision,
        review_event,
        completeness_assessment,
    ):
        original.to_metadata(schema, schema="migration_smoke")
    schema.create_all(connection)
    connection.execute(
        text(
            "CREATE TABLE canonical_dataset_release"
            " (id UUID PRIMARY KEY, frozen BOOLEAN NOT NULL);"
            "CREATE TABLE source_split_assignment"
            " (id UUID PRIMARY KEY, locked BOOLEAN NOT NULL);"
            "CREATE TABLE yolo_dataset_variant"
            " (id UUID PRIMARY KEY, release_id UUID REFERENCES canonical_dataset_release(id),"
            " validated BOOLEAN NOT NULL, non_empty_val BOOLEAN NOT NULL);"
            "CREATE TABLE derived_sample"
            " (id UUID PRIMARY KEY, variant_id UUID REFERENCES yolo_dataset_variant(id));"
        )
    )
    path = Path(__file__).parent / "versions/0001_canonical_foundation.py"
    spec = importlib.util.spec_from_file_location("canonical_guards", path)
    assert spec is not None and spec.loader is not None
    guards = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guards)
    connection.execute(text(guards.GUARDS))


def main() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS migration_smoke CASCADE"))
            install_isolated_guards(connection)
            initial_state(connection)
        with engine.connect() as connection:
            connection.execute(text("SET search_path TO migration_smoke"))
            rejects(ValueError, "stale", lambda: append(connection, expected=2))
            rejects(ValueError, "late model", lambda: append(connection, expected=1))
            rejects(
                ValueError,
                "eligibility",
                lambda: append(connection, expected=1, eligible=True),
            )
            append_review(
                connection,
                asset_id="migration-smoke-asset",
                actor_kind="human",
                actor_id="migration-test",
                expected_revision=1,
                approval_state="human_accepted",
                review_tier="adjudicated",
                scope_completeness="exhaustive_for_scope",
                objects=[],
            )
            connection.commit()
            rejects(
                sa.exc.DatabaseError,
                "immutable",
                lambda: connection.execute(
                    text("DELETE FROM annotation_revision WHERE revision = 1")
                ),
            )
        with engine.begin() as connection:
            connection.execute(text("SET search_path TO migration_smoke"))
            revisions = connection.execute(
                text("SELECT array_agg(revision ORDER BY revision) FROM annotation_revision")
            ).scalar_one()
            assert [int(value) for value in revisions] == [1, 2], revisions
        print("Canonical migration and revision guards passed isolated smoke checks.")
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS migration_smoke CASCADE"))
        engine.dispose()


if __name__ == "__main__":
    main()
