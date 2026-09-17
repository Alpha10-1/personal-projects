# Personal Projects

A single-user project tracker: projects, milestones, tasks, time logging, notes,
links, files, and a weekly view of where the time actually went.

It is a personal version of the Organization Management System, cut down to the
projects side. There are **no accounts, no login, no clients and no billing** —
one person, one SQLite file, running on your own machine.

```
Today        what's overdue, due today, in progress, blocked
Projects     the board, and a detail page per project
Tasks        every task across projects, with saved views
Time         log hours as you go
Library      notes, links and files across everything
Insights     hours by week and category, opened vs closed, cycle time
```

---

## Prerequisites

Neither is installed on this machine yet — install both before the steps below.

- **Python 3.11+** — <https://www.python.org/downloads/windows/>
  (tick *"Add python.exe to PATH"* in the installer)
- **Node.js 20+** — <https://nodejs.org/>

Verify in a new terminal:

```powershell
python --version
node --version
```

## Running it

Two terminals: one for the API, one for the web app.

**Terminal 1 — API (port 8000)**

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python seed.py                 # optional: sample data so the charts aren't empty
uvicorn app.main:app --reload --port 8000
```

**Terminal 2 — web app (port 3000)**

```powershell
npm install
npm run dev
```

Then open <http://localhost:3000>. Interactive API docs are at
<http://localhost:8000/docs>.

After the first run, starting up again is just `.\.venv\Scripts\Activate.ps1` +
`uvicorn ...` in one terminal and `npm run dev` in the other.

---

## Where your data lives

Everything is in `backend/data/`:

- `personal.db` — the SQLite database
- `uploads/` — uploaded files, stored under generated names

Both are gitignored. **Backing up means copying that one folder.** Nothing
leaves your machine: the API binds to localhost and the frontend talks straight
to it.

---

## How the pieces fit

| Concept | What it's for |
|---|---|
| **Project** | A body of work with an outcome. Carries an objective, a definition of done, a stakeholder and a stack. |
| **Milestone** | A dated checkpoint inside a project — the things you'd report upward. |
| **Task** | A concrete step. Optionally under a project and a milestone; subtasks are supported. |
| **Time log** | Hours against a task, a project, or neither, tagged with what kind of work it was. |
| **Note / link / file** | The reference material: decisions, results, papers, repos, attachments. |

A few behaviours worth knowing:

- **Progress** is derived: a manual override wins, otherwise tasks, otherwise
  milestones, otherwise the status. Early work with no tasks yet can still show
  real progress via the override.
- **Time logged against a task** automatically rolls up to that task's project,
  so project totals can't drift from task totals.
- **Archive vs. delete.** Archiving is reversible and keeps the history; delete
  removes the project and everything attached to it and cannot be undone.
- **Duplicating a project** copies its plan — tasks and milestones, reset to
  open — but not its time logs, notes or files. It reuses a structure without
  pretending the work is already done.
- **Deleting a task** keeps its time logs. Hours you actually spent are a record,
  not a property of the task.

---

## Layout

```
backend/
  app/
    db.py          SQLite engine, schema creation, additive column migration
    models.py      the whole data model
    schemas.py     request/response shapes
    enrich.py      derived counts, hours and progress
    routes/        projects, milestones, tasks, time_logs, library, dashboard
  seed.py          sample data (refuses to run if projects already exist)
src/
  app/             Next.js App Router pages
  components/      shell, forms, task list, panels, charts
  lib/             api client, hooks, formatting, palette, vocabularies
```

## Changing the schema

Adding a column: add it to `models.py` and restart the API — `ensure_columns()`
in `db.py` adds it in place, keeping your data. Dropping or retyping a column
needs a manual migration; for a personal database the practical answer is
usually to export what you need, delete `personal.db`, and start clean.

## Notes on the charts

The categorical palette in `src/lib/palette.js` is used in a fixed slot order,
which is what keeps the series distinguishable under colour-vision deficiency —
so a category keeps its colour whether or not the others are on screen. Every
chart also has a table view, so nothing depends on colour alone, and dark mode
uses its own set of steps rather than an inverted light palette.
