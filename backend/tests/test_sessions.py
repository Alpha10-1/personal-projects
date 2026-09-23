"""Hours inferred from commit times.

The arithmetic is a pile of guesses with numbers attached, so what is worth
testing is that each guess does what it says: where a sitting is split,
what the lead-in adds, what the cap does to an implausible day, and that a
day you logged by hand is never proposed over.
"""

import json
from datetime import date, datetime, timedelta

import pytest

from app import models, review, sessions


@pytest.fixture
def repo(make):
    project = make.project("Demo", repo="owner/demo")
    counter = {"n": 0}

    def commit(when: datetime, title="work", additions=0, deletions=0, project_id=None):
        counter["n"] += 1
        return make.event(
            external_id=f"github:commit:{counter['n']}",
            project_id=project_id or project.id,
            title=title,
            occurred_at=when,
            file_stats=json.dumps(
                {"files": [], "additions": additions, "deletions": deletions}
            ),
        )

    return project, commit


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 3, day, hour, minute)


# --- where a sitting starts and stops -------------------------------------


def test_commits_close_together_are_one_sitting(db, repo):
    project, commit = repo
    commit(at(2, 19, 10))
    commit(at(2, 20, 0))
    commit(at(2, 21, 15))

    found = sessions.sittings(db)
    assert len(found) == 1
    assert found[0].commits == 3
    assert found[0].span == "19:10–21:15"


def test_a_long_gap_starts_a_new_sitting(db, repo):
    project, commit = repo
    commit(at(2, 9, 0))
    commit(at(2, 17, 0))

    found = sessions.sittings(db)
    assert len(found) == 2
    assert [s.commits for s in found] == [1, 1]


def test_the_gap_boundary_is_inclusive(db, repo):
    """Exactly `GAP` apart is still one sitting."""
    project, commit = repo
    commit(at(2, 9, 0))
    commit(at(2, 9, 0) + sessions.GAP)
    assert len(sessions.sittings(db)) == 1


def test_a_sitting_never_spans_midnight(db, repo):
    """Days are the unit a time log is kept in."""
    project, commit = repo
    commit(at(2, 23, 40))
    commit(at(3, 0, 20))
    assert {s.work_date for s in sessions.sittings(db)} == {date(2026, 3, 2), date(2026, 3, 3)}


# --- the numbers ----------------------------------------------------------


def test_the_lead_in_is_added_to_the_span(db, repo):
    project, commit = repo
    commit(at(2, 9, 0))
    commit(at(2, 10, 0))
    # an hour of commits, plus the half hour before the first
    assert sessions.sittings(db)[0].hours == 1.5


def test_a_lone_commit_is_worth_the_minimum(db, repo):
    project, commit = repo
    commit(at(2, 9, 0))
    assert sessions.sittings(db)[0].hours == sessions.MIN_HOURS


def test_hours_land_on_a_quarter(db, repo):
    project, commit = repo
    commit(at(2, 9, 0))
    commit(at(2, 10, 37))
    assert sessions.sittings(db)[0].hours % 0.25 == 0


def test_an_implausible_day_is_capped_rather_than_believed(db, repo):
    """A replayed import should not read as a twenty-hour Tuesday."""
    project, commit = repo
    for hour in range(0, 24):
        commit(at(2, hour, 0))

    day = sessions.days(db)[0]
    assert day.hours <= sessions.MAX_DAY_HOURS


def test_lines_changed_are_carried_as_evidence(db, repo):
    project, commit = repo
    commit(at(2, 9, 0), additions=100, deletions=20)
    assert sessions.sittings(db)[0].lines_changed == 120


# --- what is left out -----------------------------------------------------


def test_an_unattributed_commit_is_not_billed_to_anything(db, repo):
    """Guessing which project owns an evening is worse than leaving it out."""
    project, commit = repo
    make_event = commit(at(2, 9, 0))
    make_event.project_id = None
    db.commit()
    assert sessions.sittings(db) == []


def test_a_day_is_one_proposal_even_with_several_sittings(db, repo):
    project, commit = repo
    commit(at(2, 9, 0))
    commit(at(2, 9, 30))
    commit(at(2, 20, 0))

    assert len(sessions.sittings(db)) == 2
    merged = sessions.days(db)
    assert len(merged) == 1
    assert merged[0].commits == 3
    assert merged[0].hours == 1.5  # (0.5 + 0.5) span work plus two lead-ins


def test_projects_are_kept_apart_on_the_same_day(db, repo, make):
    project, commit = repo
    other = make.project("Other", repo="owner/other")
    commit(at(2, 9, 0))
    commit(at(2, 9, 10), project_id=other.id)
    assert len(sessions.days(db)) == 2


# --- reaching the board ---------------------------------------------------


def recent(offset_days: int = 1) -> datetime:
    return datetime.combine(date.today() - timedelta(days=offset_days), datetime.min.time()) + timedelta(hours=9)


def test_a_day_with_commits_and_no_hours_is_proposed(db, repo):
    project, commit = repo
    commit(recent())
    commit(recent() + timedelta(hours=2))

    made = [s for s in review.propose(db) if s.rule == "time_from_commits"]
    assert len(made) == 1
    assert made[0].target_type == "project"
    assert made[0].field == "time_log"
    assert made[0].proposed_value.endswith((date.today() - timedelta(days=1)).isoformat())


def test_the_rationale_says_it_is_an_estimate(db, repo):
    project, commit = repo
    commit(recent())
    made = next(s for s in review.propose(db) if s.rule == "time_from_commits")
    assert "estimate" in made.rationale
    assert "cannot see" in made.rationale


def test_a_day_already_logged_by_hand_is_left_alone(db, repo, make):
    project, commit = repo
    when = recent()
    commit(when)
    make.log(work_date=when.date(), hours=2.0, project_id=project.id)

    assert [s for s in review.propose(db) if s.rule == "time_from_commits"] == []


def test_old_commits_are_not_dredged_up(db, repo):
    project, commit = repo
    commit(recent(review.TIME_LOOKBACK_DAYS + 5))
    assert [s for s in review.propose(db) if s.rule == "time_from_commits"] == []


def test_proposing_twice_does_not_duplicate(db, repo):
    project, commit = repo
    commit(recent())
    first = [s for s in review.propose(db) if s.rule == "time_from_commits"]
    second = [s for s in review.propose(db) if s.rule == "time_from_commits"]
    assert len(first) == 1
    assert second == []


def test_a_dismissed_day_is_never_offered_again(db, repo):
    project, commit = repo
    commit(recent())
    made = next(s for s in review.propose(db) if s.rule == "time_from_commits")
    made.status = "dismissed"
    db.commit()
    assert [s for s in review.propose(db) if s.rule == "time_from_commits"] == []


# --- accepting one --------------------------------------------------------


def test_accepting_writes_the_hours(db, repo):
    project, commit = repo
    when = recent()
    commit(when)
    commit(when + timedelta(hours=1))

    made = next(s for s in review.propose(db) if s.rule == "time_from_commits")
    review.apply(db, made)
    db.commit()

    log = db.query(models.TimeLog).one()
    assert log.project_id == project.id
    assert log.work_date == when.date()
    assert log.hours == 1.5


def test_an_inferred_hour_is_marked_as_inferred(db, repo):
    """So nothing downstream mistakes it for one you measured."""
    project, commit = repo
    commit(recent())
    review.apply(db, next(s for s in review.propose(db) if s.rule == "time_from_commits"))
    db.commit()
    assert db.query(models.TimeLog).one().source == "agent"


def test_nothing_is_written_until_it_is_accepted(db, repo):
    project, commit = repo
    commit(recent())
    review.propose(db)
    assert db.query(models.TimeLog).count() == 0


@pytest.mark.parametrize("hours,day", [(3.5, "2026-09-19"), (0.5, "2026-01-01")])
def test_the_proposal_text_round_trips(hours, day):
    sitting = sessions.Sitting(
        project_id=1,
        work_date=date.fromisoformat(day),
        hours=hours,
        commits=1,
        first_at=datetime(2026, 1, 1, 9),
        last_at=datetime(2026, 1, 1, 9),
    )
    assert sessions.parse_proposal(sessions.format_proposal(sitting)) == (
        hours,
        date.fromisoformat(day),
    )
