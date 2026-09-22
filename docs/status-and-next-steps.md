# Where this stands, what is left, and what IT needs to grant

*Written 21 September 2026. Sections 1–4 are for whoever is working on this
tracker. Section 5 is written to be detached and sent to an IT administrator
on its own.*

---

## 1. Where it stands today

| | |
|---|---|
| Backend tests | 961 passing, 3 deselected (network-marked) |
| Frontend tests | 193 passing across 16 files |
| Tables | 20 |
| Projects | 5, all linked to a repo, all with a summary |
| Commits ingested | 199, of which 197 have file-level detail |
| Tasks / milestones / time logs | **0 / 0 / 0** |
| Notes | 8 (2 written by the assistant) |
| Suggestions | 5 raised, all 5 accepted |
| People / dashboards / brainstorms | 0 / 0 / 0 |
| MCP tools | 34 |
| Model spend to date | $1.12 across 40 calls — see the note on agent runs below |
| Nightly analyst run | green, last ran 07:30 on 21 Sep, exit 0 |

The tracker itself — board, review rules, findings, commit timeline, spend
ledger — runs entirely locally and needs no network at all. The assistant is
the only part that sends anything anywhere.

### What is deliberately not done

Three rules are in force and should stay in force unless a decision is made
to change them:

- **Nothing identifying a Power BI report, workspace or dataset is ever
  sent to a model.** There is no setting that turns this off.
- **The assistant proposes; it never rewrites.** It may add a note and it
  may raise a suggestion. It cannot overwrite something you wrote.
- **The coding agent has no shell and cannot read credentials.** It edits
  into an overlay and nothing reaches disk until you apply it. The
  credential list (`.env*`, keys, certificates, `serviceAccountKey.json`,
  `.npmrc`, `.netrc`) is enforced on reads and on search, not only on
  writes, because a read has already left the machine by the time anyone
  reviews a diff.

---

## 2. The largest gap: the tracker has no work in it

0 tasks, 0 milestones, 0 time logs. Everything downstream of those is
therefore empty or meaningless:

- **Insights** shows nothing. Hours by week, cycle time, opened vs closed
  all need logged work.
- **Estimates are permanently uncalibrated.** The planner needs finished
  tasks that have both an estimate and logged hours before it can say
  anything about how your estimates actually run. Until then it says so
  explicitly, which is honest but not useful.
- **The nightly analyst run has almost nothing to find.** Its rules look for
  stalled work, overdue tasks and estimate overruns — none of which exist.

This is not a code gap. The fastest route out is the **Plan** tab on any
project: pick an option, and the board fills with tasks that have estimates
and dates. Then log time against them for a fortnight and the rest starts
working.

---

## 3. Still to do

Ordered by what I would actually do next.

### Worth doing soon

| | Effort | Why |
|---|---|---|
| **Deep-sync the 2 new commits** | minutes | 197 of 199 have file detail; the two newest arrived after the last pass. The Repo tab offers it. |
| **Repo hygiene rule** | ~2h | A 30-line scan over data already stored found a committed `serviceAccountKey.json` and a `functions/.env` in `course-finder-app`, plus `.firebase` cache in 38 of 53 commits. Deterministic, no model, no API calls. It belongs in `review.py` beside the other rules. |
| **Rotate the Anthropic key** | minutes | It was printed to a terminal during this work. See §4. |
| **Frontend tests for what is untested** | ~1 day | 21 components, 5 tested. Untested: `Assistant`, `ProjectHistory`, `ProjectTeam`, `BrainstormPanel`, `Dashboards`, `RepoInsights`, `TaskList`, `TimeLogPanel`, `LibraryPanels`, `ProjectForm`, `TaskForm`, `PersonForm`, `AiSuggestions`, `Shell`, `charts`, `ui`. |

### Worth doing before the schema changes again

| | Effort | Why |
|---|---|---|
| **Alembic** | ~half a day | 16 tables and `ensure_columns()` can only *add* columns. Every change so far has been an addition, which is partly luck. The first rename or type change will be hand-written SQL against live data. |
| **Delete the duplicate SSE parser** | ~20 min | `ProjectHistory.js` has its own copy of the streaming parser; the tested one is in `lib/ai.js`. The version under test and the version shipping are different code. |

### New since the Code tab landed

| | Effort | Why |
|---|---|---|
| **Point the remaining three projects at folders** | minutes each | Only `personal-projects` and `Organization_management_system` are checked out. `admin-dashboard`, `ride-native` and `course-finder-app` exist only on GitHub, so they have no Code tab. Clone them and set the folder in each brief. |
| **Watch what the model costs** | ongoing | Spend went from $0.09 to $1.12 in one afternoon, almost all of it agent runs. A run is roughly $0.10--$0.15 with caching on, an Explain is $0.02 (cached against the file, so the second look is free), a repository survey is $0.07, and a roadmap is $0.05. That is fine occasionally and not fine as a habit; the Spend page breaks it down by feature. |
| **Consider a test-running tool** | ~half a day, and a real decision | The agent's honest weakness is that it cannot verify anything. A single fixed, project-configured command (not arbitrary shell) would let it check its own work. It is a meaningfully larger security surface than reading and writing files, which is why it was left out. |
| **Frontend tests for `CodeWorkspace`** | ~1h | The file tree and the polling are the last untested part of the Code tab. |
| **Set a leader on the other projects** | minutes | Only `personal-projects` has one. Without a leader, each change has to name its approver by hand. |
| **Teach the outline more languages** | ~2h each | `impact.py` reads Python and JavaScript. Anything else gets line counts and an honest note that it cannot see definitions. |

### Larger, and genuinely optional

- **Infer time from commit timestamps** and *propose* time logs — "you
  committed six times between 19:10 and 22:40, log 3.5h?". This is the only
  realistic way the estimate calibration ever gets data, short of logging by
  hand every day.
- **Extend the MCP server to the planner.** 34 tools, none of which expose
  `/plan`, `/plan/apply`, `/history` or `/ask`. You can read a timeline in a
  conversation but not ask for options or apply one.
- **Power BI delegated sign-in** — see §5, blocked on IT.
- **Three ESLint suppressions** remain (2 in `Shell.js`, 1 in `hooks.js`).
- **`seed.py`** is now unused — the demo data it produced has been deleted.
  It still contains work-shaped sample text in a public repo. Deleting the
  file is a one-liner and closes that.

---

## 4. Things you should know rather than do

**The Anthropic API key was exposed.** During debugging on 21 September I
printed it to the terminal. It is in that session's scrollback. Treat it as
compromised and rotate it at console.anthropic.com. Nothing else in the
system ever prints it — `/ai/status` is tested not to.

**Data has already been sent to Anthropic.** Before the egress controls
existed, these went out: commit timelines and file paths for all five
repos, their READMEs, project names, summaries and notes, the full chat
context (your whole board), and one web search whose queries were derived
from `course-finder-app`. That cannot be recalled. It is all personal-repo
material, not Tharisa data.

**`--reload` misses changes on this OneDrive path.** Twice, new routes
404'd until the backend was restarted manually. If you add an endpoint and
it does not appear, restart rather than debugging the route.

**Something is auto-committing and pushing.** Several commits during this
work appeared under your name with generated messages (`feat: add Vitest…`)
while files were still being edited — one of them captured a half-finished
state and failed CI. Worth finding what is doing it, because it committed
and pushed without being asked.

---

## 5. What to ask IT for

*This section stands alone and can be sent as-is.*

### Context

A single-user project tracker running locally on one Windows laptop:
a Python API on `localhost:8000`, a web front end on `localhost:3000`, and
a SQLite file on disk. No server, no hosting, no other users. It reads
GitHub activity and, optionally, calls a hosted AI API.

### 5.1 Power BI — read reports as the signed-in person

**The ask:** a single-tenant Entra ID (Azure AD) app registration with
**delegated** permissions against the Power BI Service.

| Setting | Value |
|---|---|
| Account types | Single tenant |
| Redirect URI | `http://localhost:8000/powerbi/callback` (platform: Web) |
| Client secret | Yes |
| API permissions | **Delegated**: `Report.Read.All`, `Dataset.Read.All`, `Workspace.Read.All` |
| Power BI admin portal | The app's security group allowed to use the Power BI REST APIs |

**Delegated, not application.** The tool currently uses a service principal,
which is an application identity — row-level security is defined against
user principals and therefore does not describe it. With delegated
permissions the effective access is the intersection of what the app may do
and what the signed-in person may already see, so RLS applies as designed
and the audit trail names the person rather than the app.

**What it will read:** the list of reports and their refresh state. Dataset
*contents* are out of scope, and this tracker is explicitly built never to
send anything identifying a report, workspace or dataset to an AI model.

Admin consent may be required by tenant policy. The full write-up, including
the token flow and storage question, is in
[`docs/powerbi-delegated-access.md`](powerbi-delegated-access.md).

### 5.2 Outbound network access

From the laptop, outbound HTTPS to:

| Host | Why | Optional? |
|---|---|---|
| `api.github.com` | Commit and pull-request history | No — core |
| `api.anthropic.com` | The AI assistant | Yes — the tracker runs fully without it |
| `login.microsoftonline.com`, `api.powerbi.com` | Power BI, if §5.1 is granted | Yes |

If a proxy intercepts TLS, the Python client will need the corporate root CA
in its trust store.

### 5.3 Running local services

The tool binds `localhost:8000` and `localhost:3000`, and registers a
Windows Scheduled Task (`PersonalProjects-AnalystRun`, daily 07:30) that
runs a Python script. Both already work on this machine; flagging them in
case endpoint policy changes.

### 5.4 The AI question — the one that needs a policy decision

The assistant sends project material to **Anthropic's API**, a third party.
Today that is personal-repository content only. If this tracker is ever
pointed at Tharisa work, three things need deciding:

1. **Is sending work project metadata to a third-party AI API permitted?**
   If not, the tool has one setting — `PP_AI_EGRESS=off` — that disables all
   of it while leaving the tracker fully functional.
2. **Is there an approved alternative?** If Tharisa has **Azure OpenAI** or
   Claude via **Microsoft Foundry** provisioned, the data stays inside the
   tenant and the question largely goes away. *Note: this is not a
   configuration change — the code is written against the Anthropic SDK and
   would need real work to target a different provider. Worth knowing
   before it is promised to anyone.*
3. **Where should the API key live?** It currently sits in a gitignored
   `.env` file on disk. If there is a corporate secret store, that is
   better.

Two guarantees already implemented, in case they help the assessment:

- Nothing identifying a Power BI report, workspace or dataset can be sent —
  enforced on every outgoing call, with no setting to disable it.
- Every model call is logged locally with its token counts and cost, so what
  was sent, when, and what it cost is auditable after the fact.

### 5.5 GitHub — only if work repositories are ever tracked

Today the tool reads one personal GitHub account using a personal access
token with read-only scope, which needs nothing from IT. If it is ever
pointed at a Tharisa-owned organisation, that would need the token
authorised for the organisation (SSO) and read access to the repositories
concerned — a separate request, not needed now.
