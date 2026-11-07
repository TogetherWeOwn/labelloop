from labelloop_compiler import compiler_backend


def test_backend_is_local() -> None:
    assert compiler_backend() == "local"
