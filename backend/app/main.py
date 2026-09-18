"""Personal Projects API.

Single-user, local-first: no accounts, no login, no tenancy. The database is
one SQLite file and the server is expected to be reachable only from this
machine, which is why CORS is restricted to localhost rather than opened up.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import DB_PATH, init_db
from app.routes import (
    activity,
    ai,
    dashboard,
    library,
    milestones,
    people,
    projects,
    review,
    tasks,
    time_logs,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Personal Projects", version="1.0.0", lifespan=lifespan)

allowed_origins = [
    o.strip()
    for o in os.getenv(
        "PP_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok", "database": str(DB_PATH)}


app.include_router(projects.router)
app.include_router(milestones.router)
app.include_router(tasks.router)
app.include_router(time_logs.router)
app.include_router(library.router)
app.include_router(dashboard.router)
app.include_router(activity.router)
app.include_router(review.router)
app.include_router(people.router)
app.include_router(ai.router)
