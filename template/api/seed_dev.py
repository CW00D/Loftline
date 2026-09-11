"""Build a development graph from scratch.

    python seed_dev.py

Wipes the target database, applies the schema, and creates one user so the
auth flow is walkable on first run:

    dev@example.test  password: localdev

Refuses to run against a hosted Aura instance.
"""
import uuid

from dotenv import load_dotenv

load_dotenv()

import db
from schema import ensure_schema
from security import hash_password

if db.is_production():
    raise SystemExit(
        "Refusing to run: NEO4J_URI points at Aura. "
        "Point api/.env at the local docker compose database first."
    )

PASSWORD = "localdev"

# (handle, email, name)
USERS = [
    ("dev", "dev@example.test", "Dev One"),
]


def run():
    with db.get_session() as s:
        print("wiping database")
        s.run("MATCH (n) DETACH DELETE n").consume()

        # Exactly what main.lifespan does.
        ensure_schema(s)

        pw = hash_password(PASSWORD)
        for handle, email, name in USERS:
            s.run(
                "CREATE (:User {id: $id, email: $email, handle: $handle, "
                "name: $name, password_hash: $pw, created_at: datetime()})",
                id=str(uuid.uuid4()), email=email, handle=handle, name=name, pw=pw,
            ).consume()

        count = s.run("MATCH (u:User) RETURN count(u)").single()[0]
        print(f"seeded {count} user(s)")
        print(f"dev login: dev@example.test  password '{PASSWORD}'")


if __name__ == "__main__":
    run()
    db.close_driver()
