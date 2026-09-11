"""Test fixtures.

The tests run against a real Neo4j, the one `docker compose up` starts locally
and the service container CI starts. They wipe it. Both of those are why the
first thing here is a refusal to run against a hosted instance.
"""
import os
import sys
from pathlib import Path

import pytest
from neo4j.exceptions import ServiceUnavailable

# The API is a flat directory of modules, not a package; tests import them the
# same way uvicorn does.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("NEO4J_URI", "neo4j://127.0.0.1:7687")
os.environ.setdefault("NEO4J_USER", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", "localdev")
os.environ.setdefault("JWT_SECRET", "test-only-not-a-secret-test-only-not-a-secret")

import db  # noqa: E402
import emails  # noqa: E402
from main import app  # noqa: E402

if db.is_production():
    raise SystemExit("Refusing to run tests against Aura: NEO4J_URI points at a hosted instance.")


def pytest_sessionstart(session):
    try:
        with db.get_session() as s:
            s.run("RETURN 1").consume()
    except ServiceUnavailable:
        pytest.exit(
            f"No Neo4j at {os.environ['NEO4J_URI']}. Start one with `docker compose up -d neo4j` "
            "from the repository root.",
            returncode=2,
        )


@pytest.fixture(autouse=True)
def clean_database():
    with db.get_session() as s:
        s.run("MATCH (n) DETACH DELETE n").consume()
    yield


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    # As a context manager so the lifespan runs: the schema is applied exactly
    # the way a deploy applies it.
    with TestClient(app) as c:
        yield c


@pytest.fixture
def reset_codes(monkeypatch):
    """Capture password reset codes instead of mailing them."""
    sent = []

    def capture(to, name, code, ttl_minutes):
        sent.append((to, code))

    monkeypatch.setattr(emails, "send_password_reset", capture)
    return sent


@pytest.fixture
def user(client):
    """A signed-up user and the headers to act as them."""
    response = client.post(
        "/signup",
        json={
            "email": "dev@example.test",
            "password": "correct horse battery",
            "handle": "dev",
            "name": "Dev One",
        },
    )
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    return {
        "email": "dev@example.test",
        "password": "correct horse battery",
        "headers": {"Authorization": f"Bearer {token}"},
    }
