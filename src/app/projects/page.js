"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { FolderKanban, Plus, Search } from "lucide-react";
import { api } from "@/lib/api";
import { useAsync, useDebounced } from "@/lib/hooks";
import { formatDate, formatHours, relativeDue } from "@/lib/format";
import {
  PRIORITY_TONE,
  PROJECT_CATEGORIES,
  PROJECT_STATUSES,
  PROJECT_STATUS_TONE,
  labelFor,
} from "@/lib/constants";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNote,
  ProgressBar,
  Select,
  Spinner,
} from "@/components/ui";
import ProjectForm from "@/components/ProjectForm";

function ProjectCard({ project }) {
  const target = relativeDue(project.target_date);
  const overdueTarget = target && target.days < 0 && project.status !== "done";

  return (
    <Card className="flex flex-col p-4 transition-colors hover:border-[var(--border-strong)]">
      <div className="flex items-start justify-between gap-2">
        <Link href={`/projects/${project.id}`} className="min-w-0">
          <h3 className="truncate text-sm font-semibold hover:underline">{project.name}</h3>
        </Link>
        <Badge tone={PROJECT_STATUS_TONE[project.status] || "neutral"}>
          {labelFor(PROJECT_STATUSES, project.status)}
        </Badge>
      </div>

      {project.summary ? (
        <p className="mt-1.5 line-clamp-2 text-xs text-[var(--text-secondary)]">
          {project.summary}
        </p>
      ) : null}

      <div className="mt-3">
        <div className="mb-1 flex items-center justify-between text-[11px] text-[var(--text-muted)]">
          <span>{labelFor(PROJECT_CATEGORIES, project.category)}</span>
          <span className="tabular-nums">{project.progress}%</span>
        </div>
        <ProgressBar
          value={project.progress}
          tone={project.progress >= 100 ? "good" : "accent"}
        />
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[11px] text-[var(--text-muted)]">
        <span>
          {project.task_done}/{project.task_total} tasks
        </span>
        {project.milestone_total > 0 ? (
          <span>
            {project.milestone_done}/{project.milestone_total} milestones
          </span>
        ) : null}
        <span>{formatHours(project.hours_logged)}</span>
        {project.priority === "high" ? (
          <Badge tone={PRIORITY_TONE.high}>High priority</Badge>
        ) : null}
        {project.open_overdue > 0 ? (
          <Badge tone="critical">{project.open_overdue} overdue</Badge>
        ) : null}
      </div>

      {project.target_date ? (
        <p
          className={`mt-2 text-[11px] ${overdueTarget ? "font-medium text-[var(--critical)]" : "text-[var(--text-muted)]"}`}
        >
          Target {formatDate(project.target_date)}
          {overdueTarget ? ` · ${target.text}` : ""}
        </p>
      ) : null}
    </Card>
  );
}

export default function ProjectsPage() {
  const [status, setStatus] = useState("");
  const [category, setCategory] = useState("");
  const [search, setSearch] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [formOpen, setFormOpen] = useState(false);

  const q = useDebounced(search, 300);

  const { data, error, loading, reload } = useAsync(
    useCallback(
      () =>
        api.get("/projects", {
          status,
          category,
          q,
          include_archived: includeArchived ? "true" : "",
        }),
      [status, category, q, includeArchived],
    ),
    [status, category, q, includeArchived],
  );

  const projects = data || [];

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Projects</h1>
          <p className="text-sm text-[var(--text-muted)]">
            {projects.length} {projects.length === 1 ? "project" : "projects"}
          </p>
        </div>
        <Button variant="primary" onClick={() => setFormOpen(true)}>
          <Plus size={14} /> New project
        </Button>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-52 flex-1">
          <Search
            size={14}
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--text-muted)]"
          />
          <input
            type="text"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search projects"
            aria-label="Search projects"
            className="!pl-8"
          />
        </div>
        <Select
          includeBlank
          blankLabel="Any status"
          aria-label="Filter by status"
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          options={PROJECT_STATUSES}
          className="!w-auto"
        />
        <Select
          includeBlank
          blankLabel="Any category"
          aria-label="Filter by category"
          value={category}
          onChange={(event) => setCategory(event.target.value)}
          options={PROJECT_CATEGORIES}
          className="!w-auto"
        />
        <label className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)]">
          <input
            type="checkbox"
            checked={includeArchived}
            onChange={(event) => setIncludeArchived(event.target.checked)}
            className="!w-auto"
          />
          Show archived
        </label>
      </div>

      <ErrorNote error={error} onDismiss={() => reload()} />

      {loading && !data ? <Spinner /> : null}

      {data && projects.length === 0 ? (
        <Card>
          <EmptyState
            icon={FolderKanban}
            title="No projects match"
            description={
              search || status || category
                ? "Try clearing the filters."
                : "Create your first project to start tracking work against it."
            }
            action={
              <Button variant="primary" size="sm" onClick={() => setFormOpen(true)}>
                New project
              </Button>
            }
          />
        </Card>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {projects.map((project) => (
          <ProjectCard key={project.id} project={project} />
        ))}
      </div>

      {formOpen ? (
        <ProjectForm
          open
          onClose={() => setFormOpen(false)}
          onSaved={() => reload({ quiet: true })}
        />
      ) : null}
    </div>
  );
}
