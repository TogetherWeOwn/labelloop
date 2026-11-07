import pytest

ultralytics = pytest.importorskip(
    "ultralytics", reason="install the optional ml dependency group for the runtime smoke check"
)

from labelloop_worker_ml.main import runtime_version


def test_ml_vendor_import() -> None:
    assert runtime_version() == ultralytics.__version__
