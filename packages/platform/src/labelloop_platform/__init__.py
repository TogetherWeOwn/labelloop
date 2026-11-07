"""Ultralytics Platform adapter boundary."""

from ultralytics_platform import Platform

__all__ = ["Platform"]


def client_type() -> str:
    return Platform.__name__
