"""Notes, links and file attachments -- the reference material around a project."""

import mimetypes
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import models, schemas
from app.db import UPLOAD_DIR, get_db
from app.deps import client_source
from app.enrich import project_name_map

router = APIRouter(tags=["library"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
UPLOAD_CHUNK = 1024 * 1024


def _with_project_names(db: Session, schema, rows):
    names = project_name_map(db, [r.project_id for r in rows])
    return [
        schema.model_validate(r).model_copy(
            update={"project_name": names.get(r.project_id)}
        )
        for r in rows
    ]


def _check_project(db: Session, project_id):
    if project_id is not None and db.get(models.Project, project_id) is None:
        raise HTTPException(status_code=400, detail="Unknown project")


# --- Notes ------------------------------------------------------------------

notes_router = APIRouter(prefix="/notes", tags=["notes"])


@notes_router.get("", response_model=list[schemas.NoteOut])
def list_notes(
    db: Session = Depends(get_db),
    project_id: Optional[int] = None,
    kind: Optional[str] = None,
    pinned_only: bool = False,
    q: Optional[str] = None,
    limit: int = 200,
):
    stmt = select(models.Note)
    if project_id is not None:
        stmt = stmt.where(models.Note.project_id == project_id)
    if kind:
        stmt = stmt.where(models.Note.kind == kind)
    if pinned_only:
        stmt = stmt.where(models.Note.pinned.is_(True))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(models.Note.title.ilike(like), models.Note.body.ilike(like)))

    # Pinned first, then newest -- a pinned note is one you want to keep
    # seeing regardless of how long ago you wrote it.
    stmt = stmt.order_by(
        models.Note.pinned.desc(), models.Note.created_at.desc(), models.Note.id.desc()
    ).limit(max(1, min(limit, 1000)))
    return _with_project_names(db, schemas.NoteOut, list(db.execute(stmt).scalars()))


@notes_router.post("", response_model=schemas.NoteOut, status_code=201)
def create_note(
    payload: schemas.NoteCreate,
    db: Session = Depends(get_db),
    source: str = Depends(client_source),
):
    _check_project(db, payload.project_id)
    note = models.Note(**payload.model_dump(), source=source)
    db.add(note)
    db.commit()
    db.refresh(note)
    return _with_project_names(db, schemas.NoteOut, [note])[0]


@notes_router.patch("/{note_id}", response_model=schemas.NoteOut)
def update_note(note_id: int, payload: schemas.NoteUpdate, db: Session = Depends(get_db)):
    note = db.get(models.Note, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    changes = payload.model_dump(exclude_unset=True)
    if "project_id" in changes:
        _check_project(db, changes["project_id"])
    for field, value in changes.items():
        setattr(note, field, value)
    db.commit()
    db.refresh(note)
    return _with_project_names(db, schemas.NoteOut, [note])[0]


@notes_router.delete("/{note_id}", status_code=204)
def delete_note(note_id: int, db: Session = Depends(get_db)):
    note = db.get(models.Note, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    db.delete(note)
    db.commit()


# --- Links ------------------------------------------------------------------

links_router = APIRouter(prefix="/links", tags=["links"])


@links_router.get("", response_model=list[schemas.LinkOut])
def list_links(
    db: Session = Depends(get_db),
    project_id: Optional[int] = None,
    kind: Optional[str] = None,
    q: Optional[str] = None,
):
    stmt = select(models.Link)
    if project_id is not None:
        stmt = stmt.where(models.Link.project_id == project_id)
    if kind:
        stmt = stmt.where(models.Link.kind == kind)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                models.Link.title.ilike(like),
                models.Link.url.ilike(like),
                models.Link.note.ilike(like),
            )
        )
    stmt = stmt.order_by(models.Link.created_at.desc(), models.Link.id.desc())
    return _with_project_names(db, schemas.LinkOut, list(db.execute(stmt).scalars()))


@links_router.post("", response_model=schemas.LinkOut, status_code=201)
def create_link(payload: schemas.LinkCreate, db: Session = Depends(get_db)):
    _check_project(db, payload.project_id)
    data = payload.model_dump()
    url = data["url"].strip()
    # A bare "arxiv.org/abs/..." is what actually gets pasted; without a
    # scheme the browser would treat it as a relative path and 404.
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = f"https://{url}"
    data["url"] = url
    link = models.Link(**data)
    db.add(link)
    db.commit()
    db.refresh(link)
    return _with_project_names(db, schemas.LinkOut, [link])[0]


@links_router.patch("/{link_id}", response_model=schemas.LinkOut)
def update_link(link_id: int, payload: schemas.LinkUpdate, db: Session = Depends(get_db)):
    link = db.get(models.Link, link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="Link not found")
    changes = payload.model_dump(exclude_unset=True)
    if "project_id" in changes:
        _check_project(db, changes["project_id"])
    for field, value in changes.items():
        setattr(link, field, value)
    db.commit()
    db.refresh(link)
    return _with_project_names(db, schemas.LinkOut, [link])[0]


@links_router.delete("/{link_id}", status_code=204)
def delete_link(link_id: int, db: Session = Depends(get_db)):
    link = db.get(models.Link, link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="Link not found")
    db.delete(link)
    db.commit()


# --- Attachments ------------------------------------------------------------

files_router = APIRouter(prefix="/files", tags=["files"])


@files_router.get("", response_model=list[schemas.AttachmentOut])
def list_files(db: Session = Depends(get_db), project_id: Optional[int] = None):
    stmt = select(models.Attachment)
    if project_id is not None:
        stmt = stmt.where(models.Attachment.project_id == project_id)
    stmt = stmt.order_by(models.Attachment.created_at.desc(), models.Attachment.id.desc())
    return _with_project_names(db, schemas.AttachmentOut, list(db.execute(stmt).scalars()))


@files_router.post("", response_model=schemas.AttachmentOut, status_code=201)
async def upload_file(
    db: Session = Depends(get_db),
    upload: UploadFile = File(...),
    project_id: Optional[int] = Form(None),
    note: Optional[str] = Form(None),
):
    _check_project(db, project_id)

    original = Path(upload.filename or "upload").name
    # The stored name is a fresh UUID rather than the uploaded name: it keeps
    # the upload directory flat and collision-free, and means a crafted
    # filename can never point outside it.
    suffix = Path(original).suffix[:16]
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    destination = UPLOAD_DIR / stored_name

    size = 0
    try:
        with destination.open("wb") as out:
            while chunk := await upload.read(UPLOAD_CHUNK):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit",
                    )
                out.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    record = models.Attachment(
        project_id=project_id,
        filename=original,
        stored_name=stored_name,
        content_type=upload.content_type or mimetypes.guess_type(original)[0],
        size_bytes=size,
        note=note,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return _with_project_names(db, schemas.AttachmentOut, [record])[0]


@files_router.get("/{file_id}/download")
def download_file(file_id: int, db: Session = Depends(get_db)):
    record = db.get(models.Attachment, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")
    path = UPLOAD_DIR / record.stored_name
    if not path.exists():
        raise HTTPException(status_code=410, detail="File is missing from disk")
    return FileResponse(
        path,
        filename=record.filename,
        media_type=record.content_type or "application/octet-stream",
    )


@files_router.delete("/{file_id}", status_code=204)
def delete_file(file_id: int, db: Session = Depends(get_db)):
    record = db.get(models.Attachment, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")
    (UPLOAD_DIR / record.stored_name).unlink(missing_ok=True)
    db.delete(record)
    db.commit()


router.include_router(notes_router)
router.include_router(links_router)
router.include_router(files_router)
