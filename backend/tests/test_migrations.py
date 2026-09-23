"""The migrations and the models have to agree.

The whole value of adding Alembic is that the schema stops being whatever
`create_all` happened to produce on the machine that ran first. That only
holds if something checks, so this is that check: build a database from the
migrations alone and confirm autogenerate has nothing left to say.

It is also the test that catches the ordinary mistake -- adding a column to
`models.py` and forgetting the migration. Without it that goes unnoticed
until someone else's database is missing the column.
"""

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def alembic(*args, data_dir: Path):
    """Run the real CLI, in a scratch data directory.

    A subprocess rather than the Python API because `app.db` builds its
    engine at import time from `PP_DATA_DIR`: pointing it somewhere else
    inside a running process means re-importing the module, and a test that
    does that is testing the import machinery rather than the migrations.
    """
    env = {
        **dict(__import__("os").environ),
        "PP_DATA_DIR": str(data_dir),
        "PYTHONPATH": str(BACKEND),
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def scratch(tmp_path):
    out = tmp_path / "data"
    out.mkdir()
    return out


pytestmark = pytest.mark.skipif(
    __import__("importlib.util", fromlist=["util"]).find_spec("alembic") is None,
    reason="alembic is not installed",
)


def test_the_migrations_build_the_schema_the_models_describe(scratch):
    """The one that matters: run them, then ask if anything is missing."""
    upgraded = alembic("upgrade", "head", data_dir=scratch)
    assert upgraded.returncode == 0, upgraded.stderr

    checked = alembic("check", data_dir=scratch)
    assert checked.returncode == 0, (
        "The models and the migrations disagree. Add a migration:\n"
        f"{checked.stdout}\n{checked.stderr}"
    )


def test_there_is_exactly_one_head(scratch):
    """Two heads means two people added a migration and neither merged."""
    heads = alembic("heads", data_dir=scratch)
    assert heads.returncode == 0, heads.stderr
    lines = [line for line in heads.stdout.splitlines() if "(head)" in line]
    assert len(lines) == 1, heads.stdout


def test_a_fresh_database_comes_out_stamped(scratch, monkeypatch):
    """So the next `upgrade` has something to work from."""
    upgraded = alembic("upgrade", "head", data_dir=scratch)
    assert upgraded.returncode == 0, upgraded.stderr

    current = alembic("current", data_dir=scratch)
    assert "(head)" in current.stdout, current.stdout


def test_every_migration_can_be_walked_back(scratch):
    """Not a promise that downgrading is wise -- a check that it is written.

    A downgrade nobody ever ran is a downgrade that does not work, and the
    moment you need one is the worst moment to find that out.
    """
    assert alembic("upgrade", "head", data_dir=scratch).returncode == 0
    down = alembic("downgrade", "base", data_dir=scratch)
    assert down.returncode == 0, down.stderr

    back = alembic("upgrade", "head", data_dir=scratch)
    assert back.returncode == 0, back.stderr
