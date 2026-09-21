"""Editing a file, and the approval that puts it on disk.

The invariant these are really protecting: **saving does not write**. Every
path into the file -- the editor, an agent run, a revert -- goes through an
approval that is recorded against a person. A test that passes while a file
changed on disk before approval would mean the feature does not exist.
"""

import subprocess

import pytest

from app import models


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def run_git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


ORIGINAL = "MAX_ROWS = 100\n\n\ndef fetch_rows(source):\n    return source[:MAX_ROWS]\n"
EDITED = "MAX_ROWS = 100\n\n\ndef fetch_rows(source, limit=10):\n    return source[:limit]\n"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "demo"
    write(root / "core.py", ORIGINAL)
    write(root / "report.py", "from core import fetch_rows\n\n\ndef build():\n    return fetch_rows([])\n")
    write(root / ".env", "SECRET=1\n")
    run_git(root, "init", "--initial-branch=main")
    run_git(root, "config", "user.email", "t@e.com")
    run_git(root, "config", "user.name", "T")
    run_git(root, "add", "core.py", "report.py")
    run_git(root, "commit", "-m", "first")
    monkeypatch.setenv("PP_WORKSPACE_ROOTS", str(tmp_path))
    return root


@pytest.fixture
def project(make, repo):
    leader = make.person("Alfah Lubisi")
    return make.project("Demo", local_path=str(repo), leader_id=leader.id)


def raise_edit(client, project, content=EDITED, path="core.py", **kw):
    return client.post(
        f"/projects/{project.id}/code/changes",
        json={"path": path, "content": content, **kw},
    )


# --- raising a change ----------------------------------------------------


def test_saving_an_edit_does_not_touch_the_file(client, project, repo):
    response = raise_edit(client, project)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["action"] == "modify"
    assert (repo / "core.py").read_bytes().decode() == ORIGINAL


def test_the_change_carries_both_sides_and_a_diff(client, project):
    body = raise_edit(client, project).json()
    assert body["before_text"] == ORIGINAL
    assert body["after_text"] == EDITED
    assert "-def fetch_rows(source):" in body["diff"]
    assert "+def fetch_rows(source, limit=10):" in body["diff"]


def test_saving_the_same_content_is_refused(client, project):
    response = raise_edit(client, project, content=ORIGINAL)
    assert response.status_code == 409
    assert "already what the file says" in response.json()["detail"]


def test_a_new_file_is_a_create(client, project):
    body = raise_edit(client, project, path="fresh.py", content="x = 1\n").json()
    assert body["action"] == "create"
    assert body["before_text"] is None


def test_a_deletion_is_raised_like_any_other_change(client, project, repo):
    body = client.post(
        f"/projects/{project.id}/code/changes",
        json={"path": "core.py", "delete": True},
    ).json()
    assert body["action"] == "delete"
    assert body["after_text"] is None
    assert (repo / "core.py").exists()


def test_the_editor_cannot_touch_credentials(client, project):
    response = raise_edit(client, project, path=".env", content="SECRET=2\n")
    assert response.status_code == 400
    assert "credentials" in response.json()["detail"]


def test_the_editor_cannot_escape_the_repository(client, project):
    response = raise_edit(client, project, path="../outside.py", content="x = 1\n")
    assert response.status_code == 400


def test_a_project_with_no_folder_cannot_raise_a_change(client, make):
    project = make.project("No folder")
    assert raise_edit(client, project).status_code == 400


# --- approving -----------------------------------------------------------


def test_approving_writes_the_file_and_records_who(client, project, repo):
    change = raise_edit(client, project).json()
    body = client.post(f"/code/changes/{change['id']}/approve", json={}).json()
    assert body["status"] == "approved"
    assert body["approved_by"] == "Alfah Lubisi"
    assert body["approved_at"] is not None
    assert (repo / "core.py").read_bytes().decode() == EDITED


def test_approving_defaults_to_the_project_leader(client, project):
    change = raise_edit(client, project).json()
    body = client.post(f"/code/changes/{change['id']}/approve", json={}).json()
    assert body["approved_by_id"] == project.leader_id


def test_someone_else_can_be_named_instead(client, project, make):
    other = make.person("Reviewer Two")
    change = raise_edit(client, project).json()
    body = client.post(
        f"/code/changes/{change['id']}/approve", json={"person_id": other.id}
    ).json()
    assert body["approved_by"] == "Reviewer Two"


def test_without_a_leader_approval_says_who_is_missing(client, make, repo):
    project = make.project("Leaderless", local_path=str(repo))
    change = raise_edit(client, project).json()
    response = client.post(f"/code/changes/{change['id']}/approve", json={})
    assert response.status_code == 400
    assert "no leader set" in response.json()["detail"]


def test_approving_an_unknown_person_is_a_404(client, project):
    change = raise_edit(client, project).json()
    assert (
        client.post(
            f"/code/changes/{change['id']}/approve", json={"person_id": 9999}
        ).status_code
        == 404
    )


def test_approving_twice_is_refused(client, project):
    change = raise_edit(client, project).json()
    client.post(f"/code/changes/{change['id']}/approve", json={})
    again = client.post(f"/code/changes/{change['id']}/approve", json={})
    assert again.status_code == 409


def test_rejecting_leaves_the_file_alone_and_keeps_the_reason(client, project, repo):
    change = raise_edit(client, project).json()
    body = client.post(
        f"/code/changes/{change['id']}/reject", json={"note": "Breaks report.py."}
    ).json()
    assert body["status"] == "rejected"
    assert body["decision_note"] == "Breaks report.py."
    assert (repo / "core.py").read_bytes().decode() == ORIGINAL


def test_approving_a_deletion_removes_the_file(client, project, repo):
    change = client.post(
        f"/projects/{project.id}/code/changes", json={"path": "core.py", "delete": True}
    ).json()
    client.post(f"/code/changes/{change['id']}/approve", json={})
    assert not (repo / "core.py").exists()


# --- the outline, which is the point of the whole thing ------------------


def test_the_impact_names_who_the_change_reaches(client, project):
    change = raise_edit(client, project).json()
    outline = client.get(f"/code/changes/{change['id']}/impact").json()
    text = " ".join(e["text"] for e in outline["effects"])
    assert "different arguments" in text
    assert "report.py" in text


def test_the_impact_is_available_after_approval_too(client, project):
    """The case this exists for: it was waved through, and now you want to
    know what you just did."""
    change = raise_edit(client, project).json()
    client.post(f"/code/changes/{change['id']}/approve", json={})
    outline = client.get(f"/code/changes/{change['id']}/impact").json()
    assert outline["status"] == "approved"
    assert outline["still_as_approved"] is True
    assert any(e["level"] == "risk" for e in outline["effects"])


def test_the_outline_notices_the_file_has_moved_on_since(client, project, repo):
    change = raise_edit(client, project).json()
    client.post(f"/code/changes/{change['id']}/approve", json={})
    write(repo / "core.py", "# someone else got here\n")
    outline = client.get(f"/code/changes/{change['id']}/impact").json()
    assert outline["still_as_approved"] is False


def test_a_pending_change_has_no_opinion_on_whether_it_is_intact(client, project):
    change = raise_edit(client, project).json()
    assert client.get(f"/code/changes/{change['id']}/impact").json()["still_as_approved"] is None


def test_the_outline_says_what_it_could_not_see(client, project):
    change = raise_edit(client, project).json()
    assert client.get(f"/code/changes/{change['id']}/impact").json()["limits"]


# --- undoing -------------------------------------------------------------


def test_reverting_puts_the_file_back_exactly(client, project, repo):
    change = raise_edit(client, project).json()
    client.post(f"/code/changes/{change['id']}/approve", json={})
    assert (repo / "core.py").read_bytes().decode() == EDITED

    body = client.post(f"/code/changes/{change['id']}/revert").json()
    assert body["status"] == "reverted"
    assert (repo / "core.py").read_bytes().decode() == ORIGINAL


def test_reverting_a_created_file_deletes_it(client, project, repo):
    change = raise_edit(client, project, path="fresh.py", content="x = 1\n").json()
    client.post(f"/code/changes/{change['id']}/approve", json={})
    assert (repo / "fresh.py").exists()
    client.post(f"/code/changes/{change['id']}/revert")
    assert not (repo / "fresh.py").exists()


def test_reverting_refuses_to_discard_a_later_edit(client, project, repo):
    change = raise_edit(client, project).json()
    client.post(f"/code/changes/{change['id']}/approve", json={})
    write(repo / "core.py", "# later work nobody wants thrown away\n")

    response = client.post(f"/code/changes/{change['id']}/revert")
    assert response.status_code == 409
    assert "changed since this was approved" in response.json()["detail"]
    assert (repo / "core.py").read_bytes().decode() == "# later work nobody wants thrown away\n"


def test_only_an_approved_change_can_be_reverted(client, project):
    change = raise_edit(client, project).json()
    assert client.post(f"/code/changes/{change['id']}/revert").status_code == 409


# --- an agent run, reviewed the same way ---------------------------------


def test_an_agent_run_becomes_change_rows(client, db, project, repo):
    import json as jsonlib

    run = models.AgentRun(
        project_id=project.id,
        instruction="Add a limit.",
        status="proposed",
        summary="Added a limit.",
        changes_json=jsonlib.dumps(
            [{"path": "core.py", "action": "modify", "content": EDITED}]
        ),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    [raised] = client.post(f"/agent/runs/{run.id}/submit").json()
    assert raised["origin"] == "agent"
    assert raised["agent_run_id"] == run.id
    assert raised["status"] == "pending"
    assert (repo / "core.py").read_bytes().decode() == ORIGINAL


# --- explaining ----------------------------------------------------------


def test_explain_answers_from_the_repository(client, project):
    body = client.get(
        f"/projects/{project.id}/code/explain",
        params={"path": "core.py", "start_line": 4, "end_line": 5},
    ).json()
    assert body["enclosing"]["name"] == "fetch_rows"
    assert any("report.py" in c["text"] for c in body["consequences"])


def test_explain_reports_the_shared_value(client, project):
    body = client.get(
        f"/projects/{project.id}/code/explain",
        params={"path": "core.py", "start_line": 5, "end_line": 5},
    ).json()
    assert [s["name"] for s in body["shared_values"]] == ["MAX_ROWS"]


def test_explain_on_a_missing_file_is_a_400(client, project):
    assert (
        client.get(
            f"/projects/{project.id}/code/explain",
            params={"path": "nope.py", "start_line": 1, "end_line": 1},
        ).status_code
        == 400
    )


# --- listing -------------------------------------------------------------


def test_changes_list_newest_first_and_can_be_filtered(client, project):
    first = raise_edit(client, project).json()
    client.post(f"/code/changes/{first['id']}/approve", json={})
    raise_edit(client, project, content=ORIGINAL + "# and more\n")

    everything = client.get(f"/projects/{project.id}/code/changes").json()
    assert len(everything) == 2
    pending = client.get(
        f"/projects/{project.id}/code/changes", params={"status": "pending"}
    ).json()
    assert [c["status"] for c in pending] == ["pending"]


def test_the_list_carries_line_counts_without_the_whole_file(client, project):
    raise_edit(client, project)
    [row] = client.get(f"/projects/{project.id}/code/changes").json()
    assert row["lines"]["added"] >= 1
    assert "after_text" not in row
