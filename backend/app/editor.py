"""Handing a file to the editor.

Two ways exist and the tracker offers both, because they fail in different
places. A `vscode://file/...` URL is handled by the browser and needs
nothing installed on the server side, but the browser may refuse it or
silently do nothing depending on how the page was opened. The `code` CLI is
a process this machine either has or has not, and when it runs, it either
worked or returned an error we can show.

So: the URL is built here and handed to the front end as a link, and the CLI
is invoked here as a fallback the user can press when the link does nothing.
Neither reads or writes project content -- this module only opens things.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

# Opening an editor should be instantaneous; the CLI returns as soon as it
# has handed off to the running window. If it hasn't in five seconds, the
# most likely cause is a first-ever launch, and blocking the request longer
# helps nobody.
LAUNCH_TIMEOUT = 5.0


class EditorUnavailable(RuntimeError):
    """No editor command on this machine."""


def command() -> Optional[str]:
    """The editor CLI, if there is one.

    `PP_EDITOR_COMMAND` wins so a JetBrains or Sublime user is not stuck
    with a VS Code assumption baked into the source.
    """
    configured = (os.getenv("PP_EDITOR_COMMAND") or "").strip()
    if configured:
        return shutil.which(configured) or configured
    return shutil.which("code")


def available() -> bool:
    return command() is not None


def url_for(path: Path, line: Optional[int] = None) -> str:
    """The `vscode://` deep link for a file.

    The path goes in as posix with a leading slash -- `vscode://file/C:/x/y`
    -- which is what VS Code expects on Windows, drive letter and all.
    """
    posix = path.as_posix()
    if not posix.startswith("/"):
        posix = "/" + posix
    suffix = f":{line}" if line else ""
    return f"vscode://file{posix}{suffix}"


def open_path(path: Path, line: Optional[int] = None) -> str:
    """Open a file or folder in the editor, returning what was run.

    `-g` puts the cursor on the line; `-r` reuses the window that is already
    open on that folder rather than stacking up new ones, which is what
    actually happens when you click twenty search results.
    """
    exe = command()
    if exe is None:
        raise EditorUnavailable(
            "No editor command found. Install the VS Code CLI "
            "(Command Palette -> 'Shell Command: Install code command in PATH') "
            "or set PP_EDITOR_COMMAND."
        )
    target = f"{path}:{line}" if line else str(path)
    args = [exe, "-r", "-g", target] if line else [exe, "-r", str(path)]
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=LAUNCH_TIMEOUT, check=False
        )
    except subprocess.TimeoutExpired:
        # The editor was almost certainly launched -- it just hadn't returned.
        # Reporting a failure here would be wrong more often than right.
        return " ".join(args)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise EditorUnavailable(
            f"The editor command failed: {detail[0] if detail else 'no output'}"
        )
    return " ".join(args)
