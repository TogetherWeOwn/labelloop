"""Actions-only restore acceptance: new databases, new buckets, no installed services."""

import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
import sqlalchemy as sa
from minio import Minio
from recovery_fixture import seed

from labelloop_api import schema as db
from labelloop_api.backup_manifest import SCHEMA_REVISION, audit_seal, digest, encode
from labelloop_api.backup_store import (
    COLUMNS,
    capture_fixture,
    fresh_fixture_bucket,
    restore_fixture,
    restored_reader,
    validate_snapshot,
)
from labelloop_api.model_store import (
    change_registry,
    json_record,
    load_registry,
    record_digest,
    stored_report,
)
from labelloop_api.recovery_release import load_canonical_release
from labelloop_compiler.compiler import compile_dataset
from labelloop_core.domain import Recipe

pytestmark = pytest.mark.skipif(
    os.environ.get("LABELLOOP_RECOVERY_DRILL") != "1",
    reason="requires disposable GitHub Actions PostgreSQL and MinIO",
)
ROOT = Path(__file__).resolve().parents[3]


@contextmanager
def new_database():
    # Fixed fixture endpoint; never read DATABASE_URL, S3 credentials or service configuration.
    admin = sa.create_engine(
        "postgresql+psycopg://labelloop:labelloop-local-only@127.0.0.1:55432/postgres",
        isolation_level="AUTOCOMMIT",
    )
    name = "recovery_" + uuid.uuid4().hex
    engine = sa.create_engine(admin.url.set(database=name))
    try:
        with admin.connect() as connection:
            connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
        subprocess.run(
            # The snapshot pins one schema revision; later tables register separately.
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "apps/api/alembic.ini",
                "upgrade",
                SCHEMA_REVISION,
            ],
            cwd=ROOT,
            env=os.environ | {"DATABASE_URL": engine.url.render_as_string(hide_password=False)},
            check=True,
            timeout=60,
        )
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def hashes(root):
    return {
        str(path.relative_to(root)): digest(path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    }


def reseal(snapshot, mutate):
    document = json.loads(snapshot.manifest)
    mutate(document)
    return replace(snapshot, manifest=encode(document))


def test_isolated_restore_regenerates_variants_lineage_and_recipe_rollback(tmp_path):
    sys.path.insert(0, str(ROOT / "fixtures"))
    from load_canonical import FixtureDomain

    fixture = FixtureDomain("cc0-interface")
    client = Minio(
        "127.0.0.1:59000",
        access_key="labelloop-local-only",
        secret_key="labelloop-local-secret",
        secure=False,
    )
    started = time.monotonic()
    with new_database() as source, new_database() as target:
        with fresh_fixture_bucket(client, endpoint="127.0.0.1:59000") as source_objects:
            with source.begin() as connection:
                manifests = seed(connection, source_objects, fixture, tmp_path / "before")
                original_heads = {
                    recipe: load_registry(connection, fixture.release.project_id, recipe)
                    for recipe in manifests
                }
            with source.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as connection:
                connection.execute(sa.text("SET TRANSACTION READ ONLY"))
                snapshot = capture_fixture(connection, source_objects)
            inventory = validate_snapshot(snapshot, expected_sha256=snapshot.sha256)
            assert all(inventory.tables[name].rows for name in COLUMNS), "every table has coverage"
            assert {role for entry in inventory.objects.values() for role in entry.roles} >= {
                "original",
                "reference",
                "checkpoint-weights",
                "model-export",
                "release-manifest",
            }
            # The source is no longer accessible when the restore starts.
        source.dispose()
        restore_start = time.monotonic()
        with fresh_fixture_bucket(client, endpoint="127.0.0.1:59000") as restored_objects:
            with target.begin() as connection:
                # Missing/corrupt objects, stale schemas and incomplete audits fail visibly.
                key = next(iter(snapshot.objects))
                cases = [
                    replace(
                        snapshot, objects={k: v for k, v in snapshot.objects.items() if k != key}
                    ),
                    replace(snapshot, objects=snapshot.objects | {key: b"corrupt"}),
                    reseal(snapshot, lambda d: d.update(schema_revision="stale")),
                ]

                def truncate_events(document):
                    table = document["tables"]["promotion_event"]
                    table["rows"].pop()
                    table["sha256"] = digest(encode(table["rows"]))
                    document["audit"]["promotion_event"] = audit_seal(table["rows"]).model_dump()

                cases.append(reseal(snapshot, truncate_events))

                def empty_events(document):
                    table = document["tables"]["promotion_event"]
                    table["rows"] = []
                    table["sha256"] = digest(encode(table["rows"]))
                    document["audit"]["promotion_event"] = audit_seal(table["rows"]).model_dump()
                    heads = document["tables"]["registry_head"]
                    for row in heads["rows"]:
                        row["revision"] = 0
                    heads["sha256"] = digest(encode(heads["rows"]))

                # A fully deleted promotion history must not validate even when
                # the head revision is resealed to zero with pointers intact.
                cases.append(reseal(snapshot, empty_events))

                def tamper_last_champion(document):
                    table = document["tables"]["promotion_event"]
                    table["rows"][-1]["record"]["after"]["champion"]["weights_sha256"] = "f" * 64
                    table["sha256"] = digest(encode(table["rows"]))
                    document["audit"]["promotion_event"] = audit_seal(table["rows"]).model_dump()

                # A tampered champion payload in the final audit state must not
                # validate even when its seal is recomputed.
                cases.append(reseal(snapshot, tamper_last_champion))

                def backdate_first_event(document):
                    table = document["tables"]["promotion_event"]
                    first = table["rows"][0]
                    first["record"]["before"]["revision"] = 4
                    first["record"]["before"]["champion"] = first["record"]["after"]["champion"]
                    table["sha256"] = digest(encode(table["rows"]))
                    document["audit"]["promotion_event"] = audit_seal(table["rows"]).model_dump()

                # The audit origin is part of the contract: a first event claiming
                # a revision-4 predecessor must not validate even when resealed.
                cases.append(reseal(snapshot, backdate_first_event))

                def drop_initial_evaluation(document):
                    evaluations = document["tables"]["evaluation_run"]
                    dropped = {
                        row["id"]
                        for row in evaluations["rows"]
                        if row["run_key"] in {"eval-320", "eval-640"}
                    }
                    evaluations["rows"] = [
                        row for row in evaluations["rows"] if row["id"] not in dropped
                    ]
                    evaluations["sha256"] = digest(encode(evaluations["rows"]))
                    links = document["tables"]["model_evaluation"]
                    links["rows"] = [
                        row for row in links["rows"] if row["evaluation_id"] not in dropped
                    ]
                    links["sha256"] = digest(encode(links["rows"]))

                # Removing an initial evaluation and its links leaves no dangling
                # SQL foreign key, but the embedded lineage must still fail.
                cases.append(reseal(snapshot, drop_initial_evaluation))

                def tamper_recipe_policy(document):
                    table = document["tables"]["preprocessing_recipe"]
                    table["rows"][0]["policy"]["interpolation"] = "tampered"
                    table["sha256"] = digest(encode(table["rows"]))

                # A restored policy that disagrees with its variant manifest must
                # not validate even when only the recipe table is resealed.
                cases.append(reseal(snapshot, tamper_recipe_policy))
                for case in cases:
                    with pytest.raises(ValueError):
                        restore_fixture(
                            connection, restored_objects, case, expected_sha256=case.sha256
                        )
                with pytest.raises(ValueError, match="manifest digest"):
                    restore_fixture(
                        connection, restored_objects, snapshot, expected_sha256="0" * 64
                    )
                restore_fixture(
                    connection, restored_objects, snapshot, expected_sha256=snapshot.sha256
                )
            restore_seconds = time.monotonic() - restore_start
            with target.begin() as connection:
                release = load_canonical_release(connection, fixture.release.release_id)
                assert release.assignment == fixture.release.assignment
                # Regeneration inputs come from the restored database, never from
                # seed output: source results are only an oracle for comparison.
                restored = {
                    row["recipe_key"]: row
                    for row in connection.execute(
                        sa.select(
                            db.preprocessing_recipe.c.recipe_key,
                            db.preprocessing_recipe.c.policy,
                            db.preprocessing_recipe.c.immutable,
                            db.yolo_dataset_variant.c.class_map,
                            db.yolo_dataset_variant.c.file_manifest,
                        ).join(
                            db.yolo_dataset_variant,
                            db.yolo_dataset_variant.c.recipe_id == db.preprocessing_recipe.c.id,
                        )
                    )
                    .mappings()
                    .all()
                }
                assert set(restored) == set(manifests)
                for recipe, manifest in manifests.items():
                    policy = restored[recipe]["policy"]
                    recipe_input = Recipe(
                        **(policy | {"padding_color": tuple(policy["padding_color"])})
                    )
                    assert restored[recipe]["immutable"]
                    assert restored[recipe]["file_manifest"]["recipe"] == policy
                    assert (
                        restored[recipe]["file_manifest"]["class_map"]
                        == restored[recipe]["class_map"]
                    )
                    assert restored[recipe]["file_manifest"] == manifest
                    regenerated = tmp_path / "after" / recipe
                    compile_dataset(
                        release,
                        recipe_input,
                        regenerated,
                        restored_reader(connection, restored_objects),
                    )
                    assert hashes(regenerated) == hashes(tmp_path / "before" / recipe)
                    recovered = load_registry(connection, release.project_id, recipe)
                    assert recovered == original_heads[recipe]
                    assert recovered.champion and recovered.rollback
                    assert record_digest(policy) == recovered.champion.preprocessing_sha256
                    report = stored_report(
                        connection, recovered.champion, recovered.champion.evaluation_run_ids[0]
                    )
                    assert report.weights_sha256 == recovered.champion.weights_sha256
                    # A wrong size must not reproduce the stored variant.
                    swapped = replace(
                        recipe_input,
                        output_size=640 if recipe_input.output_size == 320 else 320,
                    )
                    wrong = tmp_path / "wrong" / recipe
                    compile_dataset(
                        release, swapped, wrong, restored_reader(connection, restored_objects)
                    )
                    assert hashes(wrong) != hashes(tmp_path / "before" / recipe)
                recipe = "full-letterbox-320-v1"
                changed = change_registry(
                    connection,
                    project_key=release.project_id,
                    recipe_key=recipe,
                    actor_id="fixture-admin",
                    expected_revision=original_heads[recipe].revision,
                )
                assert changed.champion == original_heads[recipe].rollback
                assert changed.rollback == original_heads[recipe].champion
                assert (
                    load_registry(connection, release.project_id, "full-letterbox-640-v1")
                    == original_heads["full-letterbox-640-v1"]
                )
                # No source/live overwrite and no replay into a non-empty target.
                with pytest.raises(ValueError, match="non-empty database"):
                    restore_fixture(
                        connection, restored_objects, snapshot, expected_sha256=snapshot.sha256
                    )
    evidence = {
        "fixture_only": True,
        "training_executed": False,
        "manifest_sha256": snapshot.sha256,
        "schema_revision": inventory.schema_revision,
        "inventory_counts": {name: len(table.rows) for name, table in inventory.tables.items()},
        "object_count": len(snapshot.objects),
        "restore_seconds": restore_seconds,
        "total_seconds": time.monotonic() - started,
        "byte_identical_recipes": sorted(manifests),
        "rollback": {key: json_record(value) for key, value in original_heads.items()},
        "head_sha": os.environ.get("RECOVERY_HEAD_SHA"),
        "run_url": os.environ.get("RECOVERY_RUN_URL"),
        "cleanup": "both disposable databases and all invocation-owned buckets removed",
    }
    evidence_path = Path(os.environ["RECOVERY_EVIDENCE_DIR"])
    evidence_path.mkdir(parents=True, exist_ok=True)
    (evidence_path / "restore-report.json").write_text(json.dumps(evidence, indent=2) + "\n")
    (evidence_path / "backup-manifest.json").write_bytes(snapshot.manifest)
