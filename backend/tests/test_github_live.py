"""The one test that really talks to GitHub.

Deselected by default because it needs the network and counts against the
API rate limit. Run it when the fetching itself needs checking:

    pytest -m network

Everything else about ingestion is covered in test_activity.py with an
injected fetcher.
"""

import pytest

from app import github, models

REPO = "Alpha10-1/personal-projects"

pytestmark = pytest.mark.network


def test_fetching_a_public_repo_returns_usable_events():
    fetched = github.fetch_from_github(REPO, since=None, limit=10)

    assert fetched, "expected at least one commit or pull request"
    kinds = {item["kind"] for item in fetched}
    assert kinds <= {"commit", "pull_request"}

    # Every payload has to survive translation, which is where a change in
    # GitHub's response shape would show up first.
    for item in fetched:
        event = github.to_event(REPO, item["kind"], item["payload"])
        assert event["external_id"].startswith("github:")
        assert event["title"]
        assert event["occurred_at"] is not None


def test_a_missing_repo_explains_itself():
    with pytest.raises(RuntimeError) as caught:
        github.fetch_from_github(
            "Alpha10-1/this-repo-does-not-exist-9f3a", since=None, limit=1
        )

    assert "404" in str(caught.value)


def test_a_real_sync_stores_and_links_events(db, make):
    project = make.project(name="Tracker", repo=REPO)

    result = github.sync_repo(db, REPO, limit=20)

    assert result["added"] > 0
    events = db.query(models.ActivityEvent).all()
    assert all(e.project_id == project.id for e in events)
    assert all(e.provider == "github" for e in events)

    # Running it again is a no-op.
    again = github.sync_repo(db, REPO, limit=20)
    assert again["added"] == 0
