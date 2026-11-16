"""Synthetic-only tests, explicitly run by the scanner Actions job; no scanner needed."""

import hashlib
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/gitleaks_counts.py"
MODULE = runpy.run_path(str(SCRIPT))
COUNTS = MODULE["counts"]
MARKER = "SENSITIVE_MARKER_$(false)\n::error::never-output"


def record(file="synthetic.py", line=7):
    item = {"Commit": "a" * 40, "File": file, "RuleID": "synthetic", "StartLine": line}
    item["Fingerprint"] = f"{item['Commit']}:{file}:synthetic:{line}"
    item["Secret"] = MARKER
    return item


class CountsTests(unittest.TestCase):
    def test_exact_unknown_duplicates_empty(self):
        candidate, unknown = record(), record("other.py")
        digest = hashlib.sha256(candidate["Fingerprint"].encode()).hexdigest()
        # Only synthetic identities enter test output/source; production token is fixed.
        with patch.dict(COUNTS.__globals__, REVIEWED_DIGEST=digest):
            for records, expected in [
                ([], (0, 0, 0)),
                ([candidate], (1, 1, 0)),
                ([unknown], (1, 0, 1)),
                ([candidate, unknown, candidate], (3, 2, 1)),
            ]:
                self.assertEqual(tuple(COUNTS(json.dumps(records).encode()).values()), expected)
            for field, value in [
                ("File", "other.py"),
                ("RuleID", "other"),
                ("Commit", "b" * 40),
                ("StartLine", 8),
            ]:
                with self.assertRaises(ValueError):
                    COUNTS(json.dumps([{**candidate, field: value}]).encode())

    def test_zero_line_unknown_mixed_duplicates(self):
        candidate, path_only = record(), record("synthetic.p12", line=0)
        digest = hashlib.sha256(candidate["Fingerprint"].encode()).hexdigest()
        with patch.dict(COUNTS.__globals__, REVIEWED_DIGEST=digest):
            for records, expected in [
                ([path_only], (1, 0, 1)),
                ([path_only, path_only], (2, 0, 2)),
                ([candidate, path_only, candidate, path_only], (4, 2, 2)),
            ]:
                self.assertEqual(tuple(COUNTS(json.dumps(records).encode()).values()), expected)
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "report.json"
            report.write_text(json.dumps([path_only, path_only]))
            result = self.cli(report)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, b"")
            self.assertEqual(
                json.loads(result.stdout),
                {
                    "finding_count": 2,
                    "reviewed_inert_candidate_count": 0,
                    "unmatched_finding_count": 2,
                },
            )

    def test_invalid_reports_have_no_output(self):
        valid = record()
        cases = [
            b"",
            b"[",
            b"null",
            b"{}",
            b"[0]",
            b'[{"x":1,"x":2}]',
            b'[{"nested":{"x":1,"x":2}}]',
            b"[NaN]",
            b"\xff",
            b"[" * 2000 + b"]" * 2000,
            b" " * (MODULE["MAX_BYTES"] + 1),
        ]
        for field in ("Fingerprint", "File", "Commit", "RuleID", "StartLine"):
            cases.append(json.dumps([{k: v for k, v in valid.items() if k != field}]).encode())
            for value in (None, True, [], {}, 0, ""):
                cases.append(json.dumps([{**valid, field: value}]).encode())
        cases += [
            json.dumps([valid, {**valid, "Fingerprint": MARKER}]).encode(),
            json.dumps([valid, record(line=-1)]).encode(),
            json.dumps([valid, {**record(line=0), "StartLine": -1}]).encode(),
            json.dumps([valid, {**valid, "StartLine": 0}]).encode(),
            json.dumps([valid, {**record(line=0), "StartLine": 1}]).encode(),
            json.dumps([{**valid, "Fingerprint": "\ud800"}]).encode(),
            json.dumps([valid] * (MODULE["MAX_RECORDS"] + 1)).encode(),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "report.json"
            for raw in cases:
                report.write_bytes(raw)
                result = self.cli(report)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((result.stdout, result.stderr), (b"", b""))
            report.unlink()
            for kind in ("missing", "directory", "symlink", "fifo"):
                if kind == "directory":
                    report.mkdir()
                elif kind == "symlink":
                    report.symlink_to(Path(temporary) / "absent")
                elif kind == "fifo":
                    os.mkfifo(report)
                result = self.cli(report)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((result.stdout, result.stderr), (b"", b""))
                if kind == "directory":
                    report.rmdir()
                elif kind != "missing":
                    report.unlink()

    def cli(self, report):
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), str(report)], capture_output=True, timeout=10
        )

    def test_workflow_preserves_separate_failure_steps(self):
        workflow = (ROOT / ".github/workflows/gitleaks.yml").read_text()
        scan = workflow.split("      - name: Scan full history\n", 1)[1]
        scan, summary = scan.split("      - name: Show counts without report metadata\n", 1)
        self.assertNotIn("continue-on-error", workflow)
        self.assertIn("steps.scan.outcome == 'success'", summary)
        self.assertIn("steps.scan.outcome == 'failure'", summary)
        command = " ".join(line.strip() for line in scan.split("run: >-\n", 1)[1].splitlines())
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "gitleaks.json"
            env = {**os.environ, "RUNNER_TEMP": temporary}
            for code in (0, 1, 2, 137):
                # Shell function replaces Docker; never acquires or invokes a scanner.
                result = subprocess.run(
                    [
                        "bash",
                        "-e",
                        "-c",
                        f'docker() {{ printf "%s" "$MARKER" >&2; return {code}; }}; ' + command,
                    ],
                    env={**env, "MARKER": MARKER},
                    capture_output=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, code)
                self.assertEqual((result.stdout, result.stderr), (b"", b""))
                report.write_text(json.dumps([record()]))
                self.assertEqual(self.cli(report).returncode, 0)
            report.write_text("[]")
            self.assertEqual(
                json.loads(self.cli(report).stdout),
                {
                    "finding_count": 0,
                    "reviewed_inert_candidate_count": 0,
                    "unmatched_finding_count": 0,
                },
            )
            report.write_text(MARKER)
            summary_command = "\n".join(
                line[10:] for line in summary.split("run: |\n", 1)[1].splitlines()
            )
            result = subprocess.run(
                ["bash", "-e", "-c", summary_command],
                cwd=ROOT,
                env=env,
                capture_output=True,
                timeout=10,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((result.stdout, result.stderr), (b"", b""))


if __name__ == "__main__":
    unittest.main()
