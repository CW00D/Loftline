"""Build a development database from scratch.

    python seed_dev.py

Wipes the users table, runs the migrations, and creates one user so the auth
flow is walkable on first run:

    dev@example.test  password: localdev

Refuses to run against anything but a local database.
"""
import uuid

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import delete, func, select

import db
from security import hash_password
from tables import User

if db.is_production():
    raise SystemExit(
        "Refusing to run: DATABASE_URL does not point at a local database. "
        "Point api/.env at the docker compose Postgres first."
    )

PASSWORD = "localdev"

# (handle, email, name)
USERS = [
    ("dev", "dev@example.test", "Dev One"),
]


def run():
    # Exactly what db.migrate does at boot.
    db.migrate()
    with db.get_session() as s:
        print("wiping users")
        s.execute(delete(User))

        pw = hash_password(PASSWORD)
        for handle, email, name in USERS:
            s.add(
                User(
                    id=str(uuid.uuid4()),
                    email=email,
                    handle=handle,
                    name=name,
                    password_hash=pw,
                )
            )
        s.commit()

        count = s.scalar(select(func.count()).select_from(User))
        print(f"seeded {count} user(s)")
        print(f"dev login: dev@example.test  password '{PASSWORD}'")


if __name__ == "__main__":
    run()
    db.close()
