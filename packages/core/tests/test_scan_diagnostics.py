"""Exercise the actual workflow diagnostic command with untrusted report fields."""

import json
import os
import subprocess
from pathlib import Path


def test_scan_diagnostics_only_emit_count_not_untrusted_report_fields(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[3]
    workflow = (root / ".github/workflows/gitleaks.yml").read_text()
    summary = workflow.split("      - name: Show counts without report metadata\n", 1)[1]
    script = "\n".join(line[10:] for line in summary.split("run: |\n", 1)[1].splitlines())
    marker = "synthetic-sensitive-report-metadata"
    finding: dict[str, str | int] = {key: marker for key in ("File", "RuleID", "Match", "Secret")}
    finding.update(Commit="a" * 40, StartLine=7)
    finding["Fingerprint"] = f"{finding['Commit']}:{marker}:{marker}:7"
    report = tmp_path / "gitleaks.json"
    report.write_text(json.dumps([finding, finding]))
    result = subprocess.run(
        ["bash", "-e", "-c", script],
        cwd=root,
        env={**os.environ, "RUNNER_TEMP": str(tmp_path)},
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert marker not in result.stdout + result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "finding_count": 2,
        "reviewed_inert_candidate_count": 0,
        "unmatched_finding_count": 2,
    }
    report.write_text(json.dumps([finding, {**finding, "StartLine": marker}]))
    invalid = subprocess.run(
        ["bash", "-e", "-c", script],
        cwd=root,
        env={**os.environ, "RUNNER_TEMP": str(tmp_path)},
        capture_output=True,
        timeout=10,
    )
    assert invalid.returncode != 0
    assert (invalid.stdout, invalid.stderr) == (b"", b"")
