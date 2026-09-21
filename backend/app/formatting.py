"""How the assistant should mark up what it writes.

The answers are rendered as Markdown now, so a reply that is one long run of
undifferentiated text is a choice rather than a limitation. This file is the
instruction that makes the difference visible: a file path should not look
like a sentence, and the one number that matters should not have to be hunted
for.

Kept in one place because the rule has to be identical everywhere. Formatting
that means one thing in the chat floater and something else in a brainstorm
is worse than no formatting at all -- the reader learns nothing they can
carry from one to the other.

Two rules are doing the real work:

**Backticks are for what you would type.** A path, an identifier, a command,
a branch. Those are the tokens a reader's eye should be able to land on
without reading the sentence, and they are also exactly the tokens that look
like typos when set in prose.

**Emphasis is scarce or it is nothing.** A paragraph with six bold phrases
has no emphasis in it, just noise. The instruction is capped deliberately.
"""

# Inline only: works in a paragraph, a bullet or a table cell, so it is safe
# to give to every surface including the ones that forbid structure.
INLINE = (
    "HOW TO WRITE IT\n"
    "Your answer is rendered as Markdown, so use it -- plain undifferentiated "
    "text is hard to read and hides the part that matters.\n"
    "- `Backticks` for anything the user would type or find in their code: "
    "file paths, function and variable names, commands, branch and repo "
    "names, config keys. `backend/app/github.py`, not backend/app/github.py.\n"
    "- **Bold** for the subject of the sentence: a project or task name, the "
    "decision, the number that actually matters. At most two or three bold "
    "phrases in a paragraph -- if half of it is bold, none of it is.\n"
    "- *Italics* rarely, for an aside or a word used as a word.\n"
    "- Ids stay as the user sees them and go in backticks: `#12`.\n"
    "- CAPITALS only for a short standing label like OVERDUE or BLOCKED, "
    "never for a phrase or a sentence -- it reads as shouting and is harder "
    "to scan than bold.\n"
    "- Do not decorate every noun. Formatting is for the things worth "
    "spotting; if everything is marked, nothing is."
)

# For the surfaces where structure helps: a question answered from data, or
# the floater summarising a board.
STRUCTURED = (
    INLINE
    + "\n- A short list when the answer is genuinely a list of things, and a "
    "table when you are comparing the same few facts across several items. "
    "Never a list of one, and never a table for two numbers.\n"
    "- No headings unless the answer has real sections. A heading above a "
    "single paragraph is furniture."
)
