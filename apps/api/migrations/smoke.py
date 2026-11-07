"""Isolated migration smoke checks that never change canonical application records."""

import importlib.util
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, insert, text

from labelloop_api.auth_schema import auth_event, principal, session
from labelloop_api.auth_store import PASSWORDS, AuthStore, digest
from labelloop_api.revision_store import append_review, completeness_context_hash
from labelloop_api.schema import (
    annotation_revision,
    canonical_dataset_release,
    completeness_assessment,
    derived_sample,
    metadata,
    ontology_release,
    preprocessing_recipe,
    project,
    source_asset,
    yolo_dataset_variant,
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
        insert(ontology_release).values(
            ontology_key="migration-smoke",
            project_id=project_id,
            version=1,
            classes=[],
            class_map={"item": 0},
        )
    )
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
    connection.execute(
        insert(completeness_assessment).values(
            asset_id=asset_id,
            revision=1,
            ontology_hash=completeness_context_hash({"item": 0}, [], "test"),
            scope_completeness="exhaustive_for_scope",
            actor_kind="human",
            actor_id="migration-test",
            output_truncated=False,
            unresolved_regions=[],
        )
    )


def append(
    connection: sa.Connection,
    *,
    expected: int,
    eligible: bool = False,
    objects: list[dict[str, object]] | None = None,
    actor_kind: str = "model",
) -> None:
    append_review(
        connection,
        asset_id="migration-smoke-asset",
        actor_kind=actor_kind,
        actor_id="migration-test",
        expected_revision=expected,
        approval_state="proposed" if actor_kind == "model" else "human_accepted",
        review_tier=(
            "machine_verified_no_human_review" if actor_kind == "model" else "adjudicated"
        ),
        scope_completeness="exhaustive_for_scope",
        objects=objects if objects is not None else [],
        training_eligible=eligible,
    )


def install_isolated_guards(connection: sa.Connection) -> None:
    connection.execute(text("CREATE SCHEMA migration_smoke"))
    connection.execute(text("SET search_path TO migration_smoke"))
    schema = sa.MetaData()
    for original in metadata.sorted_tables:
        original.to_metadata(schema, schema="migration_smoke")
    schema.create_all(connection)
    path = Path(__file__).parent / "versions/0001_canonical_foundation.py"
    spec = importlib.util.spec_from_file_location("canonical_guards", path)
    assert spec is not None and spec.loader is not None
    guards = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guards)
    connection.execute(text(guards.GUARDS))


def check_completeness_provenance(connection: sa.Connection) -> None:
    project_id = connection.execute(sa.select(project.c.id)).scalar_one()
    for truncated in (False, True):
        with connection.begin_nested() as transaction:
            asset_id = connection.execute(
                insert(source_asset).values(
                    asset_key="partial-smoke",
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
                    actor_kind="model",
                    actor_id="initial",
                    objects=[],
                    approval_state="proposed",
                    review_tier="unreviewed",
                    scope_completeness="partial",
                    training_eligible=False,
                    gold_eligible=False,
                )
            )
            connection.execute(
                insert(completeness_assessment).values(
                    asset_id=asset_id,
                    revision=1,
                    ontology_hash=completeness_context_hash({"item": 0}, [], "test"),
                    scope_completeness="partial",
                    actor_kind="human",
                    actor_id="inspector",
                    output_truncated=truncated,
                    unresolved_regions=[],
                )
            )
            common = dict(
                asset_id="partial-smoke",
                actor_kind="model",
                actor_id="proposal",
                expected_revision=1,
                approval_state="proposed",
                review_tier="unreviewed",
                objects=[],
            )
            rejects(
                ValueError,
                "valid current completeness",
                lambda common=common: append_review(
                    connection, scope_completeness="exhaustive_for_scope", **common
                ),
            )
            append_review(connection, scope_completeness="unreviewed", **common)
            assessment = (
                connection.execute(
                    sa.select(completeness_assessment).where(
                        completeness_assessment.c.asset_id == asset_id,
                        completeness_assessment.c.revision == 2,
                    )
                )
                .mappings()
                .one()
            )
            assert assessment["scope_completeness"] == "partial"
            assert assessment["actor_id"] == "inspector"
            assert assessment["output_truncated"] == truncated
            rejects(
                ValueError,
                "valid current completeness",
                lambda: append_review(
                    connection,
                    asset_id="partial-smoke",
                    actor_kind="human",
                    actor_id="reviewer",
                    expected_revision=2,
                    approval_state="human_accepted",
                    review_tier="adjudicated",
                    objects=[],
                    scope_completeness="exhaustive_for_scope",
                    training_eligible=True,
                ),
            )
            transaction.rollback()


def check_frozen_lineage(connection: sa.Connection) -> None:
    asset_id = connection.execute(sa.select(source_asset.c.id)).scalar_one()
    project_id = connection.execute(sa.select(project.c.id)).scalar_one()
    ontology_id = connection.execute(sa.select(ontology_release.c.id)).scalar_one()
    release_id = connection.execute(
        insert(canonical_dataset_release).values(
            release_key="frozen-smoke",
            project_id=project_id,
            ontology_id=ontology_id,
            assignment_version="smoke-v1",
            sample_revisions={"migration-smoke-asset": 1},
            ontology_hash="0" * 64,
            excluded_asset_ids=[],
            hashes={},
            frozen=True,
        )
    ).inserted_primary_key[0]
    recipe_id = connection.execute(
        insert(preprocessing_recipe).values(recipe_key="smoke", policy={}, immutable=True)
    ).inserted_primary_key[0]
    variant_id = connection.execute(
        insert(yolo_dataset_variant).values(
            release_id=release_id,
            recipe_id=recipe_id,
            class_map={"item": 0},
            file_manifest={},
            validated=True,
            non_empty_val=True,
        )
    ).inserted_primary_key[0]
    connection.execute(
        insert(derived_sample).values(
            variant_id=variant_id,
            source_id=asset_id,
            crop=[0, 0, 1, 1],
            transform={},
            image_sha256="0" * 64,
            image_key="smoke",
            label_sha256="0" * 64,
        )
    )
    for statement, message in (
        (source_asset.update().values(width=2), "immutable"),
        (source_asset.delete(), "immutable"),
        (ontology_release.update().values(class_map={"item": 1}), "immutable"),
        (ontology_release.delete(), "immutable"),
        (yolo_dataset_variant.delete(), "foreign key"),
    ):
        with connection.begin_nested() as transaction:
            rejects(
                sa.exc.DatabaseError,
                message,
                lambda statement=statement: connection.execute(statement),
            )
            transaction.rollback()


def check_authentication_schema(connection: sa.Connection) -> None:
    path = Path(__file__).parent / "versions/0003_authentication.py"
    spec = importlib.util.spec_from_file_location("auth_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        assert connection.execute(text("SELECT count(*) FROM auth_principal")).scalar_one() == 0
        connection.execute(
            text("""
            INSERT INTO auth_principal
                (id, name, kind, role, enabled, failed_attempts)
            VALUES ('fixture-worker', 'fixture-worker', 'service', 'worker-ml', true, 0)
        """)
        )
        connection.execute(
            text("""
            INSERT INTO auth_event (id, principal_id, action, occurred_at)
            VALUES ('fixture-event', 'fixture-worker', 'permission_denied', now())
        """)
        )
        for statement, message in (
            ("UPDATE auth_principal SET role = 'admin'", "check constraint"),
            ("DELETE FROM auth_event", "immutable"),
            ("UPDATE auth_event SET action = 'logout'", "immutable"),
            ("INSERT INTO auth_session VALUES ('hash', 'absent', 'csrf', now())", "foreign key"),
        ):
            with connection.begin_nested() as transaction:
                rejects(
                    sa.exc.DatabaseError,
                    message,
                    lambda statement=statement: connection.execute(text(statement)),
                )
                transaction.rollback()
        migration.downgrade()
        migration.upgrade()
        assert connection.execute(text("SELECT count(*) FROM auth_event")).scalar_one() == 0


def check_authentication_concurrency() -> None:
    # Separate PostgreSQL connections exercise real FOR UPDATE locks, not SQLite emulation.
    engine = create_engine(
        os.environ["DATABASE_URL"],
        pool_size=6,
        max_overflow=0,
        connect_args={
            "options": "-csearch_path=migration_smoke -cstatement_timeout=15000 -clock_timeout=10000"
        },
    )
    now = datetime(2026, 11, 7, tzinfo=UTC)
    password = "migration-fixture-only"
    identity = "concurrent-fixture"
    try:
        store = AuthStore(engine)
        with engine.begin() as connection:
            connection.execute(
                insert(principal).values(
                    id=identity,
                    name=identity,
                    kind="human",
                    role="reviewer",
                    password_hash=PASSWORDS.hash(password),
                    enabled=True,
                    failed_attempts=0,
                )
            )
            connection.execute(
                insert(session).values(
                    token_hash=digest("expired-fixture"),
                    principal_id=identity,
                    csrf_hash=digest("csrf-fixture"),
                    expires_at=now,
                )
            )
        start = Barrier(6, timeout=10)

        def failed_login(_: int) -> tuple[str, str] | None:
            start.wait()
            return store.login(identity, "wrong-fixture", now)

        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(failed_login, index) for index in range(6)]
            assert [future.result(timeout=30) for future in futures] == [None] * 6
        with engine.connect() as connection:
            row = connection.execute(sa.select(principal)).mappings().one()
            assert row["failed_attempts"] == 5
            assert row["locked_until"] == now + timedelta(minutes=15)
            assert (
                connection.execute(sa.select(auth_event.c.action)).scalars().all()
                == ["login_failed"] * 6
            )
        assert store.login(identity, password, now + timedelta(minutes=14)) is None
        with engine.connect() as connection:
            assert connection.execute(sa.select(principal.c.locked_until)).scalar_one() == (
                now + timedelta(minutes=15)
            )
        credentials = store.login(identity, password, now + timedelta(minutes=15))
        assert credentials is not None
        assert store.authenticate(credentials[0], service=False, now=now + timedelta(minutes=15))
        with engine.connect() as connection:
            row = connection.execute(sa.select(principal)).mappings().one()
            assert row["failed_attempts"] == 0 and row["locked_until"] is None
            # The successful login executes the cleanup SQL against the real migration schema.
            assert connection.execute(sa.select(session.c.token_hash)).scalars().all() == [
                digest(credentials[0])
            ]
            assert len(connection.execute(sa.select(auth_event)).all()) == 8
        print("PostgreSQL concurrent lockout, fixed expiry and session cleanup checks passed.")
    finally:
        engine.dispose()


def main() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS migration_smoke CASCADE"))
            install_isolated_guards(connection)
            check_authentication_schema(connection)
            initial_state(connection)
            check_completeness_provenance(connection)
            check_frozen_lineage(connection)
        check_authentication_concurrency()
        with engine.connect() as connection:
            connection.execute(text("SET search_path TO migration_smoke"))
            rejects(ValueError, "stale", lambda: append(connection, expected=2))
            rejects(ValueError, "late model", lambda: append(connection, expected=1))
            rejects(
                ValueError,
                "class",
                lambda: append(
                    connection,
                    expected=1,
                    actor_kind="human",
                    objects=[{"object_id": "one", "class_id": "other", "box": [0, 0, 1, 1]}],
                ),
            )
            rejects(
                ValueError,
                "non-negative",
                lambda: append(
                    connection,
                    expected=1,
                    actor_kind="human",
                    objects=[{"object_id": "one", "class_id": "item", "box": [-1, 0, 1, 1]}],
                ),
            )
            rejects(
                ValueError,
                "eligibility",
                lambda: append(connection, expected=1, eligible=True),
            )
            project_id = connection.execute(sa.select(project.c.id)).scalar_one()
            connection.execute(
                insert(ontology_release).values(
                    ontology_key="migration-smoke-v2",
                    project_id=project_id,
                    version=2,
                    classes=[],
                    class_map={"item": 0, "new-class": 1},
                )
            )
            rejects(
                ValueError,
                "valid current completeness",
                lambda: append(connection, expected=1, eligible=True, actor_kind="human"),
            )
            append_review(
                connection,
                asset_id="migration-smoke-asset",
                actor_kind="human",
                actor_id="migration-test",
                expected_revision=1,
                approval_state="human_accepted",
                review_tier="adjudicated",
                scope_completeness="unreviewed",
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
