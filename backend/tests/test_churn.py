"""Which files change constantly, and which of those nothing tests.

The counting is arithmetic and easy. What is worth testing is the judgement
around it: what is excluded from churn, and the difference between "nothing
tests this" and "I could not tell", which are the two answers that must
never be confused.
"""

import json
import subprocess
from datetime import datetime, timedelta

import pytest

from app import churn, models


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def run_git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(make, tmp_path, monkeypatch):
    """A checkout with one tested module and one untested one."""
    root = tmp_path / "demo"
    write(root / "core.py", "def fetch_rows(source):\n    return source\n")
    write(root / "schemas.py", "def build_payload(row):\n    return dict(row)\n")
    write(root / "tests" / "test_core.py", "from core import fetch_rows\n\n\ndef test_it():\n    assert fetch_rows([]) == []\n")
    run_git(root, "init", "--initial-branch=main")
    run_git(root, "config", "user.email", "t@e.com")
    run_git(root, "config", "user.name", "T")
    run_git(root, "add", ".")
    run_git(root, "commit", "-m", "first")
    monkeypatch.setenv("PP_WORKSPACE_ROOTS", str(tmp_path))

    project = make.project("Demo", repo="owner/demo", local_path=str(root))
    counter = {"n": 0}

    def commit(*paths, days_ago=1, actor="alubisi", lines=10):
        counter["n"] += 1
        return make.event(
            external_id=f"github:commit:{counter['n']}",
            project_id=project.id,
            actor=actor,
            occurred_at=datetime.now() - timedelta(days=days_ago),
            file_stats=json.dumps(
                {
                    "files": [
                        {
                            "path": p,
                            "status": "modified",
                            "additions": lines,
                            "deletions": 0,
                        }
                        for p in paths
                    ],
                    "additions": lines * len(paths),
                    "deletions": 0,
                }
            ),
        )

    return project, commit


# --- counting -------------------------------------------------------------


def test_the_busiest_file_comes_first(db, repo):
    project, commit = repo
    for _ in range(3):
        commit("core.py")
    commit("schemas.py")

    rows = churn.counted(db, project.id)
    assert [r.path for r in rows] == ["core.py", "schemas.py"]
    assert rows[0].commits == 3


def test_lines_changed_are_summed(db, repo):
    project, commit = repo
    commit("core.py", lines=40)
    commit("core.py", lines=2)
    assert churn.counted(db, project.id)[0].lines_changed == 42


def test_several_authors_are_counted(db, repo):
    project, commit = repo
    commit("core.py", actor="a")
    commit("core.py", actor="b")
    commit("core.py", actor="a")
    assert churn.counted(db, project.id)[0].authors == 2


def test_work_outside_the_window_is_not_counted(db, repo):
    project, commit = repo
    commit("core.py", days_ago=churn.WINDOW_DAYS + 5)
    assert churn.counted(db, project.id) == []


def test_another_project_is_not_counted(db, repo, make):
    project, commit = repo
    other = make.project("Other")
    make.event(
        external_id="github:commit:other",
        project_id=other.id,
        occurred_at=datetime.now(),
        file_stats=json.dumps({"files": [{"path": "core.py"}]}),
    )
    assert churn.counted(db, project.id) == []


# --- what churn deliberately ignores --------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "package-lock.json",
        "node_modules/left-pad/index.js",
        "dist/bundle.min.js",
        "public/logo.svg",
        "backend/app/__pycache__/main.cpython-312.pyc",
    ],
)
def test_generated_and_vendored_files_are_left_out(db, repo, path):
    """Otherwise a lockfile outranks everything you actually maintain."""
    project, commit = repo
    commit(path)
    assert churn.counted(db, project.id) == []


def test_a_test_file_is_not_itself_churn(db, repo):
    """A busy test file is a sign of care, not of risk."""
    project, commit = repo
    commit("tests/test_core.py")
    assert churn.counted(db, project.id) == []


def test_a_commit_without_file_detail_is_skipped_not_guessed(db, repo, make):
    project, commit = repo
    make.event(
        external_id="github:commit:bare",
        project_id=project.id,
        occurred_at=datetime.now(),
        file_stats=None,
    )
    assert churn.counted(db, project.id) == []


# --- coverage -------------------------------------------------------------


def test_a_file_with_a_test_is_reported_as_tested(db, repo, tmp_path):
    project, commit = repo
    for _ in range(churn.BUSY):
        commit("core.py")

    out = churn.map_project(db, project)
    entry = out["files"][0]
    assert entry["tested"] is True
    assert entry["test_files"] == ["tests/test_core.py"]


def test_a_file_with_no_test_is_reported_as_untested(db, repo):
    project, commit = repo
    for _ in range(churn.BUSY):
        commit("schemas.py")

    entry = churn.map_project(db, project)["files"][0]
    assert entry["tested"] is False
    assert entry["at_risk"] is True


def test_not_knowing_is_not_the_same_as_untested(db, repo):
    """The mistake that would matter: reporting 'unknown' as 'no test'."""
    from pathlib import Path

    project, commit = repo
    write(Path(project.local_path) / "notes.md", "# hello\n")
    for _ in range(churn.BUSY):
        commit("notes.md")

    entry = next(e for e in churn.map_project(db, project)["files"] if e["path"] == "notes.md")
    assert entry["tested"] is None
    assert entry["at_risk"] is False
    assert entry["why_unknown"]


def test_a_file_that_is_gone_is_not_at_risk(db, repo):
    project, commit = repo
    for _ in range(churn.BUSY):
        commit("deleted.py")

    entry = next(e for e in churn.map_project(db, project)["files"] if e["path"] == "deleted.py")
    assert entry["exists"] is False
    assert entry["at_risk"] is False


def test_a_rarely_changed_file_is_not_interrogated(db, repo):
    """Coverage costs greps; a file changed once does not earn them."""
    project, commit = repo
    commit("schemas.py")
    entry = churn.map_project(db, project)["files"][0]
    assert entry["tested"] is None
    assert "too rarely" in entry["why_unknown"]


def test_at_risk_needs_all_three_of_busy_untested_and_present(db, repo):
    project, commit = repo
    commit("schemas.py")  # busy enough? no
    assert churn.map_project(db, project)["at_risk"] == []

    for _ in range(churn.BUSY):
        commit("schemas.py")
    assert [e["path"] for e in churn.map_project(db, project)["at_risk"]] == ["schemas.py"]


# --- honesty --------------------------------------------------------------


def test_it_says_when_there_was_no_checkout_to_read(db, repo):
    project, commit = repo
    project.local_path = None
    db.commit()
    for _ in range(churn.BUSY):
        commit("schemas.py")

    out = churn.map_project(db, project)
    assert out["code_was_read"] is False
    assert out["files"][0]["tested"] is None


def test_it_declares_the_commits_it_could_not_read(db, repo, make):
    project, commit = repo
    commit("core.py")
    make.event(
        external_id="github:commit:bare",
        project_id=project.id,
        occurred_at=datetime.now(),
        file_stats=None,
    )
    out = churn.map_project(db, project)
    assert out["commits_scanned"] == 2
    assert out["commits_without_detail"] == 1


def test_the_limits_are_stated_rather_than_implied(db, repo):
    project, commit = repo
    commit("core.py")
    limits = " ".join(churn.map_project(db, project)["limits"]).lower()
    assert "rename" in limits
    assert "not fetched" in limits or "never fetched" in limits
    assert "coverage run" in limits


# --- "cannot read it" versus "there is nothing in it to read" -------------


@pytest.mark.parametrize(
    "path", ["README.md", ".github/workflows/ci.yml", "Dockerfile", "src/app.css"]
)
def test_a_file_with_no_code_says_so_rather_than_blaming_the_outline(
    db, repo, path
):
    """Two different answers that used to share one sentence.

    "The outline does not read this language yet" implies a gap that
    closing would help. For a README it would not: there is nothing in it
    to trace, and the wording should not send anyone off to add a parser.
    """
    from pathlib import Path

    project, commit = repo
    target = Path(project.local_path) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# hello\n")
    for _ in range(churn.BUSY):
        commit(path)

    entry = next(e for e in churn.map_project(db, project)["files"] if e["path"] == path)
    assert entry["tested"] is None
    assert entry["why_unknown"] == "not code — there are no definitions to trace"


def test_an_unsupported_language_still_says_the_outline_cannot_read_it(db, repo):
    """The other half: a gap that adding a parser really would close."""
    from pathlib import Path

    project, commit = repo
    (Path(project.local_path) / "main.go").write_text("package main\n")
    for _ in range(churn.BUSY):
        commit("main.go")

    entry = next(
        e for e in churn.map_project(db, project)["files"] if e["path"] == "main.go"
    )
    assert entry["why_unknown"] == "the outline does not read this language yet"
