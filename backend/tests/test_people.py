"""People, membership, feedback, and the shareable snapshot.

The load-bearing idea under test is attribution: a GitHub login becomes a
named person, that naming survives being done out of order, and removing a
person never destroys the record of what they did.
"""

import pytest

from app import collaboration, github, models


def commit_payload(sha="abc123", login="dev", message="Fix the loader", body=None):
    return {
        "sha": sha,
        "commit": {"message": message, "author": {"name": login, "date": "2026-09-01T10:00:00Z"}},
        "author": {"login": login},
        "html_url": f"https://github.com/owner/name/commit/{sha}",
    }


def pull_payload(number=1, login="dev", title="Add the loader", body=None, pid=9001):
    return {
        "id": pid,
        "number": number,
        "title": title,
        "body": body,
        "user": {"login": login},
        "html_url": f"https://github.com/owner/name/pull/{number}",
        "created_at": "2026-09-01T10:00:00Z",
        "updated_at": "2026-09-02T10:00:00Z",
    }


# --- People ------------------------------------------------------------------


def test_a_person_starts_with_nothing_attributed(client):
    body = client.post(
        "/people", json={"name": "Thabo Arendse", "github_login": "tarendse"}
    ).json()

    assert body["name"] == "Thabo Arendse"
    assert body["contribution_count"] == 0
    assert body["project_count"] == 0


def test_one_github_login_belongs_to_one_person(client):
    client.post("/people", json={"name": "A", "github_login": "shared"})

    response = client.post("/people", json={"name": "B", "github_login": "shared"})

    assert response.status_code == 409
    assert "already belongs" in response.json()["detail"]


def test_two_people_can_both_have_no_login(client):
    """A stakeholder who only reads digests has no GitHub account, and that
    must not collide with the next one."""
    assert client.post("/people", json={"name": "A"}).status_code == 201
    assert client.post("/people", json={"name": "B"}).status_code == 201


def test_a_name_cannot_be_nulled(client):
    person = client.post("/people", json={"name": "A"}).json()

    response = client.patch(f"/people/{person['id']}", json={"name": None})

    assert response.status_code == 422


def test_deleting_a_person_keeps_what_they_did(client, db, make):
    """The commits happened. Only the link to a name goes."""
    person = make.person(name="Dev", github_login="dev")
    project = make.project(name="P")
    make.event(external_id="c1", actor="dev", person_id=person.id, project_id=project.id)
    db.add(
        models.Feedback(
            person_id=person.id,
            project_id=project.id,
            source="pr_review",
            external_id="pr_review:1",
            author_login="dev",
            body="This needs a test for the empty case.",
            occurred_at=models.utcnow(),
        )
    )
    db.commit()

    assert client.delete(f"/people/{person.id}").status_code == 204

    event = db.query(models.ActivityEvent).one()
    item = db.query(models.Feedback).one()
    assert event.person_id is None
    assert event.actor == "dev"  # still says who
    assert item.person_id is None
    assert item.author_login == "dev"


# --- Attribution -------------------------------------------------------------


def test_sync_names_the_person_who_committed(client, db, make):
    make.project(name="Tracker", repo="owner/name")
    person = make.person(name="Dev", github_login="dev")

    github.sync_repo(
        db, "owner/name", fetcher=lambda *_a: [{"kind": "commit", "payload": commit_payload()}]
    )

    assert db.query(models.ActivityEvent).one().person_id == person.id


def test_a_login_matches_whatever_its_casing(db, make):
    """GitHub logins are case-insensitive, and the casing in a payload rarely
    matches what was typed into the person's record."""
    person = make.person(name="Dev", github_login="TArendse")

    assert collaboration.person_for_login(db, "tarendse").id == person.id
    assert collaboration.person_for_login(db, "  TARENDSE  ").id == person.id
    assert collaboration.person_for_login(db, "someone-else") is None
    assert collaboration.person_for_login(db, None) is None


def test_adding_a_person_attaches_the_work_they_already_did(client, db, make):
    """Someone has been committing for weeks before you add them. Their
    record should show those weeks, not start from zero."""
    project = make.project(name="P", repo="owner/name")
    make.event(external_id="c1", actor="dev", project_id=project.id)
    make.event(external_id="c2", actor="dev", project_id=project.id)

    body = client.post("/people", json={"name": "Dev", "github_login": "dev"}).json()

    assert body["contribution_count"] == 2


def test_changing_a_login_moves_the_attribution(client, db, make):
    make.event(external_id="c1", actor="dev")
    person = client.post("/people", json={"name": "Dev", "github_login": "dev"}).json()
    assert person["contribution_count"] == 1

    updated = client.patch(
        f"/people/{person['id']}", json={"github_login": "someone-else"}
    ).json()

    assert updated["contribution_count"] == 0
    assert db.query(models.ActivityEvent).one().person_id is None


def test_relink_never_invents_an_attribution(db, make):
    """It only ever sets person_id from the logins on record, so an event
    whose actor matches nobody is cleared rather than left pointing at
    whoever used to own that login."""
    person = make.person(name="Dev", github_login="dev")
    event = make.event(external_id="c1", actor="nobody", person_id=person.id)

    collaboration.relink(db)

    assert db.get(models.ActivityEvent, event.id).person_id is None


def test_unlinked_shows_who_is_working_but_unnamed(client, make):
    make.event(external_id="c1", actor="ghost")
    make.event(external_id="c2", actor="ghost")
    make.event(external_id="c3", actor="other")

    rows = client.get("/people/unlinked").json()

    assert [r["login"] for r in rows] == ["ghost", "other"]
    assert rows[0]["events"] == 2


# --- Membership --------------------------------------------------------------


def test_a_person_is_added_to_a_project_once(client, make):
    project = make.project(name="P")
    person = make.person(name="Dev")

    first = client.post(
        f"/projects/{project.id}/members",
        json={"person_id": person.id, "role": "contributor"},
    )
    second = client.post(
        f"/projects/{project.id}/members", json={"person_id": person.id}
    )

    assert first.status_code == 201
    assert first.json()["role"] == "contributor"
    assert second.status_code == 409


def test_membership_counts_only_this_project_s_work(client, db, make):
    person = make.person(name="Dev", github_login="dev")
    here = make.project(name="Here", repo="owner/here")
    elsewhere = make.project(name="Elsewhere")
    make.event(external_id="c1", actor="dev", person_id=person.id, project_id=here.id)
    make.event(external_id="c2", actor="dev", person_id=person.id, project_id=elsewhere.id)
    client.post(f"/projects/{here.id}/members", json={"person_id": person.id})

    members = client.get(f"/projects/{here.id}/members").json()

    assert len(members) == 1
    assert members[0]["contribution_count"] == 1
    assert client.get(f"/people/{person.id}").json()["contribution_count"] == 2


def test_removing_a_member_leaves_their_contributions(client, db, make):
    person = make.person(name="Dev", github_login="dev")
    project = make.project(name="P")
    make.event(external_id="c1", actor="dev", person_id=person.id, project_id=project.id)
    member = client.post(
        f"/projects/{project.id}/members", json={"person_id": person.id}
    ).json()

    assert client.delete(f"/members/{member['id']}").status_code == 204

    assert db.query(models.ActivityEvent).count() == 1
    assert client.get(f"/projects/{project.id}/members").json() == []


def test_a_member_must_be_a_real_person_and_project(client, make):
    project = make.project(name="P")

    assert (
        client.post(f"/projects/{project.id}/members", json={"person_id": 999}).status_code
        == 404
    )
    assert client.post("/projects/999/members", json={"person_id": 1}).status_code == 404


def test_deleting_a_project_takes_its_membership_and_feedback(client, db, make):
    """Foreign keys are enforced, so this is the path that breaks if the
    delete order is wrong."""
    project = make.project(name="Doomed")
    person = make.person(name="Dev")
    client.post(f"/projects/{project.id}/members", json={"person_id": person.id})
    client.post(
        "/feedback",
        json={
            "body": "Worth checking the edge case here.",
            "person_id": person.id,
            "project_id": project.id,
        },
    )

    assert client.delete(f"/projects/{project.id}").status_code == 204

    assert db.query(models.ProjectMember).count() == 0
    assert db.query(models.Feedback).count() == 0
    assert db.query(models.Person).count() == 1  # the person survives


# --- Feedback ----------------------------------------------------------------


def test_hand_entered_feedback_cannot_claim_to_be_from_github(client, make):
    """Only the git-sourced kinds mean "this has an id on GitHub", so source
    is fixed here rather than taken from the request."""
    person = make.person(name="Dev", github_login="dev")

    body = client.post(
        "/feedback",
        json={
            "body": "Said in the Tuesday meeting: check the ore grades.",
            "person_id": person.id,
            "source": "pr_review",
        },
    ).json()

    assert body["source"] == "manual"
    assert body["person_name"] == "Dev"


def test_resolving_feedback_stamps_when(client, make):
    person = make.person(name="Dev")
    item = client.post(
        "/feedback", json={"body": "Check the ore grades please.", "person_id": person.id}
    ).json()
    assert item["resolved_at"] is None

    done = client.patch(f"/feedback/{item['id']}", json={"status": "actioned"}).json()
    assert done["resolved_at"] is not None

    reopened = client.patch(f"/feedback/{item['id']}", json={"status": "open"}).json()
    assert reopened["resolved_at"] is None


def test_git_sourced_feedback_cannot_be_deleted(client, db, make):
    """Deleting it would just bring it back on the next sync."""
    db.add(
        models.Feedback(
            source="pr_review",
            external_id="pr_review:1",
            author_login="dev",
            body="This needs a test for the empty case.",
            occurred_at=models.utcnow(),
        )
    )
    db.commit()
    item = client.get("/feedback").json()[0]

    response = client.delete(f"/feedback/{item['id']}")

    assert response.status_code == 409
    assert "Mark it declined" in response.json()["detail"]


def test_hand_entered_feedback_can_be_deleted(client, make):
    item = client.post("/feedback", json={"body": "Never mind, wrong project."}).json()

    assert client.delete(f"/feedback/{item['id']}").status_code == 204


# --- Feedback out of git -----------------------------------------------------


def test_a_pull_request_body_costs_no_extra_request(db, make):
    """It is read out of the payload sync_repo already stored."""
    project = make.project(name="P", repo="owner/name")
    make.person(name="Dev", github_login="dev")
    github.sync_repo(
        db,
        "owner/name",
        fetcher=lambda *_a: [
            {
                "kind": "pull_request",
                "payload": pull_payload(body="Suggest we cache the lookup table here."),
            }
        ],
    )

    result = github.sync_feedback(db, "owner/name", fetcher=lambda *_a: [])

    item = db.query(models.Feedback).one()
    assert result["added"] == 1
    assert item.source == "pr_body"
    assert item.body == "Suggest we cache the lookup table here."
    assert item.project_id == project.id
    assert item.person_id is not None  # attributed to Dev


def test_comments_are_mirrored_and_attributed(db, make):
    make.project(name="P", repo="owner/name")
    person = make.person(name="Dev", github_login="dev")

    comments = [
        {
            "kind": "pr_review",
            "payload": {
                "id": 11,
                "body": "This loop reads the file twice, worth hoisting.",
                "user": {"login": "dev"},
                "html_url": "https://github.com/owner/name/pull/1#r11",
                "created_at": "2026-09-03T10:00:00Z",
            },
        },
        {
            "kind": "issue_comment",
            "payload": {
                "id": 12,
                "body": "Could we report the ore grade alongside this?",
                "user": {"login": "stranger"},
                "html_url": "https://github.com/owner/name/issues/2#c12",
                "created_at": "2026-09-04T10:00:00Z",
            },
        },
    ]

    result = github.sync_feedback(db, "owner/name", fetcher=lambda *_a: comments)

    assert result["added"] == 2
    assert result["attributed"] == 1
    items = {i.source: i for i in db.query(models.Feedback).all()}
    assert items["pr_review"].person_id == person.id
    # Nobody owns "stranger" yet -- recorded anyway rather than dropped.
    assert items["issue_comment"].person_id is None
    assert items["issue_comment"].author_login == "stranger"


def test_a_review_and_an_issue_comment_can_share_an_id(db, make):
    """GitHub ids are only unique within their own endpoint, so the stored
    external_id has to carry the source or one comment silently wins."""
    make.project(name="P", repo="owner/name")
    same_id = [
        {
            "kind": "pr_review",
            "payload": {
                "id": 7,
                "body": "Worth extracting this into a helper.",
                "user": {"login": "a"},
                "created_at": "2026-09-03T10:00:00Z",
            },
        },
        {
            "kind": "issue_comment",
            "payload": {
                "id": 7,
                "body": "Different comment, same numeric id.",
                "user": {"login": "b"},
                "created_at": "2026-09-03T11:00:00Z",
            },
        },
    ]

    result = github.sync_feedback(db, "owner/name", fetcher=lambda *_a: same_id)

    assert result["added"] == 2
    assert db.query(models.Feedback).count() == 2


def test_mirroring_twice_adds_nothing(db, make):
    make.project(name="P", repo="owner/name")
    comments = [
        {
            "kind": "pr_review",
            "payload": {
                "id": 11,
                "body": "This loop reads the file twice, worth hoisting.",
                "user": {"login": "dev"},
                "created_at": "2026-09-03T10:00:00Z",
            },
        }
    ]

    github.sync_feedback(db, "owner/name", fetcher=lambda *_a: comments)
    again = github.sync_feedback(db, "owner/name", fetcher=lambda *_a: comments)

    assert again["added"] == 0
    assert db.query(models.Feedback).count() == 1


def test_a_one_word_comment_is_not_a_suggestion(db, make):
    make.project(name="P", repo="owner/name")
    comments = [
        {
            "kind": "pr_review",
            "payload": {
                "id": 11,
                "body": "nit",
                "user": {"login": "dev"},
                "created_at": "2026-09-03T10:00:00Z",
            },
        }
    ]

    result = github.sync_feedback(db, "owner/name", fetcher=lambda *_a: comments)

    assert result["added"] == 0


def test_the_sync_route_mirrors_feedback_in_the_same_pass(client, db, make, monkeypatch):
    make.project(name="P", repo="owner/name")
    monkeypatch.setattr(
        github,
        "fetch_from_github",
        lambda *_a: [
            {
                "kind": "pull_request",
                "payload": pull_payload(body="Suggest we cache the lookup table."),
            }
        ],
    )
    monkeypatch.setattr(github, "fetch_comments", lambda *_a: [])

    body = client.post("/activity/sync").json()

    assert body[0]["added"] == 1
    assert body[0]["feedback_added"] == 1


def test_a_failing_comment_endpoint_does_not_lose_the_activity(
    client, db, make, monkeypatch
):
    """Activity feeds the deterministic rules; feedback is a mirror. Losing
    the mirror is survivable, losing the activity is not."""
    make.project(name="P", repo="owner/name")
    monkeypatch.setattr(
        github,
        "fetch_from_github",
        lambda *_a: [{"kind": "commit", "payload": commit_payload()}],
    )

    def rate_limited(*_a):
        raise RuntimeError("GitHub 403: rate limited")

    monkeypatch.setattr(github, "fetch_comments", rate_limited)

    body = client.post("/activity/sync").json()

    assert body[0]["added"] == 1
    assert body[0]["feedback_added"] == 0
    assert db.query(models.ActivityEvent).count() == 1


# --- The shareable snapshot --------------------------------------------------


def test_the_snapshot_is_one_self_contained_page(client, make):
    project = make.project(name="Shift forecast", summary="Predict shift demand.")

    response = client.get(f"/projects/{project.id}/share")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "<!DOCTYPE html>" in body
    assert "Shift forecast" in body
    assert "Predict shift demand." in body
    # Nothing to fetch and nothing to run, so it works from an email attachment.
    assert "<script" not in body
    assert "src=" not in body


def test_the_snapshot_withholds_what_is_yours(client, make):
    """Hours, retros and the stakeholder field are where you say what you
    actually think. They do not go to other people."""
    project = make.project(
        name="P",
        summary="A summary.",
        retro="Honestly this was badly scoped from the start.",
        stakeholder="Someone I would rather not name in a digest",
    )
    make.log(project_id=project.id, hours=37.5)

    body = client.get(f"/projects/{project.id}/share").text

    assert "badly scoped" not in body
    assert "rather not name" not in body
    assert "37.5" not in body


def test_the_snapshot_names_contributors(client, db, make):
    project = make.project(name="P", repo="owner/name")
    person = make.person(name="Thabo Arendse", github_login="tarendse")
    make.event(
        external_id="c1", actor="tarendse", person_id=person.id, project_id=project.id
    )

    body = client.get(f"/projects/{project.id}/share").text

    assert "Thabo Arendse" in body


def test_the_snapshot_escapes_what_people_wrote(client, db, make):
    """Feedback is other people's text landing in a file you forward on."""
    project = make.project(name="P")
    db.add(
        models.Feedback(
            project_id=project.id,
            source="pr_review",
            external_id="pr_review:1",
            author_login="dev",
            body="<script>alert('xss')</script> and a real point about caching",
            occurred_at=models.utcnow(),
        )
    )
    db.commit()

    body = client.get(f"/projects/{project.id}/share").text

    assert "<script>alert" not in body
    assert "&lt;script&gt;" in body


def test_a_missing_project_has_no_snapshot(client):
    assert client.get("/projects/999/share").status_code == 404


# --- The digest --------------------------------------------------------------


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")


def fake_digest(result):
    async def _call(db, project):
        return result

    return _call


def test_the_digest_note_is_written_as_agent_work(client, db, make, configured, monkeypatch):
    from app import assistant

    project = make.project(name="P", summary="Old summary.")
    monkeypatch.setattr(
        assistant,
        "write_digest",
        fake_digest(
            {
                "note": "Two tasks closed this week.",
                "contributions": [{"who": "Dev", "what": "closed the loader work"}],
                "risks": ["The SharePoint blocker is four days old."],
            }
        ),
    )

    body = client.post(f"/ai/projects/{project.id}/digest").json()

    note = db.query(models.Note).one()
    assert note.source == "agent"
    assert note.project_id == project.id
    assert "Two tasks closed this week." in note.body
    assert "Dev: closed the loader work" in note.body
    assert "SharePoint blocker" in note.body
    assert body["note_id"] == note.id


def test_a_better_summary_is_proposed_not_applied(client, db, make, configured, monkeypatch):
    """The whole point: the digest may well have a better sentence, and it
    still does not get to overwrite yours."""
    from app import assistant

    project = make.project(name="P", summary="Old summary.")
    monkeypatch.setattr(
        assistant,
        "write_digest",
        fake_digest({"note": "Progress.", "summary": "A sharper, current summary."}),
    )

    body = client.post(f"/ai/projects/{project.id}/digest").json()

    assert body["summary_suggested"] is True
    assert db.get(models.Project, project.id).summary == "Old summary."
    suggestion = db.query(models.Suggestion).one()
    assert suggestion.target_type == "project"
    assert suggestion.field == "summary"
    assert suggestion.current_value == "Old summary."


def test_accepting_the_summary_suggestion_applies_it(client, db, make, configured, monkeypatch):
    from app import assistant

    project = make.project(name="P", summary="Old summary.")
    monkeypatch.setattr(
        assistant,
        "write_digest",
        fake_digest({"note": "Progress.", "summary": "A sharper, current summary."}),
    )
    client.post(f"/ai/projects/{project.id}/digest")
    suggestion = db.query(models.Suggestion).one()

    response = client.post(f"/suggestions/{suggestion.id}/accept")

    assert response.status_code == 200
    db.expire_all()
    assert db.get(models.Project, project.id).summary == "A sharper, current summary."


def test_running_the_digest_twice_does_not_stack_up_proposals(
    client, db, make, configured, monkeypatch
):
    from app import assistant

    project = make.project(name="P", summary="Old summary.")
    monkeypatch.setattr(
        assistant, "write_digest", fake_digest({"note": "n", "summary": "First try."})
    )
    client.post(f"/ai/projects/{project.id}/digest")

    monkeypatch.setattr(
        assistant, "write_digest", fake_digest({"note": "n", "summary": "Second try."})
    )
    client.post(f"/ai/projects/{project.id}/digest")

    suggestions = db.query(models.Suggestion).all()
    assert len(suggestions) == 1
    assert suggestions[0].proposed_value == "Second try."


def test_no_proposal_when_the_summary_is_already_right(
    client, db, make, configured, monkeypatch
):
    from app import assistant

    project = make.project(name="P", summary="Already correct.")
    monkeypatch.setattr(
        assistant,
        "write_digest",
        fake_digest({"note": "n", "summary": "Already correct."}),
    )

    body = client.post(f"/ai/projects/{project.id}/digest").json()

    assert body["summary_suggested"] is False
    assert db.query(models.Suggestion).count() == 0


def test_the_digest_credits_only_people_it_was_given(db, make):
    """The prompt forbids inventing a contributor; the context has to actually
    contain the names for that instruction to be followable."""
    from app import assistant

    project = make.project(name="P", repo="owner/name")
    person = make.person(name="Thabo Arendse", github_login="tarendse")
    make.event(
        external_id="c1",
        actor="tarendse",
        person_id=person.id,
        project_id=project.id,
        title="Fix the loader",
    )

    context = assistant.digest_context(db, project)

    assert "Thabo Arendse: [commit] Fix the loader" in context


def test_the_digest_needs_a_project(client, configured):
    assert client.post("/ai/projects/999/digest").status_code == 404


def test_the_digest_is_unavailable_without_a_key(client, make, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    project = make.project(name="P")

    assert client.post(f"/ai/projects/{project.id}/digest").status_code == 503
