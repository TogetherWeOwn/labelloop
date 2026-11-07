#!/usr/bin/env python3
"""Aggregate CI gates without hiding failed or cancelled dependencies."""

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
UNGATED = {"changes", "full-run", "lint"}


def validate(results: dict[str, dict[str, str]]) -> None:
    """Fail on absent jobs and on skips outside a declared area gate."""
    failures = {name: "missing" for name in REQUIRED - results.keys()}
    for name in REQUIRED & results.keys():
        result = results[name]["result"]
        if result == "success" or (name not in UNGATED and result == "skipped"):
            continue
        failures[name] = result
    if failures:
        raise ValueError(f"CI failure: {failures}")


def main() -> None:
    validate(json.loads(sys.argv[1]))
    print("ci-ok: every required job succeeded or was skipped by its area gate")


if __name__ == "__main__":
    main()
