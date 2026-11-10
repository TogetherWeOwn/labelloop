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
    cases = [(*case, "private-key") for case in cases]
    compilation_test = Path("apps/api/tests/test_management_canonical_db.py")
    idempotency = "compile-evaluation-640"
    compilation = f'compile_request(compilation, release_id, key="{idempotency}")\n'
    # Public, synthetic high-entropy text tests the generic detector, not a real key.
    generic_canary = base64.b64encode(bytes(range(32, 64))).decode()
    generic_payload = f'api_key = "{generic_canary}"\n'
    cases.extend(
        (*case, "generic-api-key")
        for case in (
            (
                "idempotency-upstream-default",
                {compilation_test: compilation},
                'title = "baseline"\n[extend]\nuseDefault = true\n',
                True,
            ),
            ("idempotency-fresh-import", {compilation_test: compilation}, config, False),
            ("idempotency-squash-identity", {compilation_test: compilation}, config, False),
            ("idempotency-historical-only", {compilation_test: compilation}, config, False),
            (
                "idempotency-historical-payload",
                {compilation_test: generic_payload},
                config,
                True,
            ),
            (
                "idempotency-changed-value",
                {compilation_test: compilation.replace(idempotency, generic_canary)},
                config,
                True,
            ),
            (
                "idempotency-prefixed-value",
                {compilation_test: compilation.replace(idempotency, generic_canary + idempotency)},
                config,
                True,
            ),
            (
                "idempotency-suffixed-value",
                {compilation_test: compilation.replace(idempotency, idempotency + generic_canary)},
                config,
                True,
            ),
            (
                "idempotency-adjacent-payload",
                {compilation_test: compilation + generic_payload},
                config,
                True,
            ),
            ("idempotency-other-path", {Path("other.py"): compilation}, config, True),
            (
                "idempotency-similar-path",
                {Path("apps/api/tests/copy_test_management_canonical_db.py"): compilation},
                config,
                True,
            ),
            (
                "idempotency-other-rule",
                {compilation_test: compilation + payload},
                config,
                True,
            ),
        )
    )
    identities = {}
    with tempfile.TemporaryDirectory(prefix="platform-scanner-regression-") as temporary:
        for name, files, policy, expected_finding, rule_id in cases:
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
            if name in ("idempotency-historical-only", "idempotency-historical-payload"):
                (repo / compilation_test).unlink()
                subprocess.run(
                    ["git", "add", "-u"],
                    cwd=repo,
                    env=env,
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
                subprocess.run(
                    ["git", "-c", "commit.gpgsign=false", "commit", "-m", "remove fixture"],
                    cwd=repo,
                    env=env,
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
            if name.endswith(("fresh-import", "squash-identity")):
                identities[name] = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=repo, env=env
                ).strip()
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
            expected_rule = "private-key" if name == "idempotency-other-rule" else rule_id
            detected = any(finding["RuleID"] == expected_rule for finding in findings)
            if result.returncode != int(expected_finding) or detected != expected_finding:
                raise AssertionError(f"Scanner classification failed for {name}")
            print(
                f"PASS {name}: exit={result.returncode}, {expected_rule} finding={detected}",
                flush=True,
            )
    for prefix in ("", "idempotency-"):
        assert identities[prefix + "fresh-import"] != identities[prefix + "squash-identity"], (
            "Fixture commit identities must differ"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    check(parser.parse_args().image)
