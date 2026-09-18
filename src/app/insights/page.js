"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { formatDate, formatHours } from "@/lib/format";
import { TASK_STATUSES, TIME_CATEGORIES, labelFor } from "@/lib/constants";
import { Card, CardHeader, ErrorNote, ProgressBar, Spinner } from "@/components/ui";
import {
  HoursByProjectChart,
  HoursByWeekChart,
  ThroughputChart,
} from "@/components/charts";
import AiSpend from "@/components/AiSpend";

const WINDOWS = [
  { value: 28, label: "4 weeks" },
  { value: 56, label: "8 weeks" },
  { value: 84, label: "12 weeks" },
  { value: 182, label: "6 months" },
];

/** A number that is the whole story doesn't need a chart around it. */
function StatTile({ label, value, detail }) {
  return (
    <Card className="px-3 py-3">
      <p className="text-[11px] text-[var(--text-muted)]">{label}</p>
      <p className="mt-0.5 text-2xl font-semibold tabular-nums">{value}</p>
      {detail ? (
        <p className="mt-0.5 text-[11px] text-[var(--text-muted)]">{detail}</p>
      ) : null}
    </Card>
  );
}

export default function InsightsPage() {
  const [days, setDays] = useState(56);

  const { data, error, loading, reload } = useAsync(
    useCallback(() => api.get("/dashboard/insights", { days }), [days]),
    [days],
  );

  // A category's colour slot is its position in the vocabulary, not its
  // position among the categories that happen to have hours this window --
  // otherwise changing the window would repaint the survivors.
  const orderedCategories = useMemo(() => {
    if (!data) return [];
    const present = new Set(data.categories);
    return TIME_CATEGORIES.map((c) => c.value).filter((value) => present.has(value));
  }, [data]);

  const categoryLabels = useMemo(
    () => Object.fromEntries(TIME_CATEGORIES.map((c) => [c.value, c.label])),
    [],
  );

  const categorySlots = useMemo(
    () => Object.fromEntries(TIME_CATEGORIES.map((c, index) => [c.value, index])),
    [],
  );

  const weeksInWindow = Math.max(1, Math.round(days / 7));

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Insights</h1>
          <p className="text-sm text-[var(--text-muted)]">
            {data
              ? `${formatDate(data.window.start)} – ${formatDate(data.window.end)}`
              : "Where the time went, and whether work is closing."}
          </p>
        </div>
        <div className="flex gap-1 rounded-lg border bg-[var(--surface-1)] p-0.5">
          {WINDOWS.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => setDays(option.value)}
              aria-pressed={days === option.value}
              className={`rounded-md px-2.5 py-1 text-xs transition-colors ${
                days === option.value
                  ? "bg-[var(--accent-soft)] font-medium text-[var(--accent)]"
                  : "text-[var(--text-secondary)] hover:bg-[var(--surface-2)]"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </header>

      <ErrorNote error={error} onDismiss={() => reload()} />
      {loading && !data ? <Spinner /> : null}

      {data ? (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile
              label="Hours logged"
              value={formatHours(data.totals.hours)}
              detail={`${formatHours(data.totals.hours / weeksInWindow)} a week on average`}
            />
            <StatTile
              label="Tasks closed"
              value={data.totals.tasks_closed}
              detail={`${data.totals.tasks_opened} opened in the same window`}
            />
            <StatTile
              label="Median cycle time"
              value={
                data.cycle_time_days.median === null
                  ? "—"
                  : `${data.cycle_time_days.median}d`
              }
              detail={
                data.cycle_time_days.sample
                  ? `p90 ${data.cycle_time_days.p90}d · ${data.cycle_time_days.sample} tasks`
                  : "No completed tasks yet"
              }
            />
            <StatTile
              label="Open tasks"
              value={data.open_status_mix.reduce((sum, row) => sum + row.count, 0)}
              detail={data.open_status_mix
                .map((row) => `${row.count} ${labelFor(TASK_STATUSES, row.status).toLowerCase()}`)
                .join(" · ")}
            />
          </div>

          <Card className="p-4">
            <div className="mb-1">
              <h2 className="text-sm font-semibold">Hours a week, by what the time went into</h2>
              <p className="text-xs text-[var(--text-muted)]">
                Each column is one week; the segments show the split.
              </p>
            </div>
            <HoursByWeekChart
              data={data.hours_trend}
              categories={orderedCategories}
              categoryLabels={categoryLabels}
              categorySlots={categorySlots}
            />
          </Card>

          <div className="grid gap-5 lg:grid-cols-2">
            <Card className="p-4">
              <div className="mb-1">
                <h2 className="text-sm font-semibold">Tasks opened vs closed</h2>
                <p className="text-xs text-[var(--text-muted)]">
                  Closed below opened, week after week, means the backlog is growing.
                </p>
              </div>
              <ThroughputChart data={data.throughput} />
            </Card>

            <Card className="p-4">
              <div className="mb-1">
                <h2 className="text-sm font-semibold">Hours by project</h2>
                <p className="text-xs text-[var(--text-muted)]">
                  Top 8 in this window.
                </p>
              </div>
              <HoursByProjectChart data={data.hours_by_project} />
            </Card>
          </div>

          <Card>
            <CardHeader
              title="Every project"
              subtitle="Progress, hours and anything running late."
            />
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b text-left text-xs text-[var(--text-muted)]">
                  <tr>
                    <th scope="col" className="px-3 py-2 font-medium">Project</th>
                    <th scope="col" className="px-3 py-2 font-medium">Status</th>
                    <th scope="col" className="px-3 py-2 font-medium">Progress</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Tasks</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Hours</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Overdue</th>
                  </tr>
                </thead>
                <tbody>
                  {data.projects.map((project) => (
                    <tr key={project.id} className="border-b last:border-b-0">
                      <td className="px-3 py-2">
                        <Link
                          href={`/projects/${project.id}`}
                          className="font-medium hover:underline"
                        >
                          {project.name}
                        </Link>
                      </td>
                      <td className="px-3 py-2 text-xs text-[var(--text-secondary)]">
                        {project.status.replace("_", " ")}
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-2">
                          <div className="w-24">
                            <ProgressBar
                              value={project.progress}
                              tone={project.progress >= 100 ? "good" : "accent"}
                            />
                          </div>
                          <span className="text-xs tabular-nums text-[var(--text-muted)]">
                            {project.progress}%
                          </span>
                        </div>
                      </td>
                      <td className="px-3 py-2 text-right text-xs tabular-nums">
                        {project.task_done}/{project.task_total}
                      </td>
                      <td className="px-3 py-2 text-right text-xs tabular-nums">
                        {formatHours(project.hours)}
                      </td>
                      <td className="px-3 py-2 text-right text-xs tabular-nums">
                        {project.open_overdue > 0 ? (
                          <span className="font-medium text-[var(--critical)]">
                            {project.open_overdue}
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      ) : null}

      <AiSpend />
    </div>
  );
}
