"""Build the local migration URL without exposing or misparsing credentials."""

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import URL


def _connection_url() -> URL:
    return URL.create(
        "postgresql+psycopg",
        username=os.environ.get("POSTGRES_USER") or "labelloop",
        password=os.environ.get("POSTGRES_PASSWORD") or "labelloop-local-only",
        host=os.environ.get("POSTGRES_HOST") or "postgres",
        port=5432,
        database=os.environ.get("POSTGRES_DB") or "labelloop",
    )


def database_url() -> str:
    """Return a redacted connection identity suitable for diagnostics."""
    engine = create_engine(_connection_url())
    try:
        return engine.url.render_as_string(hide_password=True)
    finally:
        engine.dispose()


def quoted_database_url() -> str:
    """Embed local configuration in the migration shell without logging it."""
    return _connection_url().render_as_string(hide_password=False)
