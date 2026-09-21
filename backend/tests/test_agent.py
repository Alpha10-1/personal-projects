"""The coding agent.

The model is faked throughout -- a real one would cost money and give a
different answer every run, and neither is what these are testing. What is
tested is everything around it: that the overlay keeps the disk untouched
until someone applies, that a protected path forces review whatever the
request asked for, that a path outside the repository is refused however it
is spelled, and that a run always reaches a terminal status.
"""

import asyncio
import json
import subprocess
from types import SimpleNamespace

import pytest

from app import agent, ai, workspace


def write(path, text):
    path.write_bytes(text.encode("utf-8"))


def run_git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "demo"
    (root / "src").mkdir(parents=True)
    write(root / "README.md", "# demo\n")
    write(root / "src" / "main.py", "def greet():\n    return 'hello'\n")
    write(root / "models.py", "CORE = 1\n")
    run_git(root, "init", "--initial-branch=main")
    run_git(root, "config", "user.email", "t@example.com")
    run_git(root, "config", "user.name", "T")
    run_git(root, "add", ".")
    run_git(root, "commit", "-m", "first")
    monkeypatch.setenv("PP_WORKSPACE_ROOTS", str(tmp_path))
    return root


@pytest.fixture
def overlay(repo):
    return agent.Overlay(root=repo)


# --- a fake model ---------------------------------------------------------


def block(**kw):
    return SimpleNamespace(**kw)


def tool_call(name, args, call_id="c1"):
    return block(type="tool_use", name=name, input=args, id=call_id)


def reply(*blocks):
    return SimpleNamespace(content=list(blocks), usage=None)


def scripted(*turns):
    """A stand-in for `ai.converse` that replays fixed turns.

    Each element is what the model "says" next. Running out is a test bug,
    so it raises rather than looping.
    """
    queue = list(turns)

    async def fake(**_kwargs):
        if not queue:
            raise AssertionError("The agent asked for more turns than scripted.")
        return queue.pop(0)

    return fake


# --- the overlay ----------------------------------------------------------


def test_a_write_does_not_touch_the_disk(overlay, repo):
    overlay.write("src/main.py", "changed\n")
    assert (repo / "src" / "main.py").read_bytes() == b"def greet():\n    return 'hello'\n"
    assert overlay.read("src/main.py") == "changed\n"


def test_reads_see_earlier_writes_in_the_same_run(overlay):
    overlay.write("new.py", "a = 1\n")
    assert overlay.read("new.py") == "a = 1\n"


def test_rewriting_a_file_to_what_it_already_was_is_not_a_change(overlay):
    overlay.write("README.md", "# demo\n")
    assert overlay.changes() == []


def test_changes_classify_create_modify_and_delete(overlay):
    overlay.write("README.md", "# other\n")
    overlay.write("brand/new.py", "x = 1\n")
    overlay.delete("src/main.py")
    actions = {c["path"]: c["action"] for c in overlay.changes()}
    assert actions == {
        "README.md": "modify",
        "brand/new.py": "create",
        "src/main.py": "delete",
    }


def test_the_diff_reads_as_a_diff(overlay):
    overlay.write("README.md", "# changed\n")
    text = overlay.diff()
    assert "a/README.md" in text
    assert "-# demo" in text
    assert "+# changed" in text


@pytest.mark.parametrize(
    "path", ["../escape.py", "src/../../escape.py", ".git/config", "backend/.env"]
)
def test_writes_outside_or_into_forbidden_places_are_refused(overlay, path):
    with pytest.raises((agent.AgentError, workspace.WorkspaceError)):
        overlay.write(path, "nope")


def test_reading_too_much_stops_the_run(overlay, repo, monkeypatch):
    monkeypatch.setattr(agent, "MAX_READ_BYTES", 10)
    write(repo / "big.py", "x" * 100)
    with pytest.raises(agent.AgentError, match="as much of the repository"):
        overlay.read("big.py")


# --- protected paths ------------------------------------------------------


def test_the_built_in_list_applies_when_a_project_names_none():
    assert agent.protected_patterns(None) == list(agent.DEFAULT_PROTECTED)
    assert agent.protected_patterns("   \n  ") == list(agent.DEFAULT_PROTECTED)


def test_a_project_can_name_its_own():
    assert agent.protected_patterns("src/pricing.py\n# nope") == [
        "src/pricing.py",
        "# nope",
    ]


@pytest.mark.parametrize(
    "path,expected",
    [
        ("models.py", True),
        ("backend/app/models.py", True),
        ("package-lock.json", True),
        (".github/workflows/ci.yml", True),
        ("src/main.py", False),
        ("README.md", False),
    ],
)
def test_the_default_protected_globs_match_what_they_should(path, expected):
    assert agent.matches(path, agent.DEFAULT_PROTECTED) is expected


# --- the tool layer -------------------------------------------------------


def test_edit_file_replaces_an_exact_string(overlay):
    out = agent.run_tool(
        overlay, "edit_file", {"path": "src/main.py", "old": "'hello'", "new": "'hi'"}
    )
    assert out == "Edited src/main.py."
    assert "'hi'" in overlay.read("src/main.py")


def test_edit_file_says_so_when_the_string_is_missing(overlay):
    out = agent.run_tool(
        overlay, "edit_file", {"path": "src/main.py", "old": "nowhere", "new": "x"}
    )
    assert "not in the file" in out
    assert overlay.changes() == []


def test_edit_file_refuses_an_ambiguous_match(overlay):
    overlay.write("dup.py", "a = 1\na = 1\n")
    out = agent.run_tool(overlay, "edit_file", {"path": "dup.py", "old": "a = 1", "new": "b"})
    assert "appears 2 times" in out


def test_read_file_is_numbered(overlay):
    out = agent.run_tool(overlay, "read_file", {"path": "README.md"})
    assert out.strip().startswith("1  # demo")


def test_a_tool_error_comes_back_as_text_not_an_exception(overlay):
    out = agent.run_tool(overlay, "read_file", {"path": "../outside"})
    assert out.startswith("Error:")


def test_an_unknown_tool_is_reported(overlay):
    assert "unknown tool" in agent.run_tool(overlay, "rm_rf", {})


# --- whole runs -----------------------------------------------------------


def project_row(make, repo, **kw):
    return make.project("Demo", local_path=str(repo), **kw)


def test_a_run_reads_then_edits_then_finishes(make, repo, monkeypatch):
    monkeypatch.setattr(
        ai,
        "converse",
        scripted(
            reply(tool_call("read_file", {"path": "src/main.py"})),
            reply(
                tool_call(
                    "edit_file", {"path": "src/main.py", "old": "'hello'", "new": "'hi'"}
                )
            ),
            reply(tool_call("finish", {"summary": "Changed the greeting. Untested."})),
        ),
    )
    result = asyncio.run(agent.execute(project_row(make, repo), "Say hi instead."))
    assert result["error"] is None
    assert result["summary"].startswith("Changed the greeting")
    assert [c["path"] for c in result["changes"]] == ["src/main.py"]
    assert result["review_required"] is False
    assert result["turns"] == 3
    # And still nothing on disk.
    assert b"hello" in (repo / "src" / "main.py").read_bytes()


def test_touching_a_protected_file_forces_review(make, repo, monkeypatch):
    monkeypatch.setattr(
        ai,
        "converse",
        scripted(
            reply(tool_call("write_file", {"path": "models.py", "content": "CORE = 2\n"})),
            reply(tool_call("finish", {"summary": "Bumped CORE."})),
        ),
    )
    result = asyncio.run(agent.execute(project_row(make, repo), "Bump the core value."))
    assert result["review_required"] is True
    assert "models.py" in result["review_reason"]


def test_a_project_can_protect_something_of_its_own(make, repo, monkeypatch):
    monkeypatch.setattr(
        ai,
        "converse",
        scripted(
            reply(tool_call("write_file", {"path": "README.md", "content": "# new\n"})),
            reply(tool_call("finish", {"summary": "Rewrote the readme."})),
        ),
    )
    project = project_row(make, repo, protected_paths="README.md")
    result = asyncio.run(agent.execute(project, "Rewrite the readme."))
    assert result["review_required"] is True


def test_running_out_of_turns_says_the_result_is_incomplete(make, repo, monkeypatch):
    monkeypatch.setattr(
        ai,
        "converse",
        scripted(*[reply(tool_call("list_files", {"path": ""})) for _ in range(3)]),
    )
    result = asyncio.run(
        agent.execute(project_row(make, repo), "Do something.", max_turns=3)
    )
    assert "used all 3 turns" in result["error"]
    assert result["turns"] == 3


def test_a_model_failure_is_reported_not_raised(make, repo, monkeypatch):
    async def boom(**_kw):
        raise ai.AIFailed("Rate limited by the API. Try again in a moment.")

    monkeypatch.setattr(ai, "converse", boom)
    result = asyncio.run(agent.execute(project_row(make, repo), "Do something."))
    assert "Rate limited" in result["error"]
    assert result["changes"] == []


def test_a_run_needs_a_local_folder(make, monkeypatch):
    project = make.project("No folder")
    with pytest.raises(workspace.WorkspaceError):
        asyncio.run(agent.execute(project, "Do something."))


# --- applying -------------------------------------------------------------


def test_apply_writes_the_files(repo):
    changes = json.dumps(
        [
            {"path": "src/main.py", "action": "modify", "content": "new\n"},
            {"path": "deep/nested/new.py", "action": "create", "content": "x = 1\n"},
        ]
    )
    written = agent.apply(repo, changes)
    assert sorted(written) == ["deep/nested/new.py", "src/main.py"]
    assert (repo / "src" / "main.py").read_bytes() == b"new\n"
    assert (repo / "deep" / "nested" / "new.py").exists()


def test_apply_preserves_line_endings_exactly(repo):
    agent.apply(
        repo, json.dumps([{"path": "crlf.txt", "action": "create", "content": "a\r\nb\r\n"}])
    )
    assert (repo / "crlf.txt").read_bytes() == b"a\r\nb\r\n"


def test_apply_deletes(repo):
    agent.apply(repo, json.dumps([{"path": "README.md", "action": "delete"}]))
    assert not (repo / "README.md").exists()


def test_apply_refuses_the_whole_batch_if_one_path_is_bad(repo):
    before = (repo / "README.md").read_bytes()
    changes = json.dumps(
        [
            {"path": "README.md", "action": "modify", "content": "changed\n"},
            {"path": "../escape.py", "action": "create", "content": "x"},
        ]
    )
    with pytest.raises((agent.AgentError, workspace.WorkspaceError)):
        agent.apply(repo, changes)
    assert (repo / "README.md").read_bytes() == before


def test_apply_refuses_a_never_write_path(repo):
    with pytest.raises(agent.AgentError, match="Refusing to write"):
        agent.apply(repo, json.dumps([{"path": ".env", "action": "create", "content": "K=1"}]))


# --- credentials never reach the model -----------------------------------
#
# The write list stops the agent damaging a secret. This list stops it
# reading one, which is the failure that cannot be undone: by the time a
# diff is reviewed, anything read has already been sent.


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "backend/.env",
        "functions/.env",
        "serviceAccountKey.json",
        "config/serviceAccountKey.json",
        "certs/server.key",
        "certs/server.pem",
        ".npmrc",
        "home/.netrc",
        "id_rsa",
    ],
)
def test_the_agent_cannot_read_a_credential_file(overlay, repo, path):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    write(target, "SECRET=hunter2\n")
    with pytest.raises(agent.AgentError, match="never readable"):
        overlay.read(path)


def test_the_refusal_says_nothing_was_sent(overlay, repo):
    write(repo / ".env", "ANTHROPIC_API_KEY=sk-ant-real\n")
    out = agent.run_tool(overlay, "read_file", {"path": ".env"})
    assert "sk-ant-real" not in out
    assert "Nothing in it has been sent anywhere" in out


def test_search_does_not_return_lines_from_credential_files(overlay, repo):
    write(repo / "serviceAccountKey.json", '{"private_key": "-----BEGIN PRIVATE KEY-----"}\n')
    write(repo / "src" / "uses.py", "KEY = load('serviceAccountKey.json')\n")
    run_git(repo, "add", "-A")
    run_git(repo, "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-m", "add")
    out = agent.run_tool(overlay, "search", {"pattern": "PRIVATE KEY"})
    assert "BEGIN PRIVATE KEY" not in out
    assert out == "No matches."


def test_search_still_returns_ordinary_matches(overlay, repo):
    out = agent.run_tool(overlay, "search", {"pattern": "greet"})
    assert "src/main.py" in out


def test_everything_unreadable_is_also_unwritable():
    """The read list is the stricter one, so it must be a subset of the other."""
    assert set(agent.NEVER_READ) <= set(agent.NEVER_WRITE)


# --- CRLF ----------------------------------------------------------------
#
# The agent found this one itself on its first real run: every multi-line
# edit_file failed as "not in the file" while single-line ones worked, and
# the one edit that landed wrote an LF line into a CRLF file.


@pytest.fixture
def crlf_repo(repo):
    write(repo / "win.py", "def greet():\r\n    return 'hello'\r\n")
    run_git(repo, "add", "-A")
    run_git(repo, "-c", "user.email=t@e.com", "-c", "user.name=T", "commit", "-m", "crlf")
    return repo


def test_a_multi_line_edit_matches_in_a_crlf_file(crlf_repo):
    overlay = agent.Overlay(root=crlf_repo)
    out = agent.run_tool(
        overlay,
        "edit_file",
        {
            "path": "win.py",
            # As the model would send it, having read the numbered listing.
            "old": "def greet():\n    return 'hello'",
            "new": "def greet():\n    return 'hi'",
        },
    )
    assert out == "Edited win.py."


def test_the_edit_keeps_the_file_on_crlf_throughout(crlf_repo):
    overlay = agent.Overlay(root=crlf_repo)
    agent.run_tool(
        overlay,
        "edit_file",
        {"path": "win.py", "old": "    return 'hello'", "new": "    return 'hi'\n    # added"},
    )
    result = overlay.read("win.py")
    assert "\r\n" in result
    # No bare LF anywhere: a single mixed line is what produced the spurious
    # whole-line diff that gave this away.
    assert result.replace("\r\n", "") .count("\n") == 0


def test_an_lf_file_is_left_alone(overlay):
    out = agent.run_tool(
        overlay,
        "edit_file",
        {"path": "src/main.py", "old": "def greet():\n    return 'hello'", "new": "def greet():\n    return 'hi'"},
    )
    assert out == "Edited src/main.py."
    assert "\r" not in overlay.read("src/main.py")


def test_match_endings_does_not_double_up_on_a_fragment_already_crlf():
    assert agent.match_endings("a\r\nb", "x\r\ny") == "x\r\ny"


def test_match_endings_leaves_an_lf_file_untouched():
    assert agent.match_endings("a\nb", "x\ny") == "x\ny"


# --- prompt caching -------------------------------------------------------


def _user_turns(messages):
    return [m for m in messages if m["role"] == "user"]


def test_the_breakpoints_sit_on_the_newest_turns():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "one"}]},
        {"role": "assistant", "content": "opaque sdk object"},
        {"role": "user", "content": [{"type": "tool_result", "content": "two"}]},
        {"role": "user", "content": [{"type": "tool_result", "content": "three"}]},
    ]
    agent.mark_cache(messages)
    marked = [
        m["content"][-1]["content"]
        for m in _user_turns(messages)
        if "cache_control" in m["content"][-1]
    ]
    assert marked == ["two", "three"]


def test_the_breakpoints_move_rather_than_accumulate():
    messages = [
        {"role": "user", "content": [{"type": "text", "text": f"turn {i}"}]}
        for i in range(5)
    ]
    agent.mark_cache(messages)
    agent.mark_cache(messages)  # a second turn of the loop
    total = sum(
        1 for m in messages if "cache_control" in m["content"][-1]
    )
    assert total == agent.CACHE_POINTS


def test_marking_leaves_assistant_turns_untouched():
    """Those are the SDK's own objects, compared by the API against what it
    sent. Anything added to them is a difference."""
    sdk_turn = {"role": "assistant", "content": "opaque"}
    messages = [{"role": "user", "content": [{"type": "text", "text": "x"}]}, sdk_turn]
    agent.mark_cache(messages)
    assert sdk_turn == {"role": "assistant", "content": "opaque"}


def test_a_run_marks_the_opening_brief_for_caching(make, repo, monkeypatch):
    sent = {}

    async def capture(**kwargs):
        sent["messages"] = kwargs["messages"]
        return reply(tool_call("finish", {"summary": "Nothing needed."}))

    monkeypatch.setattr(ai, "converse", capture)
    asyncio.run(agent.execute(project_row(make, repo), "Have a look."))
    assert sent["messages"][0]["content"][-1]["cache_control"] == {"type": "ephemeral"}
