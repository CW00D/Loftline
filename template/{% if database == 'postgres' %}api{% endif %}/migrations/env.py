"""Alembic environment. Points migrations at the same database the API uses.

Logging is deliberately not configured here: Alembic's default env.py calls
logging.fileConfig, which would replace the API's logging when db.migrate()
runs at boot.
"""
import importlib
import pkgutil
import sys
from pathlib import Path

from alembic import context
from sqlalchemy import pool

# The API is a flat directory of modules; make it importable whether Alembic
# is run from that directory or by db.migrate() from anywhere.
API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))

import db  # noqa: E402
from tables import Base  # noqa: E402

# An overlay that owns a column declares it in a module named tables_<name>.py,
# which appends it to the shared table. Importing every such module here is
# what lets autogenerate see the whole schema without tables.py changing when
# an overlay is enabled. The same discovery main.py does for routers.
for info in pkgutil.iter_modules([str(API_DIR)]):
    if info.name.startswith("tables_"):
        importlib.import_module(info.name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it."""
    context.configure(
        url=db.database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = db.get_engine().execution_options(poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
