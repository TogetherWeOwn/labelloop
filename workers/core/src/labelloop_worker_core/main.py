"""Core worker entry point; the durable queue is implemented in a later milestone."""


def status() -> dict[str, str]:
    return {"status": "ready", "worker": "core"}


def main() -> None:
    print(status())
