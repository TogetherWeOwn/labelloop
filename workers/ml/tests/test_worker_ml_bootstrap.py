import pytest

from labelloop_worker_ml.main import runtime_version


def test_ml_vendor_import() -> None:
    ultralytics = pytest.importorskip(
        "ultralytics", reason="install the optional ml dependency group for the runtime smoke check"
    )
    assert runtime_version() == ultralytics.__version__
