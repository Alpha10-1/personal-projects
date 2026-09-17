"""Status changes and the bookkeeping that has to come with them.

Kept in one place because a status change is never just a status change: it
stamps or clears completed_at, which is what the cycle-time figures are built
from. Anything that can move a task -- the API, an accepted suggestion --
goes through here, so the two can never drift apart.
"""

from app import models


def set_task_status(task: models.Task, new_status: str) -> bool:
    """Move a task, applying the side effects. Returns whether it moved."""
    if new_status == task.status:
        return False

    # completed_at tracks the most recent transition into "done", and is
    # cleared if the task is reopened, so it never reads as finished work that
    # is still running.
    if new_status == "done":
        task.completed_at = models.utcnow()
    elif task.status == "done":
        task.completed_at = None

    # A task that is no longer blocked has no blocking reason. Leaving the old
    # one behind makes stale text look current on the board.
    if new_status != "blocked":
        task.blocked_reason = None

    task.status = new_status
    return True
