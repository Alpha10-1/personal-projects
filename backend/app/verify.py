"""Letting the agent check its own work, on exactly one command.

The agent's honest weakness has always been that it cannot tell whether
what it wrote works. It reads, it edits, it explains itself well, and every
word of that is a claim you have to check by hand. One test run turns the
whole thing from a draft you must read line by line into one you can review
the way you would review a colleague's.

The reason it was left out for so long is that "run a command" is a much
larger door than "read and write files", and it is worth being precise
about which door this actually opens.

**The model never chooses the command.** It is a field on the project,
typed by you, in the brief. The `run_tests` tool takes *no arguments at
all* -- the model decides only *when* to run, never *what*. There is no
interpolation, no template and nothing from the conversation reaches the
argument list.

**It is not a shell.** The command is split with `shlex` and executed
directly, so `;`, `&&`, backticks and redirection are argument text rather
than syntax. That rules out one command becoming two, and it means a
pipeline has to be written into a script file in the repository -- where it
is reviewable -- rather than into a settings field.

**It refuses to run on a dirty tree.** The agent's edits live in an overlay
and never touch disk; to run tests they must briefly be written out.
Writing over uncommitted work and restoring it afterwards is the kind of
cleverness that loses somebody's afternoon, so instead: if `git status` is
not clean, the run is refused and says so. The payoff is that if this
process is killed mid-run, the recovery is the `git checkout` the user
would reach for anyway, with nothing of theirs caught up in it.

**What it puts back, it puts back byte for byte.** The original contents of
every file it touches are held in memory and written back verbatim, rather
than restored with `git checkout` -- which would have rewritten line
endings through git's own configuration and quietly converted a repository
on every test run. What it cannot undo is what the *command* creates: a
`__pycache__`, a coverage file, a build directory. Those are the ordinary
residue of running the suite by hand.

**It cannot reach the network or write outside the checkout.** Neither of
those is enforced here, and saying so plainly is better than implying a
sandbox that does not exist. What is true: the process runs as you, with
your environment, under a timeout, with its output captured and truncated.
A test suite that deletes files or posts to an API will do those things.
The protection is that you wrote the command.
"""

import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app import workspace

# Long enough for a real suite, short enough that a hung run does not hold a
# request open all afternoon. The backend suite here takes about two minutes.
TIMEOUT = 300.0

# What comes back to the model. The tail rather than the head: a test run's
# useful part is the failures and the summary line, both at the end.
MAX_OUTPUT = 8_000


class VerifyError(Exception):
    """A reason the command could not be run, phrased for the model."""


@dataclass
class Result:
    ran: bool
    passed: Optional[bool] = None
    exit_code: Optional[int] = None
    output: str = ""
    command: str = ""
    reason: Optional[str] = None
    wrote: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        """What the model is shown."""
        if not self.ran:
            return f"The tests were not run. {self.reason}"
        verdict = "passed" if self.passed else f"FAILED (exit {self.exit_code})"
        return f"`{self.command}` {verdict}.\n\n{self.output}"


def configured(project) -> Optional[str]:
    command = (getattr(project, "test_command", None) or "").strip()
    return command or None


def split(command: str) -> list[str]:
    """The command as a program and its arguments, never a shell string.

    `posix=False` on Windows so that `backend\\.venv\\Scripts\\python.exe`
    survives -- POSIX splitting eats the backslashes and leaves a path that
    does not exist.
    """
    parts = shlex.split(command, posix=os.name != "nt")
    if os.name == "nt":
        parts = [part.strip('"') for part in parts]
    if not parts:
        raise VerifyError("The project's test command is empty.")
    return parts


def blockers(root: Path) -> Optional[str]:
    """Why the tests cannot be run right now, if they cannot.

    One reason, and it is the working tree. The agent's edits have to be on
    disk for a test run to mean anything, and putting them there over
    somebody's uncommitted work is not a trade worth making for a green
    tick.
    """
    try:
        dirty = [c for c in workspace.changes(root) if c.state != "ignored"]
    except workspace.WorkspaceError as exc:
        return f"The repository could not be read: {exc}"
    if dirty:
        names = ", ".join(c.path for c in dirty[:5])
        more = f" and {len(dirty) - 5} more" if len(dirty) > 5 else ""
        return (
            f"The working copy has uncommitted changes ({names}{more}), so "
            "the tests cannot be run without writing over them. Commit or "
            "stash them and ask again. Everything else still works — this "
            "only costs you the test run."
        )
    return None


def materialise(root: Path, overlay) -> dict[str, Optional[bytes]]:
    """Put the overlay's edits on disk, and hand back what was there before.

    The original **bytes**, not a way to get them back from git. `git
    checkout --` looked like the obvious restore and is wrong: it writes
    the file through git's line-ending configuration, so on a repository
    checked out with `core.autocrlf` a file that went in as LF comes back
    as CRLF. Running the tests would silently rewrite the line endings of
    every file the agent touched, which is precisely the sort of quiet
    damage this whole arrangement exists to avoid.

    `None` as a recorded value means the file did not exist, so restoring
    it means deleting it again.
    """
    before: dict[str, Optional[bytes]] = {}
    for path, content in overlay.pending.items():
        target = workspace.safe_join(root, path)
        before[path] = target.read_bytes() if target.is_file() else None
        target.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            if target.is_file():
                target.unlink()
            continue
        # Written as bytes with the newlines the model produced, for the
        # same reason: nothing here is entitled to re-line-end a file.
        target.write_bytes(content.encode("utf-8"))
    return before


def restore(root: Path, before: dict[str, Optional[bytes]]) -> None:
    """Put back exactly the bytes that were there, or remove what was not.

    Exact by construction rather than by trusting a tool. What this cannot
    undo is anything the *command itself* created -- a `__pycache__`, a
    coverage file, a build directory. Those are the ordinary residue of
    running a test suite by hand and are left alone rather than guessed at.
    """
    failed: list[str] = []
    for path, original in before.items():
        try:
            target = workspace.safe_join(root, path)
            if original is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(original)
        except (OSError, workspace.WorkspaceError):
            failed.append(path)
    if failed:
        # Loud rather than silent: a failed restore is the one outcome this
        # design exists to prevent, and the user needs to know to do it.
        raise VerifyError(
            "These files could not be put back after the test run: "
            + ", ".join(failed)
            + ". Run `git checkout -- .` in the checkout to undo them."
        )


def run(project, overlay) -> Result:
    """Write the overlay out, run the project's command, put it all back."""
    command = configured(project)
    if not command:
        return Result(
            ran=False,
            reason=(
                "This project has no test command set, so there is nothing "
                "to run. It is a field on the project's brief."
            ),
        )

    root = overlay.root
    blocked = blockers(root)
    if blocked:
        return Result(ran=False, command=command, reason=blocked)

    try:
        argv = split(command)
    except VerifyError as exc:
        return Result(ran=False, command=command, reason=str(exc))

    cwd = root
    where = (getattr(project, "test_dir", None) or "").strip()
    if where:
        try:
            cwd = workspace.safe_join(root, where)
        except workspace.WorkspaceError as exc:
            return Result(ran=False, command=command, reason=str(exc))

    before = materialise(root, overlay)
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT,
            check=False,
        )
        combined = (proc.stdout or "") + (proc.stderr or "")
        return Result(
            ran=True,
            passed=proc.returncode == 0,
            exit_code=proc.returncode,
            output=tail(combined),
            command=command,
            wrote=sorted(before),
        )
    except FileNotFoundError:
        return Result(
            ran=False,
            command=command,
            reason=f"`{argv[0]}` was not found. Check the project's test command.",
        )
    except subprocess.TimeoutExpired:
        return Result(
            ran=False,
            command=command,
            reason=f"The tests did not finish within {TIMEOUT:g}s and were stopped.",
        )
    finally:
        restore(root, before)


def tail(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return "…(earlier output cut)…\n" + text[-MAX_OUTPUT:]
