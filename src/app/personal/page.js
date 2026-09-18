"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import {
  Download,
  FolderKanban,
  GitBranch,
  Lightbulb,
  Lock,
  RefreshCw,
  Sparkles,
  Star,
} from "lucide-react";

import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { formatDate } from "@/lib/format";
import BrainstormPanel from "@/components/BrainstormPanel";
import Scaffold from "@/components/Scaffold";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNote,
  ProgressBar,
  Spinner,
} from "@/components/ui";

const TABS = [
  { key: "projects", label: "Projects", icon: FolderKanban },
  { key: "repos", label: "From GitHub", icon: GitBranch },
  { key: "new", label: "Start something", icon: Sparkles },
  { key: "brainstorm", label: "Brainstorm", icon: Lightbulb },
];

function ProjectCard({ project }) {
  return (
    <Link
      href={`/projects/${project.id}`}
      className="block border-t border-[var(--border)] px-4 py-3 first:border-t-0 hover:bg-[var(--surface-2)]"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{project.name}</span>
        <Badge tone="neutral">{project.status}</Badge>
        {project.repo ? (
          <span className="text-xs text-[var(--text-muted)]">{project.repo}</span>
        ) : null}
      </div>
      {project.summary ? (
        <p className="mt-0.5 line-clamp-2 text-xs text-[var(--text-muted)]">
          {project.summary}
        </p>
      ) : null}
      <div className="mt-2 flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <ProgressBar value={project.progress} />
        </div>
        <span className="shrink-0 text-xs text-[var(--text-muted)]">
          {project.open_tasks ?? 0} open
        </span>
      </div>
    </Link>
  );
}

function Repos({ onImported }) {
  const [picked, setPicked] = useState(() => new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const repos = useAsync(useCallback(() => api.get("/personal/repos"), []), []);

  const toggle = (name) =>
    setPicked((current) => {
      const next = new Set(current);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  const importPicked = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.post("/personal/repos/import", { repos: [...picked] });
      setPicked(new Set());
      repos.reload({ quiet: true });
      onImported?.();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const list = repos.data?.repos || [];
  const available = list.filter((r) => !r.imported);

  return (
    <div className="space-y-4">
      <ErrorNote error={repos.error} onDismiss={() => repos.reload()} />
      <ErrorNote error={error} onDismiss={() => setError(null)} />

      {repos.loading && !repos.data ? <Spinner label="Reading your repos" /> : null}

      {repos.data ? (
        <Card>
          <CardHeader
            title={`${repos.data.user} — ${repos.data.count} repo${repos.data.count === 1 ? "" : "s"}`}
            subtitle={
              repos.data.authenticated
                ? "Including private repos, via GITHUB_TOKEN."
                : "Public repos only. Add GITHUB_TOKEN to backend/.env to see private ones."
            }
            action={
              <div className="flex gap-1.5">
                <Button size="sm" onClick={() => repos.reload()}>
                  <RefreshCw size={13} />
                </Button>
                <Button
                  size="sm"
                  variant="primary"
                  busy={busy}
                  disabled={picked.size === 0}
                  onClick={importPicked}
                >
                  <Download size={13} /> Import {picked.size || ""}
                </Button>
              </div>
            }
          />

          {available.length ? (
            <div className="flex flex-wrap gap-1.5 border-b border-[var(--border)] px-4 py-2">
              <button
                type="button"
                className="text-xs font-medium text-[var(--accent)]"
                onClick={() => setPicked(new Set(available.map((r) => r.full_name)))}
              >
                Select all {available.length}
              </button>
              {picked.size ? (
                <button
                  type="button"
                  className="text-xs text-[var(--text-muted)]"
                  onClick={() => setPicked(new Set())}
                >
                  · Clear
                </button>
              ) : null}
            </div>
          ) : null}

          {list.map((repo) => (
            <label
              key={repo.full_name}
              className={`flex cursor-pointer items-start gap-3 border-t border-[var(--border)] px-4 py-2.5 first:border-t-0 ${
                repo.imported ? "opacity-55" : "hover:bg-[var(--surface-2)]"
              }`}
            >
              <input
                type="checkbox"
                className="mt-1"
                disabled={repo.imported}
                checked={picked.has(repo.full_name)}
                onChange={() => toggle(repo.full_name)}
              />
              <div className="min-w-0 flex-1">
                <p className="flex flex-wrap items-center gap-1.5 text-sm font-medium">
                  {repo.name}
                  {repo.private ? <Lock size={11} className="text-[var(--text-muted)]" /> : null}
                  {repo.imported ? <Badge tone="good">imported</Badge> : null}
                  {repo.archived ? <Badge tone="neutral">archived</Badge> : null}
                  {repo.fork ? <Badge tone="neutral">fork</Badge> : null}
                </p>
                {repo.description ? (
                  <p className="mt-0.5 line-clamp-1 text-xs text-[var(--text-muted)]">
                    {repo.description}
                  </p>
                ) : null}
              </div>
              <div className="flex shrink-0 items-center gap-2.5 text-xs text-[var(--text-muted)]">
                {repo.language ? <span>{repo.language}</span> : null}
                {repo.stars ? (
                  <span className="flex items-center gap-0.5">
                    <Star size={10} /> {repo.stars}
                  </span>
                ) : null}
                <span>{formatDate(repo.pushed_at)}</span>
              </div>
            </label>
          ))}
        </Card>
      ) : null}
    </div>
  );
}

export default function PersonalPage() {
  const [tab, setTab] = useState("projects");

  const projects = useAsync(
    useCallback(() => api.get("/projects", { workspace: "personal" }), []),
    [],
  );
  const list = projects.data || [];

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-semibold">Personal</h1>
        <p className="text-sm text-[var(--text-muted)]">
          Your own projects. The analyst doesn&apos;t come here — nothing on
          this side turns up in Review, and the scheduled run ignores it.
        </p>
      </header>

      <div className="flex flex-wrap gap-1 border-b">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            aria-current={tab === key ? "page" : undefined}
            className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm transition-colors ${
              tab === key
                ? "border-[var(--accent)] font-medium text-[var(--accent)]"
                : "border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            }`}
          >
            <Icon size={14} />
            {label}
          </button>
        ))}
      </div>

      {tab === "projects" ? (
        <>
          <ErrorNote error={projects.error} onDismiss={() => projects.reload()} />
          {projects.loading && !projects.data ? <Spinner /> : null}
          {projects.data ? (
            <Card>
              <CardHeader
                title="Your projects"
                action={list.length ? <Badge tone="neutral">{list.length}</Badge> : null}
              />
              {list.length ? (
                list.map((project) => <ProjectCard key={project.id} project={project} />)
              ) : (
                <EmptyState
                  icon={FolderKanban}
                  title="Nothing here yet"
                  description="Import your GitHub repos, or describe an idea and have the whole plan built for you."
                />
              )}
            </Card>
          ) : null}
        </>
      ) : null}

      {tab === "repos" ? (
        <Repos onImported={() => projects.reload({ quiet: true })} />
      ) : null}

      {tab === "new" ? (
        <Scaffold onCreated={() => projects.reload({ quiet: true })} />
      ) : null}

      {tab === "brainstorm" ? <BrainstormPanel projects={list} /> : null}
    </div>
  );
}
