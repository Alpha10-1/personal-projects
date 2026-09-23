"use client";

import { useCallback, useRef, useState } from "react";
import {
  Download,
  ExternalLink,
  FileText,
  Link2,
  Pin,
  PinOff,
  Plus,
  StickyNote,
  Trash2,
  Upload,
} from "lucide-react";
import { api, downloadUrl } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { formatBytes, formatDate } from "@/lib/format";
import { LINK_KINDS, NOTE_KINDS, labelFor } from "@/lib/constants";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNote,
  Field,
  Select,
} from "@/components/ui";

const NOTE_KIND_TONE = {
  note: "neutral",
  decision: "info",
  result: "good",
  blocker: "critical",
  idea: "neutral",
};

function NotesPanel({ projectId, projects, showProject }) {
  const [form, setForm] = useState({ title: "", body: "", kind: "note", project_id: "" });
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const notes = useAsync(
    useCallback(() => api.get("/notes", { project_id: projectId ?? "" }), [projectId]),
    [projectId],
  );

  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.post("/notes", {
        title: form.title.trim() || null,
        body: form.body.trim(),
        kind: form.kind,
        project_id:
          projectId ?? (form.project_id === "" ? null : Number(form.project_id)),
      });
      setForm({ title: "", body: "", kind: "note", project_id: form.project_id });
      setOpen(false);
      notes.reload({ quiet: true });
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  };

  const act = async (fn) => {
    setError(null);
    try {
      await fn();
      notes.reload({ quiet: true });
    } catch (err) {
      setError(err);
    }
  };

  const rows = notes.data || [];

  return (
    <Card>
      <CardHeader
        title="Notes"
        subtitle="Decisions, results and dead ends worth not repeating."
        action={
          <Button size="sm" variant={open ? "secondary" : "primary"} onClick={() => setOpen((o) => !o)}>
            <Plus size={13} /> {open ? "Hide" : "Add"}
          </Button>
        }
      />

      {open ? (
        <form onSubmit={submit} className="space-y-3 border-b bg-[var(--surface-2)]/50 p-3">
          <ErrorNote error={error} onDismiss={() => setError(null)} />
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Title (optional)">
              <input
                type="text"
                value={form.title}
                onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
              />
            </Field>
            <Field label="Kind">
              <Select
                value={form.kind}
                onChange={(e) => setForm((f) => ({ ...f, kind: e.target.value }))}
                options={NOTE_KINDS}
              />
            </Field>
          </div>
          {showProject ? (
            <Field label="Project">
              <Select
                includeBlank
                blankLabel="No project"
                value={form.project_id}
                onChange={(e) => setForm((f) => ({ ...f, project_id: e.target.value }))}
                options={projects.map((p) => ({ value: p.id, label: p.name }))}
              />
            </Field>
          ) : null}
          <Field label="Note">
            <textarea
              required
              rows={4}
              value={form.body}
              onChange={(e) => setForm((f) => ({ ...f, body: e.target.value }))}
              placeholder="What happened, what you decided, and why."
            />
          </Field>
          <div className="flex justify-end">
            <Button type="submit" variant="primary" busy={saving}>
              Save note
            </Button>
          </div>
        </form>
      ) : (
        <ErrorNote error={error} onDismiss={() => setError(null)} />
      )}

      {rows.length ? (
        <ul className="divide-y">
          {rows.map((note) => (
            <li key={note.id} className="px-3 py-3">
              <div className="flex items-start justify-between gap-2">
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  {note.title ? (
                    <span className="text-sm font-medium">{note.title}</span>
                  ) : null}
                  <Badge tone={NOTE_KIND_TONE[note.kind] || "neutral"}>
                    {labelFor(NOTE_KINDS, note.kind)}
                  </Badge>
                  {note.pinned ? <Badge tone="info">Pinned</Badge> : null}
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    aria-label={note.pinned ? "Unpin note" : "Pin note"}
                    onClick={() =>
                      act(() => api.patch(`/notes/${note.id}`, { pinned: !note.pinned }))
                    }
                    className="rounded p-1 text-[var(--text-muted)] hover:bg-[var(--surface-2)]"
                  >
                    {note.pinned ? <PinOff size={13} /> : <Pin size={13} />}
                  </button>
                  <button
                    type="button"
                    aria-label="Delete note"
                    onClick={() =>
                      act(async () => {
                        if (!window.confirm("Delete this note?")) return;
                        await api.del(`/notes/${note.id}`);
                      })
                    }
                    className="rounded p-1 text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--critical)]"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
              <p className="mt-1 whitespace-pre-wrap text-sm text-[var(--text-secondary)]">
                {note.body}
              </p>
              <p className="mt-1.5 text-[11px] text-[var(--text-muted)]">
                {formatDate(note.created_at)}
                {showProject && note.project_name ? ` · ${note.project_name}` : ""}
              </p>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState icon={StickyNote} title="No notes yet" />
      )}
    </Card>
  );
}

function LinksPanel({ projectId, projects, showProject }) {
  const [form, setForm] = useState({ title: "", url: "", kind: "other", project_id: "" });
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const links = useAsync(
    useCallback(() => api.get("/links", { project_id: projectId ?? "" }), [projectId]),
    [projectId],
  );

  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.post("/links", {
        title: form.title.trim(),
        url: form.url.trim(),
        kind: form.kind,
        project_id:
          projectId ?? (form.project_id === "" ? null : Number(form.project_id)),
      });
      setForm((f) => ({ ...f, title: "", url: "" }));
      links.reload({ quiet: true });
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  };

  const rows = links.data || [];

  return (
    <Card>
      <CardHeader
        title="Links"
        subtitle="Papers, repos, dashboards and docs."
        action={
          <Button size="sm" variant={open ? "secondary" : "primary"} onClick={() => setOpen((o) => !o)}>
            <Plus size={13} /> {open ? "Hide" : "Add"}
          </Button>
        }
      />

      {open ? (
        <form onSubmit={submit} className="space-y-3 border-b bg-[var(--surface-2)]/50 p-3">
          <ErrorNote error={error} onDismiss={() => setError(null)} />
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Title">
              <input
                type="text"
                required
                value={form.title}
                onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
              />
            </Field>
            <Field label="Kind">
              <Select
                value={form.kind}
                onChange={(e) => setForm((f) => ({ ...f, kind: e.target.value }))}
                options={LINK_KINDS}
              />
            </Field>
          </div>
          <Field label="URL">
            <input
              type="text"
              required
              value={form.url}
              onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))}
              placeholder="arxiv.org/abs/…"
            />
          </Field>
          {showProject ? (
            <Field label="Project">
              <Select
                includeBlank
                blankLabel="No project"
                value={form.project_id}
                onChange={(e) => setForm((f) => ({ ...f, project_id: e.target.value }))}
                options={projects.map((p) => ({ value: p.id, label: p.name }))}
              />
            </Field>
          ) : null}
          <div className="flex justify-end">
            <Button type="submit" variant="primary" busy={saving}>
              Save link
            </Button>
          </div>
        </form>
      ) : (
        <ErrorNote error={error} onDismiss={() => setError(null)} />
      )}

      {rows.length ? (
        <ul className="divide-y">
          {rows.map((link) => (
            <li key={link.id} className="flex items-start gap-2 px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <a
                  href={link.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="inline-flex items-center gap-1 text-sm font-medium text-[var(--accent)] hover:underline"
                >
                  {link.title}
                  <ExternalLink size={12} />
                </a>
                <p className="truncate text-[11px] text-[var(--text-muted)]">
                  <Badge className="mr-1.5">{labelFor(LINK_KINDS, link.kind)}</Badge>
                  {link.url}
                  {showProject && link.project_name ? ` · ${link.project_name}` : ""}
                </p>
              </div>
              <button
                type="button"
                aria-label="Delete link"
                onClick={async () => {
                  // Notes and files both ask; this one did not, so a
                  // mis-click deleted a link with no way back.
                  if (!window.confirm(`Delete "${link.title || link.url}"?`)) return;
                  try {
                    await api.del(`/links/${link.id}`);
                    links.reload({ quiet: true });
                  } catch (err) {
                    setError(err);
                  }
                }}
                className="shrink-0 rounded p-1 text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--critical)]"
              >
                <Trash2 size={13} />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState icon={Link2} title="No links saved" />
      )}
    </Card>
  );
}

function FilesPanel({ projectId, showProject }) {
  const inputRef = useRef(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);

  const files = useAsync(
    useCallback(() => api.get("/files", { project_id: projectId ?? "" }), [projectId]),
    [projectId],
  );

  const upload = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const body = new FormData();
      body.append("upload", file);
      if (projectId) body.append("project_id", String(projectId));
      await api.upload("/files", body);
      files.reload({ quiet: true });
    } catch (err) {
      setError(err);
    } finally {
      setUploading(false);
      // Clearing the input means picking the same file again still fires
      // a change event.
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const rows = files.data || [];

  return (
    <Card>
      <CardHeader
        title="Files"
        subtitle="Stored on this machine, up to 25MB each."
        action={
          <>
            <input
              ref={inputRef}
              type="file"
              onChange={upload}
              className="hidden"
              id={`upload-${projectId ?? "all"}`}
            />
            <Button
              size="sm"
              variant="primary"
              busy={uploading}
              onClick={() => inputRef.current?.click()}
            >
              <Upload size={13} /> Upload
            </Button>
          </>
        }
      />
      <ErrorNote error={error} onDismiss={() => setError(null)} />

      {rows.length ? (
        <ul className="divide-y">
          {rows.map((file) => (
            <li key={file.id} className="flex items-center gap-2 px-3 py-2.5">
              <FileText size={15} className="shrink-0 text-[var(--text-muted)]" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm">{file.filename}</p>
                <p className="text-[11px] text-[var(--text-muted)]">
                  {formatBytes(file.size_bytes)} · {formatDate(file.created_at)}
                  {showProject && file.project_name ? ` · ${file.project_name}` : ""}
                </p>
              </div>
              <a
                href={downloadUrl(file.id)}
                className="shrink-0 rounded p-1 text-[var(--text-muted)] hover:bg-[var(--surface-2)]"
                aria-label={`Download ${file.filename}`}
              >
                <Download size={14} />
              </a>
              <button
                type="button"
                aria-label={`Delete ${file.filename}`}
                onClick={async () => {
                  if (!window.confirm(`Delete "${file.filename}"?`)) return;
                  try {
                    await api.del(`/files/${file.id}`);
                    files.reload({ quiet: true });
                  } catch (err) {
                    setError(err);
                  }
                }}
                className="shrink-0 rounded p-1 text-[var(--text-muted)] hover:bg-[var(--surface-2)] hover:text-[var(--critical)]"
              >
                <Trash2 size={13} />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState icon={FileText} title="No files yet" />
      )}
    </Card>
  );
}

export default function LibraryPanels({ projectId = null, projects = [] }) {
  const showProject = projectId === null;
  return (
    <div className="space-y-5">
      <NotesPanel projectId={projectId} projects={projects} showProject={showProject} />
      <div className="grid gap-5 lg:grid-cols-2">
        <LinksPanel projectId={projectId} projects={projects} showProject={showProject} />
        <FilesPanel projectId={projectId} showProject={showProject} />
      </div>
    </div>
  );
}
