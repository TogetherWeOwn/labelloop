#!/usr/bin/env python3
"""Prove this repository's import rule with clean and violating temporary graphs."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def mirror(destination: Path) -> None:
    for path in [
        *REPO.glob("packages/*/src/*"),
        *REPO.glob("apps/api/src/*"),
        *REPO.glob("workers/*/src/*"),
    ]:
        if path.name.startswith("labelloop_"):
            shutil.copytree(path, destination / path.name)
    shutil.copy(REPO / "importlinter.ini", destination / "importlinter.ini")


def run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from importlinter.cli import lint_imports_command; "
            "lint_imports_command.main(args=['--config', 'importlinter.ini', '--no-cache'], "
            "standalone_mode=False)",
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="labelloop-import-contract-") as temporary:
        root = Path(temporary)
        mirror(root)
        baseline = run(root)
        print("=== Baseline ===")
        print(baseline.stdout + baseline.stderr)
        if baseline.returncode != 0:
            raise SystemExit("The real import contract failed its baseline")

        (root / "labelloop_core" / "violation.py").write_text(
            "import ultralytics\nimport ultralytics_platform\n"
        )
        failing = run(root)
        print("=== Deliberately violating imports ===")
        print(failing.stdout + failing.stderr)
        output = failing.stdout + failing.stderr
        expected = (
            "labelloop_core.violation -> ultralytics",
            "labelloop_core.violation -> ultralytics_platform",
        )
        if failing.returncode != 1 or not all(chain in output for chain in expected):
            raise SystemExit("Import-linter did not reject both deliberately forbidden imports")
        print("PASS: import-linter rejected both temporary violating imports")


if __name__ == "__main__":
    main()
