"""Alembic, pointed at this app's models and this app's database file.

Two things are deliberately not the generated defaults.

**The URL comes from `app.db`, not from `alembic.ini`.** The database lives
wherever `PP_DATA_DIR` says, which is how the tests run against a temporary
one; a connection string hard-coded in an ini file would quietly migrate the
wrong database, and on the day that matters it would be the real one.

**`render_as_batch` is on.** SQLite cannot `ALTER COLUMN` or drop a
constraint. Batch mode makes Alembic rebuild the table around the change --
create, copy, drop, rename -- which is exactly the hand-written SQL this
project was one rename away from having to write itself.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, event

from app.db import DB_PATH, Base

# Imported for the side effect of registering every table on `Base.metadata`;
# without it autogenerate believes the schema is empty and writes a migration
# that drops everything.
from app import models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", f"sqlite:///{DB_PATH}")

target_metadata = Base.metadata


# An engine of its own, rather than the app's.
#
# The app turns foreign keys on -- SQLite defaults to ignoring them, which
# would make every `ForeignKey` in `models.py` decorative. But batch mode
# rebuilds a table by creating a copy, moving the rows, dropping the
# original and renaming it, and with enforcement on, dropping `projects`
# breaks every row that references it: the migration fails with `FOREIGN
# KEY constraint failed` on data that is perfectly sound.
#
# It has to be set at connect time. `PRAGMA foreign_keys` is a documented
# no-op inside a transaction, and SQLAlchemy opens one on the first
# statement -- so issuing it on an open connection looks like it worked,
# changes nothing, and the failure comes back with no hint of why.
migration_engine = create_engine(f"sqlite:///{DB_PATH}")


@event.listens_for(migration_engine, "connect")
def _migrations_defer_foreign_keys(dbapi_connection, _record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=OFF")
    cursor.close()


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it, for review before applying."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run against the live database, reusing the app's own engine.

    Uses `migration_engine`, which defers foreign keys -- see above for why
    that is necessary and why it has to happen at connect time. Integrity is
    checked once the schema is whole again, which is the point at which the
    answer means anything.
    """
    with migration_engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            # Off by default, and worth having on: a column that changed from
            # String(40) to String(500) is exactly the kind of drift that
            # `ensure_columns` could never have noticed.
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()

        # Checked once the schema is whole again. A migration that leaves a
        # dangling reference should say so here rather than be discovered
        # later by a query that quietly returns nothing.
        broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if broken:
            raise RuntimeError(
                f"The migration left {len(broken)} row(s) referencing something "
                f"that no longer exists: {broken[:5]}"
            )


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
