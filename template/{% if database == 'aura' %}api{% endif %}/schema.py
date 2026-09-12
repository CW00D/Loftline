"""Constraints and indexes: the single source of truth for the graph's schema.

Applied on every boot by main.lifespan and by seed_dev.py, so development and
production cannot drift. Every statement is IF NOT EXISTS and idempotent.

This is the migration mechanism for a graph store. There is no numbered
migration directory: add a constraint here and it exists everywhere on the
next deploy.

Import-safe: no app imports, no environment reads, no side effects.
"""
import logging

from neo4j.exceptions import Neo4jError

logger = logging.getLogger("app.schema")

CONSTRAINTS = [
    "CREATE CONSTRAINT user_id IF NOT EXISTS FOR (u:User) REQUIRE u.id IS UNIQUE",
    "CREATE CONSTRAINT user_email IF NOT EXISTS FOR (u:User) REQUIRE u.email IS UNIQUE",
    "CREATE CONSTRAINT user_handle IF NOT EXISTS FOR (u:User) REQUIRE u.handle IS UNIQUE",
]


def ensure_schema(session) -> list[str]:
    """Materialise constraints and indexes. Returns the statements that failed.

    A uniqueness constraint whose data already violates it errors even with IF
    NOT EXISTS. Letting that propagate out of main.lifespan would stop the API
    booting and the host's health check would roll the deploy back, trading a
    missing guarantee for a total outage. So each statement fails alone and is
    logged, and the caller gets the failures back for its own reporting.
    """
    failed = []
    for statement in CONSTRAINTS:
        try:
            session.run(statement).consume()
        except Neo4jError as exc:
            failed.append(statement)
            logger.error("schema statement failed: %s: %s", statement, exc)
    return failed
