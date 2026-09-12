"""Test fixtures.

The tests run against a real Postgres, the one `docker compose up` starts
locally and the service container CI starts. They wipe the users table. Both
of those are why the first thing here is a refusal to run against anything
but a local database.
"""
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

# The API is a flat directory of modules, not a package; tests import them the
# same way uvicorn does.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql://app:localdev@127.0.0.1:5432/app")
os.environ.setdefault("JWT_SECRET", "test-only-not-a-secret-test-only-not-a-secret")

import db  # noqa: E402
import emails  # noqa: E402
from main import app  # noqa: E402

if db.is_production():
    raise SystemExit(
        "Refusing to run tests: DATABASE_URL does not point at a local database."
    )


def pytest_sessionstart(session):
    try:
        db.ping()
    except OperationalError as exc:
        # The error is included because "no database" and "a different
        # database" look the same from here: a Postgres already installed on
        # the machine owns port 5432 and answers instead of the container.
        pytest.exit(
            "Cannot use the Postgres at DATABASE_URL. Start the local one with "
            "`docker compose up -d postgres` from the repository root; if a Postgres "
            "is already installed on this machine, see the port note in "
            f"docker-compose.yml.\n{exc.orig}",
            returncode=2,
        )


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    # As a context manager so the lifespan runs: the migrations are applied
    # exactly the way a deploy applies them.
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_database(client):
    # Depends on `client` so the lifespan, and with it the migrations, has run
    # before the first wipe; there is no table to wipe until then.
    with db.get_session() as s:
        s.execute(text("DELETE FROM users"))
        s.commit()
    yield


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
