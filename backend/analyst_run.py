"""The analyst's unattended pass: pull activity, then propose.

Run on a schedule so that by the time you open the app, the evidence is
fresh and any suggestions are already waiting. It does only the
deterministic half of the loop -- sync, then propose. The narrative (what
this week actually amounted to) stays with the agent, which needs a
conversation, not a cron entry.

    python analyst_run.py                 # sync + propose
    python analyst_run.py --note          # also write a digest note
    python analyst_run.py --dry-run       # report only, change nothing

It talks to the HTTP API rather than the database, so the rules that apply to
the UI apply here too, and every write is stamped as agent-written. If the
backend is not running it says so and exits 1 rather than raising -- a
scheduled task that fails loudly every night is a scheduled task you turn
off.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import httpx

# Findings quote task titles, which are UTF-8. The Windows console defaults to
# cp1252 and would mangle them in a scheduled task's log file.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

API_URL = os.getenv("PP_API_URL", "http://localhost:8000").rstrip("/")
DATA_DIR = Path(os.getenv("PP_DATA_DIR", Path(__file__).resolve().parent / "data"))
RUN_LOG = DATA_DIR / "analyst-runs.jsonl"

HEADERS = {"X-PP-Source": "agent"}
TIMEOUT = httpx.Timeout(60.0)


class RunFailed(Exception):
    pass


async def call(client: httpx.AsyncClient, method: str, path: str, **kwargs):
    try:
        response = await client.request(method, path, **kwargs)
    except httpx.RequestError as exc:
        raise RunFailed(
            f"Can't reach the tracker API at {API_URL}. Is the backend running? ({exc})"
        ) from exc
    if response.is_error:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text[:200]
        raise RunFailed(f"{method} {path} -> {response.status_code}: {detail}")
    return None if response.status_code == 204 else response.json()


def digest(findings: list[dict], suggestions: list[dict]) -> str:
    """A plain summary of the state. Deliberately not prose: an unattended run
    reports, it does not narrate."""
    lines = [f"Analyst run {datetime.now():%Y-%m-%d %H:%M}", ""]

    if findings:
        lines.append(f"{len(findings)} finding(s):")
        for f in findings:
            lines.append(f"  - [{f['severity']}] {f['title']}")
            if f.get("detail"):
                lines.append(f"      {f['detail']}")
    else:
        lines.append("No findings.")

    lines.append("")
    if suggestions:
        lines.append(f"{len(suggestions)} suggestion(s) awaiting a decision:")
        for s in suggestions:
            lines.append(
                f"  - {s.get('target_title') or s['target_type']}: "
                f"{s['field']} {s['current_value']} -> {s['proposed_value']}"
            )
            lines.append(f"      {s['rationale']}")
    else:
        lines.append("No suggestions awaiting a decision.")

    return "\n".join(lines)


def record(entry: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with RUN_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError:
        pass


async def run(write_note: bool = False, dry_run: bool = False) -> dict:
    started = datetime.now()
    summary: dict = {"at": started.isoformat(), "dry_run": dry_run}

    async with httpx.AsyncClient(
        base_url=API_URL, headers=HEADERS, timeout=TIMEOUT
    ) as client:
        repos = await call(client, "GET", "/activity/repos")
        summary["repos"] = repos

        if repos and not dry_run:
            synced = await call(client, "POST", "/activity/sync")
            summary["synced"] = [
                {k: r[k] for k in ("repo", "added", "skipped")} for r in synced
            ]
        else:
            summary["synced"] = []

        if not dry_run:
            raised = await call(client, "POST", "/suggestions/refresh")
            summary["suggestions_raised"] = len(raised)
        else:
            summary["suggestions_raised"] = 0

        review = await call(client, "GET", "/review")
        summary["findings"] = len(review["findings"])
        summary["suggestions_pending"] = len(review["suggestions"])

        if write_note and not dry_run:
            await call(
                client,
                "POST",
                "/notes",
                json={
                    "title": f"Analyst digest {date.today().isoformat()}",
                    "body": digest(review["findings"], review["suggestions"]),
                    "kind": "note",
                },
            )
            summary["note_written"] = True

        summary["text"] = digest(review["findings"], review["suggestions"])

    summary["seconds"] = round((datetime.now() - started).total_seconds(), 2)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--note",
        action="store_true",
        help="also write the digest into the tracker as a note",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report the current review without syncing or proposing",
    )
    parser.add_argument("--quiet", action="store_true", help="only print on failure")
    args = parser.parse_args()

    try:
        summary = asyncio.run(run(write_note=args.note, dry_run=args.dry_run))
    except RunFailed as exc:
        record({"at": datetime.now().isoformat(), "ok": False, "error": str(exc)})
        print(f"analyst run failed: {exc}", file=sys.stderr)
        return 1

    record({**{k: v for k, v in summary.items() if k != "text"}, "ok": True})

    if not args.quiet:
        added = sum(s["added"] for s in summary["synced"])
        print(
            f"synced {added} new event(s) from {len(summary['repos'])} repo(s); "
            f"raised {summary['suggestions_raised']} suggestion(s); "
            f"{summary['findings']} finding(s), "
            f"{summary['suggestions_pending']} pending"
        )
        print()
        print(summary["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
