"""Ultralytics Platform adapter boundary."""

from .ports import (
    Operation,
    Outcome,
    PlatformPort,
    PlatformRequest,
    PlatformResult,
    StoragePort,
    StorageResult,
    StorageTarget,
)
from .storage import SignedStorage
from .transport import PlatformAdapter, Throttle

__all__ = [
    "Operation",
    "Outcome",
    "PlatformAdapter",
    "PlatformPort",
    "PlatformRequest",
    "PlatformResult",
    "SignedStorage",
    "StoragePort",
    "StorageResult",
    "StorageTarget",
    "Throttle",
]


def client_type() -> str:
    from ultralytics_platform import Platform

    return Platform.__name__
