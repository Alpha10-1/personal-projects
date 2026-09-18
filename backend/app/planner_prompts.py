"""Prompts for researching a project and planning what to do next.

Kept beside `assistant.py` rather than inside it because this is the only
pair of calls that runs in two stages -- look things up, then decide -- and
the seam between them is the interesting part: the research call can search
the web and produces prose with sources; the planning call cannot search at
all and is handed that prose as just another labelled input, no more
authoritative than the README and considerably less than the commits.
"""

from typing import Optional

from app import ai

PRIORITIES = ["low", "medium", "high"]

# --- Looking things up -------------------------------------------------------

RESEARCH_SYSTEM = (
    "You are researching a specific software project to find improvements "
    "worth making. You can search the web.\n\n"
    "You will be given what is known about the project: its README, what its "
    "commit history shows was actually built, and what is already planned.\n\n"
    "What to look for:\n"
    "- Current practice for the stack this project actually uses, where the "
    "project looks like it is doing something the ecosystem has moved on "
    "from.\n"
    "- Known problems with the specific libraries and services named, "
    "including deprecations and migration paths.\n"
    "- Concrete techniques for the problems the history shows this project "
    "has: a file changed in half the commits, generated output committed to "
    "the repository, an area abandoned mid-build.\n\n"
    "Rules:\n"
    "- Search for this project's actual problems. Do not search for generic "
    "advice: 'software best practices' helps nobody.\n"
    "- Every claim about what is current must come from something you read. "
    "If you did not find it, say the search did not turn it up rather than "
    "filling the gap from memory.\n"
    "- Say when something you found does not apply here, and why. A popular "
    "recommendation for a large team is usually wrong for one person.\n"
    "- No summary of your own summary, no list of what you searched for."
)


def research_prompt(context_text: str, focus: Optional[str] = None) -> str:
    lines = [context_text]
    if focus:
        lines.append(f"\n--- WHAT TO CONCENTRATE ON ---\n{ai.clip(focus, 500)}")
    lines.append(
        "\nResearch the improvements worth making to this specific project. "
        "Ground every recommendation in a source."
    )
    return "\n".join(lines)


async def do_research(
    context_text: str, focus: Optional[str] = None, max_searches: int = ai.MAX_SEARCHES
) -> dict:
    return await ai.research(
        system=RESEARCH_SYSTEM,
        prompt=ai.clip(research_prompt(context_text, focus), ai.MAX_CONTEXT_CHARS * 4),
        max_searches=max_searches,
    )


# --- Deciding what to do -----------------------------------------------------

PLAN_SYSTEM = (
    "You are planning the next stretch of work on a software project that "
    "already exists, for the one person who works on it.\n\n"
    "Give them a choice. Produce two or three genuinely different options -- "
    "different in what they are for, not three orderings of the same task "
    "list. A real set of options usually includes: finish or harden what is "
    "half-built, build the next thing users would notice, and pay down "
    "something the history shows is hurting. If only one option is honestly "
    "available, give one and say why the others are not.\n\n"
    "Rules:\n"
    "- Every option must be justified by something you were given. Name it: "
    "'the README promises X and no commit touches it', 'app/driver was "
    "renamed in August and left half-migrated'. An option with no evidence "
    "behind it is padding.\n"
    "- Do not plan work that is already on the board. It was given to you so "
    "you would not.\n"
    "- Milestones are outcomes you could report. Tasks are single sittings: "
    "if a task needs a paragraph to explain, it is two tasks.\n"
    "- Task titles are imperative and concrete: 'Move the Paystack keys into "
    "env vars', not 'Payment security improvements'.\n"
    "- Estimate hours honestly, per task. Most real tasks are 1-4 hours. "
    "Anything over 8 has not been broken down. Do not pad to look thorough "
    "and do not shave to look fast.\n"
    "- Do not put dates on anything. Dates are worked out from your hour "
    "estimates and how much time this person actually has, which you do not "
    "know.\n"
    "- Order within an option matters: first the thing that makes the rest "
    "possible, or the thing that would kill the option soonest if it turned "
    "out not to work.\n"
    "- This is someone's own project. No governance, no sign-off, no risk "
    "register.\n"
    "- If research was supplied, treat it as informed opinion, not fact. The "
    "commit history is what actually happened; a web page is what someone "
    "wrote. Where they disagree, the history wins."
)

TASK_ITEM = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Imperative and concrete."},
        "notes": {
            "type": "string",
            "description": "What doing it involves, and how you would know it is done.",
        },
        "estimate_hours": {
            "type": "number",
            "description": "Honest hours for one person. Most tasks are 1-4.",
        },
        "priority": {"type": "string", "enum": PRIORITIES},
        "milestone": {
            "type": "string",
            "description": "Exact title of one of this option's milestones.",
        },
    },
    "required": ["title", "estimate_hours"],
}

OPTION_ITEM = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "What this option is, in a few words."},
        "what_it_is_for": {
            "type": "string",
            "description": "The outcome, in one or two sentences. What is different afterwards.",
        },
        "why_this_project_needs_it": {
            "type": "string",
            "description": (
                "The evidence: name the file, month, README claim or feedback "
                "it comes from."
            ),
        },
        "when_to_pick_it": {
            "type": "string",
            "description": "The circumstance in which this is the right choice over the others.",
        },
        "cost_of_not_doing_it": {"type": "string"},
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
            "description": "How sure you are this is worth doing, given what you could see.",
        },
        "milestones": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                },
                "required": ["title"],
            },
            "maxItems": 5,
        },
        "tasks": {"type": "array", "items": TASK_ITEM, "maxItems": 20},
    },
    "required": [
        "title",
        "what_it_is_for",
        "why_this_project_needs_it",
        "confidence",
        "tasks",
    ],
}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "reading": {
            "type": "string",
            "description": "What state this project is in, in 2-4 sentences, from the evidence.",
        },
        "options": {"type": "array", "items": OPTION_ITEM, "minItems": 1, "maxItems": 3},
        "recommended": {
            "type": "string",
            "description": "Title of the option you would pick, and one sentence on why.",
        },
        "not_worth_doing": {
            "type": "array",
            "description": "Things that look worth doing here and are not, with the reason.",
            "items": {"type": "string"},
            "maxItems": 4,
        },
        "unknowns": {
            "type": "array",
            "description": "What you could not see that would have changed the plan.",
            "items": {"type": "string"},
            "maxItems": 4,
        },
    },
    "required": ["reading", "options"],
}


async def plan(
    context_text: str,
    *,
    research_text: Optional[str] = None,
    focus: Optional[str] = None,
) -> dict:
    """Two or three things worth doing next, each costed in hours."""
    lines = [context_text]
    if research_text:
        lines.append(
            "\n--- RESEARCH (looked up on the web just now; opinion, not fact) ---\n"
            + ai.clip(research_text, 6000)
        )
    if focus:
        lines.append(f"\n--- WHAT THEY ASKED FOR ---\n{ai.clip(focus, 500)}")
    lines.append(
        "\nPlan what to do next. Give them the choice, cost every task in "
        "hours, and put no dates on anything."
    )

    return await ai.structured(
        system=PLAN_SYSTEM,
        prompt=ai.clip("\n".join(lines), ai.MAX_CONTEXT_CHARS * 5),
        schema=PLAN_SCHEMA,
        tool_name="plan_the_work",
        model=ai.CHAT_MODEL,
        max_tokens=ai.MAX_PLAN_TOKENS,
        timeout=ai.RESEARCH_TIMEOUT,
    )
