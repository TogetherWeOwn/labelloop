import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "check_ci_results", Path(__file__).parents[3] / "scripts" / "check_ci_results.py"
)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
REQUIRED = module.REQUIRED
validate = module.validate
AREAS = {"python": True, "web": True, "docs": True}


def successful_results() -> dict[str, dict[str, str]]:
    return {name: {"result": "success"} for name in REQUIRED}


def test_ci_results_requires_every_job() -> None:
    with pytest.raises(ValueError, match="CI failure"):
        validate({}, AREAS, True)


@pytest.mark.parametrize(
    ("areas", "skipped"),
    [
        ({"python": True, "web": False, "docs": False}, {"web-build"}),
        (
            {"python": False, "web": True, "docs": False},
            {"types", "unit", "import-contract", "ml-smoke"},
        ),
        (
            {"python": False, "web": False, "docs": True},
            {"types", "unit", "import-contract", "ml-smoke", "web-build", "dev-stack"},
        ),
    ],
)
def test_ci_results_matches_exact_area_selection(areas: dict[str, bool], skipped: set[str]) -> None:
    results = successful_results()
    for name in skipped:
        results[name] = {"result": "skipped"}
    validate(results, areas, False)
    if areas["python"] or areas["web"]:
        results["dev-stack"] = {"result": "skipped"}
        with pytest.raises(ValueError, match="dev-stack.*skipped"):
            validate(results, areas, False)


def test_ci_results_rejects_unexpected_skips() -> None:
    results = successful_results()
    results["lint"] = {"result": "skipped"}
    with pytest.raises(ValueError, match="lint.*skipped"):
        validate(results, AREAS, True)
    results["web-build"] = {"result": "skipped"}
    with pytest.raises(ValueError, match="web-build.*skipped"):
        validate(results, AREAS, True)


def test_ci_results_rejects_failure() -> None:
    results = successful_results()
    results["dev-stack"] = {"result": "failure"}
    with pytest.raises(ValueError, match="dev-stack.*failure"):
        validate(results, AREAS, True)


@pytest.mark.parametrize("area_json", ["", "null", "{}", '{"python":true}'])
def test_ci_main_reports_missing_area_selection(
    monkeypatch: pytest.MonkeyPatch, area_json: str
) -> None:
    results = successful_results()
    monkeypatch.setattr(
        "sys.argv", ["check_ci_results.py", json.dumps(results), area_json, "false"]
    )
    with pytest.raises(ValueError, match="changes job produced no area map.*dev-stack"):
        module.main()
