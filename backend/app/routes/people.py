"""People, project membership, and feedback from them.

There is still no login here. A person is a record of someone involved, not
an account: linking them to a project says what you expect from them and lets
their git history be named, and a digest is something you send them rather
than somewhere they sign in.
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import collaboration, models, schemas, share
from app.db import get_db

router = APIRouter(tags=["people"])


# --- Helpers -----------------------------------------------------------------


def _get_person_or_404(db: Session, person_id: int) -> models.Person:
    person = db.get(models.Person, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return person


def _get_project_or_404(db: Session, project_id: int) -> models.Project:
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _serialize_people(db: Session, people: list[models.Person]) -> list[schemas.PersonOut]:
    stats = collaboration.stats_for_people(db, [p.id for p in people])
    out = []
    for person in people:
        row = schemas.PersonOut.model_validate(person)
        for key, value in stats.get(person.id, {}).items():
            setattr(row, key, value)
        out.append(row)
    return out


def _serialize_feedback(db: Session, items: list[models.Feedback]) -> list[schemas.FeedbackOut]:
    person_ids = {i.person_id for i in items if i.person_id}
    names = {}
    if person_ids:
        names = dict(
            db.execute(
                select(models.Person.id, models.Person.name).where(
                    models.Person.id.in_(person_ids)
                )
            ).all()
        )
    out = []
    for item in items:
        row = schemas.FeedbackOut.model_validate(item)
        row.person_name = names.get(item.person_id)
        out.append(row)
    return out


def _login_conflict(exc: IntegrityError) -> HTTPException:
    """A duplicate github_login is the only unique constraint a person has,
    and it is worth naming rather than returning a bare 500."""
    if "github_login" in str(exc.orig):
        return HTTPException(
            status_code=409,
            detail="That GitHub login already belongs to someone else.",
        )
    return HTTPException(status_code=400, detail="Could not save this person.")


# --- People ------------------------------------------------------------------


@router.get("/people", response_model=list[schemas.PersonOut])
def list_people(
    db: Session = Depends(get_db),
    include_archived: bool = False,
    limit: int = Query(200, ge=1, le=500),
):
    stmt = select(models.Person)
    if not include_archived:
        stmt = stmt.where(models.Person.archived_at.is_(None))
    people = list(db.execute(stmt.order_by(models.Person.name).limit(limit)).scalars())
    return _serialize_people(db, people)


# Declared before /people/{person_id} so "unlinked" and "relink" are not
# swallowed as person ids.
@router.get("/people/unlinked")
def unlinked_logins(db: Session = Depends(get_db)):
    """Who is in the git history but not on record yet."""
    return collaboration.unattributed_logins(db)


@router.post("/people/relink")
def relink_people(db: Session = Depends(get_db)):
    """Re-resolve attributions from the logins currently on record."""
    return collaboration.relink(db)


@router.post("/people", response_model=schemas.PersonOut, status_code=201)
def create_person(payload: schemas.PersonCreate, db: Session = Depends(get_db)):
    person = models.Person(**payload.model_dump())
    db.add(person)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _login_conflict(exc) from exc
    db.refresh(person)
    # Their earlier work is attached immediately -- adding someone who has
    # been committing for weeks should show those weeks, not start from zero.
    collaboration.relink(db)
    return _serialize_people(db, [person])[0]


@router.get("/people/{person_id}", response_model=schemas.PersonOut)
def get_person(person_id: int, db: Session = Depends(get_db)):
    return _serialize_people(db, [_get_person_or_404(db, person_id)])[0]


@router.patch("/people/{person_id}", response_model=schemas.PersonOut)
def update_person(
    person_id: int, payload: schemas.PersonUpdate, db: Session = Depends(get_db)
):
    person = _get_person_or_404(db, person_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(person, field, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _login_conflict(exc) from exc
    db.refresh(person)
    collaboration.relink(db)
    return _serialize_people(db, [person])[0]


@router.delete("/people/{person_id}", status_code=204)
def delete_person(person_id: int, db: Session = Depends(get_db)):
    """Remove a person, keeping what they did.

    Their contributions and feedback stay -- the commits happened and the
    words were said, and `author_login` still records who. Only the link to
    a named person goes, the same bargain as deleting a task and keeping its
    time logs.
    """
    person = _get_person_or_404(db, person_id)

    for event in db.execute(
        select(models.ActivityEvent).where(models.ActivityEvent.person_id == person_id)
    ).scalars():
        event.person_id = None
    for item in db.execute(
        select(models.Feedback).where(models.Feedback.person_id == person_id)
    ).scalars():
        item.person_id = None
    db.flush()

    for member in db.execute(
        select(models.ProjectMember).where(models.ProjectMember.person_id == person_id)
    ).scalars():
        db.delete(member)
    db.flush()

    db.delete(person)
    db.commit()


@router.get("/people/{person_id}/contributions", response_model=list[schemas.ActivityEventOut])
def person_contributions(
    person_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(100, ge=1, le=500),
):
    _get_person_or_404(db, person_id)
    return collaboration.contributions(db, person_id=person_id, limit=limit)


# --- Membership --------------------------------------------------------------


def _serialize_members(
    db: Session, project_id: int, members: list[models.ProjectMember]
) -> list[schemas.MemberOut]:
    person_ids = [m.person_id for m in members]
    people = {
        p.id: p
        for p in db.execute(
            select(models.Person).where(models.Person.id.in_(person_ids))
        ).scalars()
    } if person_ids else {}
    stats = collaboration.project_stats_for_people(db, project_id, person_ids)

    out = []
    for member in members:
        person = people.get(member.person_id)
        if person is None:
            continue
        counts = stats.get(member.person_id, {})
        out.append(
            schemas.MemberOut(
                id=member.id,
                person_id=member.person_id,
                project_id=member.project_id,
                role=member.role,
                added_at=member.added_at,
                name=person.name,
                email=person.email,
                github_login=person.github_login,
                role_title=person.role_title,
                contribution_count=counts.get("contribution_count", 0),
                feedback_open=counts.get("feedback_open", 0),
                last_contribution_at=counts.get("last_contribution_at"),
            )
        )
    return out


@router.get("/projects/{project_id}/members", response_model=list[schemas.MemberOut])
def list_members(project_id: int, db: Session = Depends(get_db)):
    _get_project_or_404(db, project_id)
    members = list(
        db.execute(
            select(models.ProjectMember)
            .where(models.ProjectMember.project_id == project_id)
            .order_by(models.ProjectMember.added_at)
        ).scalars()
    )
    return _serialize_members(db, project_id, members)


@router.post(
    "/projects/{project_id}/members", response_model=schemas.MemberOut, status_code=201
)
def add_member(
    project_id: int, payload: schemas.MemberCreate, db: Session = Depends(get_db)
):
    _get_project_or_404(db, project_id)
    _get_person_or_404(db, payload.person_id)

    member = models.ProjectMember(
        project_id=project_id, person_id=payload.person_id, role=payload.role
    )
    db.add(member)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="That person is already on this project."
        ) from exc
    db.refresh(member)
    return _serialize_members(db, project_id, [member])[0]


@router.patch("/members/{member_id}", response_model=schemas.MemberOut)
def update_member(
    member_id: int, payload: schemas.MemberUpdate, db: Session = Depends(get_db)
):
    member = db.get(models.ProjectMember, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Membership not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(member, field, value)
    db.commit()
    db.refresh(member)
    return _serialize_members(db, member.project_id, [member])[0]


@router.delete("/members/{member_id}", status_code=204)
def remove_member(member_id: int, db: Session = Depends(get_db)):
    """Unlink the person from the project. Their contributions are untouched:
    the commits are still theirs whether or not they are still listed."""
    member = db.get(models.ProjectMember, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Membership not found")
    db.delete(member)
    db.commit()


# --- Feedback ----------------------------------------------------------------


@router.get("/feedback", response_model=list[schemas.FeedbackOut])
def list_feedback(
    db: Session = Depends(get_db),
    project_id: int | None = None,
    person_id: int | None = None,
    status: str = "open",
    limit: int = Query(100, ge=1, le=500),
):
    stmt = select(models.Feedback)
    if project_id is not None:
        stmt = stmt.where(models.Feedback.project_id == project_id)
    if person_id is not None:
        stmt = stmt.where(models.Feedback.person_id == person_id)
    if status != "all":
        stmt = stmt.where(models.Feedback.status == status)
    items = list(
        db.execute(
            stmt.order_by(models.Feedback.occurred_at.desc()).limit(limit)
        ).scalars()
    )
    return _serialize_feedback(db, items)


@router.post("/feedback", response_model=schemas.FeedbackOut, status_code=201)
def create_feedback(payload: schemas.FeedbackCreate, db: Session = Depends(get_db)):
    """Log something a person said that git will never see.

    Source is fixed to `manual` rather than taken from the body: the other
    three mean "this came out of GitHub and has an id there", and a hand-typed
    row must not be able to claim that.
    """
    person = None
    if payload.person_id is not None:
        person = _get_person_or_404(db, payload.person_id)
    if payload.project_id is not None:
        _get_project_or_404(db, payload.project_id)

    item = models.Feedback(
        person_id=payload.person_id,
        project_id=payload.project_id,
        source="manual",
        author_login=person.github_login if person else None,
        body=payload.body,
        url=payload.url,
        occurred_at=payload.occurred_at or models.utcnow(),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _serialize_feedback(db, [item])[0]


@router.patch("/feedback/{feedback_id}", response_model=schemas.FeedbackOut)
def update_feedback(
    feedback_id: int, payload: schemas.FeedbackUpdate, db: Session = Depends(get_db)
):
    item = db.get(models.Feedback, feedback_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Feedback not found")

    fields = payload.model_dump(exclude_unset=True)
    if fields.get("person_id") is not None:
        _get_person_or_404(db, fields["person_id"])
    if fields.get("project_id") is not None:
        _get_project_or_404(db, fields["project_id"])

    for field, value in fields.items():
        setattr(item, field, value)
    if "status" in fields:
        item.resolved_at = None if fields["status"] == "open" else models.utcnow()

    db.commit()
    db.refresh(item)
    return _serialize_feedback(db, [item])[0]


@router.delete("/feedback/{feedback_id}", status_code=204)
def delete_feedback(feedback_id: int, db: Session = Depends(get_db)):
    """Only ever used for something entered by hand. A git-sourced row is
    mirrored from GitHub, so deleting it here would just come back on the
    next sync -- mark it declined instead."""
    item = db.get(models.Feedback, feedback_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Feedback not found")
    if item.source != "manual":
        raise HTTPException(
            status_code=409,
            detail=(
                "This came from GitHub and would return on the next sync. "
                "Mark it declined instead."
            ),
        )
    db.delete(item)
    db.commit()


# --- Per-project rollup ------------------------------------------------------


@router.get("/projects/{project_id}/collaboration")
def project_collaboration(project_id: int, db: Session = Depends(get_db)):
    """Everything about who worked on this project, in one call.

    The share digest and the project's Team tab both need exactly this, and
    assembling it once here keeps them from drifting apart.
    """
    _get_project_or_404(db, project_id)

    members = list(
        db.execute(
            select(models.ProjectMember)
            .where(models.ProjectMember.project_id == project_id)
            .order_by(models.ProjectMember.added_at)
        ).scalars()
    )

    by_person = db.execute(
        select(
            models.ActivityEvent.actor,
            models.ActivityEvent.person_id,
            func.count(),
            func.max(models.ActivityEvent.occurred_at),
        )
        .where(models.ActivityEvent.project_id == project_id)
        .group_by(models.ActivityEvent.actor, models.ActivityEvent.person_id)
        .order_by(func.count().desc())
    ).all()

    names = {
        p.id: p.name
        for p in db.execute(select(models.Person)).scalars()
    }

    feedback = list(
        db.execute(
            select(models.Feedback)
            .where(models.Feedback.project_id == project_id)
            .order_by(models.Feedback.occurred_at.desc())
            .limit(100)
        ).scalars()
    )

    return {
        "project_id": project_id,
        "members": _serialize_members(db, project_id, members),
        "contributors": [
            {
                "login": actor,
                "person_id": person_id,
                "name": names.get(person_id),
                "events": count,
                "last_seen": newest,
            }
            for actor, person_id, count, newest in by_person
            if actor or person_id
        ],
        "feedback": _serialize_feedback(db, feedback),
        "feedback_open": sum(1 for f in feedback if f.status == "open"),
        "generated_at": datetime.now(),
    }


# --- The shareable snapshot --------------------------------------------------


@router.get("/projects/{project_id}/share", response_class=HTMLResponse)
def share_project(project_id: int, db: Session = Depends(get_db)):
    """A read-only progress snapshot, as one self-contained HTML file.

    Not a link anyone can hit: this server is bound to localhost, so what
    this really produces is a file to save and send. `Content-Disposition` is
    `inline` so clicking it in the app opens it rather than downloading --
    the browser's own Save is the better download button.

    See `app/share.py` for what is deliberately left out.
    """
    project = _get_project_or_404(db, project_id)
    return HTMLResponse(
        content=share.render(db, project),
        headers={
            "Content-Disposition": (
                f'inline; filename="{project.name[:60].replace(chr(34), "")}'
                f'-progress-{date.today().isoformat()}.html"'
            )
        },
    )
