"""Counts-only diagnostic; never changes scanner enforcement or findings."""

import hashlib
import json
import os
import resource
import stat
import sys
from pathlib import Path

MAX_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 10_000
# Fixed equality token for the privately reviewed identity, not an exemption.
REVIEWED_DIGEST = "c55a0d927232c3adf07286a9d53c4eea26efb466fcd245049c61436723081e68"


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError


def counts(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError
    records = json.loads(
        raw.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant
    )
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise ValueError
    reviewed = 0
    for record in records:
        if not isinstance(record, dict):
            raise ValueError
        for field in ("Fingerprint", "Commit", "File", "RuleID"):
            if not isinstance(record.get(field), str) or not record[field]:
                raise ValueError
        line = record.get("StartLine")
        if type(line) is not int or line < 1:
            raise ValueError
        identity = f"{record['Commit']}:{record['File']}:{record['RuleID']}:{line}"
        if record["Fingerprint"] != identity:
            raise ValueError
        reviewed += (
            hashlib.sha256(record["Fingerprint"].encode("utf-8")).hexdigest() == REVIEWED_DIGEST
        )
    return {
        "finding_count": len(records),
        "reviewed_inert_candidate_count": reviewed,
        "unmatched_finding_count": len(records) - reviewed,
    }


def main():
    try:
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
        # No symlinks, directories, devices or blocking FIFOs; bounded read even if growing.
        fd = os.open(Path(sys.argv[1]), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as report:
            info = os.fstat(report.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                raise ValueError
            result = counts(report.read(MAX_BYTES + 1))
        sys.stdout.write(json.dumps(result) + "\n")
        return 0
    except BaseException:
        # Deliberately no exception text, report data, traceback or partial counts.
        return 1


if __name__ == "__main__":
    sys.exit(main())
