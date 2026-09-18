"""What the assistant costs.

The arithmetic is tested against the published rates. The rest of the file is
about the two properties that make a ledger worth having: that nothing can
spend money without being recorded, and that the recording can never be the
reason a feature breaks.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app import ai, models, spend


def usage(
    input_tokens=0,
    output_tokens=0,
    cache_read_input_tokens=0,
    cache_creation_input_tokens=0,
    web_search_requests=0,
):
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        server_tool_use=SimpleNamespace(web_search_requests=web_search_requests),
    )


# --- The arithmetic ----------------------------------------------------------


def test_a_sonnet_call_is_priced_at_the_published_rate():
    """$2 per million in, $10 per million out."""
    assert spend.cost_usd(
        "claude-sonnet-5", input_tokens=1_000_000, output_tokens=0
    ) == pytest.approx(2.00)
    assert spend.cost_usd(
        "claude-sonnet-5", input_tokens=0, output_tokens=1_000_000
    ) == pytest.approx(10.00)


def test_a_haiku_call_is_priced_at_the_published_rate():
    assert spend.cost_usd(
        "claude-haiku-4-5", input_tokens=1_000_000, output_tokens=1_000_000
    ) == pytest.approx(6.00)


def test_a_dated_model_id_is_priced_as_its_family():
    """The code asks for claude-haiku-4-5-20251001; the rate table has no row
    per snapshot and should not need one."""
    assert spend.rate_for("claude-haiku-4-5-20251001") == spend.RATES["claude-haiku-4-5"]


def test_the_longest_matching_prefix_wins():
    assert spend.rate_for("claude-sonnet-4-6") == spend.RATES["claude-sonnet-4-6"]
    assert spend.rate_for("claude-sonnet-5") == spend.RATES["claude-sonnet-5"]


def test_cache_reads_are_a_tenth_and_writes_a_quarter_more():
    read = spend.cost_usd("claude-sonnet-5", cache_read_tokens=1_000_000)
    write = spend.cost_usd("claude-sonnet-5", cache_write_tokens=1_000_000)

    assert read == pytest.approx(0.20)
    assert write == pytest.approx(2.50)


def test_a_web_search_costs_a_cent_on_top_of_its_tokens():
    """$10 per 1,000 searches, plus the tokens the results themselves cost."""
    assert spend.cost_usd("claude-sonnet-5", web_searches=1) == pytest.approx(0.01)
    assert spend.cost_usd(
        "claude-sonnet-5", input_tokens=12_700, web_searches=1
    ) == pytest.approx(0.01 + 12_700 * 2.00 / 1_000_000)


def test_an_unknown_model_is_counted_but_not_priced():
    """Guessing a rate for a model nobody has priced would put a made-up
    number in the ledger, which is worse than a visible gap."""
    assert spend.rate_for("claude-something-new") is None
    assert spend.cost_usd("claude-something-new", input_tokens=1_000_000) == 0.0
    # Its searches are still real money and still counted.
    assert spend.cost_usd("claude-something-new", web_searches=2) == pytest.approx(0.02)


def test_usage_fields_are_read_off_the_sdk_object():
    assert spend.from_usage(usage(input_tokens=5, output_tokens=7, web_search_requests=2)) == {
        "input_tokens": 5,
        "output_tokens": 7,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "web_searches": 2,
    }


def test_a_usage_object_missing_fields_does_not_explode():
    """This reads attributes off someone else's library. A field that moves
    should cost an accounting row, not the feature the user was using."""
    assert spend.from_usage(SimpleNamespace()) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "web_searches": 0,
    }
    assert spend.from_usage(None) == {}


# --- Recording ---------------------------------------------------------------


def test_a_call_is_written_to_the_ledger(db):
    spend.record(
        feature="plan",
        model="claude-sonnet-5",
        usage=usage(input_tokens=1000, output_tokens=500),
        project_id=None,
        seconds=2.5,
    )

    row = db.query(models.AiUsage).one()
    assert row.feature == "plan"
    assert row.input_tokens == 1000
    assert row.cost_usd == pytest.approx(0.002 + 0.005)
    assert row.ok is True
    assert row.seconds == 2.5


def test_the_cost_is_stored_not_derived_on_read(db, monkeypatch):
    """A price change from now on must not silently rewrite what last month
    cost."""
    spend.record(feature="chat", model="claude-sonnet-5", usage=usage(output_tokens=1_000_000))
    monkeypatch.setitem(spend.RATES, "claude-sonnet-5", spend.Rate(99.0, 99.0))

    assert db.query(models.AiUsage).one().cost_usd == pytest.approx(10.0)


def test_a_failed_call_is_recorded_too(db):
    """A feature that fails twice and succeeds once cost three calls' worth of
    input tokens. Counting only the successes hides that."""
    spend.record(
        feature="repo_review",
        model="claude-sonnet-5",
        usage=None,
        ok=False,
        error="Rate limited by the API. Try again in a moment.",
    )

    row = db.query(models.AiUsage).one()
    assert row.ok is False
    assert row.error.startswith("Rate limited")
    assert row.cost_usd == 0.0


def test_a_broken_ledger_never_breaks_the_feature(db, monkeypatch):
    def explode(*_a, **_kw):
        raise RuntimeError("the database is on fire")

    monkeypatch.setattr(spend, "SessionLocal", explode)

    spend.record(feature="chat", model="claude-sonnet-5", usage=usage())  # must not raise


# --- Nothing spends without being counted ------------------------------------


@pytest.mark.anyio
async def test_a_structured_call_is_metered_under_its_tool_name(db, monkeypatch):
    """Every caller already names its tool after the job it does, so the
    feature label needs no second argument to go wrong."""

    class FakeMessages:
        async def create(self, **kwargs):
            return SimpleNamespace(
                content=[SimpleNamespace(type="tool_use", input={"ok": True})],
                usage=usage(input_tokens=100, output_tokens=50),
            )

    monkeypatch.setattr(
        ai, "_client", lambda timeout=None: SimpleNamespace(messages=FakeMessages())
    )

    result = await ai.structured(
        system="s", prompt="p", schema={}, tool_name="summarise_history",
        model="claude-sonnet-5",
    )

    assert result == {"ok": True}
    row = db.query(models.AiUsage).one()
    assert row.feature == "summarise_history"
    assert row.model == "claude-sonnet-5"
    assert row.input_tokens == 100


@pytest.mark.anyio
async def test_a_call_that_raises_is_still_metered(db, monkeypatch):
    class FakeMessages:
        async def create(self, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        ai, "_client", lambda timeout=None: SimpleNamespace(messages=FakeMessages())
    )

    with pytest.raises(ai.AIFailed):
        await ai.structured(system="s", prompt="p", schema={}, tool_name="plan")

    row = db.query(models.AiUsage).one()
    assert row.ok is False
    assert row.feature == "plan"


@pytest.mark.anyio
async def test_a_streamed_answer_is_metered_once_it_finishes(db, monkeypatch):
    """Usage only exists once the stream is drained, so this is the easiest
    path to leave uncounted."""

    class FakeStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        @property
        async def text_stream(self):  # pragma: no cover - replaced below
            raise NotImplementedError

        async def get_final_message(self):
            return SimpleNamespace(usage=usage(input_tokens=10, output_tokens=20))

    async def chunks():
        for piece in ("one ", "two"):
            yield piece

    stream = FakeStream()
    type(stream).text_stream = property(lambda self: chunks())

    monkeypatch.setattr(
        ai,
        "_client",
        lambda timeout=None: SimpleNamespace(
            messages=SimpleNamespace(stream=lambda **kw: stream)
        ),
    )

    out = [chunk async for chunk in ai.stream(system="s", messages=[], feature="chat")]

    assert "".join(out) == "one two"
    row = db.query(models.AiUsage).one()
    assert row.feature == "chat"
    assert row.output_tokens == 20


# --- Reading it back ---------------------------------------------------------


@pytest.fixture
def ledger(db):
    def add(feature, model, at, cost_input=0, cost_output=0, searches=0, ok=True):
        row = models.AiUsage(
            feature=feature,
            model=model,
            at=at,
            input_tokens=cost_input,
            output_tokens=cost_output,
            web_searches=searches,
            ok=ok,
            cost_usd=spend.cost_usd(
                model,
                input_tokens=cost_input,
                output_tokens=cost_output,
                web_searches=searches,
            ),
        )
        db.add(row)
        db.commit()
        return row

    now = datetime.now()
    add("plan", "claude-sonnet-5", now, 100_000, 8_000)
    add("research", "claude-sonnet-5", now, 12_700, 400, searches=1)
    add("suggest_project", "claude-haiku-4-5-20251001", now, 800, 200)
    add("suggest_project", "claude-haiku-4-5-20251001", now, 800, 200, ok=False)
    add("chat", "claude-sonnet-5", now - timedelta(days=90), 1_000, 1_000)
    return db


def test_the_window_excludes_what_is_older_than_it(ledger):
    assert spend.summary(ledger, days=30)["total"]["calls"] == 4
    assert spend.summary(ledger, days=365)["total"]["calls"] == 5


def test_spending_is_grouped_by_what_it_was_spent_on(ledger):
    by_feature = {f["feature"]: f for f in spend.summary(ledger, days=30)["by_feature"]}

    assert set(by_feature) == {"plan", "research", "suggest_project"}
    assert by_feature["suggest_project"]["calls"] == 2
    # Most expensive first, because that is the question being asked.
    assert spend.summary(ledger, days=30)["by_feature"][0]["feature"] == "plan"


def test_failures_are_counted_separately_from_calls(ledger):
    by_feature = {f["feature"]: f for f in spend.summary(ledger, days=30)["by_feature"]}

    assert by_feature["suggest_project"]["calls"] == 2
    assert by_feature["suggest_project"]["failed"] == 1


def test_the_most_expensive_single_call_is_named(ledger):
    biggest = spend.summary(ledger, days=30)["most_expensive_call"]

    assert biggest["feature"] == "plan"
    assert biggest["cost_usd"] == pytest.approx(0.28)


def test_searches_are_totalled_because_they_are_billed_separately(ledger):
    assert spend.summary(ledger, days=30)["total"]["web_searches"] == 1


def test_an_empty_ledger_reports_zero_rather_than_nothing(db):
    body = spend.summary(db, days=30)

    assert body["total"]["calls"] == 0
    assert body["total"]["cost_usd"] == 0.0
    assert body["most_expensive_call"] is None


def test_unpriced_models_are_named_so_a_gap_is_visible(db):
    db.add(
        models.AiUsage(feature="chat", model="claude-something-new", cost_usd=0.0)
    )
    db.commit()

    assert spend.summary(db, days=30)["unpriced_models"] == ["claude-something-new"]


# --- The endpoint ------------------------------------------------------------


def test_the_endpoint_reports_the_bill(client, ledger):
    body = client.get("/ai/spend?days=30").json()

    assert body["total"]["calls"] == 4
    assert body["by_feature"][0]["feature"] == "plan"
    assert body["rates"]["claude-sonnet-5"]["output_per_mtok"] == 10.0
    assert body["web_search_usd"] == 0.01


def test_the_bill_is_readable_with_the_assistant_switched_off(client, ledger, monkeypatch):
    """The point of the page is deciding whether to keep paying, which is
    exactly when it might already be off."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert client.get("/ai/spend").status_code == 200


def test_recent_calls_come_back_newest_first(client, ledger):
    """The recent list is the last N calls whatever the window -- "what did it
    just do" and "what did this month cost" are different questions."""
    recent = client.get("/ai/spend?days=30&recent=10").json()["recent"]

    assert len(recent) == 5
    assert recent[0]["at"] >= recent[-1]["at"]
    assert recent[-1]["feature"] == "chat"  # the 90-day-old one


def test_recent_can_be_switched_off(client, ledger):
    assert client.get("/ai/spend?recent=0").json()["recent"] == []
