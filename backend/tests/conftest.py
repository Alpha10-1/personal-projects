"""Shared fixtures.

`app.db` builds its engine at import time from PP_DATA_DIR, so the temp
directory has to be in the environment before anything under `app` is
imported -- hence the assignment above the app imports rather than in a
fixture.
"""

import os
import tempfile
from datetime import date, datetime, time, timedelta

os.environ["PP_DATA_DIR"] = tempfile.mkdtemp(prefix="pp-tests-")

import pytest
from fastapi.testclient import TestClient

from app import models
from app.db import Base, SessionLocal, engine
from app.main import app


@pytest.fixture
def db():
    """A clean database per test.

    Dropping and recreating is fast on a small SQLite file and keeps each
    test's assertions about counts and totals independent of the others.
    """
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db):
    with TestClient(app) as test_client:
        yield test_client


# --- Dates ------------------------------------------------------------------
#
# Every test builds its data relative to today. The aggregations call
# date.today() internally, so anchoring fixtures to real "now" keeps them
# deterministic on any day of the week without needing to freeze the clock.

TODAY = date.today()
WEEK_START = TODAY - timedelta(days=TODAY.weekday())  # Monday of this week


def days_ago(n):
    return TODAY - timedelta(days=n)


def days_ahead(n):
    return TODAY + timedelta(days=n)


def at(day):
    """Midnight on `day`, for the naive-UTC datetime columns."""
    return datetime.combine(day, time.min)


# --- Row factories ----------------------------------------------------------


class Factory:
    def __init__(self, session):
        self.session = session

    def _add(self, row):
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def project(self, name="Project", **kw):
        return self._add(models.Project(name=name, **kw))

    def task(self, title="Task", **kw):
        return self._add(models.Task(title=title, **kw))

    def milestone(self, project, title="Milestone", **kw):
        return self._add(models.Milestone(project_id=project.id, title=title, **kw))

    def log(self, work_date=None, hours=1.0, **kw):
        return self._add(
            models.TimeLog(
                work_date=work_date or TODAY, hours=hours, **kw
            )
        )


@pytest.fixture
def make(db):
    return Factory(db)
