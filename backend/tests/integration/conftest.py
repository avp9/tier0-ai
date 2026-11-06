import os
import uuid
from collections.abc import Iterator
from urllib.parse import urlparse

import psycopg
import pytest

from tier0.db.migrate import upgrade_to_head


def _admin_url() -> str:
    url = os.environ.get("TIER0_TEST_DATABASE_URL")
    if url:
        return url
    if os.environ.get("REQUIRE_DB_TESTS") == "1":
        pytest.fail("REQUIRE_DB_TESTS=1 but TIER0_TEST_DATABASE_URL is not set")
    pytest.skip("set TIER0_TEST_DATABASE_URL to run database tests")


@pytest.fixture
def empty_database() -> Iterator[str]:
    """A new, empty database; dropped afterwards."""
    admin = _admin_url()
    name = f"tier0_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{name}"')
    try:
        yield urlparse(admin)._replace(path=f"/{name}").geturl()
    finally:
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def migrated_database(empty_database: str) -> str:
    upgrade_to_head(empty_database)
    return empty_database
