"use client";

import { useCallback, useState } from "react";
import { GitBranch, Pencil, Plus, Trash2, UserPlus, Users } from "lucide-react";

import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { formatDate } from "@/lib/format";
import PersonForm from "@/components/PersonForm";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNote,
  Spinner,
} from "@/components/ui";

function PersonRow({ person, onEdit, onDelete }) {
  return (
    <div className="flex flex-wrap items-start gap-3 border-t border-[var(--border)] px-4 py-3 first:border-t-0">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">
          {person.name}
          {person.role_title ? (
            <span className="ml-1.5 text-xs font-normal text-[var(--text-muted)]">
              {person.role_title}
            </span>
          ) : null}
        </p>
        <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-[var(--text-muted)]">
          {person.github_login ? (
            <Badge tone="neutral">@{person.github_login}</Badge>
          ) : (
            <span>No GitHub login — contributions can&apos;t be attributed</span>
          )}
          {person.email ? <span>{person.email}</span> : null}
        </p>
      </div>

      <div className="flex shrink-0 items-center gap-3 text-xs text-[var(--text-muted)]">
        <span title="Projects they're linked to">
          {person.project_count} project{person.project_count === 1 ? "" : "s"}
        </span>
        <span title="Commits and pull requests attributed to them">
          {person.contribution_count} contribution
          {person.contribution_count === 1 ? "" : "s"}
        </span>
        {person.feedback_open ? (
          <Badge tone="info">{person.feedback_open} open</Badge>
        ) : null}
        {person.last_contribution_at ? (
          <span>last {formatDate(person.last_contribution_at)}</span>
        ) : null}
      </div>

      <div className="flex shrink-0 gap-1.5">
        <Button size="sm" onClick={() => onEdit(person)} aria-label={`Edit ${person.name}`}>
          <Pencil size={13} />
        </Button>
        <Button
          size="sm"
          onClick={() => onDelete(person)}
          aria-label={`Remove ${person.name}`}
        >
          <Trash2 size={13} />
        </Button>
      </div>
    </div>
  );
}

export default function PeoplePage() {
  const [form, setForm] = useState(null);
  const [error, setError] = useState(null);

  const people = useAsync(useCallback(() => api.get("/people"), []), []);
  const unlinked = useAsync(useCallback(() => api.get("/people/unlinked"), []), []);

  const reload = () => {
    people.reload({ quiet: true });
    unlinked.reload({ quiet: true });
  };

  const remove = async (person) => {
    // Worth spelling out what survives: the wording is the whole reassurance.
    const ok = window.confirm(
      `Remove ${person.name}?\n\nTheir commits and feedback are kept — only the link to their name goes.`,
    );
    if (!ok) return;
    setError(null);
    try {
      await api.del(`/people/${person.id}`);
      reload();
    } catch (err) {
      setError(err);
    }
  };

  const rows = people.data || [];
  const ghosts = unlinked.data || [];

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">People</h1>
          <p className="text-sm text-[var(--text-muted)]">
            Who else is involved, and what they&apos;ve contributed. Nobody here
            can sign in — this names the work, and progress is something you send.
          </p>
        </div>
        <Button variant="primary" onClick={() => setForm({})}>
          <Plus size={14} /> Add person
        </Button>
      </header>

      <ErrorNote error={error} onDismiss={() => setError(null)} />
      <ErrorNote error={people.error} onDismiss={() => people.reload()} />

      {ghosts.length ? (
        <Card>
          <CardHeader
            title="Committing but unnamed"
            subtitle="These logins appear in your projects' git history and belong to nobody on record."
          />
          <div className="flex flex-wrap gap-2 px-4 pb-4">
            {ghosts.map((ghost) => (
              <button
                key={ghost.login}
                type="button"
                onClick={() => setForm({ presetLogin: ghost.login })}
                className="flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs hover:bg-[var(--surface-2)]"
              >
                <UserPlus size={12} />
                <span className="font-medium">@{ghost.login}</span>
                <span className="text-[var(--text-muted)]">
                  {ghost.events} event{ghost.events === 1 ? "" : "s"}
                </span>
              </button>
            ))}
          </div>
        </Card>
      ) : null}

      {people.loading && !people.data ? <Spinner /> : null}

      {people.data ? (
        <Card>
          <CardHeader
            title="Everyone"
            action={rows.length ? <Badge tone="neutral">{rows.length}</Badge> : null}
          />
          {rows.length ? (
            rows.map((person) => (
              <PersonRow
                key={person.id}
                person={person}
                onEdit={(p) => setForm({ person: p })}
                onDelete={remove}
              />
            ))
          ) : (
            <EmptyState
              icon={Users}
              title="Nobody added yet"
              description="Add someone to name their commits and to send them progress snapshots."
            />
          )}
        </Card>
      ) : null}

      {rows.length && !rows.some((p) => p.github_login) ? (
        <p className="flex items-start gap-2 text-xs text-[var(--text-muted)]">
          <GitBranch size={13} className="mt-0.5 shrink-0" />
          Nobody has a GitHub login set, so no contributions can be attributed.
          Add one to a person and their existing commits attach straight away.
        </p>
      ) : null}

      {form ? (
        <PersonForm
          open
          person={form.person || null}
          presetLogin={form.presetLogin}
          onClose={() => setForm(null)}
          onSaved={reload}
        />
      ) : null}
    </div>
  );
}
