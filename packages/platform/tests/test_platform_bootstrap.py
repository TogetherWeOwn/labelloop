from labelloop_platform import client_type


def test_platform_client_imports() -> None:
    assert client_type() == "Platform"
