"""The coding agent: a model with tools, editing into an overlay.

This is the first part of the system that writes source code, so it is worth
being explicit about what stops it doing damage.

**Nothing is written while the model is thinking.** Every `write_file` and
`edit_file` goes into an in-memory overlay keyed by path. Reads consult the
overlay first, so the model sees its own edits and can build on them, but
the working tree on disk is untouched until someone applies the run. A run
that goes wrong, runs out of turns, or produces nonsense costs money and
nothing else.

**The proposal is full file contents, not a patch.** A patch has to be
applied to something, and by the time anyone reviews it the file may have
moved. Storing the whole intended file, plus the commit it was reasoned
against, means staleness is detectable rather than silently resolved.

**Protected paths force a human step.** A project names the parts of itself
that are core. Touch one and the run cannot be auto-applied, regardless of
what the request asked for -- `auto_apply` is a preference, `review_required`
is not.

**Every path goes through `workspace.safe_join`.** The model chooses these
strings, which makes them exactly as untrusted as anything a user types.

What this deliberately does not have is a shell. No `run tests`, no `npm
install`, no arbitrary command. A model that can edit files and be reviewed
is a useful assistant; a model that can execute anything is a different
security question, and not one to answer by accident while adding a feature.
"""

import fnmatch
import json
from dataclasses import dataclass, field
from difflib import unified_diff
from pathlib import Path
from typing import Optional

from app import ai, workspace

# Turns, not minutes. A run that hasn't finished in this many exchanges is
# lost rather than slow, and each turn re-sends everything before it, so the
# cost of the twentieth is several times the cost of the first.
MAX_TURNS = 24

# Per run, across all reads. Without this a model that greps badly can pull
# a repository's worth of text into the context one file at a time.
MAX_READ_BYTES = 250_000

# A single write. Larger than any source file that should exist; a model
# proposing more than this has misunderstood the job.
MAX_WRITE_BYTES = 200_000

# Never *read*, and therefore never sent to the model. This is the stronger
# of the two lists and the one that matters most: a write the agent should
# not have made is visible in the diff and can be discarded, whereas a
# credential it read has already left the machine by the time anyone looks.
#
# The tracker's own repository has a `.env.local` in its root. Nothing in
# `privacy.py` would have stopped it going out -- that guard is about Power
# BI identifiers -- so this list is what stands between an agent that can
# read files and an API key in a prompt.
NEVER_READ = (
    "*.env",
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "**/*.pem",
    "**/*.key",
    "**/*.pfx",
    "**/*.p12",
    "**/id_rsa*",
    "**/id_ed25519*",
    "**/credentials.json",
    "**/serviceAccountKey.json",
    "**/*secret*.json",
    "**/.npmrc",
    "**/.pypirc",
    "**/.netrc",
)

# Never editable, whatever a project says, because editing them is never
# what was asked for and is sometimes catastrophic. `.git` would corrupt the
# repository; the rest are installed code and everything above.
NEVER_WRITE = NEVER_READ + (
    ".git/*",
    "**/.git/*",
    "node_modules/*",
    "**/node_modules/*",
    ".venv/*",
)

# The default answer to "which parts of this project are core". Migrations
# because they are append-only history, CI because it is the thing that
# would catch a mistake, lockfiles because regenerating one by hand is
# always wrong, and auth because it is auth.
DEFAULT_PROTECTED = (
    "**/migrations/*",
    "**/alembic/*",
    ".github/workflows/*",
    "package-lock.json",
    "**/requirements*.txt",
    "**/auth*.py",
    "**/privacy.py",
    "**/models.py",
)


class AgentError(RuntimeError):
    """The run could not be carried out. Shown to the user as written."""


def protected_patterns(raw: Optional[str]) -> list[str]:
    """A project's protected globs, or the built-in list if it has none."""
    lines = [line.strip() for line in (raw or "").splitlines() if line.strip()]
    return lines or list(DEFAULT_PROTECTED)


def matches(path: str, patterns) -> bool:
    """Glob match that behaves the way people expect from these patterns.

    `fnmatch` does not treat `/` specially, so `**/x` does not match a
    top-level `x`. Testing the bare name as well is the cheap fix, and it
    errs towards protecting more rather than less -- the right direction
    for a list whose job is to force a person to look.
    """
    candidate = path.replace("\\", "/")
    name = candidate.rsplit("/", 1)[-1]
    for pattern in patterns:
        if fnmatch.fnmatch(candidate, pattern):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatch(name, pattern[3:]):
            return True
    return False


# --- the overlay ----------------------------------------------------------


@dataclass
class Overlay:
    """Pending edits, held in memory for the length of a run.

    `None` as a value means "delete this file", which is distinct from
    absent, meaning "unchanged".
    """

    root: Path
    pending: dict[str, Optional[str]] = field(default_factory=dict)
    read_bytes: int = 0

    def read(self, relative: str) -> str:
        if matches(relative, NEVER_READ):
            raise AgentError(
                f"{relative} holds credentials and is never readable by the "
                "agent. Nothing in it has been sent anywhere."
            )
        if relative in self.pending:
            content = self.pending[relative]
            if content is None:
                raise AgentError(f"{relative} was deleted earlier in this run.")
            return content
        payload = workspace.read_file(self.root, relative)
        if payload["binary"]:
            raise AgentError(f"{relative} is a binary file.")
        self.read_bytes += len(payload["content"])
        if self.read_bytes > MAX_READ_BYTES:
            raise AgentError(
                "This run has read as much of the repository as it is allowed "
                "to. Narrow the instruction to the files it concerns."
            )
        return payload["content"]

    def original(self, relative: str) -> Optional[str]:
        """What is on disk now, ignoring the overlay. None if absent."""
        try:
            payload = workspace.read_file(self.root, relative)
        except workspace.WorkspaceError:
            return None
        return None if payload["binary"] else payload["content"]

    def write(self, relative: str, content: str) -> None:
        if matches(relative, NEVER_WRITE):
            raise AgentError(
                f"{relative} cannot be edited by the agent under any "
                "circumstances -- it is credentials, installed code, or git's "
                "own storage."
            )
        if len(content) > MAX_WRITE_BYTES:
            raise AgentError(f"{relative} would be larger than the write limit.")
        workspace.safe_join(self.root, relative)  # refuses anything outside
        self.pending[relative] = content

    def delete(self, relative: str) -> None:
        if matches(relative, NEVER_WRITE):
            raise AgentError(f"{relative} cannot be deleted by the agent.")
        workspace.safe_join(self.root, relative)
        self.pending[relative] = None

    def changes(self) -> list[dict]:
        """The proposal, one entry per file, ordered for reading."""
        out = []
        for path in sorted(self.pending):
            content = self.pending[path]
            before = self.original(path)
            if content is None:
                if before is None:
                    continue  # deleting something that was never there
                action = "delete"
            elif before is None:
                action = "create"
            elif before == content:
                continue  # rewritten to exactly what it already was
            else:
                action = "modify"
            out.append({"path": path, "action": action, "content": content})
        return out

    def diff(self) -> str:
        """A unified diff of the whole proposal.

        Built here rather than by git, because git would need the files
        written first -- which is the one thing this must not do.
        """
        chunks = []
        for change in self.changes():
            path = change["path"]
            before = self.original(path) or ""
            after = change["content"] or ""
            chunks.extend(
                unified_diff(
                    before.splitlines(keepends=True),
                    after.splitlines(keepends=True),
                    fromfile=f"a/{path}" if before else "/dev/null",
                    tofile=f"b/{path}" if change["action"] != "delete" else "/dev/null",
                )
            )
            if chunks and not chunks[-1].endswith("\n"):
                chunks.append("\n")
        return "".join(chunks)


# --- the tools the model is given ----------------------------------------


TOOLS = [
    {
        "name": "list_files",
        "description": (
            "List one folder of the repository, one level deep. Start here "
            "rather than guessing at paths."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Folder relative to the repository root. Empty for the root.",
                }
            },
        },
    },
    {
        "name": "search",
        "description": (
            "Fixed-string search across tracked files. Returns path, line "
            "number and the matching line. Use it to find where something is "
            "defined before reading whole files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read a file's current contents, including any edit you have "
            "already made in this run. Read a file before editing it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Replace an exact string in a file. `old` must appear exactly "
            "once, including indentation. Prefer this over write_file: it "
            "cannot silently discard the rest of the file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["path", "old", "new"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write a file's entire contents, creating it if needed. Use only "
            "for new files or a complete rewrite."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "finish",
        "description": (
            "End the run and report. Call this exactly once, when the change "
            "is complete or when you have concluded you cannot make it. Say "
            "what you changed, why, and -- importantly -- anything you were "
            "unsure of or did not do."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": (
                        "What you changed and why, in prose. Name anything "
                        "you could not verify."
                    ),
                }
            },
            "required": ["summary"],
        },
    },
]


# How many rolling cache breakpoints to keep in the conversation. Two, so a
# turn can hit the cache written by the turn before it as well as the one
# before that -- a single moving marker means every other turn starts from
# a prefix nothing has cached yet.
CACHE_POINTS = 2


def mark_cache(messages: list[dict]) -> None:
    """Move the cache breakpoints to the end of the conversation.

    Without this an agent run re-sends the whole exchange at full price on
    every turn, and the exchange grows by a file each time. The first three
    real runs here cost $0.93 for one edited line, almost all of it re-read
    input.

    Only the tool-result turns this module builds are marked. The assistant
    turns are the SDK's own objects and are left exactly as they came back,
    because the API compares them against what it sent.
    """
    ours = [
        m["content"]
        for m in messages
        if isinstance(m.get("content"), list)
        and m["content"]
        and isinstance(m["content"][-1], dict)
    ]
    for content in ours:
        content[-1].pop("cache_control", None)
    for content in ours[-CACHE_POINTS:]:
        content[-1]["cache_control"] = {"type": "ephemeral"}


def match_endings(content: str, fragment: str) -> str:
    """Give a fragment the line endings of the file it is going into.

    Found by the agent itself, on its first real run: every multi-line
    `edit_file` came back "that exact string is not in the file", while
    single-line ones worked. The cause is that `read_file` numbers lines and
    rejoins them with `\\n`, so what the model copies back can never match a
    CRLF file -- and the one edit that did land wrote an LF line into a CRLF
    file, which would have shown up as a spurious whole-line change.

    Normalising to LF first means a fragment that already has the right
    endings is left alone rather than gaining `\\r\\r\\n`.
    """
    if "\r\n" not in content:
        return fragment
    return fragment.replace("\r\n", "\n").replace("\n", "\r\n")


def run_tool(overlay: Overlay, name: str, args: dict) -> str:
    """Carry out one tool call and return what the model should see.

    Errors come back as text rather than raising: "that string appears twice"
    is information the model can act on, and killing the run over it would
    waste everything spent so far.
    """
    try:
        if name == "list_files":
            entries = workspace.listing(overlay.root, args.get("path", ""))
            if not entries:
                return "(empty folder)"
            return "\n".join(
                f"{e['type']:4} {e['path']}" for e in entries
            )

        if name == "search":
            hits = workspace.search(overlay.root, args.get("pattern", ""))
            # A search result carries the matching line, so an unfiltered
            # grep is a read of every tracked file at once. One of these
            # repositories has a committed serviceAccountKey.json; without
            # this, searching for "key" would have returned its contents.
            hits = [h for h in hits if not matches(h["path"], NEVER_READ)]
            if not hits:
                return "No matches."
            return "\n".join(f"{h['path']}:{h['line']}: {h['text']}" for h in hits)

        if name == "read_file":
            path = args["path"]
            content = overlay.read(path)
            # Numbered, because the next thing asked of this file is usually
            # "change the bit around line N" and unnumbered text makes that
            # a counting exercise.
            lines = content.splitlines()
            return "\n".join(f"{i:>5}  {line}" for i, line in enumerate(lines, 1))

        if name == "edit_file":
            path, old, new = args["path"], args["old"], args["new"]
            content = overlay.read(path)
            old, new = match_endings(content, old), match_endings(content, new)
            count = content.count(old)
            if count == 0:
                return (
                    "That exact string is not in the file. Read it again and "
                    "copy the text including indentation."
                )
            if count > 1:
                return (
                    f"That string appears {count} times. Include enough "
                    "surrounding lines to make it unique."
                )
            overlay.write(path, content.replace(old, new, 1))
            return f"Edited {path}."

        if name == "write_file":
            path, content = args["path"], args["content"]
            overlay.write(path, content)
            return f"Wrote {path} ({len(content):,} characters)."

    except (AgentError, workspace.WorkspaceError) as exc:
        return f"Error: {exc}"
    except KeyError as exc:
        return f"Error: missing argument {exc}."

    return f"Error: unknown tool {name}."


SYSTEM = """You are editing a real repository on the user's own machine, \
through tools. You are careful, and you are working on code someone depends on.

How to work:
- Look before you write. List, search, and read the files you are about to \
change. Never edit a file you have not read in this run.
- Match the code around you -- its naming, its idiom, its comment density. \
A change that reads as though it was written by the same person is worth \
more than a clever one.
- Make the change that was asked for. Do not reformat, do not tidy nearby \
code, do not upgrade anything you were not asked to upgrade.
- Prefer edit_file to write_file. write_file replaces the whole file, and a \
whole file written from memory loses whatever you did not think to include.

What you must not do:
- Do not invent APIs, files, or function names. If you need to know whether \
something exists, search for it.
- Do not write credentials, keys or tokens into any file.
- Do not claim you verified something you could not. You have no shell and \
cannot run tests, so say the change is untested -- that is expected, and \
saying so honestly is worth more than confidence.

End by calling finish with an honest summary, including what you were unsure \
of. Someone reads your diff before it is applied; write for that person."""


def brief(project, instruction: str, tree: str, protected: list[str]) -> str:
    """The opening message: the job, the project, and the ground rules.

    Keeping the protected list in the prompt as well as enforcing it means
    the model avoids those files on purpose rather than being caught doing
    it -- the enforcement is still what makes it true.
    """
    parts = [f"# The change to make\n\n{instruction.strip()}\n"]
    facts = [f"Project: {project.name}"]
    if project.summary:
        facts.append(f"Summary: {project.summary}")
    if project.tech_stack:
        facts.append(f"Stack: {project.tech_stack}")
    if project.objective:
        facts.append(f"Objective: {project.objective}")
    parts.append("# The project\n\n" + "\n".join(facts) + "\n")
    parts.append(f"# The repository root\n\n{tree}\n")
    parts.append(
        "# Files that need a person's approval\n\n"
        + "\n".join(f"- {p}" for p in protected)
        + "\n\nYou may edit these if the change genuinely requires it, but "
        "the run will then be held for review rather than applied. Say in "
        "your summary why it was necessary.\n"
    )
    return "\n".join(parts)


async def execute(project, instruction: str, *, max_turns: int = MAX_TURNS) -> dict:
    """Run the agent to completion and return the proposal.

    Returns rather than raises on a model failure: a run that ended badly is
    still a row worth keeping, and the caller needs the turn count and the
    partial overlay to say anything useful about it.
    """
    root = workspace.resolve(project.local_path)
    overlay = Overlay(root=root)
    protected = protected_patterns(project.protected_paths)

    try:
        listing = workspace.listing(root)
        tree = "\n".join(f"{e['type']:4} {e['path']}" for e in listing)
        base_sha = workspace.head(root)["last_commit"]
        base_sha = base_sha["sha"] if base_sha else None
    except workspace.WorkspaceError as exc:
        raise AgentError(str(exc)) from exc

    messages: list[dict] = [
        {
            "role": "user",
            # A list of one block rather than a bare string, so the opening
            # brief can carry a cache breakpoint like every later turn.
            "content": [
                {"type": "text", "text": brief(project, instruction, tree, protected)}
            ],
        }
    ]
    summary: Optional[str] = None
    error: Optional[str] = None
    turns = 0

    while turns < max_turns:
        turns += 1
        mark_cache(messages)
        try:
            message = await ai.converse(
                system=SYSTEM,
                messages=messages,
                tools=TOOLS,
                feature="agent_edit",
                project_id=project.id,
            )
        except (ai.AINotConfigured, ai.AIFailed) as exc:
            error = str(exc)
            break

        messages.append({"role": "assistant", "content": message.content})
        calls = [b for b in message.content if getattr(b, "type", None) == "tool_use"]

        if not calls:
            # No tools and no finish: the model has stopped of its own
            # accord. Treat whatever text it produced as the summary rather
            # than discarding a run that may have done the work already.
            summary = "".join(
                getattr(b, "text", "") for b in message.content
            ).strip() or None
            if summary is None:
                error = "The model stopped without saying anything."
            break

        results = []
        finished = False
        for call in calls:
            args = dict(call.input or {})
            if call.name == "finish":
                summary = (args.get("summary") or "").strip()
                finished = True
                results.append(
                    {"type": "tool_result", "tool_use_id": call.id, "content": "Noted."}
                )
                continue
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": run_tool(overlay, call.name, args),
                }
            )
        messages.append({"role": "user", "content": results})
        if finished:
            break
    else:
        error = (
            f"The run used all {max_turns} turns without finishing. What it "
            "had changed by then is below, but treat it as incomplete."
        )

    changes = overlay.changes()
    touched_protected = [c["path"] for c in changes if matches(c["path"], protected)]

    return {
        "changes": changes,
        "diff": overlay.diff(),
        "summary": summary,
        "error": error,
        "turns": turns,
        "base_sha": base_sha,
        "review_required": bool(touched_protected),
        "review_reason": (
            "This run changes "
            + ", ".join(touched_protected)
            + ", which this project marks as needing approval."
            if touched_protected
            else None
        ),
    }


# --- turning a proposal into files ---------------------------------------


def apply(root: Path, changes_json: Optional[str]) -> list[str]:
    """Write a stored proposal to the working tree.

    Not committed, and deliberately so: leaving the change uncommitted means
    `git diff` is the review, `git checkout` is the undo, and the Code tab
    shows it as a pending change like any edit you made yourself. Committing
    on the user's behalf would take that away.

    Ordered and validated before anything is written, so a proposal with one
    bad path does not leave the tree half-applied.
    """
    changes = json.loads(changes_json or "[]")
    targets = []
    for change in changes:
        path = change["path"]
        if matches(path, NEVER_WRITE):
            raise AgentError(f"Refusing to write {path}.")
        targets.append((workspace.safe_join(root, path), change))

    written = []
    for target, change in targets:
        if change["action"] == "delete":
            if target.exists():
                target.unlink()
            written.append(change["path"])
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        # newline="" so the content is written exactly as proposed. The
        # model was shown the file's real line endings and worked from
        # those; translating here would corrupt a CRLF repository.
        with open(target, "w", encoding="utf-8", newline="") as fh:
            fh.write(change["content"])
        written.append(change["path"])
    return written
