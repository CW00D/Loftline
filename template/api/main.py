import logging
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from neo4j.exceptions import ServiceUnavailable

import db
from auth import router as auth_router
from schema import ensure_schema

logger = logging.getLogger("skeleton.main")

# How long to wait for the database at boot before giving up. Under docker
# compose the API container can win the race against Neo4j's own startup even
# with a health check in front of it; on a hosted platform a deploy that boots
# before the database is reachable should fail loudly rather than serve 500s.
DB_WAIT_SECONDS = 60


def _wait_for_database() -> None:
    deadline = time.monotonic() + DB_WAIT_SECONDS
    while True:
        try:
            with db.get_session() as session:
                session.run("RETURN 1").consume()
            return
        except ServiceUnavailable:
            if time.monotonic() >= deadline:
                raise
            logger.warning("database not reachable yet; retrying")
            time.sleep(2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _wait_for_database()
    with db.get_session() as session:
        # Idempotent; this is the migration step. See schema.py.
        ensure_schema(session)
    yield
    db.close_driver()


app = FastAPI(title="Skeleton API", lifespan=lifespan)
app.include_router(auth_router)


@app.get("/health")
def health():
    # Touches the graph so an external uptime ping also counts as database
    # activity: free Aura instances pause after three idle days.
    try:
        with db.get_session() as session:
            session.run("RETURN 1").consume()
        return {"ok": True, "db": True}
    except Exception:
        return {"ok": True, "db": False}
