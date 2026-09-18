"""GitHub ingestion: translation, linkage and idempotency.

The fetcher is injected, so everything except the HTTP call itself is tested
here. The one test that really talks to GitHub lives in
test_github_live.py and is skipped by default.
"""

import pytest

from app import github, models

REPO = "Alpha10-1/personal-projects"


def commit_payload(sha="abc123", message="Fix the backtest window", login="Alpha10-1"):
    return {
        "sha": sha,
        "html_url": f"https://github.com/{REPO}/commit/{sha}",
        "author": {"login": login},
        "commit": {
            "message": message,
            "author": {"name": "Alpha", "date": "2026-09-16T08:30:00Z"},
        },
    }


def pr_payload(number=7, title="Add WAL mode", body="", branch="wal", merged=None):
    return {
        "number": number,
        "title": title,
        "body": body,
        "html_url": f"https://github.com/{REPO}/pull/{number}",
        "user": {"login": "Alpha10-1"},
        "head": {"ref": branch},
        "merged_at": merged,
        "updated_at": "2026-09-16T10:00:00Z",
    }


def fake_fetcher(items):
    def fetch(repo, since, limit):
        return items
    return fetch


# --- Parsing an explicit task reference --------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Task: 42 tidy the loader", 42),
        ("tidy the loader (task 42)", 42),
        ("TASK-42 tidy", 42),
        ("task#42", 42),
        ("fixes #42", None),        # a GitHub issue, not one of our tasks
        ("tasks are hard", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_task_ref(text, expected):
    assert github.parse_task_ref(text) == expected


# --- Translating provider payloads -------------------------------------------


def test_commit_becomes_an_event_with_a_stable_id():
    event = github.to_event(REPO, "commit", commit_payload(sha="deadbeef"))

    assert event["external_id"] == "github:commit:deadbeef"
    assert event["kind"] == "commit"
    assert event["title"] == "Fix the backtest window"
    assert event["actor"] == "Alpha10-1"
    assert event["occurred_at"].isoformat() == "2026-09-16T08:30:00"


def test_only_the_commit_subject_becomes_the_title():
    """A commit body can be long; the subject is what belongs in a list."""
    event = github.to_event(
        REPO, "commit", commit_payload(message="Short subject\n\nA much longer body.")
    )

    assert event["title"] == "Short subject"
    # The body is still searched for a task reference.
    assert "longer body" in event["ref_text"]


def test_pull_request_becomes_an_event():
    event = github.to_event(REPO, "pull_request", pr_payload(number=9))

    assert event["external_id"] == f"github:pr:{REPO}#9"
    assert event["kind"] == "pull_request"
    assert event["url"].endswith("/pull/9")


def test_an_unsupported_kind_is_refused():
    with pytest.raises(ValueError, match="Unsupported event kind"):
        github.to_event(REPO, "release", {})


# --- Linkage ------------------------------------------------------------------


def test_activity_in_an_unmapped_repo_links_to_nothing(db):
    assert github.link(db, REPO, "Task: 1") == {
        "project_id": None,
        "task_id": None,
        "linked_by": None,
    }


def test_the_repo_settles_the_project(db, make):
    project = make.project(name="Tracker", repo=REPO)

    assert github.link(db, REPO, "no reference here") == {
        "project_id": project.id,
        "task_id": None,
        "linked_by": "repo",
    }


def test_an_explicit_reference_links_the_task(db, make):
    project = make.project(name="Tracker", repo=REPO)
    task = make.task(title="Tidy the loader", project_id=project.id)

    assert github.link(db, REPO, f"Task: {task.id} tidy the loader") == {
        "project_id": project.id,
        "task_id": task.id,
        "linked_by": "convention",
    }


def test_a_reference_to_another_projects_task_is_not_followed(db, make):
    """Far more likely a typo than a real cross-project link, so it falls back
    to the project the repo names rather than attaching to the wrong task."""
    tracker = make.project(name="Tracker", repo=REPO)
    other = make.project(name="Something else")
    stray = make.task(title="unrelated", project_id=other.id)

    assert github.link(db, REPO, f"Task: {stray.id}") == {
        "project_id": tracker.id,
        "task_id": None,
        "linked_by": "repo",
    }


def test_a_reference_to_a_task_that_does_not_exist_is_ignored(db, make):
    make.project(name="Tracker", repo=REPO)

    assert github.link(db, REPO, "Task: 9999")["linked_by"] == "repo"


# --- Which repos get synced ---------------------------------------------------


def test_tracked_repos_come_from_live_projects(db, make):
    make.project(name="a", repo=REPO)
    make.project(name="b", repo="  ")          # blank
    make.project(name="c")                     # unset
    make.project(name="d", repo="x/y", archived_at=models.utcnow())

    assert github.tracked_repos(db) == [REPO]


def test_the_same_repo_on_two_projects_is_synced_once(db, make):
    make.project(name="a", repo=REPO)
    make.project(name="b", repo=REPO)

    assert github.tracked_repos(db) == [REPO]


# --- Syncing ------------------------------------------------------------------


def test_sync_records_events_and_links_them(db, make):
    project = make.project(name="Tracker", repo=REPO)
    task = make.task(title="Tidy", project_id=project.id)
    items = [
        {"kind": "commit", "payload": commit_payload(sha="a1", message=f"Task: {task.id} tidy")},
        {"kind": "commit", "payload": commit_payload(sha="a2", message="unrelated work")},
        {"kind": "pull_request", "payload": pr_payload(number=3)},
    ]

    result = github.sync_repo(db, REPO, fetcher=fake_fetcher(items))

    assert result["fetched"] == 3
    assert result["added"] == 3
    assert result["linked_to_task"] == 1

    events = db.query(models.ActivityEvent).all()
    assert {e.external_id for e in events} == {
        "github:commit:a1",
        "github:commit:a2",
        f"github:pr:{REPO}#3",
    }
    assert all(e.project_id == project.id for e in events)
    linked = [e for e in events if e.task_id]
    assert len(linked) == 1 and linked[0].linked_by == "convention"


def test_syncing_twice_adds_nothing_the_second_time(db, make):
    make.project(name="Tracker", repo=REPO)
    items = [{"kind": "commit", "payload": commit_payload(sha="a1")}]

    first = github.sync_repo(db, REPO, fetcher=fake_fetcher(items))
    second = github.sync_repo(db, REPO, fetcher=fake_fetcher(items))

    assert first["added"] == 1
    assert second["added"] == 0
    assert second["skipped"] == 1
    assert db.query(models.ActivityEvent).count() == 1


def test_a_duplicate_inside_one_batch_is_recorded_once(db, make):
    make.project(name="Tracker", repo=REPO)
    items = [
        {"kind": "commit", "payload": commit_payload(sha="same")},
        {"kind": "commit", "payload": commit_payload(sha="same")},
    ]

    result = github.sync_repo(db, REPO, fetcher=fake_fetcher(items))

    assert result["added"] == 1
    assert db.query(models.ActivityEvent).count() == 1


def test_the_raw_payload_is_kept(db, make):
    make.project(name="Tracker", repo=REPO)
    github.sync_repo(
        db, REPO, fetcher=fake_fetcher([{"kind": "commit", "payload": commit_payload()}])
    )

    event = db.query(models.ActivityEvent).one()
    assert "\"sha\"" in event.raw


def test_sync_is_told_where_it_left_off(db, make):
    """The second run asks GitHub only for what happened after the newest
    event already stored."""
    make.project(name="Tracker", repo=REPO)
    seen = {}

    def recording_fetcher(repo, since, limit):
        seen["since"] = since
        return [{"kind": "commit", "payload": commit_payload(sha="a1")}]

    github.sync_repo(db, REPO, fetcher=recording_fetcher)
    assert seen["since"] is None

    github.sync_repo(db, REPO, fetcher=recording_fetcher)
    assert seen["since"].isoformat() == "2026-09-16T08:30:00"


# --- Endpoints ----------------------------------------------------------------


def test_activity_endpoint_lists_newest_first(client, db, make, monkeypatch):
    make.project(name="Tracker", repo=REPO)
    items = [
        {"kind": "commit", "payload": commit_payload(sha="old", message="older")},
        {"kind": "pull_request", "payload": pr_payload(number=1, title="newer")},
    ]
    monkeypatch.setattr(github, "fetch_from_github", fake_fetcher(items))
    # The sync mirrors comments in the same pass; this test is about activity.
    monkeypatch.setattr(github, "fetch_comments", lambda *_a: [])

    assert client.post("/activity/sync").status_code == 200

    rows = client.get("/activity").json()
    assert [r["title"] for r in rows] == ["newer", "older"]
    assert rows[0]["project_name"] == "Tracker"


def test_activity_filters(client, db, make, monkeypatch):
    project = make.project(name="Tracker", repo=REPO)
    task = make.task(title="Tidy", project_id=project.id)
    items = [
        {"kind": "commit", "payload": commit_payload(sha="a1", message=f"Task: {task.id}")},
        {"kind": "pull_request", "payload": pr_payload(number=2)},
    ]
    monkeypatch.setattr(github, "fetch_from_github", fake_fetcher(items))
    # The sync mirrors comments in the same pass; this test is about activity.
    monkeypatch.setattr(github, "fetch_comments", lambda *_a: [])
    client.post("/activity/sync")

    assert len(client.get("/activity", params={"kind": "commit"}).json()) == 1
    assert len(client.get("/activity", params={"task_id": task.id}).json()) == 1
    assert len(client.get("/activity", params={"project_id": project.id}).json()) == 2
    assert client.get("/activity", params={"unlinked_only": True}).json() == []


def test_tracked_repos_endpoint(client, make):
    make.project(name="Tracker", repo=REPO)

    assert client.get("/activity/repos").json() == [REPO]


def test_sync_with_nothing_to_sync_explains_itself(client):
    response = client.post("/activity/sync")

    assert response.status_code == 400
    assert "No repos to sync" in response.json()["detail"]


def test_a_github_failure_surfaces_as_502(client, make, monkeypatch):
    make.project(name="Tracker", repo=REPO)

    def failing(repo, since, limit):
        raise RuntimeError("GitHub 404: not found. It may be private.")

    monkeypatch.setattr(github, "fetch_from_github", failing)

    response = client.post("/activity/sync")

    assert response.status_code == 502
    assert "GitHub 404" in response.json()["detail"]
