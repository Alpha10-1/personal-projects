"""What has been committed that should not have been.

The scan itself is arithmetic over stored commit detail, so most of what is
worth testing is the filter: that it catches the real thing, and that it
does not fire on `.env.example`, which is the failure that would get the
whole check ignored.
"""

import json
from datetime import datetime, timedelta

import pytest

from app import hygiene, models, review


def files(*paths):
    return json.dumps({"files": [{"path": p, "status": "added"} for p in paths]})


@pytest.fixture
def repo(make):
    """A project with three commits, oldest first."""
    project = make.project("Demo", repo="owner/demo")
    base = datetime(2026, 1, 1, 9, 0)

    def commit(n, *paths, title="work"):
        return make.event(
            external_id=f"github:commit:{str(n) * 40}",
            project_id=project.id,
            title=title,
            occurred_at=base + timedelta(days=n),
            file_stats=files(*paths) if paths else None,
        )

    return project, commit


# --- what it catches ------------------------------------------------------


def test_a_committed_key_is_found(make, db, repo):
    project, commit = repo
    commit(1, "app.py", "serviceAccountKey.json")

    report = hygiene.scan(db)[0]
    assert [entry.path for entry in report.secrets] == ["serviceAccountKey.json"]
    assert report.clean is False


def test_it_names_the_commit_that_introduced_it(make, db, repo):
    """The first one, not whichever was read first."""
    project, commit = repo
    commit(1, "functions/.env", title="add config")
    commit(2, "functions/.env", title="tweak config")
    commit(3, "functions/.env")

    entry = hygiene.scan(db)[0].secrets[0]
    assert entry.commits == 3
    assert entry.first_sha == "111111111111"
    assert entry.first_title == "add config"


def test_build_output_is_reported_separately_from_credentials(make, db, repo):
    project, commit = repo
    commit(1, ".firebase/hosting.cache", "node_modules/left-pad/index.js", ".env")

    report = hygiene.scan(db)[0]
    assert [e.path for e in report.secrets] == [".env"]
    assert {e.path for e in report.noise} == {
        ".firebase/hosting.cache",
        "node_modules/left-pad/index.js",
    }


def test_a_password_inside_a_connection_string_counts(make, db):
    assert hygiene.filled_in("DATABASE_URL=postgres://app:hunter2secret@db/app") == [
        "DATABASE_URL"
    ]


@pytest.mark.parametrize(
    "line,key",
    [
        ("STRIPE_SECRET_KEY=sk_live_abcdefghijklmnop", "STRIPE_SECRET_KEY"),
        ("GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz12", "GITHUB_TOKEN"),
        ("SESSION_SECRET=nMv82hsKqp", "SESSION_SECRET"),
    ],
)
def test_a_template_holding_a_real_secret_is_flagged(line, key):
    assert hygiene.filled_in(line) == [key]


# --- what it must not cry wolf on ----------------------------------------


def test_an_env_example_is_not_a_leak(make, db, repo):
    """The file exists to be committed. Flagging it is how a check dies."""
    project, commit = repo
    commit(1, ".env.example", "backend/.env.example")

    report = hygiene.scan(db)[0]
    assert report.secrets == []
    assert [e.path for e in report.templates] == [".env.example", "backend/.env.example"]
    assert report.clean is True


@pytest.mark.parametrize(
    "line",
    [
        "ENVIRONMENT=development",
        "NEXT_PUBLIC_API_URL=http://localhost:8000",
        "MAX_UPLOAD_SIZE_MB=25",
        "COOKIE_SECURE=true",
        "# API_KEY=sk_live_realsecretvalue",
        "ANTHROPIC_API_KEY=",
        "GITHUB_TOKEN=your-token-here",
        "SECRET_KEY=change-me",
        "API_KEY=<your key>",
    ],
)
def test_an_ordinary_template_line_is_not_a_finding(line):
    assert hygiene.filled_in(line) == []


def test_a_template_is_shown_even_though_it_was_exempted(make, db, repo):
    """The filter is shown, not just applied."""
    project, commit = repo
    commit(1, ".env.example")
    assert hygiene.scan(db)[0].templates[0].path == ".env.example"


def test_a_clean_repository_says_so(make, db, repo):
    project, commit = repo
    commit(1, "app.py", "README.md")
    assert hygiene.scan(db)[0].clean is True


# --- coverage -------------------------------------------------------------


def test_commits_without_file_detail_are_counted_not_assumed_clean(make, db, repo):
    project, commit = repo
    commit(1, "app.py")
    commit(2)  # never deep-synced

    report = hygiene.scan(db)[0]
    assert report.commits_scanned == 2
    assert report.commits_without_detail == 1


def test_a_broken_file_stats_payload_is_survived(make, db, repo):
    project, commit = repo
    event = commit(1, "app.py")
    event.file_stats = "{not json"
    db.commit()
    assert hygiene.scan(db)[0].commits_without_detail == 1


def test_only_commits_are_read(make, db, repo):
    project, commit = repo
    make.event(
        external_id="github:pr:1",
        kind="pull_request",
        project_id=project.id,
        file_stats=files(".env"),
    )
    assert hygiene.scan(db) == [] or hygiene.scan(db)[0].secrets == []


# --- the working copy sharpens the wording --------------------------------


def test_it_says_whether_the_file_is_still_there(make, db, repo, tmp_path):
    project, commit = repo
    commit(1, "serviceAccountKey.json", "gone.key")
    (tmp_path / "serviceAccountKey.json").write_text("{}")

    report = hygiene.scan(db)[0]
    hygiene.check_present(tmp_path, report)
    present = {e.path: e.still_present for e in report.secrets}
    assert present == {"serviceAccountKey.json": True, "gone.key": False}


def test_a_template_on_disk_is_read_for_real_values(make, db, repo, tmp_path):
    project, commit = repo
    commit(1, ".env.example")
    (tmp_path / ".env.example").write_text(
        "ENVIRONMENT=development\nSTRIPE_SECRET_KEY=sk_live_abcdefghijklmnop\n"
    )

    report = hygiene.scan(db)[0]
    hygiene.check_present(tmp_path, report)
    assert report.filled_templates[0].filled_keys == ["STRIPE_SECRET_KEY"]
    assert report.clean is False


# --- how it reaches the review -------------------------------------------


def test_the_review_reports_a_committed_secret_as_a_warning(make, db, repo):
    project, commit = repo
    commit(1, "serviceAccountKey.json")

    found = [f for f in review.find(db) if f.rule == "committed_secret"]
    assert len(found) == 1
    assert found[0].severity == "warn"
    assert "rotated" in found[0].detail
    assert "serviceAccountKey.json" in found[0].evidence[0]


def test_the_review_says_history_is_the_problem_not_the_file(make, db, repo):
    """The part people get wrong: deleting it now does not undo it."""
    project, commit = repo
    commit(1, ".env")
    found = next(f for f in review.find(db) if f.rule == "committed_secret")
    assert "does not remove them from history" in found.detail


def test_build_output_is_only_raised_once_it_is_a_habit(make, db, repo):
    project, commit = repo
    commit(1, "dist/app.js")
    assert [f for f in review.find(db) if f.rule == "committed_build_output"] == []

    for n in range(2, 2 + hygiene.HABIT):
        commit(n, "dist/app.js")
    found = [f for f in review.find(db) if f.rule == "committed_build_output"]
    assert len(found) == 1
    assert found[0].severity == "info"


def test_unscanned_commits_are_declared_rather_than_passed_over(make, db, repo):
    project, commit = repo
    commit(1, "app.py")
    commit(2)
    found = [f for f in review.find(db) if f.rule == "hygiene_not_fully_scanned"]
    assert len(found) == 1
    assert "1 of 2" in found[0].title


def test_a_clean_repository_raises_nothing(make, db, repo):
    project, commit = repo
    commit(1, "app.py")
    rules = {f.rule for f in review.find(db)}
    assert "committed_secret" not in rules
    assert "committed_build_output" not in rules
    assert "hygiene_not_fully_scanned" not in rules
