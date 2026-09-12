"""SQLAlchemy engine singleton for Postgres. The engine pools; create it once.

This module is the relational branch of the database slot. `main.py` is
shared across databases and asks for exactly these: `wait_until_reachable`,
`migrate`, `ping`, `close`, and `get_session` for the routers. The graph
branch provides the same five with a different store behind them.

Everything comes from DATABASE_URL. Locally that is the Postgres started by
`docker compose up`; hosted, it is injected by the platform from the database
it created alongside the service.
"""
import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

load_dotenv()

logger = logging.getLogger("app.db")

# Hostnames that can only be a development database: the loopback, and the
# service name the compose file gives Postgres.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres"}

_engine: Engine | None = None


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "postgresql://app:localdev@127.0.0.1:5432/app")
    # Hosts hand out `postgres://` or `postgresql://` with no driver named,
    # which SQLAlchemy resolves to psycopg2. This project ships psycopg 3.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        # pool_pre_ping: a hosted database drops idle connections without
        # saying so; test one before trusting it instead of surfacing a 500.
        _engine = create_engine(database_url(), pool_pre_ping=True)
    return _engine


@contextmanager
def get_session() -> Iterator[Session]:
    """A session. The caller commits; leaving without a commit rolls back.

    expire_on_commit is off because a request reads a row, commits, closes
    the session and then answers from the row. With the default, the commit
    would mark every attribute stale and the answer would fail to load them
    from a session that no longer exists.
    """
    with Session(get_engine(), expire_on_commit=False) as session:
        yield session


def close() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


def is_production() -> bool:
    """True when DATABASE_URL points anywhere but a local database.

    Anything destructive checks this first. The dev seed refuses outright;
    nothing in the base is allowed to wipe a database it did not create.
    """
    return urlsplit(database_url()).hostname not in LOCAL_HOSTS


def ping() -> None:
    """One round trip. Raises if the database is not there."""
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))


def wait_until_reachable(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            ping()
            return
        except OperationalError:
            if time.monotonic() >= deadline:
                raise
            logger.warning("database not reachable yet; retrying")
            time.sleep(2)


def migrate() -> None:
    """Bring the database to the latest migration. Idempotent; runs on every
    boot, so development and production cannot drift.

    The migrations are in migrations/versions and are derived from tables.py:
    change a table there, then `alembic revision --autogenerate -m "..."` from
    this directory writes the next one.
    """
    from alembic import command
    from alembic.config import Config

    here = Path(__file__).resolve().parent
    config = Config(str(here / "alembic.ini"))
    config.set_main_option("script_location", str(here / "migrations"))
    command.upgrade(config, "head")
