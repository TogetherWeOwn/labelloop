"""Focused scanner regression: new commit identities and no future-payload exemption.

Run in Actions with the same pinned scanner image as the full-history gate.
All repositories below are disposable test fixtures, not execution workspaces.
"""

import argparse
import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = Path("packages/platform/src/labelloop_platform/contract/openapi.json")


def check(image: str) -> None:
    snapshot = (ROOT / SNAPSHOT).read_text()
    config = (ROOT / ".gitleaks.toml").read_text()
    # Public, invalid key material; never generate or read an actual credential.
    canary = base64.b64encode(b"public scanner regression canary, not key material" * 4).decode()
    envelope = "\\n".join([f"-----{part} PRIVATE KEY-----" for part in ("BEGIN", "END")])
    placeholder = envelope.replace("\\n", "\\nYOUR_GCP_PRIVATE_KEY\\n")
    payload = envelope.replace("\\n", f"\\n{canary}\\n")
    cases = [
        (
            "upstream-default",
            {SNAPSHOT: snapshot},
            'title = "baseline"\n[extend]\nuseDefault = true\n',
            True,
        ),
        ("fresh-import", {SNAPSHOT: snapshot}, config, False),
        ("squash-identity", {SNAPSHOT: snapshot}, config, False),
        (
            "changed-payload",
            {SNAPSHOT: snapshot.replace("YOUR_GCP_PRIVATE_KEY", canary)},
            config,
            True,
        ),
        (
            "placeholder-plus-payload",
            {SNAPSHOT: snapshot.replace("YOUR_GCP_PRIVATE_KEY", f"YOUR_GCP_PRIVATE_KEY{canary}")},
            config,
            True,
        ),
        ("adjacent-payload", {SNAPSHOT: snapshot + "\n" + payload}, config, True),
        ("other-path", {Path("other.json"): placeholder}, config, True),
        ("other-key", {Path("other.json"): payload}, config, True),
    ]
    identities = []
    with tempfile.TemporaryDirectory(prefix="platform-scanner-regression-") as temporary:
        for name, files, policy, expected_finding in cases:
            repo = Path(temporary) / name
            repo.mkdir()
            for path, content in files.items():
                target = repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            (repo / ".gitleaks.toml").write_text(policy)
            env = {
                **os.environ,
                "GIT_AUTHOR_NAME": "Scanner fixture",
                "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                "GIT_COMMITTER_NAME": "Scanner fixture",
                "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
            }
            for args in (
                ["init", "--initial-branch=main"],
                ["add", "."],
                ["-c", "commit.gpgsign=false", "commit", "-m", name],
            ):
                subprocess.run(
                    ["git", *args], cwd=repo, env=env, check=True, capture_output=True, timeout=30
                )
            if name in ("fresh-import", "squash-identity"):
                identities.append(
                    subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, env=env).strip()
                )
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "-v",
                    f"{repo}:/repo",
                    image,
                    "detect",
                    "--source",
                    "/repo",
                    "--config",
                    "/repo/.gitleaks.toml",
                    "--redact",
                    "--exit-code",
                    "1",
                    "--report-format",
                    "json",
                    "--report-path",
                    "/repo/report.json",
                    "--no-banner",
                    "--log-level",
                    "error",
                ],
                capture_output=True,
                timeout=90,
            )
            if result.returncode not in (0, 1):
                raise RuntimeError(f"Scanner unavailable for {name}: exit {result.returncode}")
            findings = json.loads((repo / "report.json").read_text())
            detected = any(finding["RuleID"] == "private-key" for finding in findings)
            if result.returncode != int(expected_finding) or detected != expected_finding:
                raise AssertionError(f"Scanner classification failed for {name}")
            print(
                f"PASS {name}: exit={result.returncode}, private-key finding={detected}", flush=True
            )
    assert len(set(identities)) == 2, "Fixture commit identities must differ"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    check(parser.parse_args().image)
