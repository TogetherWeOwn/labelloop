"""Count scanner findings by history reachability without disclosing report fields."""

import json
import re
import subprocess
import sys
from pathlib import Path


def summarize(findings: object, reachable: set[str]) -> dict[str, int]:
    if not isinstance(findings, list):
        raise ValueError("Invalid report")
    counts = {
        "finding_count": len(findings),
        "reachable_finding_count": 0,
        "other_ref_finding_count": 0,
        "unclassified_finding_count": 0,
    }
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError("Invalid finding")
        commit = finding.get("Commit")
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
            counts["unclassified_finding_count"] += 1
        elif commit in reachable:
            counts["reachable_finding_count"] += 1
        else:
            counts["other_ref_finding_count"] += 1
    return counts


def main(report: Path) -> int:
    if not report.exists():
        return 0
    try:
        reachable = set(
            subprocess.check_output(
                ["git", "rev-list", "HEAD"], stderr=subprocess.PIPE, text=True, timeout=30
            ).splitlines()
        )
        counts = summarize(json.loads(report.read_text()), reachable)
    except Exception:
        # Report contents and exception messages may contain credentials or metadata.
        print(json.dumps({"summary_error": True}))
        return 1
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
