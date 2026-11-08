"""Shared canonical scope and lock order for qualification persistence."""

import sqlalchemy as sa

from labelloop_api.schema import project


def lock_project(connection: sa.Connection, project_key: str) -> object:
    """Lock before audit evidence or any recipe's registry mutation."""
    return connection.execute(
        sa.select(project.c.id).where(project.c.project_key == project_key).with_for_update()
    ).scalar_one()


def release_membership(value: object) -> dict[str, int]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not key.strip() or type(revision) is not int or revision < 1
        for key, revision in value.items()
    ):
        raise ValueError("frozen annotation membership must map asset keys to revisions")
    return value
