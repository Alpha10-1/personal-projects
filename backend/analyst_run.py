"""The analyst's unattended pass: pull activity, then propose.

Run on a schedule so that by the time you open the app, the evidence is
fresh and any suggestions are already waiting. It does only the
deterministic half of the loop -- sync, then propose. The narrative (what
this week actually amounted to) stays with the agent, which needs a
conversation, not a cron entry.

    python analyst_run.py                 # sync + propose
    python analyst_run.py --note          # also write a digest note
    python analyst_run.py --dry-run       # report only, change nothing
    python analyst_run.py --no-autostart  # fail if nothing is already serving

It talks to the HTTP API rather than the database, so the rules that apply to
the UI apply here too, and every write is stamped as agent-written.

If nothing is serving that API, it starts one for the length of the run and
stops it afterwards -- but only a backend it started itself, and only for an
address on this machine. Anything it cannot fix it reports and exits 1 rather
than raising: a scheduled task that fails loudly every night is a scheduled
task you turn off.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

# Findings quote task titles, which are UTF-8. The Windows console defaults to
# cp1252 and would mangle them in a scheduled task's log file.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

API_URL = os.getenv("PP_API_URL", "http://localhost:8000").rstrip("/")
DATA_DIR = Path(os.getenv("PP_DATA_DIR", Path(__file__).resolve().parent / "data"))
RUN_LOG = DATA_DIR / "analyst-runs.jsonl"
BACKEND_LOG = DATA_DIR / "analyst-backend.log"

HEADERS = {"X-PP-Source": "agent"}
TIMEOUT = httpx.Timeout(60.0)

# Only ever start a server for an address that is this machine. Anywhere else
# is somebody else's backend, and the right response to it being down is to
# say so rather than to race it with a second one.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

# Cold-start budget: SQLite opens fast, but a laptop waking at 07:30 has a
# disk that is doing several other things.
STARTUP_TIMEOUT = 60.0
STARTUP_POLL = 0.5


class RunFailed(Exception):
    pass


# --- Making sure there is something to talk to -------------------------------
#
# The scheduled run used to depend on the backend already being up, which on a
# laptop means it depended on the user having opened the app -- so the one run
# that exists to be unattended was the one that never worked unattended. It now
# starts a backend if there isn't one, and stops only what it started.


async def api_is_up(timeout: float = 2.0) -> bool:
    try:
        async with httpx.AsyncClient(base_url=API_URL, timeout=timeout) as client:
            response = await client.get("/health")
    except httpx.RequestError:
        return False
    return not response.is_error


def _start_backend() -> subprocess.Popen:
    """Start uvicorn on the port API_URL names.

    No --reload: the reloader forks a worker, and stopping the parent would
    leave that worker holding the port and the database.
    """
    parsed = urlparse(API_URL)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log = BACKEND_LOG.open("a", encoding="utf-8")
    log.write(f"\n--- started by analyst_run at {datetime.now():%Y-%m-%d %H:%M:%S} ---\n")
    log.flush()
    return subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", parsed.hostname or "127.0.0.1",
            "--port", str(parsed.port or 8000),
        ],
        cwd=Path(__file__).resolve().parent,
        stdout=log,
        stderr=subprocess.STDOUT,
    )


def _stop_backend(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def _why_it_died(process: subprocess.Popen) -> str:
    """The tail of the server's own log, which is where the real reason is --
    a port already taken, a missing dependency, a broken .env."""
    try:
        tail = BACKEND_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return " / ".join(line.strip() for line in tail[-3:] if line.strip())


@asynccontextmanager
async def serving(autostart: bool = True):
    """Run the body with the API reachable.

    Yields True if this call started the backend, so the run log can say
    whether anything else was up at the time.
    """
    if await api_is_up():
        yield False
        return

    if not autostart:
        raise RunFailed(
            f"Can't reach the tracker API at {API_URL}. Is the backend running?"
        )
    if urlparse(API_URL).hostname not in LOCAL_HOSTS:
        raise RunFailed(
            f"Can't reach the tracker API at {API_URL}, and it isn't on this "
            "machine, so there is nothing to start."
        )

    process = _start_backend()
    try:
        # Against the clock rather than by counting polls: a probe that takes
        # two seconds to time out has spent two seconds of the budget, and
        # counting iterations would call that no time at all.
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RunFailed(
                    f"Started a backend for {API_URL} and it exited immediately "
                    f"(code {process.returncode}). {_why_it_died(process)}".strip()
                )
            if await api_is_up():
                break
            await asyncio.sleep(STARTUP_POLL)
        else:
            raise RunFailed(
                f"Started a backend for {API_URL} but it wasn't answering after "
                f"{STARTUP_TIMEOUT:.0f}s. {_why_it_died(process)}".strip()
            )
        yield True
    finally:
        _stop_backend(process)


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
    parser.add_argument(
        "--no-autostart",
        action="store_true",
        help="fail if the backend isn't already running, instead of starting one",
    )
    args = parser.parse_args()

    async def go() -> dict:
        async with serving(autostart=not args.no_autostart) as started:
            summary = await run(write_note=args.note, dry_run=args.dry_run)
            summary["started_api"] = started
            return summary

    try:
        summary = asyncio.run(go())
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
