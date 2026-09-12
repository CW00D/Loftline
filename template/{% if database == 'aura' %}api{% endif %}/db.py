"""Neo4j driver singleton. The driver pools connections; create it once.

This module is the graph branch of the database slot. `main.py` is shared
across databases and asks for exactly these: `wait_until_reachable`,
`migrate`, `ping`, `close`, and `get_session` for the routers. A relational
branch provides the same five with a different store behind them.
"""
import logging
import os
import time
from contextlib import contextmanager

from dotenv import load_dotenv
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable

load_dotenv()

logger = logging.getLogger("app.db")

_driver = None


def get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            os.environ.get("NEO4J_URI", "neo4j://127.0.0.1:7687"),
            auth=(
                os.environ.get("NEO4J_USER", "neo4j"),
                os.environ["NEO4J_PASSWORD"],
            ),
            # Aura (and network switches) silently kill idle pooled
            # connections; ping any connection unused for 30s before
            # trusting it instead of surfacing a 500.
            liveness_check_timeout=30,
        )
    return _driver


@contextmanager
def get_session():
    with get_driver().session() as session:
        yield session


def close():
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def is_production() -> bool:
    """True when NEO4J_URI points at a hosted Aura instance.

    Anything destructive checks this first. The dev seed refuses outright;
    nothing in the base is allowed to wipe a database it did not create.
    """
    return "databases.neo4j.io" in os.environ.get("NEO4J_URI", "")


def ping() -> None:
    """One round trip. Raises if the graph is not there.

    /health calls this, so an external uptime ping also counts as database
    activity: free Aura instances pause after three idle days.
    """
    with get_session() as session:
        session.run("RETURN 1").consume()


def wait_until_reachable(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            ping()
            return
        except ServiceUnavailable:
            if time.monotonic() >= deadline:
                raise
            logger.warning("database not reachable yet; retrying")
            time.sleep(2)


def migrate() -> None:
    """Apply the schema. Idempotent; runs on every boot. See schema.py."""
    from schema import ensure_schema

    with get_session() as session:
        ensure_schema(session)
