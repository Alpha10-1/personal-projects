"""Explaining a selection with the model, grounded in what was measured.

The deterministic version of this -- `impact.explain` -- answers "who calls
it" precisely and "what does it do" not at all. That was the complaint, and
it is fair: a list of referencing files is a fact, not an explanation.

So the split here is the one the rest of the system uses. `impact.py`
establishes what is true: the enclosing definition, every file that mentions
it, which of those are tests, which module-level values the selection reads.
The model is handed those, plus the code, and asked to write the part that
needs judgement -- what it does, why it is here, and what happens if you
change it.

That ordering matters in both directions. The model does not have to guess
at the blast radius, because it was told; and it cannot invent a caller,
because the callers are listed. Where the facts say "I could not search for
this name", the model is told that too, and asked to say so rather than
fill the gap.

**Answers are cached against the file's contents.** Pressing Explain twice
on unchanged code costs nothing the second time, which is what makes it
reasonable to press at all. Edit the file and the digest changes, so the
cache misses and the answer is recomputed rather than being quietly stale.
"""

import hashlib
import json
import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ai, formatting, impact, models, workspace

# The model is given the selection plus enough around it to read: the file's
# imports, and the whole of the definition the selection sits in. Beyond
# that it is paying for lines nobody asked about.
CONTEXT_LINES = 40
MAX_SELECTION_CHARS = 6_000
MAX_CONTEXT_CHARS = 9_000

# An explanation is several paragraphs and a handful of lists. Measured at
# around 900 output tokens; the ceiling is headroom, not a target.
MAX_EXPLAIN_TOKENS = 3_000


EXPLAIN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": (
                "One or two sentences: what this code does, in plain terms, "
                "for someone who knows the language but not this codebase."
            ),
        },
        "walkthrough": {
            "type": "array",
            "description": (
                "How it works, step by step, in the order the code runs. "
                "Refer to the actual identifiers. Skip anything obvious from "
                "reading it -- explain the parts that are not."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "lines": {
                        "type": "string",
                        "description": "Which lines, e.g. '12-15' or '12'.",
                    },
                    "what": {"type": "string"},
                },
                "required": ["what"],
            },
            "maxItems": 8,
        },
        "role_in_the_system": {
            "type": "string",
            "description": (
                "Why this exists here and what depends on it. Ground this in "
                "the references you were given -- name the files. If nothing "
                "references it, say that instead of inventing a purpose."
            ),
        },
        "shared_state": {
            "type": "string",
            "description": (
                "How this shares data with the rest of the system: the "
                "module-level values it reads, what else reads them, and "
                "what changing one would do. Empty string if it shares "
                "nothing, which is worth saying."
            ),
        },
        "if_you_change_it": {
            "type": "array",
            "description": (
                "Concrete consequences of changing this, worst first. Each "
                "should name what would break or what would have to change "
                "with it, not 'be careful'."
            ),
            "items": {"type": "string"},
            "maxItems": 6,
        },
        "watch_out": {
            "type": "array",
            "description": (
                "Non-obvious behaviour a reader would miss: an edge case a "
                "guard exists for, an ordering that matters, a default that "
                "surprises. Only things actually visible in the code."
            ),
            "items": {"type": "string"},
            "maxItems": 5,
        },
        "unknowns": {
            "type": "array",
            "description": (
                "What you could not determine from what you were shown. Be "
                "specific -- 'whether callers rely on the return being "
                "sorted', not 'more context needed'."
            ),
            "items": {"type": "string"},
            "maxItems": 4,
        },
    },
    "required": ["summary", "role_in_the_system", "if_you_change_it"],
}


SYSTEM = (
    "You explain code to the person who owns it.\n\n"
    "They can read the language. What they cannot see is how this fits the "
    "rest of their system, which is why you are given measured facts about "
    "it alongside the code: the enclosing definition, every file that "
    "mentions the names involved, which of those are tests, and the "
    "module-level values the selection reads.\n\n"
    "Rules:\n"
    "- Use the facts. When you say something is depended on, name the files "
    "you were given. Never invent a caller, a test, or a file.\n"
    "- Where the facts say a name was too common to search, say the blast "
    "radius is unknown for that reason. Do not guess at it, and do not let "
    "it read as 'nothing depends on this'.\n"
    "- Explain what is not obvious. Do not narrate what the code plainly "
    "says; a reader who wanted `i += 1` explained would not have asked.\n"
    "- If a guard or an odd-looking branch exists, work out what it is "
    "for and say so. That is usually the most useful thing you can offer.\n"
    "- Not every selection is a definition. When you are told the "
    "highlight is a comment, an import or a few loose lines, answer it "
    "as what it is and keep it short, rather than dressing it up as a "
    "function with callers.\n"
    "- Say what you cannot tell. A short honest answer beats a confident "
    "wrong one, and the person reading this will act on it.\n"
    + "\n"
    + formatting.INLINE
)


# The shapes the model actually returns, as opposed to the ones the schema
# asks for. A tool schema is a strong hint, not a guarantee: asked for a
# list of consequences it will sometimes answer with one paragraph, or with
# a paragraph of Markdown bullets, and asked for a list of `{lines, what}`
# steps it will sometimes answer with a list of sentences.
#
# None of that is worth failing over, and it must never reach the browser
# unflattened -- a string where a list was expected used to crash the panel
# rather than render. So the shape is fixed here, once, before the answer
# is cached or returned.
LIST_FIELDS = ("walkthrough", "if_you_change_it", "watch_out", "unknowns")
TEXT_FIELDS = ("summary", "role_in_the_system", "shared_state")

BULLET = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s+")


def as_list(value) -> list:
    """Whatever came back, as a list of items.

    A paragraph of Markdown bullets becomes one item per bullet, because
    that is plainly what was meant; a paragraph of prose stays one item.
    """
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    if not isinstance(value, str):
        return [value]
    bullets = [
        BULLET.sub("", line).strip()
        for line in value.splitlines()
        if BULLET.match(line)
    ]
    if bullets:
        return [line for line in bullets if line]
    return [value.strip()]


def as_text(value) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value if item)
    return "" if value is None else str(value)


def as_step(item) -> dict:
    """One walkthrough step, whether it arrived as an object or a sentence."""
    if isinstance(item, dict):
        what = as_text(item.get("what") or item.get("text") or item.get("step"))
        lines = item.get("lines")
        return {
            "lines": str(lines) if lines not in (None, "") else None,
            "what": what,
        }
    return {"lines": None, "what": as_text(item)}


def normalise(answer: dict) -> dict:
    """The answer in the shape the panel expects, whatever the model sent.

    Lenient on purpose. The alternative -- rejecting the answer because a
    list arrived as a paragraph -- would throw away a perfectly good
    explanation over its packaging, and would do it most often on the
    selections that are hardest to answer in lists anyway.
    """
    out = {key: value for key, value in answer.items() if key not in LIST_FIELDS}
    for key in TEXT_FIELDS:
        if key in out:
            out[key] = as_text(out[key])
    for key in LIST_FIELDS:
        if key not in answer:
            continue
        items = as_list(answer[key])
        out[key] = (
            [as_step(item) for item in items]
            if key == "walkthrough"
            else [as_text(item) for item in items]
        )
    return out


def digest(content: str, start: int, end: int) -> str:
    """Identity of an explanation: these lines, of this exact file.

    The whole file rather than just the selection, because the explanation
    talks about the enclosing definition and the imports too -- an edit
    above the selection can make a cached answer wrong without changing a
    character inside it.
    """
    payload = f"{start}:{end}:{content}".encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()


def numbered(lines: list[str], first: int) -> str:
    return "\n".join(f"{first + i:>5}  {line}" for i, line in enumerate(lines))


def build_prompt(path: str, text: str, start: int, end: int, facts: dict) -> str:
    """Everything the model is shown, assembled where it can be read.

    Built here rather than inside the call so that what leaves the machine
    is visible at one place -- the same reason `assistant.py` builds its
    context explicitly instead of reaching into the database from a prompt.
    """
    lines = text.splitlines()
    parts: list[str] = [f"# The file\n\n`{path}`\n"]

    head_count = min(CONTEXT_LINES, len(lines))
    if start > head_count:
        parts.append(
            "# The top of the file, for the imports and module-level values\n\n"
            "```\n" + numbered(lines[:head_count], 1) + "\n```\n"
        )

    enclosing = facts.get("enclosing")
    if enclosing and (enclosing["line"] < start or enclosing["end_line"] > end):
        body = lines[enclosing["line"] - 1 : min(enclosing["end_line"], len(lines))]
        parts.append(
            f"# The whole of the {enclosing['kind']} it sits inside\n\n"
            "```\n" + numbered(body, enclosing["line"])[:MAX_CONTEXT_CHARS] + "\n```\n"
        )

    parts.append(
        f"# The selection -- lines {start} to {end}, this is what to explain\n\n"
        "```\n" + numbered(lines[start - 1 : end], start)[:MAX_SELECTION_CHARS] + "\n```\n"
    )

    # Not every highlight is a definition, and the ones that are not
    # need saying so. Left to itself the model will write a paragraph
    # about the architectural role of three import lines, which is the
    # kind of answer that teaches you to stop asking.
    selected = facts.get("selection") or {}
    if selected.get("hint"):
        parts.append(
            "# What was highlighted\n\n"
            f"This is {selected['what']}, not a definition. Say what "
            "these lines are for and stop there. Do not invent a caller, "
            "a purpose or a blast radius for them, and do not pad the "
            "answer out to the length of a function explanation -- there "
            "is genuinely less to say, and saying it briefly is the "
            "better answer.\n"
        )

    parts.append("# Measured facts about it\n")
    parts.append(
        "These were read out of the repository by search, not guessed. Use "
        "them; do not contradict them.\n"
    )

    if facts.get("shared_values"):
        parts.append(
            "Module-level values this selection reads: "
            + ", ".join(f"`{s['name']}`" for s in facts["shared_values"])
            + ". Every file importing this module sees the same value.\n"
        )
    else:
        parts.append("It reads no module-level values of this file.\n")

    if facts.get("imports_used"):
        parts.append("Imports it uses: " + ", ".join(facts["imports_used"]) + "\n")

    references = facts.get("references") or {}
    if not references:
        parts.append("No named definition was identified in the selection.\n")
    for name, found in references.items():
        if not found["searched"]:
            parts.append(
                f"- `{name}`: NOT SEARCHED. {found['reason']} The blast "
                "radius for this name is unknown -- say so.\n"
            )
            continue
        others = found["files"] or ["(none)"]
        tests = found["tests"] or ["(none)"]
        line = (
            f"- `{name}`: mentioned in {len(found['files'])} non-test file(s): "
            f"{', '.join(others[:12])}. Tests: {', '.join(tests[:6])}."
        )
        if found.get("caveat"):
            line += f" CAVEAT: {found['caveat']}"
        parts.append(line + "\n")

    return "\n".join(parts)


def cached(
    db: Session, project_id: int, path: str, start: int, end: int, sha: str
) -> Optional[models.CodeExplanation]:
    return db.execute(
        select(models.CodeExplanation).where(
            models.CodeExplanation.project_id == project_id,
            models.CodeExplanation.path == path,
            models.CodeExplanation.start_line == start,
            models.CodeExplanation.end_line == end,
            models.CodeExplanation.content_sha == sha,
        )
    ).scalars().first()


async def explain(
    db: Session, project, path: str, start: int, end: int, *, refresh: bool = False
) -> dict:
    """The measured facts, and the model's reading of them.

    Always returns the facts, even when the model is unavailable: the
    deterministic answer was useful before this module existed and is still
    the part that can be checked.
    """
    from app import agent  # local, to keep this module free of ai's import cycle

    root = workspace.resolve(project.local_path)
    if agent.matches(path, agent.NEVER_READ):
        raise workspace.WorkspaceError(
            f"{path} holds credentials and is never sent to the model."
        )

    facts = impact.explain(root, path, start, end)
    start, end = facts["start_line"], facts["end_line"]
    payload = workspace.read_file(root, path)
    sha = digest(payload["content"], start, end)

    if not refresh:
        hit = cached(db, project.id, path, start, end, sha)
        if hit is not None:
            return {
                "facts": facts,
                "explanation": normalise(json.loads(hit.payload_json)),
                "model": hit.model,
                "cached": True,
                "reason": None,
            }

    if not ai.is_configured():
        return {
            "facts": facts,
            "explanation": None,
            "model": None,
            "cached": False,
            "reason": ai.status()["reason"],
        }

    prompt = build_prompt(path, payload["content"], start, end, facts)
    answer = await ai.structured(
        system=SYSTEM,
        prompt=prompt,
        schema=EXPLAIN_SCHEMA,
        tool_name="explain_code",
        model=ai.CHAT_MODEL,
        max_tokens=MAX_EXPLAIN_TOKENS,
        feature="explain_code",
        project_id=project.id,
    )

    answer = normalise(answer)

    row = cached(db, project.id, path, start, end, sha)
    if row is None:
        row = models.CodeExplanation(
            project_id=project.id,
            path=path,
            start_line=start,
            end_line=end,
            content_sha=sha,
        )
        db.add(row)
    row.payload_json = json.dumps(answer)
    row.model = ai.CHAT_MODEL
    db.commit()

    return {
        "facts": facts,
        "explanation": answer,
        "model": ai.CHAT_MODEL,
        "cached": False,
        "reason": None,
    }
