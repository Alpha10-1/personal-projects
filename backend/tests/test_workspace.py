"""The live view of a checkout.

These build a real git repository in a temp directory and read it back. A
mocked `git` would test the parsing and nothing else, and the parsing is the
part least likely to be wrong -- what matters is that a path outside the
repository is refused, that an uncommitted edit shows up, and that a project
with no folder set gets an explanation rather than a stack trace.
"""

import subprocess

import pytest

from app import editor, workspace


def write(path, text):
    """Write with LF endings, whatever the platform would prefer.

    `Path.write_text` translates newlines on Windows, which would make every
    assertion about file contents in here platform-dependent for no reason.
    """
    path.write_bytes(text.encode("utf-8"))


def run(cwd, *args):
    subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A real, committed git repository, inside an allowed root."""
    root = tmp_path / "demo"
    (root / "src").mkdir(parents=True)
    write(root / "README.md", "# demo\n")
    write(root / "src" / "main.py", "print('hello')\n")
    run(root, "init", "--initial-branch=main")
    run(root, "config", "user.email", "test@example.com")
    run(root, "config", "user.name", "Test")
    run(root, "add", ".")
    run(root, "commit", "-m", "first")
    monkeypatch.setenv("PP_WORKSPACE_ROOTS", str(tmp_path))
    return root


# --- the guard rails ------------------------------------------------------


def test_path_outside_allowed_roots_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("PP_WORKSPACE_ROOTS", str(tmp_path / "allowed"))
    (tmp_path / "allowed").mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(workspace.WorkspaceError, match="outside the allowed roots"):
        workspace.resolve(str(elsewhere))


def test_a_plain_folder_is_not_a_repository(tmp_path, monkeypatch):
    monkeypatch.setenv("PP_WORKSPACE_ROOTS", str(tmp_path))
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(workspace.WorkspaceError, match="not a git repository"):
        workspace.resolve(str(plain))


def test_missing_path_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("PP_WORKSPACE_ROOTS", str(tmp_path))
    with pytest.raises(workspace.WorkspaceError, match="nothing at"):
        workspace.resolve(str(tmp_path / "nope"))


def test_no_path_at_all_is_its_own_message():
    with pytest.raises(workspace.WorkspaceError, match="no local folder"):
        workspace.resolve(None)


@pytest.mark.parametrize(
    "escape", ["../secret.txt", "src/../../secret.txt", "/etc/passwd", "..\\secret.txt"]
)
def test_traversal_out_of_the_repo_is_refused(repo, escape):
    write(repo.parent / "secret.txt", "nope")
    with pytest.raises(workspace.WorkspaceError):
        workspace.safe_join(repo, escape)


def test_safe_join_allows_a_path_inside(repo):
    assert workspace.safe_join(repo, "src/main.py").name == "main.py"


# --- reading the state ----------------------------------------------------


def test_head_reports_branch_and_last_commit(repo):
    info = workspace.head(repo)
    assert info["branch"] == "main"
    assert info["detached"] is False
    assert info["last_commit"]["subject"] == "first"


def test_a_clean_repo_has_no_changes(repo):
    assert workspace.changes(repo) == []


def test_an_uncommitted_edit_shows_up_immediately(repo):
    write(repo / "src" / "main.py", "print('changed')\n")
    [change] = workspace.changes(repo)
    assert change.path == "src/main.py"
    assert change.state == "modified"
    assert change.staged is False


def test_a_new_file_is_reported_as_untracked(repo):
    write(repo / "notes.txt", "hi")
    states = {c.path: c.state for c in workspace.changes(repo)}
    assert states["notes.txt"] == "untracked"


def test_a_staged_edit_is_marked_staged(repo):
    write(repo / "README.md", "# demo 2\n")
    run(repo, "add", "README.md")
    [change] = workspace.changes(repo)
    assert change.staged is True


def test_diff_contains_the_change(repo):
    write(repo / "src" / "main.py", "print('changed')\n")
    text = workspace.diff(repo)
    assert "-print('hello')" in text
    assert "+print('changed')" in text


# --- reading files and folders --------------------------------------------


def test_listing_puts_folders_first_and_hides_noise(repo):
    (repo / "node_modules").mkdir()
    write(repo / "a.txt", "a")
    names = [e["name"] for e in workspace.listing(repo)]
    assert "node_modules" not in names
    assert ".git" not in names
    assert names.index("src") < names.index("a.txt")


def test_read_file_returns_current_contents(repo):
    write(repo / "src" / "main.py", "print('now')\n")
    payload = workspace.read_file(repo, "src/main.py")
    assert payload["content"] == "print('now')\n"
    assert payload["binary"] is False
    assert payload["truncated"] is False


def test_a_binary_file_is_described_not_rendered(repo):
    (repo / "logo.png").write_bytes(b"\x89PNG\x00\x00\x00\x00binary")
    payload = workspace.read_file(repo, "logo.png")
    assert payload["binary"] is True
    assert payload["content"] is None


def test_a_large_file_is_truncated_and_says_so(repo, monkeypatch):
    monkeypatch.setattr(workspace, "MAX_FILE_BYTES", 100)
    write(repo / "big.txt", "x" * 500)
    payload = workspace.read_file(repo, "big.txt")
    assert payload["truncated"] is True
    assert len(payload["content"]) == 100
    assert payload["size"] == 500


def test_search_finds_a_tracked_line(repo):
    hits = workspace.search(repo, "hello")
    assert hits and hits[0]["path"] == "src/main.py"
    assert hits[0]["line"] == 1


def test_search_with_no_matches_is_empty_not_an_error(repo):
    assert workspace.search(repo, "nowherenearthis") == []


# --- the one-call summary -------------------------------------------------


def test_state_of_a_project_with_no_folder_explains_itself():
    state = workspace.state(None)
    assert state["available"] is False
    assert "no local folder" in state["reason"]


def test_state_of_a_dirty_repo(repo):
    write(repo / "README.md", "# edited\n")
    state = workspace.state(str(repo))
    assert state["available"] is True
    assert state["dirty"] is True
    assert state["branch"] == "main"
    assert [c["path"] for c in state["changes"]] == ["README.md"]


# --- the editor link ------------------------------------------------------


def test_vscode_url_has_a_leading_slash_and_the_drive(tmp_path):
    url = editor.url_for(tmp_path / "a.py", line=12)
    assert url.startswith("vscode://file/")
    assert url.endswith("/a.py:12")


def test_vscode_url_without_a_line(tmp_path):
    assert not editor.url_for(tmp_path / "a.py").endswith(":")


def test_a_configured_editor_command_wins(monkeypatch):
    monkeypatch.setenv("PP_EDITOR_COMMAND", "my-editor")
    assert editor.command().endswith("my-editor")


def test_open_without_an_editor_explains_how_to_get_one(monkeypatch, tmp_path):
    monkeypatch.setattr(editor, "command", lambda: None)
    with pytest.raises(editor.EditorUnavailable, match="PP_EDITOR_COMMAND"):
        editor.open_path(tmp_path)


# --- through the API ------------------------------------------------------


def test_route_reports_no_folder_as_a_200(client, make):
    project = make.project("No folder")
    body = client.get(f"/projects/{project.id}/workspace").json()
    assert body["available"] is False
    assert body["local_path"] is None


def test_route_reads_the_tree_and_a_file(client, make, repo):
    project = make.project("Linked", local_path=str(repo))
    entries = client.get(f"/projects/{project.id}/workspace/tree").json()["entries"]
    assert {e["name"] for e in entries} == {"src", "README.md"}

    body = client.get(
        f"/projects/{project.id}/workspace/file", params={"path": "src/main.py"}
    ).json()
    assert body["content"] == "print('hello')\n"
    assert body["editor_url"].startswith("vscode://file/")


def test_route_refuses_to_read_outside_the_repo(client, make, repo):
    project = make.project("Linked", local_path=str(repo))
    response = client.get(
        f"/projects/{project.id}/workspace/file", params={"path": "../../secret"}
    )
    assert response.status_code == 400


def test_route_on_a_project_without_a_folder_is_a_400_not_a_500(client, make):
    project = make.project("No folder")
    assert client.get(f"/projects/{project.id}/workspace/tree").status_code == 400


def test_local_path_round_trips_through_the_api(client, make, repo):
    project = make.project("Linked")
    patched = client.patch(
        f"/projects/{project.id}", json={"local_path": str(repo)}
    ).json()
    assert patched["local_path"] == str(repo)
    assert client.get(f"/projects/{project.id}/workspace").json()["available"] is True
