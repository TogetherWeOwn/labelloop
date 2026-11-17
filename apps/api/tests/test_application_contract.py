"""Shared app schema publication is not proof of real producer integration."""

import json
from collections import Counter

import pytest

from labelloop_api import auth, main
from labelloop_api.management_codegen import (
    application_contract_app,
    check_published,
    contract_app,
    generate,
    rendered_application_contract,
)


def test_application_schema_is_the_real_opt_in_composition():
    spec = json.loads(rendered_application_contract())
    assert spec == application_contract_app().openapi()
    assert spec["info"]["title"] == "Labelloop"
    assert set(spec["paths"]) == set(contract_app().openapi()["paths"]) | {
        "/auth/login",
        "/auth/me",
        "/auth/logout",
        "/healthz",
        "/readyz",
        "/projects/{project_id}/assets/{asset_id}/annotations",
        "/projects/{project_id}/assets/{asset_id}/annotation-history",
    }
    assert set(main.app.openapi()["paths"]) == set(spec["paths"]) - set(
        contract_app().openapi()["paths"]
    )
    assert set(spec["paths"]["/auth/logout"]["post"]["responses"]) == {"204"}


def test_application_schema_preserves_every_management_operation_and_component():
    spec = json.loads(rendered_application_contract())
    management = contract_app().openapi()
    for path, operations in management["paths"].items():
        assert spec["paths"][path] == operations
    for name, component in management["components"]["schemas"].items():
        assert spec["components"]["schemas"][name] == component


def test_application_schema_has_unique_operations_and_resolvable_references():
    spec = json.loads(rendered_application_contract())
    operations = [
        operation["operationId"]
        for methods in spec["paths"].values()
        for operation in methods.values()
    ]
    assert all(count == 1 for count in Counter(operations).values())

    def validate(value):
        if isinstance(value, dict):
            if "$ref" in value:
                reference = value["$ref"]
                assert reference.startswith("#/")
                target = spec
                for part in reference[2:].split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
            for child in value.values():
                validate(child)
        elif isinstance(value, list):
            for child in value:
                validate(child)

    validate(spec)


def test_generation_never_resolves_runtime_dependencies_or_changes_default_app(monkeypatch):
    def forbidden():
        pytest.fail("Schema generation attempted runtime I/O")

    monkeypatch.setattr(auth, "auth_store", forbidden)
    monkeypatch.setattr(main, "storage_client", forbidden)
    monkeypatch.setattr(main.psycopg, "connect", forbidden)
    monkeypatch.setattr("labelloop_api.management_codegen.unavailable_operations", forbidden)
    before = list(main.app.routes)
    assert rendered_application_contract() == rendered_application_contract()
    assert main.app.routes == before
    assert "/projects" not in main.app.openapi()["paths"]
    application = application_contract_app()
    assert application.docs_url is application.redoc_url is application.openapi_url is None
    assert len(application.user_middleware) == 1


def test_application_contract_check_reports_missing_without_creating_files(tmp_path):
    paths = [
        tmp_path / name
        for name in ("management.openapi.json", "management-client.ts", "application.openapi.json")
    ]
    assert check_published(*paths) == paths
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "name", ["management.openapi.json", "management-client.ts", "application.openapi.json"]
)
def test_application_contract_check_detects_each_artifact_without_overwriting(tmp_path, name):
    generate(tmp_path)
    paths = [
        tmp_path / item
        for item in ("management.openapi.json", "management-client.ts", "application.openapi.json")
    ]
    assert check_published(*paths) == []
    changed = tmp_path / name
    changed.write_text("stale contract\n", encoding="utf-8")
    assert check_published(*paths) == [changed]
    assert changed.read_text(encoding="utf-8") == "stale contract\n"
