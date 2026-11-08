"""Opt-in Actions-only PostgreSQL qualification; never use a shared application DB."""

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from labelloop_credentials.store import CredentialStore, audit_event, credential_version

pytestmark = pytest.mark.skipif(
    os.environ.get("LABELLOOP_CREDENTIAL_PG_TEST") != "1",
    reason="disposable PostgreSQL fixture is provided by GitHub Actions only",
)


def test_postgres_migration_guards_and_rotation(sealer, scope):
    path = Path(__file__).resolve().parents[3] / (
        "apps/api/migrations/versions/0004_provider_credentials.py"
    )
    spec = importlib.util.spec_from_file_location("credential_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(os.environ["DATABASE_URL"], hide_parameters=True)
    schema = "credential_fixture_" + uuid4().hex
    try:
        with engine.begin() as db:
            db.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            db.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
            context = MigrationContext.configure(db)
            with Operations.context(context):
                migration.upgrade()
                store = CredentialStore(db)
                connection_id = uuid4()
                envelope = sealer.seal(scope.identity(connection_id, 1), b"fixture-postgres-7239")
                store.stage(envelope, expected_active=0, actor_id="fixture-admin")
                assert (
                    store.finish_validation(
                        scope, connection_id, 1, valid=True, actor_id="worker-platform"
                    )
                    == "activated"
                )
                for statement in (
                    audit_event.delete(),
                    audit_event.update().values(actor_id="changed"),
                    credential_version.update().values(ciphertext=b"tamper"),
                    credential_version.delete(),
                    credential_version.update().values(intent_state="pending"),
                ):
                    with pytest.raises(sa.exc.DBAPIError), db.begin_nested():
                        db.execute(statement)
                assert store.lookup(scope, connection_id) == envelope
                replacement = sealer.seal(scope.identity(connection_id, 2), b"fixture-new-8240")
                store.stage(replacement, expected_active=1, actor_id="fixture-admin")
                assert (
                    store.finish_validation(
                        scope,
                        connection_id,
                        2,
                        valid=False,
                        actor_id="worker-platform",
                        denial="identity_unverifiable",
                    )
                    == "identity_unverifiable"
                )
                assert store.lookup(scope, connection_id) == envelope
                assert (
                    db.execute(
                        sa.select(audit_event.c.version).where(
                            audit_event.c.action == "identity_unverifiable"
                        )
                    ).scalar_one()
                    == 2
                )
                with pytest.raises(sa.exc.DBAPIError), db.begin_nested():
                    db.execute(
                        credential_version.update()
                        .where(credential_version.c.version == 2)
                        .values(intent_state="activated")
                    )
                migration.downgrade()
                assert not sa.inspect(db).has_table("provider_connection", schema=schema)
                migration.upgrade()
                assert sa.inspect(db).has_table("provider_connection", schema=schema)
                migration.downgrade()
            db.exec_driver_sql(f'DROP SCHEMA "{schema}"')
    finally:
        engine.dispose()
