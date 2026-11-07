#!/usr/bin/env python3
"""Aggregate CI gates without hiding failed or cancelled dependencies."""

import json
import sys


def main() -> None:
    results = json.loads(sys.argv[1])
    failures = {
        name: result["result"]
        for name, result in results.items()
        if result["result"] not in {"success", "skipped"}
    }
    if failures:
        raise SystemExit(f"CI failure: {failures}")
    print("ci-ok: every needed job succeeded or was skipped by its area gate")


if __name__ == "__main__":
    main()
