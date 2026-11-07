#!/usr/bin/env python3
"""Aggregate CI gates without hiding failed, cancelled or unexpected skips."""

import json
import sys

REQUIRED = {
    "changes",
    "full-run",
    "lint",
    "types",
    "unit",
    "import-contract",
    "web-build",
    "dev-stack",
}
PYTHON_JOBS = {"types", "unit", "import-contract"}
AREA_JOBS = PYTHON_JOBS | {"web-build"}


def validate(results: dict[str, dict[str, str]], areas: dict[str, bool], full_run: bool) -> None:
    """Accept skips only when this exact event's changed-area map disables the job."""
    failures = {name: "missing" for name in REQUIRED - results.keys()}
    web_selected = full_run or areas["web"]
    python_selected = full_run or areas["python"]
    can_skip = (
        AREA_JOBS
        if not python_selected and not web_selected
        else set()
        if python_selected and web_selected
        else PYTHON_JOBS
        if web_selected
        else {"web-build"}
    )
    for name in REQUIRED & results.keys():
        result = results[name]["result"]
        if result == "success" or (result == "skipped" and name in can_skip):
            continue
        failures[name] = result
    if failures:
        raise ValueError(f"CI failure: {failures}")


def main() -> None:
    validate(json.loads(sys.argv[1]), json.loads(sys.argv[2]), json.loads(sys.argv[3]))
    print("ci-ok: all selected checks passed; skips match the exact changed areas")


if __name__ == "__main__":
    main()
