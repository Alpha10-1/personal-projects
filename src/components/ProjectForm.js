"use client";

import { useState } from "react";
import {
  FlaskConical,
  FolderGit2,
  GitBranch,
  ShieldAlert,
  UserCheck,
} from "lucide-react";

import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { PRIORITIES, PROJECT_CATEGORIES, PROJECT_STATUSES } from "@/lib/constants";
import AiSuggestions from "@/components/AiSuggestions";
import { Badge, Button, ErrorNote, Field, Modal, Select } from "@/components/ui";

const EMPTY = {
  name: "",
  summary: "",
  status: "planning",
  category: "other",
  priority: "medium",
  start_date: "",
  target_date: "",
  objective: "",
  definition_of_done: "",
  stakeholder: "",
  tech_stack: "",
  repo: "",
  local_path: "",
  protected_paths: "",
  test_command: "",
  test_dir: "",
  leader_id: "",
  progress_override: "",
  retro: "",
};

function toForm(project) {
  if (!project) return EMPTY;
  return Object.fromEntries(
    Object.keys(EMPTY).map((key) => [key, project[key] ?? ""]),
  );
}

export default function ProjectForm({ open, onClose, onSaved, project = null }) {
  const [form, setForm] = useState(() => toForm(project));
  // Loaded here rather than passed in: the form is opened from four
  // different places and threading the list through all of them would be
  // four chances to forget.
  const { data: people } = useAsync(() => api.get("/people"), []);
  const [showMore, setShowMore] = useState(false);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  // Suggested tasks can't be created until the project has an id, so they
  // wait here and are written immediately after it saves.
  const [plannedTasks, setPlannedTasks] = useState([]);

  const set = (field) => (event) =>
    setForm((f) => ({ ...f, [field]: event.target.value }));

  const applySuggestion = (field, value) => {
    setForm((f) => ({ ...f, [field]: String(value) }));
    // Extra detail is collapsed by default, so applying something in there
    // would otherwise look like nothing happened.
    if (["objective", "definition_of_done", "tech_stack"].includes(field)) {
      setShowMore(true);
    }
  };

  const planTasks = (_field, titles) =>
    setPlannedTasks((existing) => [
      ...existing,
      ...titles.filter((title) => !existing.includes(title)),
    ]);

  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const text = (value) => (value?.trim?.() ? value.trim() : null);
      const payload = {
        name: form.name.trim(),
        summary: text(form.summary),
        status: form.status,
        category: form.category,
        priority: form.priority,
        start_date: form.start_date || null,
        target_date: form.target_date || null,
        objective: text(form.objective),
        definition_of_done: text(form.definition_of_done),
        stakeholder: text(form.stakeholder),
        tech_stack: text(form.tech_stack),
        repo: text(form.repo),
        local_path: text(form.local_path),
        protected_paths: text(form.protected_paths),
        test_command: text(form.test_command),
        test_dir: text(form.test_dir),
        leader_id: form.leader_id === "" ? null : Number(form.leader_id),
        progress_override:
          form.progress_override === "" ? null : Number(form.progress_override),
        retro: text(form.retro),
      };
      const saved = project
        ? await api.patch(`/projects/${project.id}`, payload)
        : await api.post("/projects", payload);

      // Sequential rather than parallel: if one fails, the ones before it are
      // already saved and the error names the rest, instead of leaving an
      // arbitrary subset written.
      for (const title of plannedTasks) {
        await api.post("/tasks", { title, project_id: saved.id });
      }

      onSaved?.(saved);
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      width="max-w-2xl"
      title={project ? "Edit project" : "New project"}
    >
      <form onSubmit={submit} className="space-y-3">
        <ErrorNote error={error} onDismiss={() => setError(null)} />

        <Field label="Name">
          <input
            type="text"
            required
            autoFocus
            value={form.name}
            onChange={set("name")}
            placeholder="e.g. Shift-scheduling forecast model"
          />
        </Field>

        <Field label="Summary" hint="One or two lines on what this is.">
          <textarea rows={2} value={form.summary} onChange={set("summary")} />
        </Field>

        <AiSuggestions
          kind="project"
          draft={form}
          onApply={applySuggestion}
          onApplyList={planTasks}
        />

        {plannedTasks.length ? (
          <div className="rounded-lg border bg-[var(--surface-2)]/60 p-3">
            <p className="text-xs font-medium">
              Tasks to create with this project
              <span className="ml-1 text-[var(--text-muted)]">
                ({plannedTasks.length})
              </span>
            </p>
            <ul className="mt-1.5 space-y-1">
              {plannedTasks.map((title) => (
                <li key={title} className="flex items-center gap-2 text-xs">
                  <button
                    type="button"
                    onClick={() =>
                      setPlannedTasks((tasks) => tasks.filter((t) => t !== title))
                    }
                    className="text-[var(--text-muted)] hover:text-[var(--danger,#dc2626)]"
                    aria-label={`Don't create ${title}`}
                  >
                    ×
                  </button>
                  <span>{title}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Status">
            <Select value={form.status} onChange={set("status")} options={PROJECT_STATUSES} />
          </Field>
          <Field label="Category">
            <Select
              value={form.category}
              onChange={set("category")}
              options={PROJECT_CATEGORIES}
            />
          </Field>
          <Field label="Priority">
            <Select value={form.priority} onChange={set("priority")} options={PRIORITIES} />
          </Field>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Start date">
            <input type="date" value={form.start_date} onChange={set("start_date")} />
          </Field>
          <Field label="Target date">
            <input type="date" value={form.target_date} onChange={set("target_date")} />
          </Field>
        </div>

        <Field
          label={
            <span className="inline-flex items-center gap-1.5">
              <GitBranch size={13} /> GitHub repo
              <Badge tone="neutral">optional</Badge>
            </span>
          }
          hint="Linking one pulls in commits and pull requests, and lets the analyst review them. Leave blank to keep this project off GitHub entirely."
        >
          <input
            type="text"
            value={form.repo}
            onChange={set("repo")}
            placeholder="owner/name"
            pattern="^$|^[\w.-]+/[\w.-]+$"
            title="Use owner/name, for example Alpha10-1/personal-projects"
          />
        </Field>

        <Field
          label={
            <span className="inline-flex items-center gap-1.5">
              <FolderGit2 size={13} /> Local folder
              <Badge tone="neutral">optional</Badge>
            </span>
          }
          hint="The checkout on this machine. Setting it turns on the Code tab — the live working copy, and the agent that can propose edits to it. Separate from the GitHub link: a repo you have not cloned has one and not the other."
        >
          <input
            type="text"
            value={form.local_path}
            onChange={set("local_path")}
            placeholder="C:\\Users\\you\\repos\\project"
          />
        </Field>

        <Field
          label={
            <span className="inline-flex items-center gap-1.5">
              <UserCheck size={13} /> Project leader
              <Badge tone="neutral">optional</Badge>
            </span>
          }
          hint="Who reviews and approves changes to this project's files. Not a permission -- this app has no login -- but an edit is not written to disk until it is approved in someone's name, and the record says whose."
        >
          <Select
            value={form.leader_id}
            onChange={set("leader_id")}
            includeBlank
            blankLabel="Nobody yet"
            options={(people || []).map((person) => ({
              value: String(person.id),
              label: person.role_title
                ? `${person.name} — ${person.role_title}`
                : person.name,
            }))}
          />
        </Field>

        <Field
          label={
            <span className="inline-flex items-center gap-1.5">
              <ShieldAlert size={13} /> Files that need your approval
              <Badge tone="neutral">optional</Badge>
            </span>
          }
          hint="One glob per line. The agent may still change these, but a run that does can never be applied automatically. Leave blank for the built-in list: migrations, CI, lockfiles, auth and models."
        >
          <textarea
            rows={3}
            value={form.protected_paths}
            onChange={set("protected_paths")}
            placeholder={"**/migrations/*\nsrc/pricing.py"}
          />
        </Field>

        <Field
          label={
            <span className="inline-flex items-center gap-1.5">
              <FlaskConical size={13} /> Test command
              <Badge tone="neutral">optional</Badge>
            </span>
          }
          hint="The one command the agent may run to check its own work. It chooses when, never what — nothing it writes reaches this line. Not a shell: one program and its arguments, so && and | are just text. Leave blank and the agent cannot verify anything."
        >
          <input
            value={form.test_command}
            onChange={set("test_command")}
            placeholder="python -m pytest -q"
          />
        </Field>

        <Field
          label="Run the tests from"
          hint="A folder inside the repository. Blank means the repository root."
        >
          <input
            value={form.test_dir}
            onChange={set("test_dir")}
            placeholder="backend"
          />
        </Field>

        <button
          type="button"
          onClick={() => setShowMore((open) => !open)}
          className="text-xs font-medium text-[var(--accent)]"
        >
          {showMore ? "Hide extra detail" : "Add objective, stakeholder, done criteria…"}
        </button>

        {showMore ? (
          <div className="space-y-3 rounded-lg border bg-[var(--surface-2)]/60 p-3">
            <Field label="Objective" hint="Why this is worth doing.">
              <textarea rows={2} value={form.objective} onChange={set("objective")} />
            </Field>
            <Field label="Definition of done" hint="How you'll know it's finished.">
              <textarea
                rows={2}
                value={form.definition_of_done}
                onChange={set("definition_of_done")}
              />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Stakeholder" hint="Who asked for it / who sees the result.">
                <input
                  type="text"
                  value={form.stakeholder}
                  onChange={set("stakeholder")}
                />
              </Field>
              <Field label="Tools & stack">
                <input
                  type="text"
                  value={form.tech_stack}
                  onChange={set("tech_stack")}
                  placeholder="Python, Power BI, Azure OpenAI"
                />
              </Field>
            </div>
            <Field
              label="Progress override (%)"
              hint="Leave blank to derive progress from tasks and milestones."
            >
              <input
                type="number"
                min="0"
                max="100"
                value={form.progress_override}
                onChange={set("progress_override")}
              />
            </Field>
            <Field label="Retro / lessons learned">
              <textarea rows={2} value={form.retro} onChange={set("retro")} />
            </Field>
          </div>
        ) : null}

        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" busy={saving}>
            {project ? "Save changes" : "Create project"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
