"""Exercise the actual workflow diagnostic command with untrusted report fields."""

import json
import subprocess
import sys
from pathlib import Path


def test_scan_diagnostics_only_emit_count_not_untrusted_report_fields(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[3]
    workflow = root / ".github/workflows/gitleaks.yml"
    assert (
        'run: python3 -I scripts/summarize-secret-scan.py "$RUNNER_TEMP/gitleaks.json"'
        in workflow.read_text()
    )
    marker = "synthetic-sensitive-report-metadata"
    finding = {key: marker for key in ("File", "RuleID", "Commit", "Match", "Secret", "StartLine")}
    report = tmp_path / "gitleaks.json"
    report.write_text(json.dumps([finding, finding]))
    result = subprocess.run(
        [sys.executable, "-I", str(root / "scripts/summarize-secret-scan.py"), str(report)],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert marker not in result.stdout + result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "finding_count": 2,
        "reachable_finding_count": 0,
        "other_ref_finding_count": 0,
        "unclassified_finding_count": 2,
    }
