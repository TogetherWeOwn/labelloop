"""Standard-library tests: no report content may escape count-only diagnostics."""

import contextlib
import io
import json
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE = runpy.run_path(str(Path(__file__).resolve().parents[1] / "summarize-secret-scan.py"))
summarize = MODULE["summarize"]
main = MODULE["main"]


class SecretScanSummaryTests(unittest.TestCase):
    def test_counts_reachable_other_ref_and_unclassified_findings(self):
        reachable = "a" * 40
        findings = [
            {"Commit": reachable, "File": "private metadata", "Secret": "not a credential"},
            {"Commit": "b" * 40},
            {"Commit": "c" * 64},
            {"Commit": "invalid metadata"},
            {"Commit": [reachable]},
            {},
        ]
        self.assertEqual(
            summarize(findings, {reachable}),
            {
                "finding_count": 6,
                "reachable_finding_count": 1,
                "other_ref_finding_count": 2,
                "unclassified_finding_count": 3,
            },
        )

    def test_empty_report(self):
        self.assertEqual(sum(summarize([], set()).values()), 0)

    def test_malformed_reports_fail_without_content(self):
        for findings in ({"private": "metadata"}, ["private metadata"]):
            with self.subTest(findings=findings), self.assertRaisesRegex(ValueError, "^Invalid"):
                summarize(findings, set())

    def test_main_emits_only_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            report.write_text(json.dumps([{"Commit": "a" * 40, "Secret": "private metadata"}]))
            output = io.StringIO()
            with (
                patch("subprocess.check_output", return_value="a" * 40 + "\n"),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(main(report), 0)
            self.assertEqual(json.loads(output.getvalue())["reachable_finding_count"], 1)
            self.assertNotIn("private metadata", output.getvalue())

    def test_main_sanitizes_errors_and_missing_report(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            self.assertEqual(main(report), 0)
            report.write_text("private invalid JSON")
            output = io.StringIO()
            with (
                patch("subprocess.check_output", side_effect=RuntimeError("private error")),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(main(report), 1)
            self.assertEqual(json.loads(output.getvalue()), {"summary_error": True})


if __name__ == "__main__":
    unittest.main()
