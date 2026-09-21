"""What a change touches, and what a piece of code is connected to.

This answers two questions -- "what did that change actually affect" and
"what does this bit of code do here" -- and it answers both from the
repository rather than from a model. Everything here is a regex over source
text or a `git grep`, which has two consequences worth being plain about.

**It is checkable.** "Eleven files reference this function" is either true
or it is not, and the list of them is right there. A model's account of the
same change is a paraphrase you have to trust, and it costs money each time
you want one.

**It is approximate.** These are patterns, not parsers. A symbol defined
inside a conditional, a name built at runtime, a dynamic import: none of
those are found, and the output says so rather than implying completeness.
That is the honest trade for something that runs instantly and free on
every keystroke-sized change.

The split is the same one `review.py` makes and `history.py` makes: compute
the facts here, and if a model is ever asked to comment, hand it these
rather than the raw file.
"""

import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from app import workspace

# How many referencing files to name before summarising the rest. Enough to
# see the shape of the blast radius; short enough to read.
MAX_REFERENCES = 25

# Names too common to search for usefully. A grep for `get` or `read`
# returns the repository, which is not a blast radius, it is noise -- and a
# panel that cries wolf on every change is one you stop reading.
#
# The line is "would a text search for this tell you anything": a compound
# name like `fetch_rows` or `check_outgoing` always would, a bare English
# verb almost never does. Erring towards saying "I cannot tell you" beats
# erring towards a list of twenty-five irrelevant files.
TOO_COMMON = {
    # pronouns, articles, and the shortest identifiers
    "id", "on", "to", "of", "is", "at", "in", "as", "by", "it", "self", "cls",
    # verbs that appear as a method on half the classes in any codebase
    "get", "set", "run", "add", "put", "pop", "map", "new", "use",
    "read", "write", "open", "close", "load", "save", "send", "start", "stop",
    "list", "find", "make", "call", "init", "next", "prev", "copy", "move",
    "check", "parse", "build", "apply", "clear", "reset", "fetch", "render",
    "update", "delete", "remove", "create", "handle", "format", "serialize",
    # nouns that are everywhere in a web application
    "app", "db", "key", "type", "name", "data", "value", "main", "test",
    "index", "props", "state", "error", "result", "item", "row", "path",
    "file", "text", "line", "size", "body", "title", "url", "status", "kind",
    "count", "note", "user", "code", "page", "view", "form", "task", "time",
    "date", "week", "year", "month", "day", "info", "meta", "args", "kwargs",
}
MIN_NAME_LENGTH = 3


@dataclass
class Symbol:
    """A named thing defined in a file, found by pattern rather than parsed."""

    name: str
    kind: str  # function | class | constant | component
    line: int
    end_line: int
    signature: str
    exported: bool = True


# Per language, the patterns that find a definition. Ordered: the first that
# matches a line wins, so `export function` is a function rather than being
# caught by something looser later.
PATTERNS = {
    "python": [
        ("function", re.compile(r"^(?P<indent>\s*)(?:async\s+)?def\s+(?P<name>\w+)")),
        ("class", re.compile(r"^(?P<indent>\s*)class\s+(?P<name>\w+)")),
        ("constant", re.compile(r"^(?P<indent>)(?P<name>[A-Z_][A-Z0-9_]{2,})\s*[:=]")),
    ],
    "javascript": [
        (
            "function",
            re.compile(
                r"^(?P<indent>\s*)(?:export\s+)?(?:default\s+)?(?:async\s+)?"
                r"function\s+\*?(?P<name>\w+)"
            ),
        ),
        ("class", re.compile(r"^(?P<indent>\s*)(?:export\s+)?class\s+(?P<name>\w+)")),
        (
            "constant",
            re.compile(
                r"^(?P<indent>\s*)(?:export\s+)?(?:const|let|var)\s+(?P<name>\w+)"
            ),
        ),
    ],
}

EXTENSIONS = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "javascript",
    ".tsx": "javascript",
}


def language_of(path: str) -> Optional[str]:
    return EXTENSIONS.get(Path(path).suffix.lower())


def is_test(path: str) -> bool:
    """Whether a path is a test, across the conventions in use here.

    Worth its own function because "does anything test this" is the single
    most useful thing to say about a change, and it is asked in three
    places.
    """
    lowered = path.replace("\\", "/").lower()
    name = lowered.rsplit("/", 1)[-1]
    return (
        name.startswith("test_")
        or ".test." in name
        or ".spec." in name
        or "/tests/" in lowered
        or "/__tests__/" in lowered
    )


# A parameter list can run to twenty lines in a FastAPI route. Reading only
# the first line meant adding a parameter looked like an ordinary edit
# rather than a signature change, which is the one distinction that decides
# whether other files can still call it.
MAX_SIGNATURE_LINES = 40


def signature_from(lines: list[str], index: int) -> str:
    """The whole declaration, gathered until its brackets balance.

    Collapsed to one line so two signatures can be compared as strings:
    re-wrapping a parameter list is a formatting change, and treating it as
    a breaking one would cry wolf.
    """
    parts: list[str] = []
    depth = 0
    for line in lines[index : index + MAX_SIGNATURE_LINES]:
        parts.append(line.strip())
        depth += line.count("(") - line.count(")")
        if depth <= 0 and parts:
            break
    joined = " ".join(p for p in parts if p)
    # Canonicalised, so that re-wrapping a parameter list across lines --
    # which changes nothing a caller can see -- does not read as a breaking
    # change. Only spacing, trailing commas and the body's opening brace are
    # normalised; anything else is a real difference.
    text = re.sub(r"\s+", " ", joined).rstrip("{").strip()
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"[\s,]+\)", ")", text)
    return text.rstrip(":").strip() + (":" if text.endswith(":") else "")


def symbols(text: Optional[str], path: str) -> list[Symbol]:
    """Every definition the patterns can find, in order.

    `end_line` is the line before the next definition at the same or
    shallower indentation. That is a heuristic and it is wrong for a
    function followed by module-level code, but it is right often enough to
    attribute a changed line to the thing it is inside, which is all it is
    used for.
    """
    language = language_of(path)
    if not language or not text:
        return []
    patterns = PATTERNS[language]
    lines = text.splitlines()

    found: list[tuple[Symbol, int]] = []  # symbol, indent width
    for number, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith(("#", "//", "*")):
            continue
        for kind, pattern in patterns:
            match = pattern.match(line)
            if not match:
                continue
            indent = len(match.group("indent"))
            found.append(
                (
                    Symbol(
                        name=match.group("name"),
                        # A nested def is a method, which is worth
                        # distinguishing: renaming one is a smaller event
                        # than renaming a module-level function.
                        kind="method" if indent and kind == "function" else kind,
                        line=number,
                        end_line=len(lines),
                        signature=signature_from(lines, number - 1),
                        exported=indent == 0 and not match.group("name").startswith("_"),
                    ),
                    indent,
                )
            )
            break

    for position, (symbol, indent) in enumerate(found):
        for later, later_indent in found[position + 1 :]:
            if later_indent <= indent:
                symbol.end_line = later.line - 1
                break
    return [symbol for symbol, _ in found]


def symbol_at(text: Optional[str], path: str, line: int) -> Optional[Symbol]:
    """The innermost definition containing a line.

    Innermost because a method inside a class should answer as the method;
    the class is still reachable from the list.
    """
    containing = [s for s in symbols(text, path) if s.line <= line <= s.end_line]
    return containing[-1] if containing else None


# --- what changed ---------------------------------------------------------


def changed_lines(before: Optional[str], after: Optional[str]) -> tuple[set[int], set[int]]:
    """Line numbers that differ, in the old file and in the new one."""
    old_lines = (before or "").splitlines()
    new_lines = (after or "").splitlines()
    old_hit: set[int] = set()
    new_hit: set[int] = set()
    for tag, i1, i2, j1, j2 in SequenceMatcher(
        None, old_lines, new_lines, autojunk=False
    ).get_opcodes():
        if tag == "equal":
            continue
        old_hit.update(range(i1 + 1, i2 + 1))
        new_hit.update(range(j1 + 1, j2 + 1))
    return old_hit, new_hit


@dataclass
class SymbolChange:
    name: str
    kind: str
    change: str  # added | removed | modified | signature_changed
    signature: str
    line: Optional[int] = None


def changed_symbols(
    before: Optional[str], after: Optional[str], path: str
) -> list[SymbolChange]:
    """Which definitions were added, removed, altered, or re-signed.

    `signature_changed` is called out separately from `modified` because it
    is the one that breaks callers. Changing what a function does is a
    question about behaviour; changing what it takes is a question about
    every other file.
    """
    old_symbols = {s.name: s for s in symbols(before, path)}
    new_symbols = {s.name: s for s in symbols(after, path)}
    old_hit, new_hit = changed_lines(before, after)

    out: list[SymbolChange] = []
    for name, symbol in new_symbols.items():
        if name not in old_symbols:
            out.append(
                SymbolChange(name, symbol.kind, "added", symbol.signature, symbol.line)
            )
            continue
        was = old_symbols[name]
        touched = any(symbol.line <= n <= symbol.end_line for n in new_hit)
        if was.signature != symbol.signature:
            out.append(
                SymbolChange(
                    name, symbol.kind, "signature_changed", symbol.signature, symbol.line
                )
            )
        elif touched:
            out.append(
                SymbolChange(name, symbol.kind, "modified", symbol.signature, symbol.line)
            )
    for name, symbol in old_symbols.items():
        if name not in new_symbols:
            out.append(SymbolChange(name, symbol.kind, "removed", symbol.signature, None))

    order = {"removed": 0, "signature_changed": 1, "added": 2, "modified": 3}
    return sorted(out, key=lambda c: (order.get(c.change, 9), c.name))


# --- who else cares -------------------------------------------------------


def searchable(name: str) -> bool:
    return len(name) >= MIN_NAME_LENGTH and name.lower() not in TOO_COMMON


@dataclass
class References:
    name: str
    searched: bool
    files: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    hits: int = 0
    reason: Optional[str] = None
    # Set when the result is less trustworthy than a bare count suggests,
    # so the caveat travels with the number instead of being remembered.
    caveat: Optional[str] = None


def references(
    root: Path, name: str, exclude: Optional[str] = None, kind: Optional[str] = None
) -> References:
    """Everywhere else in the repository that mentions a name.

    A text search, so it finds the word in a comment as readily as a call.
    That over-reports, which for "what might this break" is the safer
    direction: a list that is too long can be read, a list that is too
    short is misleading.
    """
    if not searchable(name):
        return References(
            name=name,
            searched=False,
            reason=(
                f"'{name}' is too short or too common to search for usefully -- "
                "the results would be the whole repository."
            ),
        )
    hits = workspace.search(root, name, limit=400)
    paths: list[str] = []
    for hit in hits:
        if exclude and hit["path"] == exclude:
            continue
        if hit["path"] not in paths:
            paths.append(hit["path"])
    return References(
        name=name,
        searched=True,
        files=[p for p in paths if not is_test(p)][:MAX_REFERENCES],
        tests=[p for p in paths if is_test(p)][:MAX_REFERENCES],
        hits=len(hits),
        # A method belongs to its class, but the search does not know that.
        # Two unrelated classes with a `refresh` each look like one symbol
        # used twice, and saying so is the difference between a useful
        # number and a misleading one.
        caveat=(
            f"{name} is a method, and this is a search by name -- another "
            "class with a method called the same thing counts as a hit."
            if kind == "method"
            else None
        ),
    )


# --- module-level state ---------------------------------------------------


IMPORT_PATTERNS = {
    "python": re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))"),
    "javascript": re.compile(r"""^\s*(?:import\b[^'"]*from\s*)?['"]([^'"]+)['"]"""),
}


def module_state(text: Optional[str], path: str) -> list[Symbol]:
    """Names defined at the top level of a file.

    This is the closest honest answer to "how are variables shared". In
    both languages here, sharing happens through module scope: a top-level
    constant is read by everything that imports it, and reassigning one is
    how a change in one file is felt in another. Locals are not shared and
    are deliberately left out.
    """
    return [s for s in symbols(text, path) if s.exported and s.kind == "constant"]


def imports(text: Optional[str], path: str) -> list[str]:
    language = language_of(path)
    if not language or not text:
        return []
    pattern = IMPORT_PATTERNS[language]
    out: list[str] = []
    for line in text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        module = next((g for g in match.groups() if g), None)
        if module and module not in out:
            out.append(module)
    return out


# --- the outline for a change --------------------------------------------


# What an effect means, so the UI can colour it and a reader can skim.
# `good` is not decoration: "nothing else references this" is genuinely
# reassuring, and an outline that only ever lists dangers trains you to
# ignore it.
LEVELS = ("risk", "watch", "good", "note")


@dataclass
class Effect:
    level: str
    text: str


def line_counts(before: Optional[str], after: Optional[str]) -> dict:
    old_hit, new_hit = changed_lines(before, after)
    return {"added": len(new_hit), "removed": len(old_hit)}


def mentions(selection: str, name: str) -> bool:
    return re.search(rf"\b{re.escape(name)}\b", selection) is not None


def assess(
    root: Path,
    path: str,
    before: Optional[str],
    after: Optional[str],
    protected: Optional[list[str]] = None,
) -> dict:
    """What this change did, and what else in the repository it reaches.

    Built to be read after the fact as much as before it -- the case this
    exists for is a change that was approved too quickly, where the
    question is no longer "should I?" but "what have I just done?".
    """
    from app import agent  # local: agent imports ai, which this need not

    action = "create" if before is None else "delete" if after is None else "modify"
    changes = changed_symbols(before, after, path)
    counts = line_counts(before, after)

    # Only look up names that can actually go wrong for someone else.
    interesting = [
        c for c in changes if c.change in ("removed", "signature_changed", "added")
    ]
    refs = {
        c.name: references(root, c.name, exclude=path, kind=c.kind)
        for c in interesting
    }

    effects: list[Effect] = []

    for change in changes:
        found = refs.get(change.name)
        elsewhere = found.files if found and found.searched else []
        tested_by = found.tests if found and found.searched else []
        more = "..." if len(elsewhere) > 5 else ""

        if change.change == "removed":
            if elsewhere:
                effects.append(
                    Effect(
                        "risk",
                        f"{change.kind} {change.name} was removed, but "
                        f"{len(elsewhere)} other file(s) still mention it: "
                        + ", ".join(elsewhere[:5])
                        + more,
                    )
                )
            else:
                effects.append(
                    Effect(
                        "good",
                        f"{change.kind} {change.name} was removed and nothing "
                        "else in the repository mentions it.",
                    )
                )
        elif change.change == "signature_changed":
            if elsewhere:
                effects.append(
                    Effect(
                        "risk",
                        f"{change.name} takes different arguments now, and "
                        f"{len(elsewhere)} other file(s) mention it: "
                        + ", ".join(elsewhere[:5])
                        + more
                        + ". Callers may not match.",
                    )
                )
            else:
                effects.append(
                    Effect(
                        "watch",
                        f"{change.name} takes different arguments now. Nothing "
                        "outside this file mentions it, so the risk is local.",
                    )
                )
        elif change.change == "added" and elsewhere:
            effects.append(
                Effect(
                    "note",
                    f"{change.name} is new here, but the name already appears "
                    f"in {len(elsewhere)} other file(s) -- check it is not a clash.",
                )
            )

        if tested_by:
            effects.append(
                Effect(
                    "good",
                    f"{change.name} is mentioned by {len(tested_by)} test file(s): "
                    + ", ".join(tested_by[:3]),
                )
            )
        if found and found.caveat and elsewhere:
            effects.append(Effect("note", found.caveat))

    # Shared state: the way a change in one file is felt in another.
    old_state = {s.name: s.signature for s in module_state(before, path)}
    new_state = {s.name: s.signature for s in module_state(after, path)}
    for name, signature in new_state.items():
        was = old_state.get(name)
        if was is not None and was != signature:
            found = references(root, name, exclude=path)
            where = (
                f" {len(found.files)} other file(s) read it."
                if found.searched and found.files
                else ""
            )
            effects.append(
                Effect(
                    "watch",
                    f"The shared value {name} changed. Everything that imports "
                    f"this module sees the new value.{where}",
                )
            )

    if protected and agent.matches(path, protected):
        effects.append(
            Effect("watch", f"{path} is one of this project's protected paths.")
        )

    searched = [c.name for c in interesting if refs[c.name].searched]
    if is_test(path):
        effects.append(
            Effect("good", "This is a test file, so the change adds or alters checks.")
        )
    elif changes and not any(refs[name].tests for name in searched):
        effects.append(
            Effect(
                "watch",
                "No test file mentions anything that changed here, so the suite "
                "is unlikely to catch a mistake in it.",
            )
        )

    if action == "delete":
        effects.append(Effect("risk", f"{path} was deleted outright."))
    elif action == "create":
        effects.append(Effect("note", f"{path} is a new file."))
    elif before and counts["removed"] > len(before.splitlines()) * 0.5:
        effects.append(
            Effect(
                "watch",
                f"More than half of {path} changed -- this is a rewrite rather "
                "than an edit.",
            )
        )

    if not changes and action == "modify":
        effects.append(
            Effect(
                "note",
                "No definition changed. This is a change inside a body, to "
                "comments, or to text.",
            )
        )

    limits = [
        "Found by pattern matching, not by parsing: a name built at runtime, "
        "a dynamic import, or a definition inside a conditional is not seen.",
        "References are a text search, so a mention in a comment counts the "
        "same as a call.",
    ]
    if not language_of(path):
        limits.insert(
            0,
            f"{Path(path).suffix or 'This file type'} is not one this can read "
            "for definitions, so only the line counts are meaningful.",
        )

    order = {level: index for index, level in enumerate(LEVELS)}
    return {
        "path": path,
        "action": action,
        "lines": counts,
        "symbols": [asdict(c) for c in changes],
        "references": {name: asdict(found) for name, found in refs.items()},
        "effects": [
            asdict(e) for e in sorted(effects, key=lambda e: order.get(e.level, 9))
        ],
        "limits": limits,
    }


# --- explaining a selection ----------------------------------------------


def explain(root: Path, path: str, start_line: int, end_line: int) -> dict:
    """What a selected span of a file is, and what depends on it.

    Written for the question someone actually asks when they highlight
    something they did not write: what is this, who calls it, and what
    breaks if I change it. All three are answerable from the repository
    itself, which is why this costs nothing to ask and can be asked again.
    """
    payload = workspace.read_file(root, path)
    if payload["binary"]:
        raise workspace.WorkspaceError(f"{path} is a binary file.")
    text = payload["content"]
    lines = text.splitlines()
    start = max(1, min(start_line, len(lines) or 1))
    end = max(start, min(end_line, len(lines) or 1))
    selection = "\n".join(lines[start - 1 : end])

    enclosing = symbol_at(text, path, start)
    inside = [s for s in symbols(text, path) if start <= s.line <= end]

    # The shared values this selection reads. Module-level names are how
    # these two languages share anything at all, so "which of them does
    # this touch" is the whole of the question.
    shared = [s for s in module_state(text, path) if mentions(selection, s.name)]

    named = {s.name for s in inside}
    if enclosing and not inside:
        # A selection inside a function is a question about that function.
        named = {enclosing.name}
    kinds = {s.name: s.kind for s in inside}
    if enclosing:
        kinds.setdefault(enclosing.name, enclosing.kind)
    refs = {
        name: asdict(references(root, name, exclude=path, kind=kinds.get(name)))
        for name in sorted(named)
    }

    used_imports = [
        module
        for module in imports(text, path)
        if mentions(selection, module.rsplit(".", 1)[-1].rsplit("/", 1)[-1])
    ]

    consequences: list[Effect] = []
    for name in sorted(named):
        found = refs[name]
        if not found["searched"]:
            consequences.append(Effect("note", found["reason"]))
            continue
        if found["files"]:
            consequences.append(
                Effect(
                    "watch",
                    f"Changing {name} affects {len(found['files'])} other file(s): "
                    + ", ".join(found["files"][:5])
                    + ("..." if len(found["files"]) > 5 else ""),
                )
            )
        else:
            consequences.append(
                Effect(
                    "good",
                    f"Nothing outside this file mentions {name}, so a change to "
                    "it is contained here.",
                )
            )
        if found["tests"]:
            consequences.append(
                Effect(
                    "good",
                    f"{len(found['tests'])} test file(s) mention {name}: "
                    + ", ".join(found["tests"][:3]),
                )
            )
        else:
            consequences.append(Effect("watch", f"No test file mentions {name}."))
        if found["caveat"]:
            consequences.append(Effect("note", found["caveat"]))

    for symbol in shared:
        found = references(root, symbol.name, exclude=path)
        consequences.append(
            Effect(
                "watch",
                f"{symbol.name} is defined at the top of this file, so every "
                "file that imports it reads the same value."
                + (
                    f" {len(found.files)} do."
                    if found.searched and found.files
                    else " Nothing else reads it."
                ),
            )
        )

    order = {level: index for index, level in enumerate(LEVELS)}
    return {
        "path": path,
        "start_line": start,
        "end_line": end,
        "language": language_of(path),
        "enclosing": asdict(enclosing) if enclosing else None,
        "defines": [asdict(s) for s in inside],
        "shared_values": [asdict(s) for s in shared],
        "imports_used": used_imports,
        "references": refs,
        "consequences": [
            asdict(e) for e in sorted(consequences, key=lambda e: order.get(e.level, 9))
        ],
        "limits": [
            "Read from the repository by pattern and text search, not by a "
            "model: it reports what is there, and does not interpret it.",
            "A caller that reaches this through a dynamic name is not found.",
        ],
    }
