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


def validate(results: dict[str, dict[str, str]]) -> None:
    """Fail on any absent, skipped, cancelled or failed bootstrap job."""
    failures = {name: "missing" for name in REQUIRED - results.keys()}
    for name in REQUIRED & results.keys():
        result = results[name]["result"]
        if result == "success":
            continue
        failures[name] = result
    if failures:
        raise ValueError(f"CI failure: {failures}")


def main() -> None:
    validate(json.loads(sys.argv[1]))
    print("ci-ok: every required bootstrap job succeeded")


if __name__ == "__main__":
    main()
