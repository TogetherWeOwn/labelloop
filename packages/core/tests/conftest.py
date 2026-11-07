import sys
from pathlib import Path

import pytest

from labelloop_core.grouping import AssetEvidence

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "fixtures"))
from load_canonical import load_evidence  # noqa: E402


@pytest.fixture(params=["commons-office-keyboards", "cc0-interface"])
def evidence(request: pytest.FixtureRequest) -> list[AssetEvidence]:
    return load_evidence(request.param)
