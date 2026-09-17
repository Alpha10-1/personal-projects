"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { PRIORITIES, PROJECT_CATEGORIES, PROJECT_STATUSES } from "@/lib/constants";
import { Button, ErrorNote, Field, Modal, Select } from "@/components/ui";

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
  const [showMore, setShowMore] = useState(false);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  const set = (field) => (event) =>
    setForm((f) => ({ ...f, [field]: event.target.value }));

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
        progress_override:
          form.progress_override === "" ? null : Number(form.progress_override),
        retro: text(form.retro),
      };
      const saved = project
        ? await api.patch(`/projects/${project.id}`, payload)
        : await api.post("/projects", payload);
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
