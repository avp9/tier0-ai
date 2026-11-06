from __future__ import annotations

from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _sqlalchemy_url(database_url: str) -> str:
    for prefix in ("postgresql://", "postgres://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url[len(prefix) :]
    return database_url


def _config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    # configparser treats % specially; URL-encoded passwords contain it.
    config.set_main_option("sqlalchemy.url", _sqlalchemy_url(database_url).replace("%", "%%"))
    return config


def upgrade_to_head(database_url: str) -> None:
    command.upgrade(_config(database_url), "head")


MIGRATION_LOCK_ID = 727_274_001


def upgrade_with_lock(database_url: str) -> None:
    """``upgrade_to_head`` that is safe when several servers start at the same moment.

    A Postgres advisory lock makes the others wait, then find nothing left to do.
    """
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_ID,))
        try:
            upgrade_to_head(database_url)
        finally:
            connection.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_ID,))


def downgrade_to_base(database_url: str) -> None:
    command.downgrade(_config(database_url), "base")


def head_revision() -> str:
    head = ScriptDirectory(str(MIGRATIONS_DIR)).get_current_head()
    if head is None:
        raise RuntimeError("no migrations found")
    return head


def current_revision(database_url: str) -> str | None:
    engine = create_engine(_sqlalchemy_url(database_url))
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()
