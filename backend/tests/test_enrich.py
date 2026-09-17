"""Unit tests for the derived values in app.enrich.

compute_progress is a pure function with a documented precedence order, so it
is tested directly rather than through a route.
"""

import pytest
from conftest import TODAY, at

from app import models
from app.enrich import compute_progress, enrich_tasks


def project(**kw):
    return models.Project(name="P", **kw)


def test_manual_override_beats_task_counts():
    assert compute_progress(project(progress_override=10), 4, 4, 0, 0) == 10


def test_override_of_zero_is_respected():
    """0 is a real override; only None means 'not set'."""
    assert compute_progress(project(progress_override=0), 4, 4, 0, 0) == 0


def test_tasks_drive_progress_when_no_override():
    assert compute_progress(project(), 4, 1, 0, 0) == 25


def test_milestones_are_the_fallback_when_there_are_no_tasks():
    assert compute_progress(project(), 0, 0, 4, 3) == 75


def test_status_done_reads_as_complete_with_nothing_to_count():
    assert compute_progress(project(status="done"), 0, 0, 0, 0) == 100


def test_empty_project_reads_as_zero():
    assert compute_progress(project(status="active"), 0, 0, 0, 0) == 0


@pytest.mark.parametrize(
    "total,done,expected",
    [(3, 1, 33), (3, 2, 67), (7, 1, 14)],
)
def test_task_progress_is_rounded(total, done, expected):
    assert compute_progress(project(), total, done, 0, 0) == expected


def test_enrich_tasks_attaches_hours_and_subtask_counts(db, make):
    parent = make.task(title="parent")
    make.task(title="child_done", parent_task_id=parent.id, status="done",
              completed_at=at(TODAY))
    make.task(title="child_open", parent_task_id=parent.id)
    make.log(work_date=TODAY, hours=1.5, task_id=parent.id)
    make.log(work_date=TODAY, hours=0.75, task_id=parent.id)

    (_, extra), = enrich_tasks(db, [parent])

    assert extra["hours_logged"] == 2.25
    assert extra["subtask_total"] == 2
    assert extra["subtask_done"] == 1
    assert extra["project_name"] is None


def test_enrich_tasks_on_empty_input_does_no_work(db):
    assert enrich_tasks(db, []) == []
