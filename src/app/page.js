"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { CalendarCheck, Flag, Plus } from "lucide-react";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { formatDate, formatHours } from "@/lib/format";
import { Badge, Button, Card, CardHeader, EmptyState, ErrorNote, ProgressBar, Spinner } from "@/components/ui";
import TaskList from "@/components/TaskList";
import TaskForm from "@/components/TaskForm";

function StatTile({ label, value, tone }) {
  const color =
    tone === "critical"
      ? "text-[var(--critical)]"
      : tone === "warning"
        ? "text-[var(--text-primary)]"
        : "text-[var(--text-primary)]";
  return (
    <Card className="px-3 py-2.5">
      <p className="text-[11px] text-[var(--text-muted)]">{label}</p>
      <p className={`mt-0.5 text-xl font-semibold tabular-nums ${color}`}>{value}</p>
    </Card>
  );
}

export default function TodayPage() {
  const [formOpen, setFormOpen] = useState(false);

  const view = useAsync(useCallback(() => api.get("/dashboard/today"), []), []);
  const projectList = useAsync(
    useCallback(() => api.get("/projects", { status: "" }), []),
    [],
  );

  const data = view.data;
  const reload = () => {
    view.reload({ quiet: true });
  };

  const greeting = new Date().getHours() < 12 ? "Good morning" : "Here's where things stand";

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">{greeting}</h1>
          <p className="text-sm text-[var(--text-muted)]">
            {new Date().toLocaleDateString(undefined, {
              weekday: "long",
              day: "numeric",
              month: "long",
            })}
          </p>
        </div>
        <Button variant="primary" onClick={() => setFormOpen(true)}>
          <Plus size={14} /> New task
        </Button>
      </header>

      <ErrorNote error={view.error} onDismiss={() => view.reload()} />

      {view.loading && !data ? <Spinner /> : null}

      {data ? (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <StatTile label="Overdue" value={data.counts.overdue} tone="critical" />
            <StatTile label="Due today" value={data.counts.due_today} tone="warning" />
            <StatTile label="In progress" value={data.counts.in_progress} />
            <StatTile label="Blocked" value={data.counts.blocked} tone="critical" />
            <StatTile label="Hours today" value={formatHours(data.hours.today)} />
            <StatTile label="Hours this week" value={formatHours(data.hours.this_week)} />
          </div>

          <div className="grid gap-5 lg:grid-cols-3">
            <div className="space-y-5 lg:col-span-2">
              {data.overdue.length ? (
                <Card>
                  <CardHeader
                    title="Overdue"
                    subtitle="Past their due date and still open."
                  />
                  <TaskList tasks={data.overdue} onChanged={reload} />
                </Card>
              ) : null}

              <Card>
                <CardHeader title="Due today" />
                <TaskList
                  tasks={data.due_today}
                  onChanged={reload}
                  emptyTitle="Nothing due today"
                  emptyDescription="Anything with today's date will show up here."
                />
              </Card>

              <Card>
                <CardHeader title="In progress" subtitle="What you've picked up." />
                <TaskList
                  tasks={data.in_progress}
                  onChanged={reload}
                  emptyTitle="Nothing in progress"
                  emptyDescription="Move a task to 'In progress' to park it here."
                />
              </Card>

              {data.blocked.length ? (
                <Card>
                  <CardHeader title="Blocked" subtitle="Waiting on something else." />
                  <TaskList tasks={data.blocked} onChanged={reload} />
                </Card>
              ) : null}

              <Card>
                <CardHeader title="Next 7 days" />
                <TaskList
                  tasks={data.upcoming}
                  onChanged={reload}
                  emptyTitle="Clear week ahead"
                />
              </Card>
            </div>

            <div className="space-y-5">
              <Card>
                <CardHeader
                  title="Active projects"
                  action={
                    <Link
                      href="/projects"
                      className="text-xs font-medium text-[var(--accent)]"
                    >
                      All
                    </Link>
                  }
                />
                {data.projects.length ? (
                  <ul className="divide-y">
                    {data.projects.slice(0, 6).map((project) => (
                      <li key={project.id} className="px-3 py-2.5">
                        <div className="flex items-center justify-between gap-2">
                          <Link
                            href={`/projects/${project.id}`}
                            className="truncate text-sm font-medium hover:underline"
                          >
                            {project.name}
                          </Link>
                          <span className="shrink-0 text-xs tabular-nums text-[var(--text-muted)]">
                            {project.progress}%
                          </span>
                        </div>
                        <div className="mt-1.5">
                          <ProgressBar
                            value={project.progress}
                            tone={project.progress >= 100 ? "good" : "accent"}
                          />
                        </div>
                        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px] text-[var(--text-muted)]">
                          <span>
                            {project.task_done}/{project.task_total} tasks
                          </span>
                          <span>{formatHours(project.hours_logged)} logged</span>
                          {project.open_overdue > 0 ? (
                            <Badge tone="critical">{project.open_overdue} overdue</Badge>
                          ) : null}
                        </div>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <EmptyState
                    icon={CalendarCheck}
                    title="No active projects"
                    description="Create one to start grouping your work."
                    action={
                      <Link href="/projects">
                        <Button variant="primary" size="sm">
                          Go to projects
                        </Button>
                      </Link>
                    }
                  />
                )}
              </Card>

              <Card>
                <CardHeader title="Milestones ahead" subtitle="Next 7 days." />
                {data.milestones.length ? (
                  <ul className="divide-y">
                    {data.milestones.map((milestone) => (
                      <li key={milestone.id} className="flex items-start gap-2 px-3 py-2.5">
                        <Flag
                          size={14}
                          className={`mt-0.5 shrink-0 ${milestone.overdue ? "text-[var(--critical)]" : "text-[var(--text-muted)]"}`}
                        />
                        <div className="min-w-0">
                          <p className="truncate text-sm">{milestone.title}</p>
                          <p className="text-[11px] text-[var(--text-muted)]">
                            <Link
                              href={`/projects/${milestone.project_id}`}
                              className="text-[var(--accent)] hover:underline"
                            >
                              {milestone.project_name}
                            </Link>
                            {" · "}
                            {formatDate(milestone.due_date, { withYear: false })}
                            {milestone.overdue ? " · overdue" : ""}
                          </p>
                        </div>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <EmptyState title="No milestones due soon" />
                )}
              </Card>
            </div>
          </div>
        </>
      ) : null}

      {formOpen ? (
        <TaskForm
          open
          onClose={() => setFormOpen(false)}
          onSaved={reload}
          projects={projectList.data || []}
        />
      ) : null}
    </div>
  );
}
