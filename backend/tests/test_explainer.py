"""Explaining a selection with the model.

The model is faked. What is worth testing is everything else: that the
measured facts reach the prompt so the answer can be grounded, that a
credential file is refused before anything is sent, that the same question
is only paid for once, and that losing the model costs the prose but not
the facts.
"""

import json
import re
import subprocess

import pytest

from app import ai, explainer, models, workspace


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def run_git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


CORE = (
    "MAX_ROWS = 100\n"
    "\n"
    "\n"
    "def fetch_rows(source):\n"
    "    # Guard: an empty source is a configuration error, not no data.\n"
    "    if source is None:\n"
    "        raise ValueError('no source')\n"
    "    return source[:MAX_ROWS]\n"
)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "demo"
    write(root / "core.py", CORE)
    write(root / "report.py", "from core import fetch_rows\n\n\ndef build():\n    return fetch_rows([])\n")
    write(root / "tests" / "test_core.py", "from core import fetch_rows\n\n\ndef test_it():\n    pass\n")
    write(root / ".env", "ANTHROPIC_API_KEY=sk-ant-real\n")
    run_git(root, "init", "--initial-branch=main")
    run_git(root, "config", "user.email", "t@e.com")
    run_git(root, "config", "user.name", "T")
    run_git(root, "add", "core.py", "report.py", "tests")
    run_git(root, "commit", "-m", "first")
    monkeypatch.setenv("PP_WORKSPACE_ROOTS", str(tmp_path))
    return root


@pytest.fixture
def project(make, repo):
    return make.project("Demo", local_path=str(repo))


ANSWER = {
    "summary": "Returns at most `MAX_ROWS` items from `source`.",
    "walkthrough": [{"lines": "6-7", "what": "Rejects a missing source outright."}],
    "role_in_the_system": "Called by `report.py`.",
    "shared_state": "Reads `MAX_ROWS`.",
    "if_you_change_it": ["`report.py` calls it with one argument."],
    "watch_out": ["A `None` source raises rather than returning empty."],
    "unknowns": [],
}


def fake_model(captured=None, answer=None):
    async def structured(**kwargs):
        if captured is not None:
            captured.update(kwargs)
        return answer if answer is not None else ANSWER

    return structured


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(ai, "is_configured", lambda: True)


# --- what the model is shown ---------------------------------------------


def test_the_prompt_carries_the_selection_and_the_measured_facts(
    db, project, repo, monkeypatch, configured
):
    captured = {}
    monkeypatch.setattr(ai, "structured", fake_model(captured))
    import asyncio

    asyncio.run(explainer.explain(db, project, "core.py", 4, 8))

    prompt = captured["prompt"]
    assert "def fetch_rows(source):" in prompt
    # The facts, so the answer can name real files rather than guess.
    assert "report.py" in prompt
    assert "tests/test_core.py" in prompt
    assert "MAX_ROWS" in prompt
    # And the line numbers, so the walkthrough can point at lines.
    assert re.search(r"^\s+6\s+if source is None:", prompt, re.M)


def test_the_prompt_says_when_a_name_could_not_be_searched(
    db, project, repo, monkeypatch, configured
):
    """The dangerous case: the model must not read "no references found" as
    "nothing depends on this" when the truth is the search was skipped."""
    write(repo / "common.py", "def read():\n    return 1\n")
    captured = {}
    monkeypatch.setattr(ai, "structured", fake_model(captured))
    import asyncio

    asyncio.run(explainer.explain(db, project, "common.py", 1, 2))
    assert "NOT SEARCHED" in captured["prompt"]
    assert "blast radius for this name is unknown" in captured["prompt"]


def test_the_system_prompt_forbids_inventing_callers(db):
    assert "Never invent a caller" in explainer.SYSTEM
    assert "Do not guess at it" in explainer.SYSTEM


def test_the_top_of_the_file_is_included_for_a_selection_further_down(
    db, project, repo, monkeypatch, configured
):
    long_file = "IMPORTANT = 1\n" + "\n".join(f"# filler {i}" for i in range(60))
    long_file += "\n\n\ndef late():\n    return IMPORTANT\n"
    write(repo / "long.py", long_file)
    captured = {}
    monkeypatch.setattr(ai, "structured", fake_model(captured))
    import asyncio

    asyncio.run(explainer.explain(db, project, "long.py", 64, 65))
    assert "the imports and module-level values" in captured["prompt"]
    assert "IMPORTANT = 1" in captured["prompt"]


# --- the credential guard ------------------------------------------------


@pytest.mark.parametrize("path", [".env", "backend/.env", "id_rsa"])
def test_a_credential_file_is_never_sent(db, project, repo, monkeypatch, path, configured):
    write(repo / path, "SECRET=1\n")

    async def explode(**_kwargs):
        raise AssertionError("A credential file reached the model.")

    monkeypatch.setattr(ai, "structured", explode)
    import asyncio

    with pytest.raises(workspace.WorkspaceError, match="never sent to the model"):
        asyncio.run(explainer.explain(db, project, path, 1, 1))


# --- paying once ---------------------------------------------------------


def test_the_same_question_is_only_asked_once(db, project, repo, monkeypatch, configured):
    calls = []

    async def counting(**kwargs):
        calls.append(kwargs)
        return ANSWER

    monkeypatch.setattr(ai, "structured", counting)
    import asyncio

    first = asyncio.run(explainer.explain(db, project, "core.py", 4, 8))
    second = asyncio.run(explainer.explain(db, project, "core.py", 4, 8))

    assert len(calls) == 1
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["explanation"] == first["explanation"]


def test_editing_the_file_invalidates_the_answer(db, project, repo, monkeypatch, configured):
    calls = []

    async def counting(**kwargs):
        calls.append(kwargs)
        return ANSWER

    monkeypatch.setattr(ai, "structured", counting)
    import asyncio

    asyncio.run(explainer.explain(db, project, "core.py", 4, 8))
    # An edit *above* the selection, which does not change a character of
    # it -- and still has to invalidate, because the explanation talks
    # about the module-level values at the top of the file.
    write(repo / "core.py", CORE.replace("MAX_ROWS = 100", "MAX_ROWS = 5"))
    result = asyncio.run(explainer.explain(db, project, "core.py", 4, 8))

    assert len(calls) == 2
    assert result["cached"] is False


def test_refresh_asks_again_on_unchanged_code(db, project, repo, monkeypatch, configured):
    calls = []

    async def counting(**kwargs):
        calls.append(kwargs)
        return ANSWER

    monkeypatch.setattr(ai, "structured", counting)
    import asyncio

    asyncio.run(explainer.explain(db, project, "core.py", 4, 8))
    asyncio.run(explainer.explain(db, project, "core.py", 4, 8, refresh=True))
    assert len(calls) == 2


def test_only_one_row_is_kept_per_selection(db, project, repo, monkeypatch, configured):
    monkeypatch.setattr(ai, "structured", fake_model())
    import asyncio

    asyncio.run(explainer.explain(db, project, "core.py", 4, 8))
    asyncio.run(explainer.explain(db, project, "core.py", 4, 8, refresh=True))
    assert db.query(models.CodeExplanation).count() == 1


# --- when the model is unavailable ---------------------------------------


def test_without_a_key_the_facts_still_come_back(db, project, repo, monkeypatch):
    monkeypatch.setattr(ai, "is_configured", lambda: False)
    monkeypatch.setattr(
        ai, "status", lambda: {"reason": "No ANTHROPIC_API_KEY is set."}
    )
    import asyncio

    result = asyncio.run(explainer.explain(db, project, "core.py", 4, 8))
    assert result["explanation"] is None
    assert result["reason"] == "No ANTHROPIC_API_KEY is set."
    assert result["facts"]["enclosing"]["name"] == "fetch_rows"


def test_the_facts_are_always_returned_alongside_the_prose(
    db, project, repo, monkeypatch, configured
):
    monkeypatch.setattr(ai, "structured", fake_model())
    import asyncio

    result = asyncio.run(explainer.explain(db, project, "core.py", 4, 8))
    assert result["explanation"]["summary"]
    assert result["facts"]["references"]["fetch_rows"]["files"] == ["report.py"]


# --- through the API ------------------------------------------------------


def test_the_route_returns_both_halves(client, project, repo, monkeypatch, configured):
    monkeypatch.setattr(ai, "structured", fake_model())
    body = client.post(
        f"/projects/{project.id}/code/explain",
        json={"path": "core.py", "start_line": 4, "end_line": 8},
    ).json()
    assert body["explanation"]["summary"].startswith("Returns at most")
    assert body["facts"]["path"] == "core.py"
    assert body["cached"] is False


def test_the_route_survives_the_model_failing(client, project, repo, monkeypatch, configured):
    async def boom(**_kwargs):
        raise ai.AIFailed("Rate limited by the API. Try again in a moment.")

    monkeypatch.setattr(ai, "structured", boom)
    body = client.post(
        f"/projects/{project.id}/code/explain",
        json={"path": "core.py", "start_line": 4, "end_line": 8},
    ).json()
    assert body["explanation"] is None
    assert "Rate limited" in body["reason"]
    assert body["facts"]["enclosing"]["name"] == "fetch_rows"


def test_the_route_refuses_a_credential_path(client, project, repo, configured):
    response = client.post(
        f"/projects/{project.id}/code/explain",
        json={"path": ".env", "start_line": 1, "end_line": 1},
    )
    assert response.status_code == 400
    assert "never sent to the model" in response.json()["detail"]


def test_the_free_route_still_works_on_its_own(client, project, repo):
    body = client.get(
        f"/projects/{project.id}/code/explain",
        params={"path": "core.py", "start_line": 4, "end_line": 8},
    ).json()
    assert body["enclosing"]["name"] == "fetch_rows"
    assert "references" in body


def test_the_explanation_is_stored_as_json_not_prose(db, project, repo, monkeypatch, configured):
    monkeypatch.setattr(ai, "structured", fake_model())
    import asyncio

    asyncio.run(explainer.explain(db, project, "core.py", 4, 8))
    [row] = db.query(models.CodeExplanation).all()
    assert json.loads(row.payload_json)["summary"]
    assert row.model


# --- when the highlight is not a definition ------------------------------
#
# The case that used to crash the panel. Two separate faults met: the
# selection was not a definition, so there was little to say, and the model
# answered the list fields with prose. The first is now described; the
# second is now reshaped.


def test_the_prompt_says_when_the_highlight_is_not_a_definition(
    db, project, repo, monkeypatch, configured
):
    captured = {}
    monkeypatch.setattr(ai, "structured", fake_model(captured))
    write(repo / "core.py", "import os\nimport sys\n\n\ndef go():\n    return os, sys\n")

    import asyncio

    asyncio.run(explainer.explain(db, project, "core.py", 1, 2))

    assert "What was highlighted" in captured["prompt"]
    assert "This is imports, not a definition" in captured["prompt"]


def test_the_facts_name_what_was_highlighted(db, project, repo):
    from app import impact

    facts = impact.explain(repo, "core.py", 1, 1)
    assert facts["selection"]["kind"] == "definition"
    assert facts["selection"]["hint"] is None


def test_a_comment_inside_a_function_still_answers_as_that_function(db, project, repo):
    """The enclosing definition is the better answer, so it wins."""
    from app import impact

    facts = impact.explain(repo, "core.py", 5, 5)
    assert facts["selection"]["kind"] == "inside_definition"
    assert facts["selection"]["hint"] is None


def test_a_comment_outside_everything_is_described_as_prose(db, project, repo):
    from app import impact

    write(repo / "notes.py", "# Why this exists at all.\n# Nobody remembers.\n")
    facts = impact.explain(repo, "notes.py", 1, 2)
    assert facts["selection"]["kind"] == "comment"
    assert "prose" in facts["selection"]["hint"]


def test_a_blank_highlight_says_there_is_nothing_in_it(db, project, repo):
    from app import impact

    facts = impact.explain(repo, "core.py", 2, 3)
    assert facts["selection"]["kind"] == "blank"
    assert "nothing in the highlight" in facts["selection"]["hint"]


@pytest.mark.parametrize(
    "sent,expected",
    [
        (["a", "b"], ["a", "b"]),
        ("one paragraph", ["one paragraph"]),
        ("- first\n- second", ["first", "second"]),
        ("1. first\n2. second", ["first", "second"]),
        ("", []),
        (None, []),
    ],
)
def test_a_list_field_answered_as_prose_becomes_a_list(sent, expected):
    assert explainer.normalise({"if_you_change_it": sent})["if_you_change_it"] == expected


def test_a_walkthrough_of_sentences_becomes_steps():
    out = explainer.normalise({"walkthrough": ["does a thing", {"lines": "1", "what": "y"}]})
    assert out["walkthrough"] == [
        {"lines": None, "what": "does a thing"},
        {"lines": "1", "what": "y"},
    ]


def test_a_text_field_answered_as_a_list_becomes_text():
    assert explainer.normalise({"summary": ["one", "two"]})["summary"] == "one\ntwo"


def test_what_is_stored_is_already_the_right_shape(
    db, project, repo, monkeypatch, configured
):
    """The cache must not hold a shape the browser cannot render."""
    import asyncio

    monkeypatch.setattr(
        ai,
        "structured",
        fake_model(answer={**ANSWER, "if_you_change_it": "just the one thing"}),
    )
    result = asyncio.run(explainer.explain(db, project, "core.py", 4, 8))
    assert result["explanation"]["if_you_change_it"] == ["just the one thing"]

    stored = db.query(models.CodeExplanation).one()
    assert json.loads(stored.payload_json)["if_you_change_it"] == ["just the one thing"]


def test_an_answer_cached_in_the_old_shape_is_reshaped_on_the_way_out(
    db, project, repo, monkeypatch, configured
):
    """Rows written before this existed still render."""
    import asyncio

    monkeypatch.setattr(ai, "structured", fake_model())
    asyncio.run(explainer.explain(db, project, "core.py", 4, 8))

    row = db.query(models.CodeExplanation).one()
    row.payload_json = json.dumps({**ANSWER, "unknowns": "one loose end"})
    db.commit()

    result = asyncio.run(explainer.explain(db, project, "core.py", 4, 8))
    assert result["cached"] is True
    assert result["explanation"]["unknowns"] == ["one loose end"]
