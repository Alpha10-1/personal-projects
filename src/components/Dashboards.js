"use client";

import { useCallback, useState } from "react";
import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  ExternalLink,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";

import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { formatDate } from "@/lib/format";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNote,
  Field,
  Modal,
  Spinner,
} from "@/components/ui";

function AddDashboard({ open, onClose, projectId, onSaved }) {
  const [form, setForm] = useState({ name: "", url: "", note: "" });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const set = (field) => (event) =>
    setForm((f) => ({ ...f, [field]: event.target.value }));

  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.post("/dashboards", {
        name: form.name.trim(),
        url: form.url.trim() || null,
        note: form.note.trim() || null,
        project_id: projectId ?? null,
      });
      onSaved?.();
      onClose();
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Link a report">
      <form onSubmit={submit} className="space-y-3">
        <ErrorNote error={error} onDismiss={() => setError(null)} />
        <Field label="Name">
          <input
            type="text"
            required
            autoFocus
            value={form.name}
            onChange={set("name")}
            placeholder="e.g. Plant availability"
          />
        </Field>
        <Field
          label="Link"
          hint="A Power BI report URL is recognised — if you connect the Service later, this same row fills in rather than appearing twice."
        >
          <input
            type="url"
            value={form.url}
            onChange={set("url")}
            placeholder="https://app.powerbi.com/groups/…/reports/…"
          />
        </Field>
        <Field label="Note">
          <textarea rows={2} value={form.note} onChange={set("note")} />
        </Field>
        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" busy={saving} disabled={!form.name.trim()}>
            Add
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function Refresh({ item }) {
  if (item.source !== "powerbi" || !item.refresh_status) {
    // Nothing can be known about a link nobody can check. Saying so is
    // better than a green tick that means "we didn't look".
    return <span className="text-xs text-[var(--text-muted)]">not checked</span>;
  }
  const failed = item.refresh_status === "Failed";
  return (
    <span className="flex items-center gap-1 text-xs">
      {failed ? (
        <AlertTriangle size={12} className="text-[var(--warning,#d97706)]" />
      ) : (
        <CheckCircle2 size={12} className="text-[var(--good,#3d6b53)]" />
      )}
      <span className={failed ? "font-medium" : "text-[var(--text-muted)]"}>
        {item.refresh_status}
      </span>
      {item.last_refresh_at ? (
        <span className="text-[var(--text-muted)]">{formatDate(item.last_refresh_at)}</span>
      ) : null}
    </span>
  );
}

/**
 * Reports linked to a project, and what the Power BI Service says about them.
 *
 * Works with Power BI switched off: a pasted link is a perfectly good record
 * of which report belongs to which work. Connecting the Service adds refresh
 * state to the rows it recognises, rather than replacing what's here.
 */
export default function Dashboards({ projectId }) {
  const [adding, setAdding] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState(null);
  const [synced, setSynced] = useState(null);

  const status = useAsync(useCallback(() => api.get("/powerbi/status"), []), []);
  const boards = useAsync(
    useCallback(
      () => api.get("/dashboards", projectId ? { project_id: projectId } : undefined),
      [projectId],
    ),
    [projectId],
  );

  const sync = async () => {
    setSyncing(true);
    setError(null);
    try {
      setSynced(await api.post("/powerbi/sync"));
      boards.reload({ quiet: true });
    } catch (err) {
      setError(err);
    } finally {
      setSyncing(false);
    }
  };

  const remove = async (item) => {
    if (
      !window.confirm(
        item.source === "powerbi"
          ? `Remove ${item.name} from the tracker?\n\nIt stays in Power BI, and will come back on the next sync.`
          : `Remove ${item.name}?`,
      )
    )
      return;
    await api.del(`/dashboards/${item.id}`);
    boards.reload({ quiet: true });
  };

  const rows = boards.data || [];
  const connected = status.data?.configured;

  return (
    <Card>
      <CardHeader
        title="Reports & dashboards"
        subtitle={
          connected
            ? "Linked reports, with what Power BI says about their data."
            : "Linked reports. Connect Power BI to see whether their data is still refreshing."
        }
        action={
          <div className="flex gap-1.5">
            {connected ? (
              <Button size="sm" busy={syncing} onClick={sync}>
                <RefreshCw size={13} /> Sync
              </Button>
            ) : null}
            <Button size="sm" variant="primary" onClick={() => setAdding(true)}>
              <Plus size={13} /> Link
            </Button>
          </div>
        }
      />

      <div className="px-4 pt-3">
        <ErrorNote error={error} onDismiss={() => setError(null)} />
        {synced ? (
          <p className="mb-2 text-xs text-[var(--text-muted)]">
            {synced.reports_seen} report(s) seen — {synced.added} new,{" "}
            {synced.adopted} matched to links you&apos;d added
            {synced.failing ? `, ${synced.failing} failing` : ""}.
          </p>
        ) : null}
        {status.data && !connected ? (
          <p className="mb-2 text-xs text-[var(--text-muted)]">{status.data.reason}</p>
        ) : null}
      </div>

      {boards.loading && !boards.data ? <Spinner /> : null}

      {rows.length
        ? rows.map((item) => (
            <div
              key={item.id}
              className="flex flex-wrap items-center gap-2 border-t border-[var(--border)] px-4 py-2.5"
            >
              <div className="min-w-0 flex-1">
                <p className="flex flex-wrap items-center gap-1.5 text-sm font-medium">
                  {item.url ? (
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 hover:text-[var(--accent)]"
                    >
                      {item.name}
                      <ExternalLink size={11} className="opacity-60" />
                    </a>
                  ) : (
                    item.name
                  )}
                  {item.source === "powerbi" ? (
                    <Badge tone="info">Power BI</Badge>
                  ) : null}
                </p>
                <p className="text-xs text-[var(--text-muted)]">
                  {[item.workspace_name, item.dataset_name].filter(Boolean).join(" · ")}
                  {item.note ? ` — ${item.note}` : ""}
                </p>
              </div>
              <Refresh item={item} />
              <Button size="sm" onClick={() => remove(item)} aria-label={`Remove ${item.name}`}>
                <Trash2 size={12} />
              </Button>
            </div>
          ))
        : boards.data && (
            <EmptyState
              icon={BarChart3}
              title="No reports linked"
              description="Paste a Power BI link so the tracker knows which report belongs to this work."
            />
          )}

      {adding ? (
        <AddDashboard
          open
          projectId={projectId}
          onClose={() => setAdding(false)}
          onSaved={() => boards.reload({ quiet: true })}
        />
      ) : null}
    </Card>
  );
}
