"""Planning the next stretch of work on a project that already exists.

The arithmetic -- what the model is told, and how its hours become dates --
is tested here directly. What it makes of that is not testable and is not
tested; what *is* tested is that it is handed facts, told where each one came
from, and never asked for a date.
"""

import json
from datetime import date, datetime, timedelta

import pytest

from app import ai, planner, planner_prompts


def at(day, month=6, year=2026):
    return datetime(year, month, day, 12, 0)


def stats(files, additions=10, deletions=2):
    return json.dumps(
        {
            "files": [
                {"path": p, "status": "modified", "additions": 5, "deletions": 1}
                for p in files
            ],
            "additions": additions,
            "deletions": deletions,
        }
    )


@pytest.fixture
def project(db, make):
    row = make.project(name="Course finder", repo="me/courses", workspace="personal")
    make.event(
        repo="me/courses",
        external_id="c1",
        title="add the APS calculator",
        occurred_at=at(3, month=6),
        project_id=row.id,
        actor="me",
        raw=json.dumps({"sha": "c1"}),
        file_stats=stats(["src/utils/marksToAPS.js"]),
    )
    make.event(
        repo="me/courses",
        external_id="c2",
        title="tidy",
        occurred_at=at(9, month=8),
        project_id=row.id,
        actor="me",
        raw=json.dumps({"sha": "c2"}),
        file_stats=stats(["src/pages/Results.jsx"]),
    )
    return row


# --- What the model is given -------------------------------------------------


def test_the_readme_is_labelled_as_a_claim_not_as_evidence(db, project):
    """The README says what a project is meant to be; the commits say what it
    became. Conflating them is how a plan ends up written for the imaginary
    version of a project."""
    text = planner.as_text(planner.context(db, project, readme="# Course finder\nDoes X."))

    assert "what the project says about itself" in text.lower()
    assert "Does X." in text
    assert "what was actually done" in text.lower()


def test_a_missing_readme_says_so_rather_than_being_silent(db, project):
    text = planner.as_text(planner.context(db, project, readme=""))

    assert "There is none" in text
    assert "Do not assume what it would have said" in text


def test_work_already_planned_is_handed_over_so_it_is_not_planned_twice(
    db, project, make
):
    make.task(title="Write the APS tests", project_id=project.id, estimate_hours=3)
    make.task(title="Already finished", project_id=project.id, status="done")

    text = planner.as_text(planner.context(db, project))

    assert "do not plan it again" in text.lower()
    assert "Write the APS tests" in text
    assert "[3.0h]" in text or "[3h]" in text
    # Done work is not "already planned" -- offering to redo it would be wrong.
    assert "Already finished" not in text


def test_an_empty_board_is_stated_as_empty(db, project):
    text = planner.as_text(planner.context(db, project))

    assert "Nothing. The board is empty." in text


def test_uncalibrated_estimates_are_admitted_as_uncalibrated(db, project):
    """With nothing ever measured, the model must not imply its numbers are
    grounded in this person's history."""
    text = planner.as_text(planner.context(db, project))

    assert "Unknown: no finished task" in text
    assert "unvalidated" in text


def test_estimates_are_calibrated_once_there_is_something_to_compare(db, project, make):
    task = make.task(
        title="Ship it", project_id=project.id, status="done", estimate_hours=2.0
    )
    make.log(hours=3.0, task_id=task.id, project_id=project.id)

    calibration = planner.estimate_calibration(db)

    assert calibration["tasks"] == 1
    assert calibration["median_ratio"] == 1.5
    assert "median of 1.5x the estimate" in planner.as_text(planner.context(db, project))


def test_an_estimate_with_no_logged_time_cannot_calibrate_anything(db, project, make):
    make.task(title="Ship it", project_id=project.id, status="done", estimate_hours=2.0)

    assert planner.estimate_calibration(db) is None


# --- How it actually gets worked on -----------------------------------------


def test_cadence_reports_the_shape_of_the_work(db, project):
    from app import history

    pace = planner.cadence(history.timeline(db, project))

    assert pace["months_active"] == 2
    assert pace["commits_per_active_month"] == 1.0
    assert pace["busiest_month"]["commits"] == 1


def test_a_repo_untouched_for_months_is_called_dormant(db, project):
    from app import history

    pace = planner.cadence(history.timeline(db, project))

    # The fixture's last commit is in August 2026; "today" in tests is later.
    assert pace["days_since_last_commit"] > 0
    assert pace["dormant"] is (pace["days_since_last_commit"] > planner.DORMANT_DAYS)


# --- Hours into dates --------------------------------------------------------

OPTION = {
    "title": "Harden the core",
    "milestones": [{"title": "Tests exist"}, {"title": "Data checked"}],
    "tasks": [
        {"title": "Unit tests for APS", "estimate_hours": 3, "milestone": "Tests exist"},
        {"title": "Unit tests for matching", "estimate_hours": 3, "milestone": "Tests exist"},
        {"title": "Spot-check the data", "estimate_hours": 4, "milestone": "Data checked"},
    ],
}


def test_milestones_are_dated_cumulatively_not_all_on_the_same_day():
    """Each milestone waits for the work in front of it, which is the whole
    difference between a schedule and a wish."""
    dated = planner.schedule(OPTION, hours_per_week=10, start=date(2026, 1, 1))

    first, second = dated["milestones"]
    assert first["estimated_hours"] == 6.0
    assert second["estimated_hours"] == 4.0
    assert second["cumulative_hours"] == 10.0
    assert first["due_date"] < second["due_date"]
    assert dated["total_hours"] == 10.0


def test_the_same_plan_at_a_different_pace_is_arithmetic_not_another_call():
    slow = planner.schedule(OPTION, hours_per_week=5, start=date(2026, 1, 1))
    fast = planner.schedule(OPTION, hours_per_week=20, start=date(2026, 1, 1))

    assert slow["total_hours"] == fast["total_hours"] == 10.0
    assert slow["finishes"] > fast["finishes"]
    assert fast["finishes"] == "2026-01-05"  # half a week
    assert slow["finishes"] == "2026-01-15"  # two weeks


def test_work_belonging_to_no_milestone_is_still_paid_for():
    """Otherwise the last milestone's date is a lie: the hours exist whether
    or not the model filed them under something."""
    option = {
        "milestones": [{"title": "A"}, {"title": "B"}],
        "tasks": [
            {"title": "assigned", "estimate_hours": 4, "milestone": "A"},
            {"title": "loose", "estimate_hours": 6},
        ],
    }

    dated = planner.schedule(option, hours_per_week=10, start=date(2026, 1, 1))

    assert dated["total_hours"] == 10.0
    assert dated["milestones"][0]["estimated_hours"] == 7.0  # 4 + half of 6
    assert dated["milestones"][1]["estimated_hours"] == 3.0
    assert dated["milestones"][-1]["cumulative_hours"] == 10.0


def test_a_plan_with_no_milestones_still_says_when_it_finishes():
    dated = planner.schedule(
        {"tasks": [{"title": "one thing", "estimate_hours": 20}]},
        hours_per_week=10,
        start=date(2026, 1, 1),
    )

    assert dated["milestones"] == []
    assert dated["total_hours"] == 20.0
    assert dated["finishes"] == "2026-01-15"


def test_an_absurd_pace_cannot_divide_by_zero():
    dated = planner.schedule(OPTION, hours_per_week=0.0001, start=date(2026, 1, 1))

    assert dated["hours_per_week"] == 0.5


# --- The prompt --------------------------------------------------------------


def test_the_model_is_told_not_to_invent_dates():
    """Dates depend on how much time the person has, which the model has no
    way of knowing -- so it estimates effort and the code does the division."""
    assert "Do not put dates on anything" in planner_prompts.PLAN_SYSTEM


def test_research_is_ranked_below_the_commit_history():
    assert "the history wins" in planner_prompts.PLAN_SYSTEM


@pytest.mark.anyio
async def test_research_is_passed_through_as_opinion_rather_than_fact(monkeypatch):
    captured = {}

    async def fake_structured(**kwargs):
        captured.update(kwargs)
        return {"reading": "ok", "options": []}

    monkeypatch.setattr(ai, "structured", fake_structured)

    await planner_prompts.plan("CONTEXT", research_text="Use gitignore.")

    assert "opinion, not fact" in captured["prompt"]
    assert "Use gitignore." in captured["prompt"]


# --- The routes --------------------------------------------------------------


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")


PLAN_RESULT = {
    "reading": "A working app with no tests.",
    "options": [
        {
            "title": "Harden the core",
            "what_it_is_for": "Make the main flow provably correct.",
            "why_this_project_needs_it": "marksToAPS.js changed in 15 commits, no test file.",
            "confidence": "high",
            **OPTION,
        }
    ],
    "recommended": "Harden the core",
}


def test_planning_writes_nothing(client, project, monkeypatch, configured):
    async def fake_plan(*a, **kw):
        return PLAN_RESULT

    monkeypatch.setattr(planner_prompts, "plan", fake_plan)
    monkeypatch.setattr("app.github.fetch_readme", lambda repo, max_chars=8000: "# Hi")

    body = client.post(f"/ai/projects/{project.id}/plan", json={}).json()

    assert body["options"][0]["total_hours"] == 10.0
    assert client.get(f"/tasks?project_id={project.id}").json() == []
    assert client.get(f"/projects/{project.id}/milestones").json() == []


def test_the_reply_says_what_the_plan_was_built_from(client, project, monkeypatch, configured):
    """So a thin plan can be recognised as a thin input rather than a bad model."""

    async def fake_plan(*a, **kw):
        return PLAN_RESULT

    monkeypatch.setattr(planner_prompts, "plan", fake_plan)
    monkeypatch.setattr("app.github.fetch_readme", lambda repo, max_chars=8000: "# Hi")

    body = client.post(f"/ai/projects/{project.id}/plan", json={}).json()

    assert body["grounded_in"] == {
        "readme": True,
        "commits": 2,
        "commits_detailed": 2,
        "open_tasks": 0,
        "notes": 0,
        "estimates_calibrated": False,
    }


def test_research_is_off_unless_it_is_asked_for(client, project, monkeypatch, configured):
    """It costs more and sends queries about this project to a search engine,
    so it is never the default. The conftest guard fails the test if the
    research path is reached at all."""

    async def fake_plan(*a, **kw):
        return PLAN_RESULT

    monkeypatch.setattr(planner_prompts, "plan", fake_plan)
    monkeypatch.setattr("app.github.fetch_readme", lambda repo, max_chars=8000: "")

    body = client.post(f"/ai/projects/{project.id}/plan", json={}).json()

    assert body["research"] is None


def test_a_failed_search_costs_the_plan_a_section_not_the_whole_call(
    client, project, monkeypatch, configured
):
    async def fake_plan(*a, **kw):
        return PLAN_RESULT

    async def failing_research(*a, **kw):
        raise ai.AIFailed("Rate limited by the API. Try again in a moment.")

    monkeypatch.setattr(planner_prompts, "plan", fake_plan)
    monkeypatch.setattr(ai, "research", failing_research)
    monkeypatch.setattr("app.github.fetch_readme", lambda repo, max_chars=8000: "")

    response = client.post(f"/ai/projects/{project.id}/plan", json={"research": True})

    assert response.status_code == 200
    assert response.json()["research"]["error"].startswith("Rate limited")
    assert response.json()["options"]


def test_a_readme_that_cannot_be_fetched_does_not_take_the_plan_down(
    client, project, monkeypatch, configured
):
    def broken(repo, max_chars=8000):
        raise RuntimeError("GitHub said no")

    async def fake_plan(*a, **kw):
        return PLAN_RESULT

    monkeypatch.setattr(planner_prompts, "plan", fake_plan)
    monkeypatch.setattr("app.github.fetch_readme", broken)

    body = client.post(f"/ai/projects/{project.id}/plan", json={}).json()

    assert body["grounded_in"]["readme"] is False
    assert body["options"]


def test_applying_an_option_creates_its_milestones_and_tasks(client, project):
    """No model call: it builds exactly what was on screen, including the rows
    that were removed from it."""
    option = {**OPTION, "title": "Harden the core"}

    body = client.post(
        f"/ai/projects/{project.id}/plan/apply",
        json={"option": option, "hours_per_week": 10},
    ).json()

    assert body["tasks_created"] == 3
    assert body["milestones_created"] == 2
    assert body["total_hours"] == 10.0

    tasks = client.get(f"/tasks?project_id={project.id}").json()
    assert {t["title"] for t in tasks} == {
        "Unit tests for APS",
        "Unit tests for matching",
        "Spot-check the data",
    }
    assert all(t["source"] == "agent" for t in tasks)
    assert sorted(t["estimate_hours"] for t in tasks) == [3.0, 3.0, 4.0]


def test_applied_milestones_carry_the_computed_dates(client, project):
    client.post(
        f"/ai/projects/{project.id}/plan/apply",
        json={"option": OPTION, "hours_per_week": 10},
    )

    milestones = client.get(f"/projects/{project.id}/milestones").json()
    dates = [m["due_date"] for m in milestones]
    assert all(dates)
    assert dates[0] < dates[1]


def test_dates_can_be_declined(client, project):
    client.post(
        f"/ai/projects/{project.id}/plan/apply",
        json={"option": OPTION, "set_target_dates": False},
    )

    milestones = client.get(f"/projects/{project.id}/milestones").json()
    assert [m["due_date"] for m in milestones] == [None, None]


def test_a_task_naming_a_milestone_that_is_not_there_is_kept_anyway(client, project):
    """Losing someone's work to a typo in the model's own output is the worst
    failure available here."""
    option = {
        "title": "x",
        "milestones": [{"title": "Real"}],
        "tasks": [{"title": "orphan", "estimate_hours": 1, "milestone": "Imaginary"}],
    }

    client.post(f"/ai/projects/{project.id}/plan/apply", json={"option": option})

    tasks = client.get(f"/tasks?project_id={project.id}").json()
    assert [t["title"] for t in tasks] == ["orphan"]
    assert tasks[0]["milestone_id"] is None


def test_applying_adds_to_the_project_and_rewrites_nothing(client, project, make):
    make.task(title="mine", project_id=project.id, status="in_progress")
    before = client.get(f"/projects/{project.id}").json()

    client.post(f"/ai/projects/{project.id}/plan/apply", json={"option": OPTION})

    after = client.get(f"/projects/{project.id}").json()
    assert after["summary"] == before["summary"]
    assert after["status"] == before["status"]
    titles = {t["title"] for t in client.get(f"/tasks?project_id={project.id}").json()}
    assert "mine" in titles


def test_new_milestones_are_ordered_after_the_ones_already_there(client, project, make):
    make.milestone(project, title="Existing", position=0)

    client.post(f"/ai/projects/{project.id}/plan/apply", json={"option": OPTION})

    titles = [m["title"] for m in client.get(f"/projects/{project.id}/milestones").json()]
    assert titles == ["Existing", "Tests exist", "Data checked"]


def test_an_empty_option_is_refused(client, project):
    response = client.post(
        f"/ai/projects/{project.id}/plan/apply", json={"option": {"title": "nothing"}}
    )

    assert response.status_code == 400
    assert "nothing in it" in response.json()["detail"]


def test_planning_an_unknown_project_is_a_404(client, configured):
    assert client.post("/ai/projects/999/plan", json={}).status_code == 404


def test_planning_needs_a_key(client, project, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    response = client.post(f"/ai/projects/{project.id}/plan", json={})

    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_applying_needs_no_key_because_it_calls_no_model(client, project, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    response = client.post(
        f"/ai/projects/{project.id}/plan/apply", json={"option": OPTION}
    )

    assert response.status_code == 200


def test_a_project_with_no_repo_can_still_be_planned(client, db, make, monkeypatch, configured):
    """Most of the evidence is the commit history, but a project that is only
    tasks and notes is still a project."""
    row = make.project(name="No repo")

    async def fake_plan(*a, **kw):
        return PLAN_RESULT

    monkeypatch.setattr(planner_prompts, "plan", fake_plan)

    body = client.post(f"/ai/projects/{row.id}/plan", json={}).json()

    assert body["grounded_in"]["commits"] == 0
    assert body["options"]


def test_the_context_says_plainly_when_there_are_no_commits(db, make):
    row = make.project(name="No repo")

    text = planner.as_text(planner.context(db, row))

    assert "No commits are recorded" in text
    assert "nothing here is known about what has been built" in text


def test_the_schedule_starts_today_by_default(client, project):
    body = client.post(
        f"/ai/projects/{project.id}/plan/apply",
        json={"option": OPTION, "hours_per_week": 10},
    ).json()

    assert body["finishes"] == (date.today() + timedelta(days=7)).isoformat()
