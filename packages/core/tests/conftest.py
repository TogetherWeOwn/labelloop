import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "fixtures"))


@pytest.fixture(params=["commons-office-keyboards", "cc0-interface"])
def evidence(request: pytest.FixtureRequest) -> list[object]:
    from load_canonical import load_evidence

    return load_evidence(request.param)
