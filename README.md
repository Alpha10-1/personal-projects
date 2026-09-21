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

## Activity from GitHub

Commits and pull requests are pulled in and recorded as **facts** — what
happened, when, by whom. Nothing in the ingestion layer decides what that
means for a project's progress; that inference belongs somewhere it can be
reviewed, and keeping it out means the facts can be re-interpreted later
without re-fetching them.

Point a project at a repo, then sync:

```bash
curl -X PATCH localhost:8000/projects/1 \
  -H 'Content-Type: application/json' \
  -d '{"repo": "owner/name"}'

curl -X POST localhost:8000/activity/sync
curl localhost:8000/activity
```

`GET /activity/repos` lists what will be synced. Syncing is idempotent —
events are keyed by GitHub's own id, and each run asks only for what happened
after the newest event already stored, so running it twice adds nothing.

Private repos need `GITHUB_TOKEN` in the environment.

**How an event gets linked**, in order of how much it can be trusted:

| `linked_by` | Meaning |
|---|---|
| `convention` | The message said `Task: 42` explicitly, and task 42 is in this repo's project. |
| `repo` | The repo maps to a project; no task was named. |
| *null* | No project maps to this repo. `GET /activity?unlinked_only=true` is the review queue. |

A task reference pointing at *another* project's task is ignored rather than
followed — far more likely a typo than a real cross-project link. `#42` is
deliberately not treated as a task reference: on GitHub that already means an
issue.

### Who wrote a row

`tasks`, `time_logs` and `notes` carry a `source` of `human` or `agent`.
It is taken from the `X-PP-Source` header, not the request body, so an agent
has to declare itself and anything that doesn't — the web UI, curl, a script
— counts as human. The MCP server sets it on every request.

---

## The review: findings and suggestions

The **Review** page in the app shows both, with accept and dismiss buttons.
`GET /review` is the same thing over HTTP. Two kinds of output, kept apart
on purpose:

- **Findings** — observations, computed fresh, never stored. Stale work in
  progress, long-standing blockers, overdue tasks, estimate overruns,
  projects past their target date, activity matching no project.
- **Suggestions** — proposed changes, persisted, waiting on a decision.

Every rule is deterministic. That's the point: a rule that fires can be
explained, tested and switched off. The *narrative* — the standup paragraph,
the judgement about what matters this week — is the agent's job, written
**from** these findings rather than instead of them.

```bash
curl localhost:8000/review                    # read-only
curl -X POST localhost:8000/suggestions/refresh
curl -X POST localhost:8000/suggestions/1/accept
curl -X POST localhost:8000/suggestions/1/dismiss
```

**Nothing is applied on its own.** `GET /review` never changes a task, and
`?refresh=true` only ever *raises* suggestions. Two rules currently propose:

| Rule | Proposes |
|---|---|
| `activity_suggests_started` | Commits exist against a task still marked `todo` → `in_progress` |
| `merged_pr_suggests_done` | A merged PR names the task → `done` |

Accepting applies the change through the same transition as an edit made by
hand, so `completed_at` and the cycle-time figures stay consistent.
Dismissing is permanent: the dismissed row keeps its fingerprint, which is
what stops the rule proposing the same thing again.

---

## Running it unattended

`backend/analyst_run.py` is the loop without a conversation: pull activity,
then propose. It does only the deterministic half — the narrative stays with
the agent, which needs a conversation rather than a cron entry.

```bash
cd backend
python analyst_run.py              # sync + propose
python analyst_run.py --dry-run    # report only, change nothing
python analyst_run.py --note       # also write the digest into the tracker
python analyst_run.py --quiet      # print only on failure (what the task runs)
python analyst_run.py --no-autostart   # fail if nothing is already serving
```

It talks to the HTTP API, stamps its writes as `agent`, and appends every run
to `backend/data/analyst-runs.jsonl`.

If nothing is serving that API it starts a backend itself, runs, and stops it
again -- so an unattended run does not depend on your having opened the app
first. It only ever stops a backend it started (`started_api` in the run log
says which happened), and only starts one for an address on this machine;
that server's own output goes to `backend/data/analyst-backend.log`.

A Windows scheduled task **`PersonalProjects-AnalystRun`** runs it daily at
07:30:

```powershell
Get-ScheduledTaskInfo -TaskName PersonalProjects-AnalystRun
Start-ScheduledTask   -TaskName PersonalProjects-AnalystRun   # run it now
Unregister-ScheduledTask -TaskName PersonalProjects-AnalystRun -Confirm:$false
```

The task is set to start when available, so a 07:30 it slept through runs
when the machine next wakes. It does not wake the machine by itself.

---

## The assistant

Off by default. It turns on when `ANTHROPIC_API_KEY` is set in `backend/.env`
(copy `backend/.env.example`), and `GET /ai/status` is how the UI decides
whether to offer any of it. With no key the routes answer 503 with that
reason and the buttons are not rendered at all — a disabled feature should be
invisible, not broken.

**Two rules govern what leaves this machine.**

`PP_AI_EGRESS=off` in `backend/.env` turns the assistant off entirely --
one setting, checked at the single function that builds the API client, so
no route can bypass it. The tracker itself is unaffected: the board, the
review rules, the computed commit timeline and the spend ledger never
touched the network.

Separately, and with no setting to turn it off: **nothing identifying a
Power BI report, workspace or dataset is ever sent.** Those sit over data
that may carry row-level security, where what a person may see depends on
who they are, and copying it somewhere that permission model does not reach
defeats the point. No prompt-building code reads the dashboards table, and
every outgoing call is checked for those identifiers before it goes. The
check matches ids and URLs, never names -- a report called "Sales" must not
blocklist the word.

Power BI is currently connected with a service principal, which is an
application identity and therefore not subject to RLS the way a person is.
Reading as yourself needs delegated sign-in: see
[docs/powerbi-delegated-access.md](docs/powerbi-delegated-access.md).

**The assistant is the only part of the system that sends your data anywhere.** What
goes out is built in `assistant.py` from explicit queries, so it can be read
rather than inferred from a prompt string, and everything is length-capped
with truncation marked so the model can tell it was cut off.

| Where | What it does |
|---|---|
| New project / new task | Suggests summary, objective, done criteria, priority and first tasks **as you type** |
| Floating chat, every page | Answers questions about your projects, tasks, notes and GitHub activity |
| Project → Repo tab | Summary, possible bugs and improvements over recent commits, read against the README |
| Project → Repo tab | What the whole commit history says the project is, and questions answered from it |
| Project → Plan tab | What to do next, as two or three costed options you choose between |

**Nothing is applied on its own.** Every suggestion is a button, and the chat
can read the tracker but cannot write to it. That is the same line
`review.py` draws: the moment generated content can quietly become record,
none of the numbers mean anything — and you stop being able to tell which
words were yours.

Two models, because the jobs differ. Suggestions go to Haiku, which is fast
enough to feel live; chat and repo review go to Sonnet, where the answer
matters more than a second of latency. Override with `PP_AI_FAST_MODEL` and
`PP_AI_MODEL`.

### Planning the next stretch

The **Plan** tab reads four things and says which is which: the README (what
the project claims), the commit history (what was actually built), the board
(so it does not re-plan planned work) and your notes. It comes back with two
or three *different* directions -- not one instruction -- each with
milestones, tasks and hour estimates, and each having to name the evidence it
rests on.

**The model estimates effort; the dates are arithmetic.** How long 12 hours
takes depends on how many hours a week you have, which is a fact about you
rather than about the work, so it is computed here. Changing 10 hours a week
to 4 re-dates the whole plan instantly and costs nothing.

Nothing is written until you pick an option and press the button, and what
gets created is exactly what is on screen -- including the tasks you dropped
from it. Everything lands stamped `agent`.

Estimates are honest about being unvalidated: until finished tasks have both
an estimate and logged hours against them, the prompt says so in those words
rather than implying the numbers are calibrated. Once they do, the median
ratio of actual to estimated is passed in and the model is told to bias
accordingly.

### Research (off by default)

Ticking **Search the web first** lets the model look things up before
planning -- current practice for the stack this project actually uses, known
problems with the libraries it names -- and every claim comes back with the
page it came from, listed and linked.

It is off unless you ask, for two reasons: one search costs roughly what a
whole ordinary call does, and the queries are derived from your project, so a
repository name or a problem description can reach a search engine. Research
is also ranked *below* the commit history in the prompt: a web page is what
someone wrote, the history is what happened, and where they disagree the
history wins.

### What it costs

Every model call is recorded when it is made -- which feature spent it, which
model, the token counts the API reported, and the dollar figure computed from
them. **Insights -> What the assistant costs** reads it back, grouped by
feature so "is the repo review worth it" is answerable, and `GET /ai/spend`
is the same thing over HTTP. That endpoint is deliberately not behind the AI
guard: the moment you most want to read the bill is after switching the
assistant off because of it.

Failed calls are counted too. A feature that fails twice and succeeds once
spent three calls' worth of input tokens, and a ledger of successes hides
that. Costs are stored per call rather than derived on read, so a later
change to the rate table cannot silently rewrite what last month cost, and a
model with no published rate is counted, left unpriced, and named in the
reply rather than being guessed at.


Suggestions fire on a pause in typing, not a keystroke: 900ms of quiet, and
only once a draft has something in it. A request supersedes the one before
it, so a long sentence is one call rather than forty. The repo review reads
diffs and is the expensive one, which is why it is a button rather than
something that happens when you open the tab.

```bash
curl localhost:8000/ai/status
curl -X POST localhost:8000/ai/suggest/project   -H 'Content-Type: application/json'   -d '{"draft": {"name": "Shift-scheduling forecast model"}}'
curl -X POST localhost:8000/ai/projects/1/repo-review
```

### Linking a repo

A project's **GitHub repo** field is on the project form and is optional —
leave it blank and that project stays off GitHub entirely. Filling it in is
what turns on activity ingestion, the two suggestion rules, and the Repo tab.

### Reading a project out of its history

Different question from the repo review above, which reads recent commits
looking for bugs. This reads the *whole* history to answer "what is this, and
how did it get this way" — which is the question you have when you come back
to something after three months.

```bash
curl -X POST localhost:8000/activity/deep-sync     # which files each commit touched
curl localhost:8000/ai/projects/1/timeline          # the arithmetic, free
curl -X POST localhost:8000/ai/projects/1/history   # the written summary
```

**The timeline needs no key and costs nothing.** Commits per month, which
parts of the tree they touched, when each area first and last appeared, the
most-changed files, who wrote them. That is arithmetic, and computing it here
rather than asking a model means the answer can be checked — and that the
model is handed facts to narrate instead of commit messages to guess from.

The **deep sync** is what makes file-level detail possible. The commit *list*
endpoint does not include which files changed, so that costs one request per
commit and is therefore a separate, explicit pass. It is incremental: only
commits missing their detail are fetched, newest first, so a long history is
walked back a chunk at a time. `still_missing` says whether there is more.

The written summary says what the project is, the phases it went through,
where the work concentrated, and what the shape suggests — a two-month gap is
a pause, an area touched once is abandoned or finished. It lands as a note,
and a better project summary is only ever *proposed*.

### Asking about a project

```bash
curl -X POST localhost:8000/ai/projects/1/ask   -H 'Content-Type: application/json'   -d '{"question": "When did testing start, and what was there before?"}'
```

Streamed, and grounded in that same timeline, so an answer cites the month,
area or file it came from. It is explicitly **not** the source code — the
history says what changed and when, not how a function works — and the prompt
says so, because a model asked about code it cannot see will otherwise
describe what such code usually looks like.

Asked whether a project has two-factor authentication, it answers "no commit
mentions it" and names the file you would have to read to be sure. That is
the intended behaviour, not a limitation to work around.

---

## The personal side

`/personal` is your own projects, separate from work. Same database, one
discriminator (`Project.workspace`), and **the analyst does not come here** —
no findings, no suggestion rules, nothing in the scheduled run. A side
project you pick up every few months is not "stalled", and a review that says
it is trains you to ignore the ones that matter.

Everything already in the tracker still applies: tasks, time, notes, the repo
tab, the chat floater.

### Import your repos

Your repositories show up **without being configured and without being
projects first**:

```bash
curl localhost:8000/personal/repos
```

The account is worked out rather than asked for, in this order — and the
answer comes back as `resolved_from` so the page can say *why* it is showing
that account rather than silently picking one:

| Order | Source |
|---|---|
| 1 | An explicit `?user=` |
| 2 | `PP_GITHUB_USER` in `backend/.env` |
| 3 | Whoever `GITHUB_TOKEN` belongs to — the only one that also unlocks private repos |
| 4 | **This checkout's own git remote**, read straight out of `.git/config` |
| 5 | The owner of a repo already mapped to a project |

Step 4 is why it needs no setup: the tracker is itself a repository on the
account in question, so the answer is already on disk. It is parsed rather
than shelled out to, so it works whether or not `git` is on PATH, and it
reads every remote, not just `origin` — a fork's `origin` may be someone
else's account.

```bash
curl -X POST localhost:8000/personal/repos/import   -H 'Content-Type: application/json'   -d '{"repos": ["you/weather_etl", "you/course-finder-app"]}'
```

Already-imported repos are marked, and importing the same repo twice is a
no-op — the obvious thing to do after importing five is to come back for the
sixth.

### The rate limit, and the token

**Unauthenticated GitHub allows 60 requests an hour.** That is less than it
sounds: listing your repos is one, a sync is two per repo, and a repo review
is one per commit. An afternoon of opening the personal page can spend it.

Two things keep that survivable. The repo listing is **cached for five
minutes**, so revisiting the page costs nothing — the refresh button asks
again (`?fresh=true`) when you actually want it to. And the remaining quota
is shown on the page, read from headers GitHub already sends, so it costs no
call of its own. When it does run out the error says so plainly, rather than
the bare `403` GitHub returns, which reads as a permissions problem.

**A token raises the limit to 5000 an hour and shows your private repos.**

1. github.com → Settings → Developer settings → Personal access tokens →
   **Fine-grained tokens** → Generate new token
2. Repository access: **All repositories** (or just the ones you want listed)
3. Permissions → Repository permissions → **Contents: Read-only** and
   **Metadata: Read-only**. Nothing else — this only ever reads.
4. Put it in `backend/.env` as `GITHUB_TOKEN=github_pat_…` and restart the
   backend.

A classic token works too; it needs the `repo` scope, which grants
considerably more than the fine-grained pair above, so prefer fine-grained.
The token is also the more authoritative answer to *whose* repos to list, so
setting it takes priority over the git remote.

### From an idea to a full plan

```bash
curl -X POST localhost:8000/ai/scaffold   -H 'Content-Type: application/json'   -d '{"idea": "An app that tracks my runs and warns me when mileage ramps too fast"}'
```

Returns a project with milestones, tasks and hour estimates, and writes
nothing. Add `"apply": true` to build it in one call instead — the UI has
both, **Draft a plan** and **Just build it**. Pass `"repo": "you/name"` and it
reads the README and plans the work that is *left*, not what is already done.

Generated tasks are stamped `source: agent`, so a board filled in thirty
seconds is still distinguishable from one you typed.

`POST /ai/scaffold/apply` builds a plan you already have, after you have
dropped the rows you did not want. No model call, so what gets created is
exactly what was on screen.

### Brainstorming

Saved sessions, unlike the throwaway chat floater — because the useful part
of a brainstorm is usually the third exchange, and because what comes out of
one should become tasks without retyping it.

```bash
curl -X POST localhost:8000/personal/brainstorms   -H 'Content-Type: application/json' -d '{"topic": "Strava API or manual entry?"}'

curl -X POST localhost:8000/ai/brainstorms/1/turn   -H 'Content-Type: application/json' -d '{"content": "Which should I do first?"}'

curl -X POST localhost:8000/ai/brainstorms/1/harvest   -H 'Content-Type: application/json' -d '{"apply": true}'
```

The prompt tells it to have opinions and to name what would sink the idea
early. **Harvest** pulls out what was actually decided — and returns nothing
when nothing was, rather than inventing a plan from a conversation that did
not land anywhere.

Your message is saved before the model is called, so a failed or abandoned
reply still leaves the question in the transcript. A brainstorm outlives the
project it was filed under: deleting the project clears the link, not the
thinking.

---

## The Code tab: the working copy, and an agent that edits it

Set a project's **local folder** in its brief and a Code tab appears. It
shows the checkout as it is right now -- branch, last commit, uncommitted
changes, the file tree, any file's contents, and a coloured diff -- read
from disk on every request and refreshed every four seconds, so an edit you
make in VS Code shows up here without a reload. None of it touches the
network.

The folder is separate from the GitHub link because they answer different
questions. `repo` says whose history to ingest; `local_path` says which
folder to read now. A repository you have not cloned has one and not the
other.

### Opening things in VS Code

Every file offers **Open in VS Code**. It runs the `code` CLI on the server,
which works because the server and the editor are the same machine -- the
first thing to revisit if this were ever hosted. If the CLI is missing, the
panel offers the `vscode://` deep link instead, which needs nothing
installed. `PP_EDITOR_COMMAND` overrides the command for a different editor.

### Asking for a change

Below the file view, describe a change. The agent reads the repository
through the same six tools -- list, search, read, edit, write, finish -- and
produces a **proposal**: the whole new contents of each file it wants to
change, a diff, and its own account of what it did and what it could not
verify.

Nothing is written while it thinks. Every edit goes into an in-memory
overlay, so a run that goes wrong, runs out of turns or produces nonsense
costs money and nothing else. **Apply** writes the files and stops there --
uncommitted, so `git diff` is the review and `git checkout` is the undo.

**Apply automatically** exists for small changes, and is overridden whenever
the run touches a path the project marks as core. Those globs are per
project, because "core" differs: a migrations folder here, a pricing module
there. Left blank, the built-in list covers migrations, CI workflows,
lockfiles, auth, `privacy.py` and `models.py`. `auto_apply` is a preference;
`review_required` is not.

### What it cannot do

- **No shell.** It cannot run your tests, install anything, or execute a
  command. It says so in its own summaries: a change it proposes is
  untested, and it is told to say that rather than imply otherwise.
- **No credentials.** A separate, stricter list -- `.env` and its variants,
  keys, certificates, `serviceAccountKey.json`, `.npmrc`, `.netrc` -- can
  neither be read nor searched. This matters more than the write list: a bad
  write shows up in the diff and can be discarded, whereas anything read has
  already left the machine by the time anyone looks. This repository has a
  `.env.local` in its root, and nothing in `privacy.py` would have stopped
  it going out, because that guard is about Power BI identifiers.
- **Nothing outside the folder.** Every path is re-resolved and proved to be
  inside the repository after joining, so `..` and a symlink fail
  identically. The folder itself must sit under an allowed root --
  your home directory, unless `PP_WORKSPACE_ROOTS` says otherwise.
- **One run per project at a time.** Two agents on the same tree would build
  two proposals from the same base, and applying both would silently lose
  one.

### What it costs

Every turn re-sends the whole exchange, so the input grows through a run.
Prompt caching is on: the system prompt and tools are cached, plus a rolling
pair of breakpoints over the conversation. Measured on this repository, a
7-turn uncached run cost $0.13 and an 8-turn cached one cost $0.10 -- the
saving is smaller than the token counts suggest, because cache writes cost
1.25x and a growing conversation writes on every turn. Longer runs save
more. Either way it is in the spend ledger with everything else.

A worked example, both of which are in this repository's history: asked to
add a `limit` parameter to `/tasks`, the agent read the file, found the
pattern used by `people.py` and `review.py`, noticed that this route sorts
in Python rather than in SQL, and applied the cap after the sort instead of
calling `.limit()` on the statement. It said the change was untested. It
also reported that `edit_file` was rejecting multi-line matches -- which was
true, and was a real bug in the tool: this repository is CRLF on disk, and
the numbered file listing rejoins lines with `\n`, so nothing multi-line
could ever match. Fixed; the same run then took 7 turns instead of 17.

---

## Power BI

Two halves, and the first needs nothing set up.

**Linking a report works immediately.** Paste its URL on a project's *Notes,
files & reports* tab and the tracker knows which report belongs to which
work. That is useful on its own, and it is all most projects need.

**Connecting the Service adds refresh state** — whether the data behind a
report is still updating, and when it last did. Nothing in the tracker would
otherwise tell you a report has been quietly showing last month's numbers.

```bash
curl localhost:8000/powerbi/status
curl -X POST localhost:8000/powerbi/sync
```

A synced report whose last refresh **failed**, on a live work project,
becomes a `dashboard_refresh_failed` finding on the Review page. Personal
projects are out of scope like everything else there, and so is a report
nobody has linked to a project — there would be no project for the finding to
be about.

### What to ask IT for

Authentication is a **service principal** (client credentials, no user
sign-in) because this has to run from a scheduled task with nobody at the
keyboard. Two things are needed, and neither can be done from an ordinary
account:

1. An **Azure AD app registration**, giving you a tenant ID, client ID and
   client secret.
2. **"Service principals can use Power BI APIs"** enabled in the Power BI
   admin portal, with the app added to a security group if the setting is
   scoped to one.

Then add the principal **to each workspace you want read**, as Viewer. Scope
it to those workspaces rather than granting tenant-wide read — it sees every
workspace it is added to, and this code being careful is not a substitute for
not granting it in the first place.

```
PBI_TENANT_ID=…
PBI_CLIENT_ID=…
PBI_CLIENT_SECRET=…
```

**To try it before raising a ticket**, paste a token from the Power BI
developer tools as `PBI_ACCESS_TOKEN`. It expires in about an hour, which is
long enough to see whether any of this is worth the request.

### What a sync does to your rows

Idempotent on the report id. An existing row **keeps its project link and
your note** — those are yours — while the name, workspace, dataset and
refresh state are overwritten, because those are the Service's to state.

A link you pasted earlier is *adopted* rather than duplicated: the report id
is pulled out of the URL when you paste it, so the sync recognises the same
report. Deleting a synced row removes it here only; it stays in Power BI and
returns on the next sync, which is correct — this mirrors the Service, it
does not govern it.

---

## People and collaboration

Other people can be linked to a project, either to be kept informed or
because they are doing some of the work. **There is still no login.** A
person here is a record of someone involved, not an account — which is the
honest shape of a single-user app, and it means adding collaborators changed
nothing about how the API is secured.

So collaboration is tracked, not hosted:

| | |
|---|---|
| **What they did** | Read out of git. `Person.github_login` matched against the `actor` on each commit and pull request. |
| **What they suggested** | Mirrored from pull request reviews, pull request descriptions and issue comments — plus anything you note by hand. |
| **What they see** | A read-only progress snapshot you send them. |

The **People** page lists everyone with their contribution counts, and each
project has a **Team** tab.

### Naming a contributor

`github_login` is the whole join. Set it and their existing history attaches
immediately — adding someone who has been committing for weeks shows those
weeks rather than starting from zero:

```bash
curl -X POST localhost:8000/people   -H 'Content-Type: application/json'   -d '{"name": "Thabo Arendse", "github_login": "tarendse", "role_title": "Data engineering lead"}'

curl localhost:8000/people/unlinked      # committing here, but nobody on record
curl -X POST localhost:8000/people/relink
```

Attribution is *stored* on the event, not computed on read, so someone
renaming their GitHub account doesn't silently reassign three months of
history. `relink` re-derives it from the logins currently on record, and only
ever from those — it cannot invent an attribution.

**Removing a person keeps what they did.** The commits happened and the words
were said; `author_login` still records who. Only the link to a name goes —
the same bargain as deleting a task and keeping its time logs.

### Suggestions from people

Four sources. Three are mirrored out of GitHub on every `/activity/sync`, so
a suggestion made in a pull request is recorded somewhere durable and this
only reflects it:

| Source | Where it comes from |
|---|---|
| `pr_review` | Review comments on a pull request |
| `pr_body` | A pull request's own description — read from a payload already stored, so it costs no extra request |
| `issue_comment` | Discussion on issues and pull requests |
| `manual` | Something said in a meeting, entered by you |

These live in `feedback`, deliberately **not** in the `suggestions` table. A
suggestion is a machine-proposed change to one field with a fingerprint so it
can be applied or suppressed; this is prose from a human. Flattening the two
would mean either losing the words or pretending a sentence is a field change.

A git-sourced row can't be deleted — it would return on the next sync. Mark
it declined instead.

### Sharing progress

```bash
curl localhost:8000/projects/1/share > progress.html
```

One self-contained HTML file: inline styles, no scripts, no external
requests. It has to survive being attached to an email and opened on a
machine that has never heard of this app, and it must not phone home from
someone else's laptop.

**What it leaves out is the point:**

```
included   name, summary, objective, definition of done, status, dates,
           progress, milestones, task titles and states, who contributed,
           open feedback, and the latest digest
excluded   retro / lessons learned, hours and time logs, the stakeholder
           field, and every note except a digest
```

Those exclusions are the fields where you say what you actually think. A
sharing feature that needed proofreading before every send would not get used.

### The progress update

```bash
curl -X POST localhost:8000/ai/projects/1/digest
```

Writes a progress note crediting contributions by name, from the git history.
The note is written straight away — it is additive and stamped `agent`.

A better project **summary** is only ever *proposed*. It lands on the Review
page as a suggestion with your current wording beside it. The digest often
does have a sharper sentence than the one you typed six weeks ago, and
replacing it would mean that over time nobody could tell which words in the
tracker were anyone's.

---

## Using it from Claude (MCP)

`backend/mcp_server.py` exposes the tracker as an MCP server, so Claude Code
can read and update it conversationally — "what's overdue?", "log 2 hours
against the forecast model", "write up this week as a note".

It talks to the HTTP API rather than the database, so **the backend has to be
running**, and every rule the API enforces applies to the agent exactly as it
does to the UI. Pointing it at a hosted backend later is a change of
`PP_API_URL`, not a rewrite.

```bash
cd backend
pip install -r requirements-mcp.txt
```

`.mcp.json` in the repo root registers it for Claude Code; restart Claude Code
and approve the server when prompted. To run it by hand:

```bash
cd backend
PP_API_URL=http://localhost:8000 python mcp_server.py
```

Nineteen tools: eleven read-only (`today`, `insights`, `review`, `list_projects`,
`list_tasks`, `list_milestones`, `list_time_logs`, `list_notes`, `list_activity`,
`list_suggestions`) and eight that write (`create_task`, `update_task`,
`log_time`, `add_note`, `update_project`, `sync_activity`, `refresh_suggestions`,
`accept_suggestion`, `dismiss_suggestion`). They are annotated with MCP's
`read_only_hint`, so a client can tell the difference before calling.

Every write is appended to `backend/data/agent-audit.jsonl` — including the
ones that failed — so there's a record of what the agent did that doesn't
depend on the agent.

> The paths in `.mcp.json` assume the Windows venv layout
> (`backend/.venv/Scripts/python.exe`). On macOS or Linux it's
> `backend/.venv/bin/python`.

---

## Tests and linting

Both run in CI on every push and pull request (`.github/workflows/ci.yml`),
and can be re-run by hand from the Actions tab. Every run reports its
pytest counts as an annotation on the run page, and a red one lists the
failing test ids there too — so a build can be diagnosed without
downloading the log archive, which needs credentials.

Backend — install the dev extras once, then:

```bash
cd backend
pip install -r requirements-dev.txt
pytest          # tests point PP_DATA_DIR at a temp dir, so your data is untouched
ruff check .
```

Frontend — Next 16 removed `next lint`, so linting is the ESLint CLI:

```bash
npm run lint    # eslint .
npm run lint:fix
npm test        # vitest run -- jsdom, no dev server and no backend
npm run test:watch
npm run build
```

---

## What is left to do

[`docs/status-and-next-steps.md`](docs/status-and-next-steps.md) is the
running list: what is built, what is missing, what is known to be wrong,
and the permissions an IT administrator would need to grant for the Power
BI and AI pieces. Its last section is written to be sent on its own.

---

## Where your data lives

Everything is in `backend/data/`:

- `personal.db` — the SQLite database
- `uploads/` — uploaded files, stored under generated names

Both are gitignored. **Backing up means copying that one folder.**

The API binds to localhost and the frontend talks straight to it, so nothing
leaves your machine — **with one exception, and only if you switch it on.**
The assistant below sends project text, commit messages and code diffs to
Anthropic. Without `ANTHROPIC_API_KEY` set, none of it runs and the guarantee
above holds exactly as it reads.

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
    github.py      fetching, translating and linking activity
    review.py      the deterministic rules: findings and suggestions
    collaboration.py  turning git history into named people
    routes/personal.py  your own repos, imports and brainstorms
    history.py     what a repo's commit history says, as arithmetic
    share.py       the read-only snapshot you send someone
    powerbi.py     the Power BI Service: reports and refresh state
    workspace.py   the checkout on disk: branch, status, diff, files
    editor.py      handing a file to VS Code, by link or by CLI
    agent.py       the coding agent -- tools, overlay, protected paths
    ai.py          the model client -- the only thing that leaves the machine
    assistant.py   what the model is told, and what it is asked for
    routes/        projects, milestones, tasks, time_logs, library, dashboard, ai
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
