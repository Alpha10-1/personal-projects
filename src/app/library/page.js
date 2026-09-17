"use client";

import { useCallback } from "react";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import LibraryPanels from "@/components/LibraryPanels";

export default function LibraryPage() {
  const projects = useAsync(useCallback(() => api.get("/projects"), []), []);

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-semibold">Library</h1>
        <p className="text-sm text-[var(--text-muted)]">
          Notes, links and files across every project.
        </p>
      </header>

      <LibraryPanels projects={projects.data || []} />
    </div>
  );
}
