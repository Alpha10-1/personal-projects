"""The analyst's findings and suggestions.

Every rule here is deterministic, so these tests pin the exact conditions
under which each one fires -- and, just as importantly, when it stays quiet.
A rule that fires on healthy work is worse than no rule: it trains you to
ignore the review.
"""

import json

from conftest import TODAY, at, days_ago, days_ahead

from app import models, review

REPO = "owner/name"


def rules(findings):
    return {f.rule for f in findings}


def by_rule(findings, rule):
    return [f for f in findings if f.rule == rule]


def add_event(db, task=None, project=None, kind="commit", when=None, raw=None,
              title="some commit", external_id=None):
    event = models.ActivityEvent(
        provider="github",
        external_id=external_id or f"github:{kind}:{title}:{task.id if task else 0}",
        kind=kind,
        repo=REPO,
        title=title,
        url=f"https://github.com/{REPO}/x",
        occurred_at=when or at(TODAY),
        raw=json.dumps(raw or {}),
        project_id=project.id if project else None,
        task_id=task.id if task else None,
        linked_by="convention" if task else "repo",
    )
    db.add(event)
    db.commit()
    return event


# --- Healthy work stays quiet ------------------------------------------------


def test_an_empty_board_has_nothing_to_say(db):
    assert review.find(db) == []


def test_recent_work_in_progress_is_not_flagged(db, make):
    task = make.task(title="Backtest", status="in_progress")
    make.log(work_date=TODAY, hours=2, task_id=task.id)

    assert "stale_in_progress" not in rules(review.find(db))


def test_a_task_due_in_the_future_is_not_overdue(db, make):
    make.task(title="Later", due_date=days_ahead(3))

    assert "overdue_task" not in rules(review.find(db))


def test_done_work_is_left_alone(db, make):
    make.task(
        title="Finished", status="done", due_date=days_ago(30), completed_at=at(TODAY)
    )

    assert review.find(db) == []


# --- stale_in_progress -------------------------------------------------------


def test_in_progress_with_nothing_recorded_is_flagged(db, make):
    make.task(title="Backtest", status="in_progress")

    finding = by_rule(review.find(db), "stale_in_progress")[0]

    assert "Backtest" in finding.title
    assert "never" in finding.detail or "ever" in finding.detail


def test_in_progress_goes_stale_after_the_window(db, make):
    task = make.task(title="Backtest", status="in_progress")
    make.log(work_date=days_ago(10), hours=2, task_id=task.id)

    assert "stale_in_progress" in rules(review.find(db, stale_days=7))
    assert "stale_in_progress" not in rules(review.find(db, stale_days=30))


def test_a_commit_counts_as_a_sign_of_life(db, make):
    """Either an hour logged or a commit keeps a task off the stale list."""
    task = make.task(title="Backtest", status="in_progress")
    add_event(db, task=task, when=at(days_ago(1)))

    assert "stale_in_progress" not in rules(review.find(db))


def test_only_in_progress_work_can_go_stale(db, make):
    make.task(title="Not started", status="todo")

    assert "stale_in_progress" not in rules(review.find(db))


# --- stale_blocker -----------------------------------------------------------


def test_a_long_standing_blocker_is_flagged(db, make):
    task = make.task(
        title="Get SharePoint access",
        status="blocked",
        blocked_reason="waiting on IT",
    )
    task.updated_at = at(days_ago(9))
    db.commit()

    finding = by_rule(review.find(db), "stale_blocker")[0]

    assert "9 days" in finding.title
    assert finding.detail == "waiting on IT"


def test_a_fresh_blocker_is_not_flagged(db, make):
    make.task(title="Blocked today", status="blocked", blocked_reason="waiting")

    assert "stale_blocker" not in rules(review.find(db))


# --- overdue and overrun -----------------------------------------------------


def test_overdue_tasks_report_how_late_they_are(db, make):
    make.task(title="Write the script", due_date=days_ago(4))

    finding = by_rule(review.find(db), "overdue_task")[0]

    assert "4 days overdue" in finding.title


def test_running_past_the_estimate_is_reported(db, make):
    task = make.task(title="Backtest", estimate_hours=4)
    make.log(work_date=TODAY, hours=7, task_id=task.id)

    finding = by_rule(review.find(db), "estimate_overrun")[0]

    assert "7h logged against an estimate of 4h" in finding.detail


def test_being_a_little_over_the_estimate_is_not_reported(db, make):
    task = make.task(title="Backtest", estimate_hours=4)
    make.log(work_date=TODAY, hours=5, task_id=task.id)

    assert "estimate_overrun" not in rules(review.find(db))


# --- projects and activity ---------------------------------------------------


def test_a_project_past_its_target_is_flagged(db, make):
    make.project(name="Forecast", status="active", target_date=days_ago(3))

    finding = by_rule(review.find(db), "project_past_target")[0]

    assert "Forecast is past its target date" == finding.title


def test_an_archived_project_past_target_is_not_flagged(db, make):
    make.project(
        name="Old", status="active", target_date=days_ago(3), archived_at=at(days_ago(1))
    )

    assert "project_past_target" not in rules(review.find(db))


def test_activity_matching_no_project_is_surfaced_once(db):
    add_event(db, external_id="a")
    add_event(db, external_id="b")

    findings = by_rule(review.find(db), "unlinked_activity")

    assert len(findings) == 1
    assert "2 activity events" in findings[0].title


def test_findings_put_warnings_before_notes(db, make):
    task = make.task(title="Backtest", estimate_hours=1)
    make.log(work_date=TODAY, hours=5, task_id=task.id)
    make.task(title="Late", due_date=days_ago(2))

    severities = [f.severity for f in review.find(db)]

    assert severities == sorted(severities, key=lambda s: {"warn": 0, "info": 1}[s])


# --- Suggestions -------------------------------------------------------------


def test_commits_against_an_unstarted_task_suggest_starting_it(db, make):
    project = make.project(name="Tracker", repo=REPO)
    task = make.task(title="Tidy the loader", project_id=project.id, status="todo")
    add_event(db, task=task, project=project, title="Task: 1 tidy")

    created = review.propose(db)

    assert len(created) == 1
    suggestion = created[0]
    assert suggestion.rule == "activity_suggests_started"
    assert suggestion.target_id == task.id
    assert suggestion.current_value == "todo"
    assert suggestion.proposed_value == "in_progress"
    assert "Task: 1 tidy" in json.loads(suggestion.evidence)[0]


def test_a_merged_pull_request_suggests_the_task_is_done(db, make):
    project = make.project(name="Tracker", repo=REPO)
    task = make.task(title="Add WAL", project_id=project.id, status="in_progress")
    add_event(
        db, task=task, project=project, kind="pull_request",
        title="Add WAL mode", raw={"merged_at": "2026-09-16T10:00:00Z"},
    )

    created = review.propose(db)

    assert [s.rule for s in created] == ["merged_pr_suggests_done"]
    assert created[0].proposed_value == "done"


def test_an_unmerged_pull_request_suggests_nothing(db, make):
    project = make.project(name="Tracker", repo=REPO)
    task = make.task(title="Add WAL", project_id=project.id, status="in_progress")
    add_event(
        db, task=task, project=project, kind="pull_request",
        title="Add WAL mode", raw={"merged_at": None},
    )

    assert review.propose(db) == []


def test_a_task_with_no_activity_gets_no_suggestion(db, make):
    make.task(title="Untouched", status="todo")

    assert review.propose(db) == []


def test_proposing_twice_raises_each_suggestion_once(db, make):
    project = make.project(name="Tracker", repo=REPO)
    task = make.task(title="Tidy", project_id=project.id, status="todo")
    add_event(db, task=task, project=project)

    first = review.propose(db)
    second = review.propose(db)

    assert len(first) == 1
    assert second == []
    assert db.query(models.Suggestion).count() == 1


def test_a_dismissed_suggestion_is_never_raised_again(db, make, client):
    project = make.project(name="Tracker", repo=REPO)
    task = make.task(title="Tidy", project_id=project.id, status="todo")
    add_event(db, task=task, project=project)
    suggestion = review.propose(db)[0]

    client.post(f"/suggestions/{suggestion.id}/dismiss")

    assert review.propose(db) == []
    assert db.query(models.Suggestion).count() == 1


# --- Accepting ---------------------------------------------------------------


def test_accepting_applies_the_change(client, db, make):
    project = make.project(name="Tracker", repo=REPO)
    task = make.task(title="Tidy", project_id=project.id, status="todo")
    add_event(db, task=task, project=project)
    suggestion = review.propose(db)[0]

    body = client.post(f"/suggestions/{suggestion.id}/accept").json()

    assert body["status"] == "accepted"
    assert body["resolved_at"] is not None
    assert client.get(f"/tasks/{task.id}").json()["status"] == "in_progress"


def test_accepting_a_done_suggestion_stamps_completion(client, db, make):
    """An accepted suggestion goes through the same transition as a manual
    change, so cycle time still sees it."""
    project = make.project(name="Tracker", repo=REPO)
    task = make.task(title="Add WAL", project_id=project.id, status="in_progress")
    add_event(
        db, task=task, project=project, kind="pull_request",
        raw={"merged_at": "2026-09-16T10:00:00Z"},
    )
    suggestion = review.propose(db)[0]

    client.post(f"/suggestions/{suggestion.id}/accept")

    assert client.get(f"/tasks/{task.id}").json()["completed_at"] is not None


def test_accepting_clears_a_stale_blocked_reason(client, db, make):
    project = make.project(name="Tracker", repo=REPO)
    task = make.task(
        title="Tidy", project_id=project.id, status="blocked",
        blocked_reason="waiting on IT",
    )
    add_event(
        db, task=task, project=project, kind="pull_request",
        raw={"merged_at": "2026-09-16T10:00:00Z"},
    )
    suggestion = review.propose(db)[0]

    client.post(f"/suggestions/{suggestion.id}/accept")

    assert client.get(f"/tasks/{task.id}").json()["blocked_reason"] is None


def test_a_suggestion_cannot_be_resolved_twice(client, db, make):
    project = make.project(name="Tracker", repo=REPO)
    task = make.task(title="Tidy", project_id=project.id, status="todo")
    add_event(db, task=task, project=project)
    suggestion = review.propose(db)[0]
    client.post(f"/suggestions/{suggestion.id}/accept")

    again = client.post(f"/suggestions/{suggestion.id}/accept")

    assert again.status_code == 409
    assert "already accepted" in again.json()["detail"]


def test_dismissing_changes_nothing_on_the_task(client, db, make):
    project = make.project(name="Tracker", repo=REPO)
    task = make.task(title="Tidy", project_id=project.id, status="todo")
    add_event(db, task=task, project=project)
    suggestion = review.propose(db)[0]

    client.post(f"/suggestions/{suggestion.id}/dismiss")

    assert client.get(f"/tasks/{task.id}").json()["status"] == "todo"


def test_accepting_a_suggestion_for_a_deleted_task_is_refused(client, db, make):
    project = make.project(name="Tracker", repo=REPO)
    task = make.task(title="Tidy", project_id=project.id, status="todo")
    add_event(db, task=task, project=project)
    suggestion = review.propose(db)[0]
    client.delete(f"/tasks/{task.id}")

    response = client.post(f"/suggestions/{suggestion.id}/accept")

    assert response.status_code == 409
    assert "no longer exists" in response.json()["detail"]


def test_unknown_suggestion_is_404(client):
    assert client.post("/suggestions/999/accept").status_code == 404


# --- The review endpoint -----------------------------------------------------


def test_review_is_read_only_by_default(client, db, make):
    project = make.project(name="Tracker", repo=REPO)
    task = make.task(title="Tidy", project_id=project.id, status="todo")
    add_event(db, task=task, project=project)

    body = client.get("/review").json()

    assert body["suggestions"] == []
    assert db.query(models.Suggestion).count() == 0


def test_review_with_refresh_raises_suggestions(client, db, make):
    project = make.project(name="Tracker", repo=REPO)
    task = make.task(title="Tidy", project_id=project.id, status="todo")
    add_event(db, task=task, project=project)

    body = client.get("/review", params={"refresh": True}).json()

    assert len(body["suggestions"]) == 1
    assert body["counts"]["suggestions_pending"] == 1


def test_review_counts_findings_by_rule(client, make):
    make.task(title="Late one", due_date=days_ago(2))
    make.task(title="Late two", due_date=days_ago(5))

    counts = client.get("/review").json()["counts"]

    assert counts["overdue_task"] == 2


def test_review_never_applies_a_suggestion(client, db, make):
    project = make.project(name="Tracker", repo=REPO)
    task = make.task(title="Tidy", project_id=project.id, status="todo")
    add_event(db, task=task, project=project)

    client.get("/review", params={"refresh": True})

    assert client.get(f"/tasks/{task.id}").json()["status"] == "todo"


def test_suggestions_listing_can_show_resolved_ones(client, db, make):
    project = make.project(name="Tracker", repo=REPO)
    task = make.task(title="Tidy", project_id=project.id, status="todo")
    add_event(db, task=task, project=project)
    suggestion = review.propose(db)[0]
    client.post(f"/suggestions/{suggestion.id}/dismiss")

    assert client.get("/suggestions").json() == []
    assert len(client.get("/suggestions", params={"status": "all"}).json()) == 1


def test_a_suggestion_carries_the_task_title_and_evidence(client, db, make):
    project = make.project(name="Tracker", repo=REPO)
    task = make.task(title="Tidy the loader", project_id=project.id, status="todo")
    add_event(db, task=task, project=project, title="Task: 1 tidy")
    review.propose(db)

    row = client.get("/suggestions").json()[0]

    assert row["target_title"] == "Tidy the loader"
    assert row["evidence"] and "Task: 1 tidy" in row["evidence"][0]
    assert row["rationale"]


# --- Reading a suggestion before deciding on it ------------------------------


def _summary_suggestion(db, project, proposed="A better summary.", current="Old."):
    row = models.Suggestion(
        rule="history_summary",
        fingerprint=f"history_summary:project:{project.id}",
        target_type="project",
        target_id=project.id,
        field="summary",
        current_value=current,
        proposed_value=proposed,
        rationale="Read from the repository's whole commit history.",
        evidence=json.dumps(["46 commits, Apr 2026 to Aug 2026"]),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_a_suggestion_about_a_project_is_named_not_numbered(client, db, make):
    """Every summary proposal read as "project 8" on the Review page, because
    only task titles were ever looked up."""
    project = make.project(name="admin-dashboard")
    _summary_suggestion(db, project)

    listed = client.get("/suggestions").json()

    assert listed[0]["target_title"] == "admin-dashboard"


def test_a_task_and_a_project_sharing_an_id_do_not_borrow_each_other_s_name(
    client, db, make
):
    """Titles used to be keyed by id alone, so whichever was looked up last
    won."""
    project = make.project(name="the project")
    task = make.task(title="the task")
    assert project.id == task.id  # both are the first row in their table

    _summary_suggestion(db, project)
    db.add(
        models.Suggestion(
            rule="activity_suggests_started",
            fingerprint="started:task:1",
            target_type="task",
            target_id=task.id,
            field="status",
            current_value="todo",
            proposed_value="in_progress",
            rationale="There are commits against it.",
            evidence=json.dumps([]),
        )
    )
    db.commit()

    by_type = {s["target_type"]: s["target_title"] for s in client.get("/suggestions").json()}

    assert by_type == {"project": "the project", "task": "the task"}


def test_the_detail_shows_the_value_as_it_stands_now(client, db, make):
    """The stored current value is a snapshot from when the rule fired.
    Accepting overwrites what is there now, so that is what has to be read."""
    project = make.project(name="OMS", summary="What it actually says today")
    suggestion = _summary_suggestion(db, project, current="What it said back then")

    body = client.get(f"/suggestions/{suggestion.id}").json()

    assert body["live_value"] == "What it actually says today"
    assert body["current_value"] == "What it said back then"
    assert body["changed_since_raised"] is True


def test_an_unchanged_target_is_not_flagged_as_drifted(client, db, make):
    project = make.project(name="OMS", summary="Old.")
    suggestion = _summary_suggestion(db, project, current="Old.")

    body = client.get(f"/suggestions/{suggestion.id}").json()

    assert body["changed_since_raised"] is False
    assert body["already_applied"] is False


def test_a_proposal_already_in_place_says_so(client, db, make):
    project = make.project(name="OMS", summary="A better summary.")
    suggestion = _summary_suggestion(db, project, proposed="A better summary.")

    assert client.get(f"/suggestions/{suggestion.id}").json()["already_applied"] is True


def test_a_deleted_target_is_reported_rather_than_left_to_fail_on_accept(
    client, db, make
):
    project = make.project(name="Doomed")
    suggestion = _summary_suggestion(db, project)
    client.delete(f"/projects/{project.id}")

    body = client.get(f"/suggestions/{suggestion.id}").json()

    assert body["target_exists"] is False
    assert body["live_value"] is None


def test_the_detail_explains_the_rule_and_what_accepting_does(client, db, make):
    project = make.project(name="OMS")
    suggestion = _summary_suggestion(db, project)

    body = client.get(f"/suggestions/{suggestion.id}").json()

    assert "commit history" in body["rule_explanation"]
    assert body["applies"] == "Replaces this project's summary with the proposed text."
    assert body["can_apply"] is True


def test_a_combination_nothing_knows_how_to_apply_admits_it(client, db, make):
    """Better to say so before the button is pressed than to 400 afterwards."""
    project = make.project(name="OMS")
    db.add(
        models.Suggestion(
            rule="something_new",
            fingerprint="x",
            target_type="project",
            target_id=project.id,
            field="category",
            current_value="build",
            proposed_value="research",
            rationale="why not",
            evidence=json.dumps([]),
        )
    )
    db.commit()
    suggestion_id = client.get("/suggestions").json()[0]["id"]

    body = client.get(f"/suggestions/{suggestion_id}").json()

    assert body["can_apply"] is False
    assert body["applies"] is None


def test_nothing_is_abbreviated_in_the_detail(client, db, make):
    """The whole point: the row could not show a 240-character summary."""
    long_summary = "A. " * 80
    project = make.project(name="OMS")
    suggestion = _summary_suggestion(db, project, proposed=long_summary)

    body = client.get(f"/suggestions/{suggestion.id}").json()

    assert body["proposed_value"] == long_summary
    assert body["evidence"] == ["46 commits, Apr 2026 to Aug 2026"]


def test_reading_a_suggestion_changes_nothing(client, db, make):
    project = make.project(name="OMS", summary="Untouched")
    suggestion = _summary_suggestion(db, project)

    client.get(f"/suggestions/{suggestion.id}")

    assert client.get(f"/projects/{project.id}").json()["summary"] == "Untouched"
    assert db.get(models.Suggestion, suggestion.id).status == "pending"


def test_an_already_decided_suggestion_can_still_be_read(client, db, make):
    """Accept and dismiss refuse a resolved suggestion; reading one is how you
    check what you agreed to."""
    project = make.project(name="OMS")
    suggestion = _summary_suggestion(db, project)
    client.post(f"/suggestions/{suggestion.id}/dismiss")

    body = client.get(f"/suggestions/{suggestion.id}").json()

    assert body["status"] == "dismissed"
    assert body["resolved_at"]


def test_an_unknown_suggestion_is_a_404(client):
    assert client.get("/suggestions/999").status_code == 404
