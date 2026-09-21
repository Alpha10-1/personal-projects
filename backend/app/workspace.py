"""The working copy on this machine, read as fact.

`history.py` answers "what has happened to this repository" from commits the
tracker has ingested from GitHub. This module answers a different question --
"what does the checkout look like *right now*" -- and it answers it by
looking at the disk, not at the database. Uncommitted edits, the branch you
are actually on, a file's current contents: none of that exists in the
activity table and none of it can be inferred from it.

Everything here is deterministic. No model is involved, nothing leaves the
machine, and every answer is either a filesystem read or a `git` invocation
whose arguments are visible at the call site.

Three rules hold throughout, because this is the first module in the system
that touches paths the user supplies:

- **A project's path must sit inside an allowed root.** Without that, a typo
  in a form field is a licence to read anything the account can read.
- **Every relative path is re-resolved and re-checked** after joining. `..`
  and symlinks both escape a naive prefix test; `Path.resolve()` then a
  containment check is the only version that holds.
- **git is run as an argument list, never through a shell,** with a timeout.
  A repository in a bad state should surface as an error, not a hung request.
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Directories that are never worth walking into. `.git` is excluded because
# browsing object files helps nobody and would dwarf the real tree; the rest
# are build output and dependencies -- the same list `history.py` calls noise,
# which is the same judgement applied to a different source.
SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".next",
    "dist",
    "build",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".turbo",
    "coverage",
}

# Read caps. A file view is for reading code, not for loading a 50MB fixture
# into a browser tab, and the agent that will later read through this module
# pays for every byte in tokens.
MAX_FILE_BYTES = 400_000
MAX_DIFF_BYTES = 200_000

# git should answer instantly on a local repository. If it hasn't in ten
# seconds something is wrong -- an index lock, a network remote, a hung
# credential prompt -- and waiting longer only moves the failure.
GIT_TIMEOUT = 10.0

# Enough to be sure: NUL anywhere in the first block means binary. Checking
# the whole file would mean reading the whole file, which is the thing the
# cap above exists to avoid.
SNIFF_BYTES = 8_000


class WorkspaceError(RuntimeError):
    """The path is missing, outside the allowed roots, or not a repository.

    Carries a sentence meant to be shown to the user, because every one of
    these is something they can fix.
    """


def allowed_roots() -> list[Path]:
    """Where a project's checkout is permitted to live.

    Defaults to the user's home directory, which covers every plausible
    location on a single-user machine while still excluding the system. Set
    `PP_WORKSPACE_ROOTS` to a path-separator-delimited list to narrow or
    widen it.
    """
    configured = (os.getenv("PP_WORKSPACE_ROOTS") or "").strip()
    if configured:
        roots = [Path(p.strip()) for p in configured.split(os.pathsep) if p.strip()]
    else:
        roots = [Path.home()]
    out = []
    for root in roots:
        try:
            out.append(root.resolve())
        except OSError:
            continue
    return out


def within_allowed(path: Path) -> bool:
    for root in allowed_roots():
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def is_repo(path: Path) -> bool:
    """A git work tree, rather than merely a folder.

    `.git` is a directory in a normal clone and a file in a worktree or a
    submodule, so both count.
    """
    return (path / ".git").exists()


def resolve(local_path: Optional[str]) -> Path:
    """Turn a stored path into one that is safe to read from.

    Raises rather than returning None: every caller needs the reason, and a
    None would only be turned back into one at each call site.
    """
    if not (local_path or "").strip():
        raise WorkspaceError("This project has no local folder set.")
    try:
        path = Path(local_path).expanduser().resolve()
    except OSError as exc:
        raise WorkspaceError(f"That path cannot be read: {exc}") from exc
    if not path.exists():
        raise WorkspaceError(f"There is nothing at {path}.")
    if not path.is_dir():
        raise WorkspaceError(f"{path} is a file, not a folder.")
    if not within_allowed(path):
        roots = ", ".join(str(r) for r in allowed_roots()) or "(none configured)"
        raise WorkspaceError(
            f"{path} is outside the allowed roots ({roots}). "
            "Set PP_WORKSPACE_ROOTS if the checkout lives elsewhere."
        )
    if not is_repo(path):
        raise WorkspaceError(f"{path} is not a git repository.")
    return path


def safe_join(root: Path, relative: str) -> Path:
    """Join and then prove the result is still inside the root.

    The proof happens after `resolve()`, so a symlink pointing out of the
    tree fails here exactly as `..` does. Testing the string before resolving
    would pass both.
    """
    candidate = (root / (relative or "")).expanduser()
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise WorkspaceError(f"That path cannot be read: {exc}") from exc
    try:
        resolved.relative_to(root)
    except ValueError:
        raise WorkspaceError(f"{relative} is outside the project folder.") from None
    return resolved


def git(root: Path, *args: str, timeout: float = GIT_TIMEOUT) -> str:
    """Run git in the checkout and return stdout.

    An argument list, never a shell string, so a branch called `; rm -rf /`
    is a branch name and nothing else.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise WorkspaceError("git is not installed, or not on the PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceError(
            f"git {args[0] if args else ''} did not finish in {timeout:g}s."
        ) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise WorkspaceError(
            f"git {' '.join(args)} failed: {detail[0] if detail else 'no output'}"
        )
    return proc.stdout


# --- reading the state of the tree ---------------------------------------


# The two porcelain columns are index and work tree. Spelling them out beats
# a lookup table nobody can read: these are the only combinations that reach
# a user, and anything else falls through to the raw code.
STATUS_WORDS = {
    "M": "modified",
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "?": "untracked",
    "!": "ignored",
    "U": "conflicted",
}


@dataclass
class Change:
    path: str
    state: str
    staged: bool
    code: str


def changes(root: Path) -> list[Change]:
    """Everything that differs from HEAD, staged or not."""
    raw = git(root, "status", "--porcelain=v1", "--untracked-files=normal")
    out: list[Change] = []
    for line in raw.splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:].strip()
        # A rename is reported as "old -> new". The new name is the one that
        # exists on disk and therefore the only one worth offering to open.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip('"')
        index, tree = code[0], code[1]
        if code == "??":
            state, staged = "untracked", False
        elif tree != " ":
            state, staged = STATUS_WORDS.get(tree, tree), False
        else:
            state, staged = STATUS_WORDS.get(index, index), True
        out.append(Change(path=path, state=state, staged=staged, code=code))
    return sorted(out, key=lambda c: c.path)


def head(root: Path) -> dict:
    """Branch, last commit, and whether anything is uncommitted.

    `symbolic-ref` rather than `rev-parse --abbrev-ref` so a detached HEAD is
    reported as detached instead of the string "HEAD", which reads like a
    branch name and has confused better systems than this one.
    """
    try:
        branch = git(root, "symbolic-ref", "--short", "HEAD").strip()
    except WorkspaceError:
        branch = None
    try:
        sha, subject, when, author = git(
            root, "log", "-1", "--format=%h%x00%s%x00%cI%x00%an"
        ).strip().split("\x00")
    except (WorkspaceError, ValueError):
        sha = subject = when = author = None
    return {
        "branch": branch,
        "detached": branch is None,
        "last_commit": (
            {"sha": sha, "subject": subject, "committed_at": when, "author": author}
            if sha
            else None
        ),
    }


def diff(root: Path, path: Optional[str] = None, staged: bool = False) -> str:
    """The unified diff against HEAD, truncated rather than refused.

    Truncation is announced in the text itself. A diff that silently stops
    halfway is worse than no diff, because it reads as complete.
    """
    args = ["diff", "--no-color"]
    if staged:
        args.append("--cached")
    if path:
        safe_join(root, path)  # validate before handing it to git
        args += ["--", path]
    text = git(root, *args, timeout=GIT_TIMEOUT * 2)
    if len(text) > MAX_DIFF_BYTES:
        return (
            text[:MAX_DIFF_BYTES]
            + f"\n\n[diff truncated at {MAX_DIFF_BYTES:,} characters]\n"
        )
    return text


def looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:SNIFF_BYTES]


def read_file(root: Path, relative: str) -> dict:
    """One file's current contents, as it is on disk right now.

    Binary files and oversized files come back described rather than
    rendered, so the caller always gets a usable answer.

    Line endings are preserved exactly. A CRLF file comes back with its
    CRLFs, because the agent writes these files back and normalising here
    would turn a one-line edit into a diff touching every line.
    """
    target = safe_join(root, relative)
    if not target.exists():
        raise WorkspaceError(f"{relative} does not exist.")
    if target.is_dir():
        raise WorkspaceError(f"{relative} is a folder.")
    size = target.stat().st_size
    with open(target, "rb") as fh:
        head_bytes = fh.read(SNIFF_BYTES)
        if looks_binary(head_bytes):
            return {
                "path": relative,
                "size": size,
                "binary": True,
                "truncated": False,
                "content": None,
            }
        # max(0, ...) because the sniff already read more than the cap when
        # the cap is small, and a negative length is an error rather than
        # "read nothing".
        rest = fh.read(max(0, MAX_FILE_BYTES - len(head_bytes) + 1))
    raw = head_bytes + rest
    truncated = len(raw) > MAX_FILE_BYTES
    text = raw[:MAX_FILE_BYTES].decode("utf-8", errors="replace")
    return {
        "path": relative,
        "size": size,
        "binary": False,
        "truncated": truncated,
        "content": text,
    }


def listing(root: Path, relative: str = "") -> list[dict]:
    """One directory, one level deep.

    Lazy rather than recursive: a full tree of a real repository is tens of
    thousands of entries, almost all of which nobody will expand.
    """
    target = safe_join(root, relative)
    if not target.is_dir():
        raise WorkspaceError(f"{relative or '.'} is not a folder.")
    entries = []
    for child in target.iterdir():
        if child.name in SKIP_DIRS:
            continue
        is_dir = child.is_dir()
        rel = child.relative_to(root).as_posix()
        entries.append(
            {
                "name": child.name,
                "path": rel,
                "type": "dir" if is_dir else "file",
                "size": None if is_dir else child.stat().st_size,
            }
        )
    # Folders first, then alphabetical -- the order every file tree uses, and
    # the one that makes a repository root readable at a glance.
    return sorted(entries, key=lambda e: (e["type"] != "dir", e["name"].lower()))


def search(
    root: Path, pattern: str, limit: int = 60, whole_word: bool = False
) -> list[dict]:
    """Fixed-string search across tracked files.

    `git grep` rather than a walk: it respects .gitignore for free, which is
    the difference between searching a repository and searching its
    `node_modules`. Fixed-string, because the caller is looking for an
    identifier far more often than for a regex, and a stray `(` should not
    be an error.

    `whole_word` is off by default because the Code tab's search box should
    behave like a search box. Callers reasoning about whether something
    exists want it on: `export` appearing inside `exported` is not evidence
    of anything.
    """
    if not (pattern or "").strip():
        return []
    try:
        args = ["grep", "--fixed-strings", "--line-number", "--no-color", "-I"]
        if whole_word:
            # `--word-regexp`, because a substring match is often the wrong
            # answer: searching for `export` found `exported` and concluded
            # a CSV export already existed.
            args.append("--word-regexp")
        raw = git(root, *args, "-e", pattern)
    except WorkspaceError:
        # git grep exits non-zero when there are simply no matches, which is
        # an answer rather than a failure.
        return []
    hits = []
    for line in raw.splitlines()[:limit]:
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        hits.append({"path": parts[0], "line": int(parts[1]), "text": parts[2][:300]})
    return hits


def state(local_path: Optional[str]) -> dict:
    """Everything the Code tab needs in one call, including why it can't work.

    A project with no folder set is the normal case, not an error, so this
    reports it as `{"available": False, "reason": ...}` rather than raising.
    The route layer would only have to catch it and do the same.
    """
    try:
        root = resolve(local_path)
    except WorkspaceError as exc:
        return {"available": False, "reason": str(exc), "root": None}
    try:
        changed = changes(root)
        return {
            "available": True,
            "reason": None,
            "root": str(root),
            **head(root),
            "changes": [
                {"path": c.path, "state": c.state, "staged": c.staged, "code": c.code}
                for c in changed
            ],
            "dirty": bool(changed),
        }
    except WorkspaceError as exc:
        return {"available": False, "reason": str(exc), "root": str(root)}
