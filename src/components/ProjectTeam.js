"use client";

import { useCallback, useState } from "react";
import {
  Check,
  ExternalLink,
  MessageSquare,
  Plus,
  Share2,
  Sparkles,
  Users,
  X,
} from "lucide-react";

import { API_URL, api } from "@/lib/api";
import { useAiStatus } from "@/lib/ai";
import { useAsync } from "@/lib/hooks";
import { formatDate } from "@/lib/format";
import { FEEDBACK_SOURCES, MEMBER_ROLES, labelFor } from "@/lib/constants";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNote,
  Field,
  Modal,
  Select,
  Spinner,
} from "@/components/ui";

function AddMember({ open, onClose, projectId, taken, onSaved }) {
  const people = useAsync(useCallback(() => api.get("/people"), []), []);
  const [personId, setPersonId] = useState("");
  const [role, setRole] = useState("viewer");
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  const available = (people.data || []).filter((p) => !taken.includes(p.id));

  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.post(`/projects/${projectId}/members`, {
        person_id: Number(personId),
        role,
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
    <Modal open={open} onClose={onClose} title="Add someone to this project">
      <form onSubmit={submit} className="space-y-3">
        <ErrorNote error={error} onDismiss={() => setError(null)} />

        {people.loading ? <Spinner /> : null}

        {people.data && available.length === 0 ? (
          <p className="text-sm text-[var(--text-muted)]">
            Everyone on record is already on this project. Add a new person on
            the People page first.
          </p>
        ) : null}

        {available.length ? (
          <>
            <Field label="Person">
              <Select
                includeBlank
                blankLabel="Choose someone"
                required
                value={personId}
                onChange={(event) => setPersonId(event.target.value)}
                options={available.map((p) => ({
                  value: p.id,
                  label: p.github_login ? `${p.name} (@${p.github_login})` : p.name,
                }))}
              />
            </Field>
            <Field
              label="Role"
              hint="A contributor is expected to show up in the git history; a viewer just gets progress."
            >
              <Select
                value={role}
                onChange={(event) => setRole(event.target.value)}
                options={MEMBER_ROLES}
              />
            </Field>
          </>
        ) : null}

        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" busy={saving} disabled={!personId}>
            Add
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function NoteFeedback({ open, onClose, projectId, people, onSaved }) {
  const [personId, setPersonId] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.post("/feedback", {
        body: body.trim(),
        project_id: projectId,
        person_id: personId ? Number(personId) : null,
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
    <Modal open={open} onClose={onClose} title="Note something someone said">
      <form onSubmit={submit} className="space-y-3">
        <ErrorNote error={error} onDismiss={() => setError(null)} />
        <p className="text-xs text-[var(--text-muted)]">
          For what git never sees — a meeting, a call, a corridor. Anything said
          in a pull request or an issue is mirrored automatically.
        </p>

        <Field label="Who said it">
          <Select
            includeBlank
            blankLabel="Not sure / someone else"
            value={personId}
            onChange={(event) => setPersonId(event.target.value)}
            options={people.map((p) => ({ value: p.person_id, label: p.name }))}
          />
        </Field>

        <Field label="What they said">
          <textarea
            rows={4}
            required
            autoFocus
            value={body}
            onChange={(event) => setBody(event.target.value)}
            placeholder="e.g. Wants the ore grade reported alongside the forecast."
          />
        </Field>

        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" busy={saving} disabled={!body.trim()}>
            Save
          </Button>
        </div>
      </form>
    </Modal>
  );
}

/**
 * Who is on a project, what they did, and what they said.
 *
 * The share button opens the snapshot rather than downloading it: the
 * browser's own Save is a better download button than anything here, and
 * opening it first means you see exactly what the other person will see
 * before you send it.
 */
export default function ProjectTeam({ project }) {
  const status = useAiStatus();
  const [memberForm, setMemberForm] = useState(false);
  const [feedbackForm, setFeedbackForm] = useState(false);
  const [digest, setDigest] = useState(null);
  const [digesting, setDigesting] = useState(false);
  const [error, setError] = useState(null);

  const data = useAsync(
    useCallback(() => api.get(`/projects/${project.id}/collaboration`), [project.id]),
    [project.id],
  );
  const reload = () => data.reload({ quiet: true });

  const members = data.data?.members || [];
  const contributors = data.data?.contributors || [];
  const feedback = data.data?.feedback || [];

  const runDigest = async () => {
    setDigesting(true);
    setError(null);
    try {
      setDigest(await api.post(`/ai/projects/${project.id}/digest`));
      reload();
    } catch (err) {
      setError(err);
    } finally {
      setDigesting(false);
    }
  };

  const resolve = async (item, status_) => {
    setError(null);
    try {
      await api.patch(`/feedback/${item.id}`, { status: status_ });
      reload();
    } catch (err) {
      setError(err);
    }
  };

  return (
    <div className="space-y-5">
      <ErrorNote error={error} onDismiss={() => setError(null)} />
      <ErrorNote error={data.error} onDismiss={() => data.reload()} />

      <div className="flex flex-wrap gap-2">
        <a
          href={`${API_URL}/projects/${project.id}/share`}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm hover:bg-[var(--surface-2)]"
        >
          <Share2 size={14} /> Open shareable snapshot
          <ExternalLink size={11} className="opacity-60" />
        </a>
        {status?.configured ? (
          <Button variant="primary" busy={digesting} onClick={runDigest}>
            <Sparkles size={14} /> Write a progress update
          </Button>
        ) : null}
      </div>

      <p className="text-xs text-[var(--text-muted)]">
        The snapshot is a single self-contained file — save it and send it. It
        shows progress only: hours, retros and your internal notes stay here.
      </p>

      {digest ? (
        <Card>
          <CardHeader
            title={digest.title}
            subtitle="Saved as a note on this project, stamped as agent-written."
            action={
              digest.summary_suggested ? (
                <Badge tone="info">Summary change proposed</Badge>
              ) : null
            }
          />
          <div className="space-y-2 px-4 pb-4 text-sm whitespace-pre-wrap">
            {digest.note}
          </div>
          {digest.summary_suggested ? (
            <p className="border-t border-[var(--border)] px-4 py-3 text-xs text-[var(--text-muted)]">
              It also thinks the project summary is out of date. That&apos;s
              waiting on the Review page as a suggestion — it will not replace
              what you wrote unless you accept it.
            </p>
          ) : null}
        </Card>
      ) : null}

      {data.loading && !data.data ? <Spinner /> : null}

      {data.data ? (
        <div className="grid gap-5 lg:grid-cols-2">
          <Card>
            <CardHeader
              title="On this project"
              subtitle="Linked people, and what they've done here."
              action={
                <Button size="sm" onClick={() => setMemberForm(true)}>
                  <Plus size={13} /> Add
                </Button>
              }
            />
            {members.length ? (
              members.map((member) => (
                <div
                  key={member.id}
                  className="flex flex-wrap items-center gap-2 border-t border-[var(--border)] px-4 py-3 first:border-t-0"
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{member.name}</p>
                    <p className="text-xs text-[var(--text-muted)]">
                      {member.github_login ? `@${member.github_login}` : "no GitHub login"}
                      {member.role_title ? ` — ${member.role_title}` : ""}
                    </p>
                  </div>
                  <Badge tone={member.role === "contributor" ? "info" : "neutral"}>
                    {labelFor(MEMBER_ROLES, member.role)}
                  </Badge>
                  <span className="text-xs text-[var(--text-muted)]">
                    {member.contribution_count} here
                  </span>
                  <Button
                    size="sm"
                    onClick={async () => {
                      await api.del(`/members/${member.id}`);
                      reload();
                    }}
                    aria-label={`Remove ${member.name} from this project`}
                  >
                    <X size={13} />
                  </Button>
                </div>
              ))
            ) : (
              <EmptyState
                icon={Users}
                title="Nobody linked yet"
                description="Link someone to name their commits here and to track what they suggest."
              />
            )}

            {contributors.some((c) => !c.person_id) ? (
              <div className="border-t border-[var(--border)] px-4 py-3">
                <p className="text-xs font-medium">Also committing here</p>
                <p className="mt-0.5 text-xs text-[var(--text-muted)]">
                  {contributors
                    .filter((c) => !c.person_id)
                    .map((c) => `@${c.login} (${c.events})`)
                    .join(", ")}
                  {" — add them on the People page to name them."}
                </p>
              </div>
            ) : null}
          </Card>

          <Card>
            <CardHeader
              title="Suggestions from people"
              subtitle="Mirrored from pull requests and issues, plus anything you note yourself."
              action={
                <Button size="sm" onClick={() => setFeedbackForm(true)}>
                  <MessageSquare size={13} /> Note one
                </Button>
              }
            />
            {feedback.length ? (
              feedback.map((item) => (
                <div
                  key={item.id}
                  className="border-t border-[var(--border)] px-4 py-3 first:border-t-0"
                >
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-sm font-medium">
                      {item.person_name || item.author_login || "unknown"}
                    </span>
                    <Badge tone="neutral">
                      {labelFor(FEEDBACK_SOURCES, item.source)}
                    </Badge>
                    {item.status !== "open" ? (
                      <Badge tone={item.status === "actioned" ? "good" : "neutral"}>
                        {item.status}
                      </Badge>
                    ) : null}
                    <span className="text-xs text-[var(--text-muted)]">
                      {formatDate(item.occurred_at)}
                    </span>
                  </div>
                  <p className="mt-1 text-xs whitespace-pre-wrap text-[var(--text-secondary)]">
                    {item.body}
                  </p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-2">
                    {item.url ? (
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-xs text-[var(--accent)]"
                      >
                        On GitHub
                      </a>
                    ) : null}
                    {item.status === "open" ? (
                      <>
                        <Button size="sm" onClick={() => resolve(item, "actioned")}>
                          <Check size={12} /> Actioned
                        </Button>
                        <Button size="sm" onClick={() => resolve(item, "declined")}>
                          <X size={12} /> Decline
                        </Button>
                      </>
                    ) : (
                      <Button size="sm" onClick={() => resolve(item, "open")}>
                        Reopen
                      </Button>
                    )}
                  </div>
                </div>
              ))
            ) : (
              <EmptyState
                icon={MessageSquare}
                title="Nothing suggested yet"
                description="Review comments, pull request descriptions and issue comments land here on the next sync."
              />
            )}
          </Card>
        </div>
      ) : null}

      {memberForm ? (
        <AddMember
          open
          projectId={project.id}
          taken={members.map((m) => m.person_id)}
          onClose={() => setMemberForm(false)}
          onSaved={reload}
        />
      ) : null}

      {feedbackForm ? (
        <NoteFeedback
          open
          projectId={project.id}
          people={members}
          onClose={() => setFeedbackForm(false)}
          onSaved={reload}
        />
      ) : null}
    </div>
  );
}
