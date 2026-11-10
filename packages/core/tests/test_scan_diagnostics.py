"""Exercise the actual workflow diagnostic snippet with untrusted report fields."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_scan_diagnostics_only_emit_count_not_untrusted_report_fields(tmp_path: Path) -> None:
    workflow = Path(__file__).resolve().parents[3] / ".github/workflows/gitleaks.yml"
    script = workflow.read_text().split("python3 -I - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    script = "\n".join(line.removeprefix("          ") for line in script.splitlines())
    marker = "synthetic-sensitive-report-metadata"
    finding = {key: marker for key in ("File", "RuleID", "Commit", "Match", "Secret", "StartLine")}
    (tmp_path / "gitleaks.json").write_text(json.dumps([finding, finding]))
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        env={**os.environ, "RUNNER_TEMP": str(tmp_path)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert marker not in result.stdout + result.stderr
    assert json.loads(result.stdout) == {"finding_count": 2}
