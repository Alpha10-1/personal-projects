"""Dashboards and reports, linked to the work they report on.

Two halves that meet in one table. You can paste a link today with nothing
configured; if the Power BI Service is connected, a sync fills in the
workspace, dataset and refresh state on the rows it recognises rather than
creating duplicates beside them.
"""

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, powerbi, schemas
from app.db import get_db

router = APIRouter(tags=["dashboards"])

# A Power BI report URL carries its own id. Pulling it out is what lets a
# pasted link and a synced report turn out to be the same row.
REPORT_ID = re.compile(
    r"/reports/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


def report_id_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    match = REPORT_ID.search(url)
    return match.group(1).lower() if match else None


class DashboardCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    url: Optional[str] = None
    note: Optional[str] = None
    project_id: Optional[int] = None


class DashboardUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    url: Optional[str] = None
    note: Optional[str] = None
    project_id: Optional[int] = None


def _get_or_404(db: Session, dashboard_id: int) -> models.Dashboard:
    row = db.get(models.Dashboard, dashboard_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return row


def _serialize(db: Session, rows: list[models.Dashboard]) -> list[schemas.DashboardOut]:
    names = {}
    project_ids = {r.project_id for r in rows if r.project_id}
    if project_ids:
        names = dict(
            db.execute(
                select(models.Project.id, models.Project.name).where(
                    models.Project.id.in_(project_ids)
                )
            ).all()
        )
    out = []
    for row in rows:
        item = schemas.DashboardOut.model_validate(row)
        item.project_name = names.get(row.project_id)
        out.append(item)
    return out


@router.get("/dashboards", response_model=list[schemas.DashboardOut])
def list_dashboards(
    db: Session = Depends(get_db),
    project_id: Optional[int] = None,
    unlinked_only: bool = False,
    limit: int = Query(200, ge=1, le=500),
):
    stmt = select(models.Dashboard)
    if project_id is not None:
        stmt = stmt.where(models.Dashboard.project_id == project_id)
    if unlinked_only:
        stmt = stmt.where(models.Dashboard.project_id.is_(None))
    rows = list(
        db.execute(stmt.order_by(models.Dashboard.name).limit(limit)).scalars()
    )
    return _serialize(db, rows)


@router.post("/dashboards", response_model=schemas.DashboardOut, status_code=201)
def create_dashboard(payload: DashboardCreate, db: Session = Depends(get_db)):
    """Paste a link. Needs nothing configured.

    If the URL is a Power BI report, its id is recorded now so a later sync
    recognises this row instead of adding a second one for the same report.
    """
    if payload.project_id is not None and db.get(models.Project, payload.project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")

    external_id = report_id_from_url(payload.url)
    if external_id:
        existing = db.execute(
            select(models.Dashboard).where(models.Dashboard.external_id == external_id)
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=f"That report is already here as '{existing.name}'.",
            )

    row = models.Dashboard(
        name=payload.name,
        url=payload.url,
        note=payload.note,
        project_id=payload.project_id,
        source="manual",
        external_id=external_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _serialize(db, [row])[0]


@router.patch("/dashboards/{dashboard_id}", response_model=schemas.DashboardOut)
def update_dashboard(
    dashboard_id: int, payload: DashboardUpdate, db: Session = Depends(get_db)
):
    row = _get_or_404(db, dashboard_id)
    fields = payload.model_dump(exclude_unset=True)
    if fields.get("project_id") is not None:
        if db.get(models.Project, fields["project_id"]) is None:
            raise HTTPException(status_code=404, detail="Project not found")
    for field, value in fields.items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return _serialize(db, [row])[0]


@router.delete("/dashboards/{dashboard_id}", status_code=204)
def delete_dashboard(dashboard_id: int, db: Session = Depends(get_db)):
    """Removes the row here. It does not touch anything in Power BI.

    A synced row will come back on the next sync, which is correct: this
    tracker mirrors the Service, it does not govern it.
    """
    row = _get_or_404(db, dashboard_id)
    db.delete(row)
    db.commit()


# --- The Power BI Service ----------------------------------------------------


@router.get("/powerbi/status")
def powerbi_status():
    """Whether the Service is connected. Not behind the guard: the UI calls
    this to decide whether to offer a sync at all."""
    return powerbi.status()


@router.post("/powerbi/sync")
def sync_powerbi(
    db: Session = Depends(get_db),
    workspace: Optional[str] = None,
):
    """Mirror reports and their refresh state from the Power BI Service.

    Idempotent on the report id. An existing row keeps its project link and
    its note -- those are yours -- while the name, workspace, dataset and
    refresh state are overwritten, because those are the Service's to state.
    """
    if not powerbi.is_configured():
        raise HTTPException(status_code=503, detail=powerbi.status()["reason"])

    try:
        reports = powerbi.fetch_reports(workspace)
    except powerbi.NotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except powerbi.PowerBIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    existing = {
        row.external_id: row
        for row in db.execute(
            select(models.Dashboard).where(models.Dashboard.external_id.is_not(None))
        ).scalars()
    }

    added = adopted = updated = 0
    for report in reports:
        external_id = (report.get("external_id") or "").lower()
        if not external_id:
            continue
        row = existing.get(external_id)
        if row is None:
            row = models.Dashboard(external_id=external_id, source="powerbi")
            db.add(row)
            added += 1
        else:
            # A link someone pasted, now recognised. It keeps its project and
            # note; only its provenance changes.
            if row.source == "manual":
                adopted += 1
            else:
                updated += 1
            row.source = "powerbi"

        row.name = report.get("name") or row.name or "Untitled report"
        row.url = report.get("url") or row.url
        row.workspace_id = report.get("workspace_id")
        row.workspace_name = report.get("workspace_name")
        row.dataset_id = report.get("dataset_id")
        row.dataset_name = report.get("dataset_name")
        row.last_refresh_at = report.get("last_refresh_at")
        row.refresh_status = report.get("refresh_status")
        row.refresh_error = report.get("refresh_error")

    db.commit()
    return {
        "reports_seen": len(reports),
        "added": added,
        "adopted": adopted,
        "updated": updated,
        "failing": sum(1 for r in reports if (r.get("refresh_status") or "") == "Failed"),
    }
