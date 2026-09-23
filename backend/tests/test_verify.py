"""The agent running the project's test command.

This is the only thing in the system that executes anything, so most of
what is tested here is the boundary rather than the happy path: that the
model cannot choose the command, that a shell line stays argument text,
that a dirty working copy refuses rather than being written over, and --
the one that matters most -- that the checkout is exactly as it was
afterwards, including when the command fails or hangs.
"""

import subprocess
import sys

import pytest

from app import agent, verify


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def run_git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "demo"
    write(root / "core.py", "VALUE = 1\n")
    write(root / "check.py", "import core\nassert core.VALUE == 2, 'core.VALUE is wrong'\n")
    run_git(root, "init", "--initial-branch=main")
    run_git(root, "config", "user.email", "t@e.com")
    run_git(root, "config", "user.name", "T")
    run_git(root, "add", ".")
    run_git(root, "commit", "-m", "first")
    monkeypatch.setenv("PP_WORKSPACE_ROOTS", str(tmp_path))
    return root


@pytest.fixture
def project(make, repo):
    return make.project(
        "Demo",
        local_path=str(repo),
        test_command=f'"{sys.executable}" check.py',
    )


@pytest.fixture
def overlay(repo):
    return agent.Overlay(root=repo)


# --- it actually runs -----------------------------------------------------


def test_a_failing_suite_comes_back_as_failing(project, overlay):
    result = verify.run(project, overlay)
    assert result.ran is True
    assert result.passed is False
    assert "core.VALUE is wrong" in result.output


def test_the_agents_edit_is_what_gets_tested(project, overlay):
    """The whole point: the overlay, not what is on disk."""
    overlay.write("core.py", "VALUE = 2\n")
    result = verify.run(project, overlay)
    assert result.passed is True


def test_the_model_is_told_the_verdict_in_words(project, overlay):
    overlay.write("core.py", "VALUE = 2\n")
    assert "passed" in verify.run(project, overlay).as_text()


def test_a_missing_program_is_reported_not_raised(make, repo, overlay):
    project = make.project("Demo", local_path=str(repo), test_command="definitely-not-a-program")
    result = verify.run(project, overlay)
    assert result.ran is False
    assert "not found" in result.reason


def test_a_relative_program_resolves_against_the_test_directory(make, repo, overlay):
    """The bug that made this silently useless the first time it ran for real.

    A relative `argv[0]` is resolved against the calling process's working
    directory, not the `cwd=` given to subprocess, so the most natural thing
    to type -- a path to the project's own interpreter -- was always "not
    found".
    """
    import shutil

    tools = repo / "tools"
    tools.mkdir()
    local = tools / ("py.exe" if sys.platform == "win32" else "py")
    shutil.copy(sys.executable, local)
    run_git(repo, "add", "tools")
    run_git(repo, "commit", "-m", "tools")

    project = make.project(
        "Demo",
        local_path=str(repo),
        test_command="tools/py.exe -c pass" if sys.platform == "win32" else "tools/py -c pass",
    )
    result = verify.run(project, overlay)
    # It was found and executed, which is the whole claim. A copied
    # interpreter has no pyvenv.cfg beside it and exits non-zero; what
    # matters is that it ran at all rather than coming back "not found".
    assert result.ran is True, result.reason
    assert result.exit_code is not None


def test_a_bare_program_name_is_left_for_the_path(make, repo, overlay):
    """`npm test` must still find npm on PATH."""
    project = make.project("Demo", local_path=str(repo), test_command="definitely-not-real")
    assert "not found" in verify.run(project, overlay).reason


def test_no_command_means_no_run(make, repo, overlay):
    project = make.project("Demo", local_path=str(repo))
    result = verify.run(project, overlay)
    assert result.ran is False
    assert "no test command" in result.reason


# --- the checkout is put back ---------------------------------------------


def test_the_working_copy_is_unchanged_afterwards(project, overlay, repo):
    overlay.write("core.py", "VALUE = 2\n")
    verify.run(project, overlay)
    assert (repo / "core.py").read_bytes() == b"VALUE = 1\n"


def test_a_file_the_run_created_is_removed_again(project, overlay, repo):
    overlay.write("brand_new.py", "x = 1\n")
    verify.run(project, overlay)
    assert not (repo / "brand_new.py").exists()


def test_a_file_the_run_deleted_comes_back(project, overlay, repo):
    overlay.delete("core.py")
    verify.run(project, overlay)
    assert (repo / "core.py").read_bytes() == b"VALUE = 1\n"


def test_the_checkout_is_restored_even_when_the_command_fails(project, overlay, repo):
    """The failing case is the one that must not leave a mess."""
    overlay.write("core.py", "VALUE = 99\n")
    result = verify.run(project, overlay)
    assert result.passed is False
    assert (repo / "core.py").read_bytes() == b"VALUE = 1\n"


def test_no_file_the_agent_touched_is_left_changed(project, overlay, repo):
    """Artefacts the command itself drops are its business, not ours."""
    from app import workspace

    overlay.write("core.py", "VALUE = 2\n")
    overlay.write("extra.py", "y = 2\n")
    verify.run(project, overlay)

    left = {c.path for c in workspace.changes(repo)}
    assert "core.py" not in left
    assert "extra.py" not in left


def test_line_endings_survive_the_round_trip(project, overlay, repo):
    """`git checkout` would have rewritten these; writing bytes does not."""
    (repo / "crlf.py").write_bytes(b"A = 1\r\nB = 2\r\n")
    run_git(repo, "add", "crlf.py")
    run_git(repo, "commit", "-m", "crlf")

    overlay.write("crlf.py", "A = 9\r\nB = 2\r\n")
    verify.run(project, overlay)
    assert (repo / "crlf.py").read_bytes() == b"A = 1\r\nB = 2\r\n"


# --- what it refuses ------------------------------------------------------


def test_a_dirty_working_copy_refuses_rather_than_overwriting(project, overlay, repo):
    (repo / "core.py").write_bytes(b"MY UNCOMMITTED WORK\n")
    result = verify.run(project, overlay)

    assert result.ran is False
    assert "uncommitted changes" in result.reason
    # and crucially, it is still there
    assert (repo / "core.py").read_bytes() == b"MY UNCOMMITTED WORK\n"


def test_an_untracked_file_also_counts_as_dirty(project, overlay, repo):
    (repo / "scratch.txt").write_bytes(b"notes\n")
    assert verify.run(project, overlay).ran is False


def test_the_refusal_tells_the_model_what_to_do(project, overlay, repo):
    (repo / "core.py").write_bytes(b"x\n")
    reason = verify.run(project, overlay).reason
    assert "Commit or stash" in reason
    assert "only costs you the test run" in reason


# --- the command is not a shell -------------------------------------------


def test_a_shell_operator_is_argument_text_not_syntax(make, repo, overlay):
    """`;` and `&&` must not be able to turn one command into two."""
    canary = repo.parent / "canary.txt"
    project = make.project(
        "Demo",
        local_path=str(repo),
        test_command=f'"{sys.executable}" -c pass && echo pwned > "{canary}"',
    )
    verify.run(project, overlay)
    assert not canary.exists()


def test_the_split_keeps_a_windows_path_intact():
    parts = verify.split(r'"C:\proj\.venv\Scripts\python.exe" -m pytest')
    assert parts[0] == r"C:\proj\.venv\Scripts\python.exe"
    assert parts[1:] == ["-m", "pytest"]


def test_an_empty_command_is_not_a_command():
    with pytest.raises(verify.VerifyError):
        verify.split("   ")


def test_the_tool_takes_no_arguments():
    """The model chooses when, never what."""
    tool = next(t for t in agent.TOOLS if t["name"] == "run_tests")
    assert tool["input_schema"].get("properties") == {}
    assert not tool["input_schema"].get("required")


# --- output -------------------------------------------------------------


def test_long_output_keeps_the_end_not_the_start(project, overlay):
    """Failures and the summary line are both at the bottom."""
    text = "\n".join(f"line {n}" for n in range(5000))
    trimmed = verify.tail(text)
    assert len(trimmed) <= verify.MAX_OUTPUT + 40
    assert trimmed.endswith("line 4999")
    assert "earlier output cut" in trimmed


def test_short_output_is_left_alone():
    assert verify.tail("all good") == "all good"


# --- reaching the run row -------------------------------------------------


def test_the_result_is_carried_out_of_the_tool(project, overlay):
    overlay.write("core.py", "VALUE = 2\n")
    text = agent.run_tool(overlay, "run_tests", {}, project)
    assert "passed" in text
    assert overlay.last_test.passed is True


def test_without_a_project_the_tool_says_so(overlay):
    assert "cannot be run" in agent.run_tool(overlay, "run_tests", {}, None)


def test_never_running_is_not_the_same_as_failing(project, overlay):
    assert overlay.last_test is None
