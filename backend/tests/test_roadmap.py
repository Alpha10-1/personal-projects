"""Proposing milestones and tasks, and refusing to propose what exists.

The model is faked. What is tested is the machinery: that the board and
the code both reach the prompt, that a proposal already on the board never
becomes a suggestion, that one already built never does either, and that
accepting one produces the right kind of row.

There are two checks because a piece of work can already exist in two
places -- written on the board, or simply built and never written down --
and both have to be looked for.
"""

import json
import subprocess

import pytest

from app import ai, models, review, roadmap


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def run_git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "demo"
    write(
        root / "app" / "main.py",
        "from fastapi import APIRouter\n\nrouter = APIRouter()\n\n\n"
        '@router.get("/widgets")\n'
        "def list_widgets():\n    return []\n",
    )
    write(root / "README.md", "# demo\n")
    run_git(root, "init", "--initial-branch=main")
    run_git(root, "config", "user.email", "t@e.com")
    run_git(root, "config", "user.name", "T")
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "first")
    monkeypatch.setenv("PP_WORKSPACE_ROOTS", str(tmp_path))
    return root


@pytest.fixture
def project(make, repo):
    p = make.project("Demo", local_path=str(repo), objective="Ship the widgets.")
    make.milestone(p, "Design agreed", status="done")
    make.task("Draw the wireframes", project_id=p.id, status="done")
    return p


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(ai, "is_configured", lambda: True)


RESULT = {
    "where_it_stands": "Design is done; nothing is shipped.",
    "milestones": [
        {
            "title": "Beta released to the first users",
            "detail": "Ten people using it daily.",
            "why": "There is no checkpoint between design and done.",
            "look_for": ["release_notes", "CHANGELOG"],
        }
    ],
    "tasks": [
        {
            "title": "Add a health check endpoint",
            "why": "Nothing says whether the service is up.",
            "look_for": ["healthz", "health_check", "/health"],
            "estimate_hours": 2,
            "value": "medium",
        }
    ],
}


def model(result=None, captured=None):
    async def structured(**kwargs):
        if captured is not None:
            captured.update(kwargs)
        return result if result is not None else RESULT

    return structured


def go(db, project):
    import asyncio

    return asyncio.run(roadmap.run(db, project))


# --- what the model is shown ---------------------------------------------


def test_the_board_reaches_the_prompt(db, project, monkeypatch, configured):
    captured = {}
    monkeypatch.setattr(ai, "structured", model(captured=captured))
    go(db, project)
    prompt = captured["prompt"]
    assert "Design agreed" in prompt
    assert "Draw the wireframes" in prompt
    assert "do not propose any of these again" in prompt


def test_the_code_reaches_the_prompt_too(db, project, monkeypatch, configured):
    captured = {}
    monkeypatch.setattr(ai, "structured", model(captured=captured))
    go(db, project)
    assert "list_widgets" in captured["prompt"]
    assert "What the code already does" in captured["prompt"]


def test_without_a_checkout_the_prompt_says_the_code_was_not_read(
    db, make, monkeypatch, configured
):
    captured = {}
    monkeypatch.setattr(ai, "structured", model(captured=captured))
    result = go(db, make.project("No folder"))
    assert "no local checkout" in captured["prompt"]
    assert result["code_was_read"] is False


def test_turned_down_proposals_are_shown_as_such(db, project, monkeypatch, configured):
    db.add(
        models.Suggestion(
            rule="roadmap_task",
            fingerprint="roadmap_task:project:%d:Add a GraphQL layer" % project.id,
            target_type="project",
            target_id=project.id,
            field="task",
            proposed_value="Add a GraphQL layer",
            rationale="no",
            status="dismissed",
        )
    )
    db.commit()
    captured = {}
    monkeypatch.setattr(ai, "structured", model(captured=captured))
    go(db, project)
    assert "turned down" in captured["prompt"]
    assert "Add a GraphQL layer" in captured["prompt"]


def test_the_system_prompt_forbids_proposing_what_exists():
    assert "Never propose what is already there" in roadmap.SYSTEM
    assert "Finished work counts as there" in roadmap.SYSTEM


# --- raising what survives ------------------------------------------------


def test_a_genuine_milestone_becomes_a_suggestion(db, project, monkeypatch, configured):
    monkeypatch.setattr(ai, "structured", model())
    result = go(db, project)
    assert [m["title"] for m in result["milestones"]] == [
        "Beta released to the first users"
    ]
    [suggestion] = db.query(models.Suggestion).filter_by(field="milestone").all()
    assert suggestion.rule == "roadmap_milestone"
    assert suggestion.target_type == "project"


def test_a_genuine_task_becomes_a_suggestion(db, project, monkeypatch, configured):
    monkeypatch.setattr(ai, "structured", model())
    go(db, project)
    [suggestion] = db.query(models.Suggestion).filter_by(field="task").all()
    assert suggestion.rule == "roadmap_task"
    assert suggestion.proposed_value == "Add a health check endpoint"


def test_the_evidence_says_what_was_checked(db, project, monkeypatch, configured):
    monkeypatch.setattr(ai, "structured", model())
    go(db, project)
    suggestion = db.query(models.Suggestion).filter_by(field="task").one()
    evidence = " ".join(json.loads(suggestion.evidence))
    assert "healthz" in evidence
    assert "none found" in evidence
    assert "already on the board by title" in evidence


def test_running_twice_does_not_double_the_list(db, project, monkeypatch, configured):
    monkeypatch.setattr(ai, "structured", model())
    go(db, project)
    go(db, project)
    assert db.query(models.Suggestion).count() == 2


def test_a_dismissed_proposal_is_not_raised_again(db, project, monkeypatch, configured):
    monkeypatch.setattr(ai, "structured", model())
    go(db, project)
    for suggestion in db.query(models.Suggestion).all():
        suggestion.status = "dismissed"
    db.commit()
    go(db, project)
    assert db.query(models.Suggestion).count() == 2


# --- refusing what already exists -----------------------------------------


def test_a_task_already_on_the_board_is_discarded(db, project, make, monkeypatch, configured):
    make.task("Add a health check", project_id=project.id)
    monkeypatch.setattr(ai, "structured", model())
    result = go(db, project)
    assert result["tasks"] == []
    dropped = [d for d in result["already_there"] if d["kind"] == "task"]
    assert "covers this" in dropped[0]["already_there_because"]
    assert db.query(models.Suggestion).filter_by(field="task").count() == 0


def test_a_finished_task_still_counts_as_already_there(
    db, project, make, monkeypatch, configured
):
    """"You should do X" about something delivered last month is the same
    failure as proposing what is already in the code."""
    make.task("Health check endpoint", project_id=project.id, status="done")
    monkeypatch.setattr(ai, "structured", model())
    result = go(db, project)
    assert result["tasks"] == []
    assert "already done" in result["already_there"][0]["already_there_because"]


def test_a_milestone_already_on_the_board_is_discarded(
    db, project, make, monkeypatch, configured
):
    make.milestone(project, "Beta released")
    monkeypatch.setattr(ai, "structured", model())
    result = go(db, project)
    assert result["milestones"] == []
    assert db.query(models.Suggestion).filter_by(field="milestone").count() == 0


def test_a_task_already_built_in_the_code_is_discarded(
    db, project, repo, monkeypatch, configured
):
    """The second check. Nothing on the board mentions it, but the code
    has it -- which is just as much a reason not to propose it."""
    monkeypatch.setattr(
        ai,
        "structured",
        model(
            {
                **RESULT,
                "tasks": [
                    {
                        "title": "Expose the widgets over HTTP",
                        "why": "There is no way to read them.",
                        "look_for": ["list_widgets"],
                    }
                ],
            }
        ),
    )
    result = go(db, project)
    assert result["tasks"] == []
    assert "list_widgets" in result["already_there"][0]["already_there_because"]


def test_a_milestone_is_not_blocked_by_a_task_of_the_same_name(
    db, project, make, monkeypatch, configured
):
    """A checkpoint and the work towards it are different things, and one
    should not suppress the other."""
    make.task("Beta released to the first users", project_id=project.id)
    monkeypatch.setattr(ai, "structured", model())
    result = go(db, project)
    assert len(result["milestones"]) == 1


def test_everything_discarded_comes_back_rather_than_vanishing(
    db, project, make, monkeypatch, configured
):
    make.task("Add a health check", project_id=project.id)
    make.milestone(project, "Beta released")
    monkeypatch.setattr(ai, "structured", model())
    result = go(db, project)
    assert {d["kind"] for d in result["already_there"]} == {"task", "milestone"}
    assert all(d["already_there_because"] for d in result["already_there"])


def test_without_a_checkout_only_the_board_check_runs(db, make, monkeypatch, configured):
    project = make.project("No folder")
    monkeypatch.setattr(ai, "structured", model())
    result = go(db, project)
    assert len(result["tasks"]) == 1
    assert result["tasks"][0]["verified"] is False
    suggestion = db.query(models.Suggestion).filter_by(field="task").one()
    assert "Not verified against the code" in " ".join(json.loads(suggestion.evidence))


# --- the model not honouring its own schema ------------------------------


def test_bare_strings_are_coerced_rather_than_crashing(
    db, project, monkeypatch, configured
):
    monkeypatch.setattr(
        ai,
        "structured",
        model(
            {
                "where_it_stands": "Early.",
                "milestones": ["First release"],
                "tasks": ["Add a changelog", None, 12, {"why": "no title"}],
            }
        ),
    )
    result = go(db, project)
    assert [m["title"] for m in result["milestones"]] == ["First release"]
    assert [t["title"] for t in result["tasks"]] == ["Add a changelog"]


def test_an_empty_answer_is_legitimate(db, project, monkeypatch, configured):
    monkeypatch.setattr(
        ai,
        "structured",
        model({"where_it_stands": "In good shape.", "milestones": [], "tasks": []}),
    )
    result = go(db, project)
    assert result["milestones"] == []
    assert result["tasks"] == []
    assert db.query(models.Suggestion).count() == 0


# --- accepting one --------------------------------------------------------


def test_accepting_a_milestone_creates_a_milestone_at_the_end(
    db, project, monkeypatch, configured
):
    monkeypatch.setattr(ai, "structured", model())
    go(db, project)
    suggestion = db.query(models.Suggestion).filter_by(field="milestone").one()

    review.apply(db, suggestion)
    db.commit()

    milestones = db.query(models.Milestone).filter_by(project_id=project.id).all()
    added = [m for m in milestones if m.title == "Beta released to the first users"]
    assert len(added) == 1
    assert added[0].status == "pending"
    assert added[0].due_date is None
    assert added[0].position > min(m.position for m in milestones)


def test_accepting_a_task_creates_a_task(db, project, monkeypatch, configured):
    monkeypatch.setattr(ai, "structured", model())
    go(db, project)
    suggestion = db.query(models.Suggestion).filter_by(field="task").one()
    review.apply(db, suggestion)
    db.commit()
    task = (
        db.query(models.Task)
        .filter_by(project_id=project.id, title="Add a health check endpoint")
        .one()
    )
    assert task.status == "todo"
    assert task.source == "agent"


# --- when the model is unavailable ---------------------------------------


def test_without_a_key_the_board_still_comes_back(db, project, monkeypatch):
    monkeypatch.setattr(ai, "is_configured", lambda: False)
    monkeypatch.setattr(ai, "status", lambda: {"reason": "No ANTHROPIC_API_KEY is set."})
    result = go(db, project)
    assert result["reason"] == "No ANTHROPIC_API_KEY is set."
    assert result["board"]["totals"]["milestones"] == 1
    assert db.query(models.Suggestion).count() == 0


# --- through the API ------------------------------------------------------


def test_the_route_returns_both_the_kept_and_the_discarded(
    client, project, make, monkeypatch, configured
):
    make.task("Add a health check", project_id=project.id)
    monkeypatch.setattr(ai, "structured", model())
    body = client.post(f"/projects/{project.id}/roadmap").json()
    assert [m["title"] for m in body["milestones"]] == [
        "Beta released to the first users"
    ]
    assert body["tasks"] == []
    assert body["already_there"][0]["kind"] == "task"
