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

    def person(self, name="Person", **kw):
        return self._add(models.Person(name=name, **kw))

    def event(self, repo="owner/name", external_id="e1", kind="commit", **kw):
        kw.setdefault("title", "a commit")
        kw.setdefault("occurred_at", models.utcnow())
        return self._add(
            models.ActivityEvent(
                provider="github", external_id=external_id, kind=kind, repo=repo, **kw
            )
        )

    def log(self, work_date=None, hours=1.0, **kw):
        return self._add(
            models.TimeLog(
                work_date=work_date or TODAY, hours=hours, **kw
            )
        )


@pytest.fixture
def make(db):
    return Factory(db)


@pytest.fixture(autouse=True)
def no_outbound_http(monkeypatch, request):
    """Fail loudly if a test reaches the real GitHub API.

    An earlier version of sync_repo bound its fetcher as a default argument,
    so patching the module attribute silently did nothing and the suite went
    to the network for real. This makes that failure mode impossible to miss
    rather than merely slow.

    Marking a test `network` opts back in.
    """
    if request.node.get_closest_marker("network"):
        return

    def refuse(repo, since, limit):
        raise AssertionError(
            f"Test tried to fetch {repo} from GitHub. Inject a fetcher, or "
            "mark the test with @pytest.mark.network."
        )

    def refuse_comments(repo, since, limit):
        raise AssertionError(
            f"Test tried to fetch comments for {repo} from GitHub. Inject a "
            "fetcher, or mark the test with @pytest.mark.network."
        )

    def refuse_repos(user, limit=100, **kw):
        raise AssertionError(
            f"Test tried to list {user}'s repos from GitHub. Patch "
            "github.fetch_user_repos, or mark the test with @pytest.mark.network."
        )

    # Guarded at the uncached call rather than the wrapper, so the cache in
    # front of it stays real -- a guard on the wrapper would make the caching
    # itself untestable, which is the part that protects the quota.

    def refuse_readme(repo, max_chars=8000):
        raise AssertionError(
            f"Test tried to fetch {repo}'s README from GitHub. Patch "
            "github.fetch_readme, or mark the test with @pytest.mark.network."
        )

    def refuse_diffs(repo, shas, **kw):
        raise AssertionError(
            f"Test tried to fetch diffs for {repo} from GitHub. Patch "
            "github.fetch_diffs, or mark the test with @pytest.mark.network."
        )

    def refuse_model(*_args, **_kwargs):
        raise AssertionError(
            "Test tried to call the Anthropic API, which costs money. Patch "
            "ai.structured or ai.stream instead."
        )

    from app import ai, github

    monkeypatch.setattr(github, "fetch_from_github", refuse)
    monkeypatch.setattr(github, "fetch_comments", refuse_comments)
    monkeypatch.setattr(github, "_fetch_user_repos_uncached", refuse_repos)
    monkeypatch.setattr(github, "fetch_readme", refuse_readme)
    monkeypatch.setattr(github, "fetch_diffs", refuse_diffs)
    # The model is guarded at the client rather than at structured()/stream(),
    # so a test that patches neither is caught instead of quietly billing.
    monkeypatch.setattr(ai, "_client", refuse_model)
