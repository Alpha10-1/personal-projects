"use client";

import { useCallback } from "react";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import TimeLogPanel from "@/components/TimeLogPanel";

export default function TimePage() {
  const projects = useAsync(useCallback(() => api.get("/projects"), []), []);
  const tasks = useAsync(
    useCallback(() => api.get("/tasks", { open_only: "true" }), []),
    [],
  );

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-semibold">Time</h1>
        <p className="text-sm text-[var(--text-muted)]">
          Log hours as you go. The weekly split lives on Insights.
        </p>
      </header>

      <TimeLogPanel
        projects={projects.data || []}
        tasks={tasks.data || []}
        title="All time logged"
        days={60}
      />
    </div>
  );
}
