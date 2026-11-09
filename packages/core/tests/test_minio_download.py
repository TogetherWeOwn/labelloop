"""Exercise the actual Docker RUN with a deterministic, network-free wget stand-in."""

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

DOCKERFILE = Path(__file__).parents[3] / "dev/minio/Dockerfile"
PAYLOAD = b"licence-clean fake executable\n"


@pytest.mark.parametrize(
    ("architecture", "failures", "corrupt", "expected_attempts", "succeeds"),
    [
        ("amd64", 0, False, 1, True),
        ("arm64", 2, False, 3, True),
        ("amd64", 3, False, 3, False),
        ("amd64", 0, True, 1, False),
        ("amd64", 2, True, 3, False),
        ("unsupported", 0, False, 0, False),
    ],
)
def test_minio_download_keeps_bounded_attempts_and_checksum_gate(
    tmp_path: Path,
    architecture: str,
    failures: int,
    corrupt: bool,
    expected_attempts: int,
    succeeds: bool,
) -> None:
    dockerfile = DOCKERFILE.read_text()
    command = dockerfile.split("RUN ", 1)[1].split("\nEXPOSE ", 1)[0].replace("\\\n", "\n")
    destination = tmp_path / "minio"
    command = command.replace("/usr/local/bin/minio", str(destination))
    counter = tmp_path / "attempts"
    downloader = tmp_path / "wget"
    downloader.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'test "$#" -eq 6\n'
        'test "$1" = "--no-verbose" && test "$2" = "-T" && test "$3" = "30"\n'
        'test "$4" = "https://github.com/minio/minio/releases/download/'
        'RELEASE.2025-09-07T16-13-09Z/minio.linux-$TARGETARCH.RELEASE.2025-09-07T16-13-09Z"\n'
        'test "$5" = "-O" && test "$6" = "$DESTINATION"\n'
        "attempt=0\n"
        'if test -f "$COUNTER"; then read -r attempt < "$COUNTER"; fi\n'
        "attempt=$((attempt + 1))\n"
        'printf "%s\\n" "$attempt" > "$COUNTER"\n'
        'if test "$attempt" -le "$FAILURES"; then\n'
        '  printf "partial" > "$6"\n'
        "  exit 1\n"
        "fi\n"
        'if test "$CORRUPT" = "yes"; then\n'
        '  printf "corrupt" > "$6"\n'
        "else\n"
        '  printf "licence-clean fake executable\\n" > "$6"\n'
        "fi\n"
    )
    downloader.chmod(0o700)
    checksum = hashlib.sha256(PAYLOAD).hexdigest()
    result = subprocess.run(
        ["sh", "-c", command],
        env={
            "PATH": str(tmp_path) + os.pathsep + os.defpath,
            "TARGETARCH": architecture,
            "MINIO_SHA256_AMD64": checksum,
            "MINIO_SHA256_ARM64": checksum,
            "DESTINATION": str(destination),
            "COUNTER": str(counter),
            "FAILURES": str(failures),
            "CORRUPT": "yes" if corrupt else "no",
        },
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert (result.returncode == 0) is succeeds, result.stderr
    assert (int(counter.read_text()) if counter.exists() else 0) == expected_attempts
    if succeeds:
        assert destination.read_bytes() == PAYLOAD
        assert destination.stat().st_mode & 0o777 == 0o555
    elif destination.exists():
        assert destination.stat().st_mode & 0o111 == 0
    assert "RELEASE.2025-09-07T16-13-09Z" in dockerfile
    assert "sha256sum -c -" in command
