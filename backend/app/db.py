"""SQLite + SQLAlchemy setup.

Single-user app: one local database file, no connection pooling concerns,
no migrations tool. The schema is created on startup from the models, and
`ensure_columns` handles the only kind of change a solo project actually
needs -- adding a column to an existing table without losing data.
"""

import os
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATA_DIR = Path(os.getenv("PP_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "personal.db"
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record):
    """Bring SQLite's defaults in line with what the schema already assumes.

    `foreign_keys` is off by default, which means the ForeignKey columns in
    models.py were decorative: the routes enforce those relationships by hand.
    Turning it on makes the database enforce them too, so a bug that skips a
    check fails loudly instead of leaving an orphan row behind. It is a
    per-connection setting, hence doing this on every connect.

    `journal_mode=WAL` lets a read run while a write is in progress. With the
    dev server reloading and several tabs open, the default rollback journal
    turns that overlap into "database is locked". WAL is stored in the file
    itself, so it only actually changes anything the first time.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_columns():
    """Add any model column missing from an existing table.

    Lets the schema grow over time without Alembic. Dropping or retyping a
    column still needs a manual migration, but adding one is the only change
    that comes up in practice here.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in have:
                    continue
                ddl = column.type.compile(engine.dialect)
                default = ""
                if column.default is not None and column.default.is_scalar:
                    value = column.default.arg
                    literal = f"'{value}'" if isinstance(value, str) else str(int(value))
                    default = f" DEFAULT {literal}"
                conn.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl}{default}')
                )


def init_db():
    from app import models  # noqa: F401  (registers the tables on Base)

    Base.metadata.create_all(engine)
    ensure_columns()
