"""Surveying a repository: the outline into notes, the gaps into suggestions.

The model is faked. What matters here is the machinery around it -- that
the inventory reaches the prompt, that a gap already built never becomes a
suggestion, that one already on the board never becomes a second one, and
that accepting a gap produces a task rather than silently editing code.
"""

import json
import subprocess

import pytest

from app import ai, models, review, survey


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
    return make.project("Demo", local_path=str(repo))


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(ai, "is_configured", lambda: True)


RESULT = {
    "what_it_is": "A small HTTP service that lists widgets.",
    "features": [
        {
            "name": "Widget listing",
            "what_it_does": "Serves the widgets over HTTP.",
            "where": ["app/main.py"],
            "state": "complete",
        }
    ],
    "gaps": [
        {
            "title": "Add rate limiting to the API",
            "why": "Nothing caps how often a caller can hit it.",
            "look_for": ["rate_limit", "slowapi", "throttle"],
            "area": "app",
            "size": "small",
            "value": "high",
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

    return asyncio.run(survey.run(db, project))


# --- what the model is shown ---------------------------------------------


def test_the_inventory_reaches_the_prompt(db, project, repo, monkeypatch, configured):
    captured = {}
    monkeypatch.setattr(ai, "structured", model(captured=captured))
    go(db, project)
    prompt = captured["prompt"]
    assert "GET /widgets" in prompt
    assert "list_widgets" in prompt


def test_the_board_is_shown_so_it_is_not_proposed_again(
    db, project, repo, make, monkeypatch, configured
):
    make.task("Add rate limiting to the API", project_id=project.id)
    captured = {}
    monkeypatch.setattr(ai, "structured", model(captured=captured))
    go(db, project)
    assert "do not propose any of these" in captured["prompt"]
    assert "Add rate limiting to the API" in captured["prompt"]


def test_previously_dismissed_gaps_are_shown_as_turned_down(
    db, project, repo, monkeypatch, configured
):
    db.add(
        models.Suggestion(
            rule="inventory_gap",
            fingerprint="inventory_gap:project:%d:Add a GraphQL layer" % project.id,
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


def test_the_system_prompt_forbids_proposing_what_exists(db):
    assert "Never propose something the inventory shows already exists" in survey.SYSTEM


# --- the outline into notes ----------------------------------------------


def test_the_outline_is_written_to_the_project_notes(db, project, repo, monkeypatch, configured):
    monkeypatch.setattr(ai, "structured", model())
    go(db, project)
    [note] = db.query(models.Note).filter_by(project_id=project.id).all()
    assert note.title == survey.NOTE_TITLE
    assert "A small HTTP service" in note.body
    assert "**Widget listing**" in note.body
    assert note.source == "agent"


def test_running_it_twice_updates_the_note_rather_than_adding_one(
    db, project, repo, monkeypatch, configured
):
    monkeypatch.setattr(ai, "structured", model())
    go(db, project)
    monkeypatch.setattr(
        ai,
        "structured",
        model({**RESULT, "what_it_is": "Actually it does rather more than that."}),
    )
    go(db, project)
    notes = db.query(models.Note).filter_by(project_id=project.id).all()
    assert len(notes) == 1
    assert "rather more" in notes[0].body


def test_a_partial_feature_is_marked_as_such(db, project, repo, monkeypatch, configured):
    partial = {
        **RESULT,
        "features": [
            {"name": "Search", "what_it_does": "Finds widgets.", "state": "partial"}
        ],
    }
    monkeypatch.setattr(ai, "structured", model(partial))
    go(db, project)
    [note] = db.query(models.Note).filter_by(project_id=project.id).all()
    assert "*(partial)*" in note.body


# --- the gaps into suggestions -------------------------------------------


def test_a_genuine_gap_becomes_a_suggestion(db, project, repo, monkeypatch, configured):
    monkeypatch.setattr(ai, "structured", model())
    result = go(db, project)
    assert [g["title"] for g in result["gaps"]] == ["Add rate limiting to the API"]
    [suggestion] = db.query(models.Suggestion).all()
    assert suggestion.rule == "inventory_gap"
    assert suggestion.field == "task"
    assert suggestion.proposed_value == "Add rate limiting to the API"


def test_the_evidence_records_what_was_checked(db, project, repo, monkeypatch, configured):
    monkeypatch.setattr(ai, "structured", model())
    go(db, project)
    [suggestion] = db.query(models.Suggestion).all()
    evidence = " ".join(json.loads(suggestion.evidence))
    assert "rate_limit" in evidence
    assert "none found" in evidence


def test_a_gap_that_is_already_built_never_becomes_a_suggestion(
    db, project, repo, monkeypatch, configured
):
    """The requirement. The model proposes something that exists; the check
    catches it before anyone is shown it."""
    monkeypatch.setattr(
        ai,
        "structured",
        model(
            {
                **RESULT,
                "gaps": [
                    {
                        "title": "Add an endpoint to list widgets",
                        "why": "There is no way to read them.",
                        "look_for": ["list_widgets", "/widgets"],
                    }
                ],
            }
        ),
    )
    result = go(db, project)
    assert result["gaps"] == []
    assert db.query(models.Suggestion).count() == 0
    assert len(result["already_done"]) == 1
    assert "list_widgets" in result["already_done"][0]["already_done_because"]


def test_a_gap_already_on_the_board_is_dropped_too(
    db, project, repo, make, monkeypatch, configured
):
    """The repository cannot see the board, so this is checked separately."""
    make.task("Rate limit the API", project_id=project.id)
    monkeypatch.setattr(ai, "structured", model())
    result = go(db, project)
    assert result["gaps"] == []
    assert "covers this" in result["already_done"][0]["already_done_because"]


def test_a_gap_already_done_on_the_board_is_dropped(
    db, project, repo, make, monkeypatch, configured
):
    make.task("Rate limit the API", project_id=project.id, status="done")
    monkeypatch.setattr(ai, "structured", model())
    result = go(db, project)
    assert result["gaps"] == []
    assert "already done" in result["already_done"][0]["already_done_because"]


def test_an_unrelated_task_does_not_suppress_a_gap(
    db, project, repo, make, monkeypatch, configured
):
    make.task("Write the deployment guide", project_id=project.id)
    monkeypatch.setattr(ai, "structured", model())
    assert len(go(db, project)["gaps"]) == 1


def test_running_twice_does_not_duplicate_the_suggestion(
    db, project, repo, monkeypatch, configured
):
    monkeypatch.setattr(ai, "structured", model())
    go(db, project)
    go(db, project)
    assert db.query(models.Suggestion).count() == 1


def test_a_dismissed_gap_is_not_raised_again(db, project, repo, monkeypatch, configured):
    monkeypatch.setattr(ai, "structured", model())
    go(db, project)
    suggestion = db.query(models.Suggestion).one()
    suggestion.status = "dismissed"
    db.commit()
    go(db, project)
    assert db.query(models.Suggestion).count() == 1


# --- accepting one --------------------------------------------------------


def test_accepting_a_gap_creates_a_task_with_the_reasoning(
    db, project, repo, monkeypatch, configured
):
    monkeypatch.setattr(ai, "structured", model())
    go(db, project)
    suggestion = db.query(models.Suggestion).one()

    review.apply(db, suggestion)
    db.commit()

    [task] = db.query(models.Task).filter_by(project_id=project.id).all()
    assert task.title == "Add rate limiting to the API"
    assert task.notes == "Nothing caps how often a caller can hit it."
    assert task.status == "todo"
    assert task.source == "agent"


def test_accepting_one_writes_no_code(db, project, repo, monkeypatch, configured):
    before = (repo / "app" / "main.py").read_bytes()
    monkeypatch.setattr(ai, "structured", model())
    go(db, project)
    review.apply(db, db.query(models.Suggestion).one())
    db.commit()
    assert (repo / "app" / "main.py").read_bytes() == before


# --- when the model is unavailable ---------------------------------------


def test_without_a_key_the_inventory_still_comes_back(db, project, repo, monkeypatch):
    monkeypatch.setattr(ai, "is_configured", lambda: False)
    monkeypatch.setattr(ai, "status", lambda: {"reason": "No ANTHROPIC_API_KEY is set."})
    result = go(db, project)
    assert result["outline"] is None
    assert result["reason"] == "No ANTHROPIC_API_KEY is set."
    assert result["inventory"]["counts"]["routes"] == 1
    assert db.query(models.Suggestion).count() == 0


# --- through the API ------------------------------------------------------


def test_the_inventory_route_needs_no_model(client, project, repo):
    body = client.get(f"/projects/{project.id}/inventory").json()
    assert body["counts"]["routes"] == 1
    assert body["routes"][0]["path"] == "/widgets"


def test_the_inventory_route_explains_a_missing_folder(client, make):
    project = make.project("No folder")
    response = client.get(f"/projects/{project.id}/inventory")
    assert response.status_code == 400
    assert "no local folder" in response.json()["detail"]


def test_the_survey_route_returns_both_the_kept_and_the_dropped(
    client, project, repo, monkeypatch, configured
):
    monkeypatch.setattr(
        ai,
        "structured",
        model(
            {
                **RESULT,
                "gaps": RESULT["gaps"]
                + [
                    {
                        "title": "Add an endpoint to list widgets",
                        "why": "x",
                        "look_for": ["list_widgets"],
                    }
                ],
            }
        ),
    )
    body = client.post(f"/projects/{project.id}/survey").json()
    assert [g["title"] for g in body["gaps"]] == ["Add rate limiting to the API"]
    assert [g["title"] for g in body["already_done"]] == [
        "Add an endpoint to list widgets"
    ]


# --- the model not honouring its own schema ------------------------------
#
# A tool schema's top-level `required` is enforced; `required` inside an
# array's items is not. The first real run of this returned `features` as a
# list of strings and took the whole request down with an AttributeError.


def test_a_feature_returned_as_a_bare_string_is_still_usable():
    assert survey.as_feature("Time tracking") == {
        "name": "Time tracking",
        "what_it_does": "Time tracking",
        "where": [],
        "state": "complete",
    }


def test_a_gap_returned_as_a_bare_string_is_kept_but_unverifiable():
    gap = survey.as_gap("Add caching")
    assert gap["title"] == "Add caching"
    assert gap["look_for"] == []


def test_nonsense_entries_are_dropped_rather_than_crashing():
    assert survey.as_feature(None) is None
    assert survey.as_feature({}) is None
    assert survey.as_gap({"why": "no title"}) is None
    assert survey.as_gap(12) is None


def test_an_unknown_state_falls_back_rather_than_reaching_the_renderer():
    assert survey.as_feature({"name": "X", "state": "half-done"})["state"] == "complete"


def test_a_survey_of_bare_strings_gets_all_the_way_through(
    db, project, repo, monkeypatch, configured
):
    """The regression, end to end."""
    monkeypatch.setattr(
        ai,
        "structured",
        model(
            {
                "what_it_is": "A service.",
                "features": ["Widget listing", "Health checks"],
                "gaps": ["Add rate limiting"],
            }
        ),
    )
    result = go(db, project)
    assert len(result["outline"]["features"]) == 2
    [note] = db.query(models.Note).filter_by(project_id=project.id).all()
    assert "Widget listing" in note.body
    # Unverifiable, so it is raised but marked as such rather than claimed
    # to have been checked.
    [suggestion] = db.query(models.Suggestion).all()
    assert "Not verified" in " ".join(json.loads(suggestion.evidence))
