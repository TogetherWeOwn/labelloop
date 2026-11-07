import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "check_ci_results", Path(__file__).parents[3] / "scripts" / "check_ci_results.py"
)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
REQUIRED = module.REQUIRED
UNGATED = module.UNGATED
validate = module.validate


def successful_results() -> dict[str, dict[str, str]]:
    return {name: {"result": "success"} for name in REQUIRED}


def test_ci_results_requires_every_job() -> None:
    with pytest.raises(ValueError, match="CI failure"):
        validate({})


def test_ci_results_allows_only_area_gate_skips() -> None:
    results = successful_results()
    for name in REQUIRED - UNGATED:
        results[name] = {"result": "skipped"}
    validate(results)
    results["lint"] = {"result": "skipped"}
    with pytest.raises(ValueError, match="lint.*skipped"):
        validate(results)


def test_ci_results_rejects_failure() -> None:
    results = successful_results()
    results["dev-stack"] = {"result": "failure"}
    with pytest.raises(ValueError, match="dev-stack.*failure"):
        validate(results)
