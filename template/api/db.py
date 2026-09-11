"""Neo4j driver singleton. The driver pools connections; create it once."""
import os
from contextlib import contextmanager

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

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


def close_driver():
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
