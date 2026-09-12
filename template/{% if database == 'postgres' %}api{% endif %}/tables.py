"""The tables: the single source of truth for the relational schema.

migrations/ is derived from this file, never the other way round. Change a
table here, then from this directory:

    alembic revision --autogenerate -m "what changed"

and review the file it writes before committing it.

Import-safe: no app imports, no environment reads, no side effects.

An overlay that needs a column of its own adds a migration and a
`tables_<name>.py` module that appends the column to the table, not a line
here. This file is shared by every project on this database; those two are
conditional paths an overlay can own, and migrations/env.py discovers the
module so autogenerate still sees the whole schema.
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    # Named so a violation says which field is taken; routers/auth.py reads
    # the constraint name out of the error to answer 409 with the field.
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        UniqueConstraint("handle", name="uq_users_handle"),
    )

    # A UUID string rather than a native uuid: it is the `sub` of every token
    # and is only ever compared, never computed with.
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320))
    handle: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Password reset state; see routers/auth.py. The code is stored hashed,
    # like the password, so a dump of the table is not a pile of live
    # account-takeover tokens.
    reset_code_hash: Mapped[str | None] = mapped_column(String(255))
    reset_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reset_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reset_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
