"""What the project already has, and recognising it said differently.

`inventory.py` can grep for `list_widgets` and be certain. A board cannot:
the same work is written down five ways, so the check here is vocabulary
overlap and it is tuned to over-match. These tests pin both ends of that
-- what must be recognised as the same work, and what must not be
collapsed into it.
"""

import pytest

from app import board


# --- recognising the same work said differently --------------------------


@pytest.mark.parametrize(
    "a,b",
    [
        ("Add rate limiting to the API", "Rate limit the API"),
        ("Paginate the activity feed", "Add pagination to the activity feed"),
        ("Write the deployment runbook", "Deployment runbook"),
        ("Set up CI", "Set up CI for the backend"),
    ],
)
def test_the_same_work_is_recognised(a, b):
    assert board.overlaps(a, b) is True
    assert board.overlaps(b, a) is True


@pytest.mark.parametrize(
    "a,b",
    [
        ("Add rate limiting", "Add caching"),
        ("Write the deployment guide", "Rate limit the API"),
        ("Paginate the activity feed", "Paginate the task list"),
        ("Add a settings page", "Add a login page"),
    ],
)
def test_different_work_is_left_alone(a, b):
    assert board.overlaps(a, b) is False


def test_a_longer_restatement_still_counts_as_the_same_thing():
    """Measured against the shorter title, so adding detail to a task does
    not make it a different task."""
    assert board.overlaps(
        "Rate limit the API", "Add rate limiting to every API endpoint we expose"
    )


def test_the_verbs_every_task_starts_with_carry_no_weight():
    assert board.significant("Add the new thing") == {"thing"}
    assert board.overlaps("Add a cache", "Build a cache")


def test_synonyms_are_not_caught_and_that_is_known():
    """The honest limit of a bag-of-words check. Catching this is the job
    of the repository search in `roadmap.sift`, which is why there are two
    checks and not one."""
    assert board.overlaps("Back up the database", "Database dump script") is False


def test_light_inflections_are_caught():
    assert board.overlaps("Release the beta", "Beta released")
    assert board.overlaps("Add rate limiting", "Rate limit the requests")


def test_a_two_letter_acronym_is_the_whole_content_of_a_title():
    assert board.significant("Set up CI") == {"ci"}
    assert board.overlaps("Set up CI", "Set up CI for the backend")


def test_a_short_word_is_not_stemmed_into_nothing():
    assert board.stem("thing") == "thing"
    assert board.stem("limiting") == "limit"
    assert board.stem("released") == "releas"


def test_an_empty_title_matches_nothing():
    assert board.overlaps("", "Anything") is False
    assert board.overlaps("the and of", "the and of") is False


# --- the snapshot ---------------------------------------------------------


@pytest.fixture
def project(make):
    p = make.project("Demo", summary="A tracker.", objective="Ship it.")
    first = make.milestone(p, "Design agreed", status="done")
    second = make.milestone(p, "Beta released")
    make.task("Draw the wireframes", project_id=p.id, milestone_id=first.id, status="done")
    make.task("Build the API", project_id=p.id, milestone_id=second.id, status="in_progress")
    make.task("Write the docs", project_id=p.id)
    return p


def test_it_reads_the_whole_board(db, project):
    snap = board.snapshot(db, project)
    assert snap["totals"] == {
        "milestones": 2,
        "milestones_done": 1,
        "tasks": 3,
        "tasks_done": 1,
        "tasks_truncated": 0,
    }
    assert snap["unassigned_tasks"] == 1


def test_it_counts_tasks_under_each_milestone(db, project):
    snap = board.snapshot(db, project)
    by_title = {m["title"]: m for m in snap["milestones"]}
    assert by_title["Design agreed"]["tasks"] == 1
    assert by_title["Design agreed"]["tasks_done"] == 1
    assert by_title["Beta released"]["tasks_done"] == 0


def test_finished_work_is_included_rather_than_filtered_out(db, project):
    """A task marked done still has to be visible, or it gets proposed
    again as though it had never happened."""
    titles = [t["title"] for t in board.snapshot(db, project)["tasks"]]
    assert "Draw the wireframes" in titles


def test_the_prompt_lists_every_task_rather_than_counting_them(db, project):
    text = board.as_prompt(board.snapshot(db, project))
    assert "Draw the wireframes" in text
    assert "Build the API" in text
    assert "do not propose any of these again" in text


def test_the_prompt_says_plainly_when_there_is_nothing(db, make):
    empty = make.project("Nothing yet")
    text = board.as_prompt(board.snapshot(db, empty))
    assert "this project has no milestones at all" in text
    assert "this project has no tasks at all" in text


def test_a_truncated_task_list_says_so(db, make, monkeypatch):
    monkeypatch.setattr(board, "MAX_TASKS", 2)
    p = make.project("Busy")
    for n in range(5):
        make.task(f"Task number {n}", project_id=p.id)
    snap = board.snapshot(db, p)
    assert snap["totals"]["tasks_truncated"] == 3
    assert "not listed" in board.as_prompt(snap)


def test_notes_come_through_so_decisions_are_not_re_proposed(db, project, make):
    from app import models

    db.add(
        models.Note(
            project_id=project.id,
            title="Decision: no auth",
            body="Single user, so no login.",
            kind="decision",
        )
    )
    db.commit()
    text = board.as_prompt(board.snapshot(db, project))
    assert "Decision: no auth" in text
    assert "Single user, so no login." in text


def test_a_very_long_note_is_truncated_visibly(db, project):
    from app import models

    db.add(
        models.Note(project_id=project.id, title="Long", body="x" * 5000, kind="note")
    )
    db.commit()
    text = board.as_prompt(board.snapshot(db, project), note_chars=100)
    assert "truncated" in text


# --- the coverage check ---------------------------------------------------


def test_something_already_a_task_is_reported_as_covered(db, project):
    snap = board.snapshot(db, project)
    assert "already" in board.covered(snap, "Build the API endpoints")


def test_something_already_done_says_it_is_done(db, project):
    snap = board.snapshot(db, project)
    assert "already done" in board.covered(snap, "Draw up the wireframes")


def test_something_already_a_milestone_is_reported(db, project):
    snap = board.snapshot(db, project)
    assert "milestone" in board.covered(snap, "Release the beta")


def test_genuinely_new_work_is_not_covered(db, project):
    assert board.covered(board.snapshot(db, project), "Add a backup script") is None


def test_the_kinds_checked_can_be_narrowed(db, project):
    """A proposed task should not be blocked by a milestone of the same
    name when the caller says to compare tasks only."""
    snap = board.snapshot(db, project)
    assert board.covered(snap, "Release the beta", ("task",)) is None
    assert board.covered(snap, "Release the beta", ("milestone",)) is not None
