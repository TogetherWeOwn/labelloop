import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "fixtures"))
from load_canonical import FixtureDomain  # noqa: E402


@pytest.fixture(params=["commons-office-keyboards", "cc0-interface"])
def domain(request: pytest.FixtureRequest) -> FixtureDomain:
    return FixtureDomain(request.param)


@pytest.fixture(params=["full-letterbox-640-v1", "full-letterbox-320-v1"])
def recipe(request: pytest.FixtureRequest) -> str:
    return request.param
