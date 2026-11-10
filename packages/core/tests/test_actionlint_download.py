"""Exercise the pinned workflow shell without downloads or retry sleeps."""

import hashlib
import io
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).parents[3] / ".github/workflows/ci.yml"
CHECKSUM = "023070a287cd8cccd71515fedc843f1985bf96c436b7effaecce67290e7e0757"


@pytest.mark.parametrize(
    ("download", "member", "lint_exit", "expected_exit", "executes"),
    [
        ("success", "actionlint", 0, 0, True),
        ("exhausted", "actionlint", 0, 22, False),
        ("corrupt", "actionlint", 0, 1, False),
        ("success", "other", 0, 2, False),
        ("success", "actionlint", 3, 3, True),
    ],
)
def test_actionlint_download_preserves_pins_failure_gate_and_cleanup(
    tmp_path: Path,
    download: str,
    member: str,
    lint_exit: int,
    expected_exit: int,
    executes: bool,
) -> None:
    workflow = WORKFLOW.read_text()
    step = workflow.split("      - name: actionlint (checksum-pinned)\n", 1)[1].split(
        "      - uses:", 1
    )[0]
    assert "timeout-minutes: 3" in step
    command = step.split("        run: |\n", 1)[1]
    command = "\n".join(line.removeprefix("          ") for line in command.splitlines())
    assert command.count(CHECKSUM) == 1
    assert '"$download_dir/actionlint" -shellcheck= -pyflakes=' in command

    archive = tmp_path / "fixture.tgz"
    executable = b'#!/bin/sh\nset -eu\nprintf "%s\\n" "$@" > "$EXECUTED"\nexit "$LINT_EXIT"\n'
    with tarfile.open(archive, "w:gz") as bundle:
        info = tarfile.TarInfo(member)
        info.size = len(executable)
        info.mode = 0o700
        bundle.addfile(info, io.BytesIO(executable))
    # Only the test payload hash is substituted; URL, options and shell remain real.
    command = command.replace(CHECKSUM, hashlib.sha256(archive.read_bytes()).hexdigest())
    runner_temp = tmp_path / "runner temp"
    runner_temp.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    downloader = tools / "curl"
    downloader.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'test "$#" -eq 14\n'
        'test "$1" = "-fsSL" && test "$2" = "--connect-timeout" && test "$3" = "10"\n'
        'test "$4" = "--max-time" && test "$5" = "30"\n'
        'test "$6" = "--retry" && test "$7" = "2"\n'
        'test "$8" = "--retry-delay" && test "$9" = "5"\n'
        "shift 9\n"
        'test "$1" = "--retry-max-time" && test "$2" = "120" && test "$3" = "-o"\n'
        'test "$5" = "https://github.com/rhysd/actionlint/releases/download/v1.7.7/'
        'actionlint_1.7.7_linux_amd64.tar.gz"\n'
        'printf "called\\n" >> "$CALLED"\n'
        'case "$DOWNLOAD" in\n'
        '  exhausted) printf "partial" > "$4"; exit 22 ;;\n'
        '  corrupt) printf "corrupt" > "$4" ;;\n'
        '  success) cp -- "$ARCHIVE" "$4" ;;\n'
        "  *) exit 99 ;;\n"
        "esac\n"
    )
    downloader.chmod(0o700)
    called = tmp_path / "called"
    executed = tmp_path / "executed"
    result = subprocess.run(
        ["bash", "-e", "-c", command],
        env={
            "PATH": str(tools) + os.pathsep + os.defpath,
            "RUNNER_TEMP": str(runner_temp),
            "ARCHIVE": str(archive),
            "DOWNLOAD": download,
            "LINT_EXIT": str(lint_exit),
            "CALLED": str(called),
            "EXECUTED": str(executed),
        },
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == expected_exit, result.stderr
    assert called.read_text().splitlines() == ["called"]
    assert executed.exists() is executes
    if executes:
        assert executed.read_text().splitlines() == ["-shellcheck=", "-pyflakes="]
    assert list(runner_temp.iterdir()) == []
