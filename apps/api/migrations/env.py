"""Alembic configuration for the canonical PostgreSQL schema."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from labelloop_api.budget_schema import metadata as budget_metadata
from labelloop_api.model_schema import metadata as model_metadata
from labelloop_api.schema import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"].replace("%", "%%"))
target_metadata = [metadata, model_metadata, budget_metadata]


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with connection.begin():
            context.run_migrations()


run_migrations_online()
