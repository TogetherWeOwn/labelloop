from labelloop_worker_core.main import status


def test_core_worker_status() -> None:
    assert status() == {"status": "ready", "worker": "core"}
