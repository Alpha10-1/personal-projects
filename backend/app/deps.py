"""Shared request dependencies."""

from typing import Optional

from fastapi import Header, HTTPException

from app import models

SOURCE_HEADER = "X-PP-Source"


def client_source(x_pp_source: Optional[str] = Header(default=None)) -> str:
    """Who is making this write: the person, or an agent acting for them.

    The header is how the MCP server identifies itself, so provenance is
    recorded once at the edge rather than being threaded through every
    payload. Anything that does not send it -- the web UI, curl, a script --
    counts as human, which is the honest default: an agent has to say so.
    """
    if x_pp_source is None:
        return "human"
    value = x_pp_source.strip().lower()
    if value not in models.SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"{SOURCE_HEADER} must be one of {', '.join(models.SOURCES)}",
        )
    return value
