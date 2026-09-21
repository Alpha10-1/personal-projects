"""Everything the repository actually contains, read rather than remembered.

The point of this module is one sentence: **a suggestion for something that
already exists is worse than no suggestion at all.** It teaches you that the
suggestions are not worth reading, and once you have learned that, the ones
that matter go unread too.

So the shape here is deliberate and has two halves.

`scan()` builds an inventory of the repository from the files themselves --
every module, every definition, every route, every table, every environment
variable it reads. That is what a model is given, so "what has been built"
is something it is told rather than something it guesses.

`verify()` is the backstop, and is the part that does the real work. For
each gap proposed, the model must say what it would expect to find in the
repository *if that thing already existed*. Those terms are searched for. A
gap whose evidence turns up is dropped before it ever becomes a suggestion,
and the reason is reported rather than swallowed.

That filter errs towards dropping. A real gap wrongly filtered out is
visible -- it is listed as considered and dropped, with the term that
matched -- whereas a suggestion to build what is already built is the
failure that makes the whole feature worthless. Given the choice, be quiet.
"""

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

from app import impact, workspace

# Files to read. A repository with more source files than this is one where
# the inventory should be scoped to an area rather than silently truncated,
# and the result says which it was.
MAX_FILES = 600
MAX_FILE_BYTES = 200_000

# Two segments, as `history.py` uses: `backend/app`, `src/components`.
AREA_DEPTH = 2

SOURCE_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".css", ".sql", ".sh", ".ps1",
}
DOC_SUFFIXES = {".md", ".rst", ".txt"}
CONFIG_NAMES = {
    "package.json", "requirements.txt", "pyproject.toml", "Dockerfile",
    "docker-compose.yml", "vitest.config.mjs", "next.config.mjs",
    ".env.example", "Makefile",
}

# A FastAPI or Flask route, and a Next.js App Router page.
HTTP_ROUTE = re.compile(
    r"""@(?:router|app)\.(get|post|put|patch|delete)\(\s*["']([^"']*)["']""",
    re.IGNORECASE,
)
TABLE = re.compile(r"""__tablename__\s*=\s*["']([^"']+)["']""")
ENV_VAR = re.compile(
    r"""(?:os\.getenv|os\.environ\.get|os\.environ\[)\s*\(?\s*["']([A-Z][A-Z0-9_]*)["']"""
)
JS_ENV_VAR = re.compile(r"""process\.env\.([A-Z][A-Z0-9_]*)""")


def tracked_files(root: Path) -> list[str]:
    """What git knows about, which is the honest definition of "the project".

    Using the index rather than walking means build output, caches and
    anything gitignored are excluded for free -- and excluded the same way
    the repository's own author decided they should be.
    """
    try:
        raw = workspace.git(root, "ls-files", timeout=30.0)
    except workspace.WorkspaceError:
        return []
    return [line.strip() for line in raw.splitlines() if line.strip()]


def area_of(path: str) -> str:
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if len(parts) <= 1:
        return "(root)"
    return "/".join(parts[:AREA_DEPTH][: max(1, len(parts) - 1)])


def read(root: Path, relative: str) -> Optional[str]:
    try:
        payload = workspace.read_file(root, relative)
    except workspace.WorkspaceError:
        return None
    if payload["binary"] or len(payload["content"]) > MAX_FILE_BYTES:
        return None
    return payload["content"]


def scan(root: Path) -> dict:
    """An inventory of the repository as it stands.

    Everything here is read off the files. Nothing is inferred, nothing is
    remembered from a previous run, and nothing is asked of a model -- which
    is what makes it usable as the ground truth a model is checked against.
    """
    paths = tracked_files(root)
    truncated = len(paths) > MAX_FILES
    considered = paths[:MAX_FILES]

    modules: list[dict] = []
    routes: list[dict] = []
    tables: list[str] = []
    env_vars: set[str] = set()
    pages: list[str] = []
    components: list[str] = []
    docs: list[str] = []
    configs: list[str] = []
    areas: dict[str, dict] = defaultdict(lambda: {"files": 0, "lines": 0, "tests": 0})

    total_lines = 0
    test_files = 0

    for relative in considered:
        suffix = Path(relative).suffix.lower()
        name = Path(relative).name

        if name in CONFIG_NAMES:
            configs.append(relative)
        if suffix in DOC_SUFFIXES:
            docs.append(relative)
            continue
        if suffix not in SOURCE_SUFFIXES:
            continue

        text = read(root, relative)
        if text is None:
            continue

        lines = text.count("\n") + 1
        total_lines += lines
        is_test = impact.is_test(relative)
        test_files += 1 if is_test else 0

        bucket = areas[area_of(relative)]
        bucket["files"] += 1
        bucket["lines"] += lines
        bucket["tests"] += 1 if is_test else 0

        for method, http_path in HTTP_ROUTE.findall(text):
            routes.append(
                {"method": method.upper(), "path": http_path, "file": relative}
            )
        tables.extend(TABLE.findall(text))
        env_vars.update(ENV_VAR.findall(text))
        env_vars.update(JS_ENV_VAR.findall(text))

        normalised = relative.replace("\\", "/")
        if normalised.endswith("/page.js") or normalised.endswith("/page.jsx"):
            pages.append(normalised)
        elif "/components/" in normalised and not is_test:
            components.append(normalised)

        if is_test:
            continue

        symbols = [
            {"name": s.name, "kind": s.kind, "signature": s.signature, "line": s.line}
            for s in impact.symbols(text, relative)
            if s.exported
        ]
        if symbols or suffix in {".py", ".js", ".jsx", ".ts", ".tsx"}:
            modules.append(
                {
                    "path": relative,
                    "area": area_of(relative),
                    "lines": lines,
                    "docstring": first_doc(text, suffix),
                    "symbols": symbols,
                }
            )

    return {
        "counts": {
            "tracked_files": len(paths),
            "scanned": len(considered),
            "source_files": len(modules) + test_files,
            "test_files": test_files,
            "lines": total_lines,
            "routes": len(routes),
            "tables": len(set(tables)),
        },
        "truncated": truncated,
        "areas": [
            {"area": area, **stats}
            for area, stats in sorted(
                areas.items(), key=lambda kv: -kv[1]["lines"]
            )
        ],
        "modules": modules,
        "routes": sorted(routes, key=lambda r: (r["file"], r["path"])),
        "tables": sorted(set(tables)),
        "pages": sorted(pages),
        "components": sorted(components),
        "env_vars": sorted(env_vars),
        "docs": sorted(docs),
        "configs": sorted(configs),
    }


DOCSTRING = re.compile(r'^\s*(?:"""|\'\'\')(.+?)(?:"""|\'\'\')', re.S)
JS_BLOCK = re.compile(r"^\s*/\*\*(.+?)\*/", re.S)


def first_doc(text: str, suffix: str) -> Optional[str]:
    """The module's own one-line account of itself.

    Worth carrying into the inventory: a file that says what it is for at
    the top is telling the truth more reliably than a name can, and it costs
    nothing to include.
    """
    pattern = DOCSTRING if suffix in {".py", ".pyi"} else JS_BLOCK
    match = pattern.match(text.lstrip("﻿"))
    if not match:
        return None
    body = match.group(1).strip().lstrip("*").strip()
    first = body.split("\n\n")[0].replace("\n", " ").replace("*", " ")
    return re.sub(r"\s+", " ", first).strip()[:200] or None


# --- proving a gap is really a gap ---------------------------------------


# A term shorter than this matches everything. The model is asked for
# identifiers, not words, and this is the floor below which a "match" says
# nothing either way.
MIN_TERM = 4

# Free text needs a higher bar than an exact one. Matching `list_widgets`
# against the list of definitions is unambiguous at four characters;
# grepping for `auth` is not, and on a real run that discarded a genuine
# "there is no authentication" gap because the word appears in a comment,
# in a glob pattern, and inside the SDK's own error strings.
MIN_SEARCH_TERM = 6


def index(root: Path) -> dict:
    """Everything the repository defines, by name, plus its paths.

    Built once per review rather than per gap: a review proposes a dozen
    gaps with several terms each, and running that many greps is the
    difference between a second and a minute.
    """
    inventory = scan(root)
    names = set()
    # word -> the definitions containing it, so "how common is this word
    # here" is answerable without re-walking every symbol per gap.
    word_index: dict[str, set[str]] = defaultdict(set)
    for module in inventory["modules"]:
        for symbol in module["symbols"]:
            names.add(symbol["name"].lower())
            for word in words_of(symbol["name"]):
                word_index[word].add(symbol["name"])
    return {
        "inventory": inventory,
        "names": names,
        "word_index": word_index,
        "paths": {m["path"].lower() for m in inventory["modules"]}
        | {p.lower() for p in inventory["pages"] + inventory["components"]},
        "routes": {r["path"].lower() for r in inventory["routes"]},
        "tables": {t.lower() for t in inventory["tables"]},
        "env_vars": {e.lower() for e in inventory["env_vars"]},
    }


def already_there(root: Path, term: str, built: dict) -> Optional[str]:
    """Evidence that `term` is already present, or None.

    Checked against the inventory first because that is free, and only then
    against the file contents. The string returned is shown to the user --
    it is the reason a suggestion was not made, and a reason nobody can read
    is the same as no reason.
    """
    needle = (term or "").strip().lower()
    if len(needle) < MIN_TERM:
        return None

    if needle in built["names"]:
        return f"`{term}` is already defined in this repository"
    if needle in built["tables"]:
        return f"there is already a `{term}` table"
    if needle in built["env_vars"]:
        return f"`{term}` is already a configuration variable"
    for path in built["paths"]:
        if needle in path:
            return f"`{path}` already exists"
    for route in built["routes"]:
        if needle in route:
            return f"the route `{route}` already exists"

    if len(needle) < MIN_SEARCH_TERM:
        return None
    hits = workspace.search(root, term, limit=12, whole_word=True)
    # A mention in a test is a test for something that may not exist; a
    # mention in the README is a plan, a caveat, or a description of what
    # is missing. Neither is an implementation, and treating them as one
    # is how a real gap gets filtered out.
    real = [
        h
        for h in hits
        if not impact.is_test(h["path"])
        and Path(h["path"]).suffix.lower() not in DOC_SUFFIXES
    ]
    if real:
        where = ", ".join(sorted({h["path"] for h in real})[:3])
        return f"`{term}` already appears in {where}"
    return None


# A word this long is worth comparing at all. Shorter than this and "list"
# or "get" matches half the repository.
DISTINCTIVE = 5

# How many different definitions a word may appear in before it stops being
# evidence of anything. `project` is in forty names here and says nothing;
# `protected` is in two and says a great deal.
COMMON_ENOUGH_TO_IGNORE = 4

# Words that appear in a title without saying what the work is.
EMPTY_WORDS = {
    "expose", "configure", "override", "support", "provide", "create",
    "improve", "better", "simple", "proper", "handle", "system", "should",
    "feature", "option", "ability", "instead", "within", "across", "before",
    "command", "endpoint", "endpoints", "automatically", "window",
}

CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def words_of(name: str) -> set[str]:
    """An identifier split into the words it is made of.

    Whole words, not substrings. Matching on substrings found `backup`
    inside `FeedbackUpdate`, which is the kind of evidence that makes a
    warning worse than no warning.
    """
    parts = CAMEL.sub(" ", name or "").replace("_", " ").replace("-", " ")
    return {word.lower() for word in parts.split() if word}


def related(title: str, built: dict) -> list[str]:
    """Existing definitions that share a distinctive word with a proposed title.

    Weaker evidence than a `look_for` hit, and treated as such: it warns
    rather than discards. The case it exists for is real -- a model
    proposed "configure protected file patterns per project" for a codebase
    that already had `Project.protected_paths` and
    `agent.protected_patterns`, and the gap survived only because it had
    guessed different identifiers to search for.

    It never drops anything, because it is deliberately imprecise: a gap
    about paginating the activity feed shares a word with a dozen
    functions and is still a real gap.
    """
    index = built.get("word_index") or {}
    wanted = {
        word
        for word in words_of(title.replace("/", " "))
        if len(word) >= DISTINCTIVE and word not in EMPTY_WORDS
    }
    hits: set[str] = set()
    for word in wanted:
        found = index.get(word) or set()
        if 0 < len(found) <= COMMON_ENOUGH_TO_IGNORE:
            hits.update(found)
    return sorted(hits, key=str.lower)[:8]


def verify(root: Path, gaps: list[dict], built: Optional[dict] = None) -> dict:
    """Split proposed gaps into the ones worth raising and the ones already done.

    A gap survives only if *none* of the terms it named can be found. That
    is deliberately strict: the cost of a false negative is a gap you have
    to notice yourself, and the cost of a false positive is a suggestion
    telling you to build what you built last week.

    Both lists come back. A gap dropped in silence is indistinguishable
    from one the model never thought of, and the difference matters when
    you are deciding whether to trust the filter.
    """
    built = built or index(root)
    keep: list[dict] = []
    dropped: list[dict] = []

    for gap in gaps:
        terms = [t for t in (gap.get("look_for") or []) if t]
        if not terms:
            # Nothing to check it against. Kept, but marked, because a gap
            # that cannot be verified is not the same as one that was.
            keep.append(
                {
                    **gap,
                    "verified": False,
                    "possibly_related": related(gap.get("title", ""), built),
                }
            )
            continue
        found = [(term, already_there(root, term, built)) for term in terms]
        evidence = [why for _, why in found if why]
        if evidence:
            dropped.append(
                {
                    **gap,
                    "already_done_because": evidence[0],
                    "all_evidence": evidence,
                }
            )
        else:
            keep.append(
                {
                    **gap,
                    "verified": True,
                    "checked": terms,
                    "possibly_related": related(gap.get("title", ""), built),
                }
            )

    return {"gaps": keep, "already_done": dropped}


def summarise(inventory: dict) -> str:
    """The inventory in the few hundred characters a prompt can afford.

    The module list is the expensive part and the useful part, so it is the
    one thing given in full; everything else is counted.
    """
    counts = inventory["counts"]
    return (
        f"{counts['source_files']} source files ({counts['test_files']} tests), "
        f"{counts['lines']:,} lines, {counts['routes']} HTTP routes, "
        f"{counts['tables']} database tables across "
        f"{len(inventory['areas'])} areas"
    )


def as_prompt(inventory: dict, max_chars: int = 32_000) -> str:
    """The inventory laid out for a model to read.

    Ordered largest area first, because that is where the project actually
    is, and truncated with a marker rather than silently -- a model that
    thinks it has seen everything will confidently propose what it did not.
    """
    parts: list[str] = [f"# What this repository contains\n\n{summarise(inventory)}\n"]

    parts.append("## Areas\n")
    for area in inventory["areas"][:20]:
        parts.append(
            f"- `{area['area']}`: {area['files']} files, {area['lines']:,} lines"
            + (f", {area['tests']} of them tests" if area["tests"] else "")
        )

    if inventory["routes"]:
        parts.append("\n## HTTP routes\n")
        for route in inventory["routes"][:80]:
            parts.append(f"- `{route['method']} {route['path']}` ({route['file']})")

    if inventory["tables"]:
        parts.append("\n## Database tables\n")
        parts.append(", ".join(f"`{t}`" for t in inventory["tables"]))

    if inventory["pages"]:
        parts.append("\n## Pages\n")
        parts.append(", ".join(f"`{p}`" for p in inventory["pages"]))

    if inventory["env_vars"]:
        parts.append("\n## Configuration it reads\n")
        parts.append(", ".join(f"`{e}`" for e in inventory["env_vars"][:60]))

    parts.append("\n## Modules and what they define\n")
    for module in inventory["modules"]:
        head = f"\n### `{module['path']}` ({module['lines']} lines)"
        if module["docstring"]:
            head += f"\n{module['docstring']}"
        names = ", ".join(
            f"`{s['name']}`" for s in module["symbols"][:40]
        )
        parts.append(head + (f"\nDefines: {names}" if names else "\nDefines nothing at the top level."))

    if inventory["docs"]:
        parts.append("\n## Documentation present\n")
        parts.append(", ".join(f"`{d}`" for d in inventory["docs"]))

    if inventory["truncated"]:
        parts.append(
            f"\n**Only the first {MAX_FILES} tracked files were read.** Treat "
            "anything you did not see as unknown rather than absent."
        )

    text = "\n".join(parts)
    if len(text) > max_chars:
        return (
            text[:max_chars]
            + "\n\n**The inventory was cut off here.** Anything below this "
            "point you have not seen -- do not assume it is missing."
        )
    return text
