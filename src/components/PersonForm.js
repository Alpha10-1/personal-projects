"use client";

import { useState } from "react";

import { api } from "@/lib/api";
import { Button, ErrorNote, Field, Modal } from "@/components/ui";

const EMPTY = {
  name: "",
  email: "",
  github_login: "",
  role_title: "",
  notes: "",
};

function toForm(person) {
  if (!person) return EMPTY;
  return Object.fromEntries(
    Object.keys(EMPTY).map((key) => [key, person[key] ?? ""]),
  );
}

export default function PersonForm({ open, onClose, onSaved, person = null, presetLogin }) {
  const [form, setForm] = useState(() => ({
    ...toForm(person),
    ...(presetLogin ? { github_login: presetLogin } : {}),
  }));
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
        email: text(form.email),
        github_login: text(form.github_login),
        role_title: text(form.role_title),
        notes: text(form.notes),
      };
      const saved = person
        ? await api.patch(`/people/${person.id}`, payload)
        : await api.post("/people", payload);
      onSaved?.(saved);
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title={person ? "Edit person" : "Add a person"}>
      <form onSubmit={submit} className="space-y-3">
        <ErrorNote error={error} onDismiss={() => setError(null)} />

        <Field label="Name">
          <input
            type="text"
            required
            autoFocus
            value={form.name}
            onChange={set("name")}
            placeholder="e.g. Thabo Arendse"
          />
        </Field>

        <Field
          label="GitHub login"
          hint="This is what names their commits. Leave blank for someone who only reads updates — they'll just have no contributions."
        >
          <input
            type="text"
            value={form.github_login}
            onChange={set("github_login")}
            placeholder="octocat"
          />
        </Field>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Role" hint="Their job, not their access.">
            <input
              type="text"
              value={form.role_title}
              onChange={set("role_title")}
              placeholder="Data engineering lead"
            />
          </Field>
          <Field label="Email" hint="For sending them a progress snapshot.">
            <input type="email" value={form.email} onChange={set("email")} />
          </Field>
        </div>

        <Field label="Notes">
          <textarea rows={2} value={form.notes} onChange={set("notes")} />
        </Field>

        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" busy={saving}>
            {person ? "Save changes" : "Add person"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
