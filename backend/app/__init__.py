"""Loads backend/.env before anything else in the package reads the
environment, so an API key put in that file is picked up without having
to export it in every shell that starts the server.

Absent python-dotenv or an absent file, the real environment is used as
before -- this only ever adds values, and never overrides one already set.
"""

from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional at runtime
    pass
else:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
