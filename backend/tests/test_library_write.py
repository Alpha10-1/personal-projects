"""Write paths for notes, links and file attachments.

The upload endpoint is the only place the app writes to disk, so the size
cap, the cleanup-on-failure and the delete-removes-the-file behaviour all
get exercised here.
"""

import io

import pytest

from app.db import UPLOAD_DIR
from app.routes import library


def upload(client, name="report.csv", content=b"a,b\n1,2\n", content_type="text/csv",
           **data):
    return client.post(
        "/files",
        files={"upload": (name, io.BytesIO(content), content_type)},
        data=data,
    )


@pytest.fixture
def upload_dir_count():
    """Counts files in the shared upload directory.

    The directory is not reset between tests, so assertions compare a
    before/after delta rather than an absolute count.
    """
    return lambda: len(list(UPLOAD_DIR.iterdir()))


# --- Notes ------------------------------------------------------------------


def test_create_note_applies_defaults(client):
    body = client.post("/notes", json={"body": "ran the backfill"}).json()

    assert body["kind"] == "note"
    assert body["pinned"] is False
    assert body["title"] is None


def test_note_body_is_required(client):
    assert client.post("/notes", json={"body": ""}).status_code == 422
    assert client.post("/notes", json={}).status_code == 422


def test_note_rejects_unknown_kind(client):
    response = client.post("/notes", json={"body": "b", "kind": "rambling"})
    assert response.status_code == 422


def test_note_rejects_unknown_project(client):
    response = client.post("/notes", json={"body": "b", "project_id": 999})
    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown project"


def test_patch_note_validates_a_reassigned_project(client):
    note = client.post("/notes", json={"body": "b"}).json()

    response = client.patch("/notes/%d" % note["id"], json={"project_id": 999})

    assert response.status_code == 400


def test_pinned_notes_are_listed_first(client):
    client.post("/notes", json={"body": "ordinary"})
    client.post("/notes", json={"body": "important", "pinned": True})

    assert [n["body"] for n in client.get("/notes").json()] == [
        "important",
        "ordinary",
    ]


def test_note_search_matches_title_and_body(client):
    client.post("/notes", json={"body": "about the forecast model"})
    client.post("/notes", json={"title": "forecast", "body": "unrelated text"})
    client.post("/notes", json={"body": "nothing relevant"})

    rows = client.get("/notes", params={"q": "forecast"}).json()

    assert len(rows) == 2


def test_delete_note(client):
    note = client.post("/notes", json={"body": "b"}).json()

    assert client.delete("/notes/%d" % note["id"]).status_code == 204
    assert client.get("/notes").json() == []
    assert client.delete("/notes/%d" % note["id"]).status_code == 404


# --- Links ------------------------------------------------------------------


def test_a_pasted_url_without_a_scheme_gets_https(client):
    body = client.post(
        "/links", json={"title": "paper", "url": "arxiv.org/abs/1234"}
    ).json()

    assert body["url"] == "https://arxiv.org/abs/1234"


@pytest.mark.parametrize(
    "url",
    ["https://example.com", "http://example.com", "ftp://files.example.com"],
)
def test_an_existing_scheme_is_left_alone(client, url):
    body = client.post("/links", json={"title": "t", "url": url}).json()

    assert body["url"] == url


def test_link_requires_a_title_and_url(client):
    assert client.post("/links", json={"title": "", "url": "x"}).status_code == 422
    assert client.post("/links", json={"title": "t", "url": ""}).status_code == 422


def test_link_rejects_unknown_project(client):
    response = client.post(
        "/links", json={"title": "t", "url": "x.com", "project_id": 999}
    )
    assert response.status_code == 400


def test_delete_link(client):
    link = client.post("/links", json={"title": "t", "url": "x.com"}).json()

    assert client.delete("/links/%d" % link["id"]).status_code == 204
    assert client.get("/links").json() == []


# --- Attachments ------------------------------------------------------------


def test_upload_records_the_original_filename_and_size(client):
    content = b"col_a,col_b\n1,2\n"

    body = upload(client, name="results.csv", content=content).json()

    assert body["filename"] == "results.csv"
    assert body["size_bytes"] == len(content)
    assert body["content_type"] == "text/csv"


def test_uploaded_bytes_come_back_on_download(client):
    content = b"the exact bytes\n"
    record = upload(client, name="notes.txt", content=content).json()

    response = client.get("/files/%d/download" % record["id"])

    assert response.status_code == 200
    assert response.content == content
    # Served as a download rather than rendered inline.
    assert "attachment" in response.headers["content-disposition"]
    assert "notes.txt" in response.headers["content-disposition"]


def test_upload_rejects_an_unknown_project(client):
    response = upload(client, project_id="999")

    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown project"


def test_upload_over_the_size_cap_is_rejected(client, monkeypatch):
    monkeypatch.setattr(library, "MAX_UPLOAD_BYTES", 1024)

    response = upload(client, name="big.bin", content=b"x" * 4096)

    assert response.status_code == 413
    assert "limit" in response.json()["detail"]


def test_a_rejected_upload_leaves_no_file_behind(client, monkeypatch, upload_dir_count):
    monkeypatch.setattr(library, "MAX_UPLOAD_BYTES", 1024)
    before = upload_dir_count()

    upload(client, name="big.bin", content=b"x" * 4096)

    assert upload_dir_count() == before
    assert client.get("/files").json() == []


def test_a_file_at_exactly_the_cap_is_accepted(client, monkeypatch):
    monkeypatch.setattr(library, "MAX_UPLOAD_BYTES", 1024)

    response = upload(client, name="exact.bin", content=b"x" * 1024)

    assert response.status_code == 201
    assert response.json()["size_bytes"] == 1024


def test_uploads_with_the_same_name_do_not_collide(client):
    first = upload(client, name="report.csv", content=b"first").json()
    second = upload(client, name="report.csv", content=b"second").json()

    assert first["id"] != second["id"]
    assert client.get("/files/%d/download" % first["id"]).content == b"first"
    assert client.get("/files/%d/download" % second["id"]).content == b"second"


def test_delete_removes_the_row_and_the_file_on_disk(client, upload_dir_count):
    before = upload_dir_count()
    record = upload(client).json()
    assert upload_dir_count() == before + 1

    assert client.delete("/files/%d" % record["id"]).status_code == 204

    assert upload_dir_count() == before
    assert client.get("/files").json() == []
    assert client.get("/files/%d/download" % record["id"]).status_code == 404


def test_download_reports_410_when_the_file_vanished_from_disk(client):
    record = upload(client).json()
    # Simulate the directory being cleaned out underneath the database.
    for path in UPLOAD_DIR.iterdir():
        path.unlink()

    response = client.get("/files/%d/download" % record["id"])

    assert response.status_code == 410
    assert response.json()["detail"] == "File is missing from disk"


def test_files_can_be_filtered_by_project(client):
    project = client.post("/projects", json={"name": "P"}).json()
    upload(client, name="attached.csv", project_id=str(project["id"]))
    upload(client, name="loose.csv")

    rows = client.get("/files", params={"project_id": project["id"]}).json()

    assert [r["filename"] for r in rows] == ["attached.csv"]
    assert rows[0]["project_name"] == "P"
