"use client";

/**
 * A unified diff, coloured.
 *
 * Reviewing a change is the point of the agent existing, and a wall of
 * monospace in one colour is not a review -- the same complaint that got the
 * chat replies formatted. So: added lines green, removed red, hunk headers
 * set apart, file headers as their own row.
 *
 * Parsing is deliberately shallow. This renders what git and difflib emit;
 * it does not attempt to understand hunks, reconstruct either side, or offer
 * per-line staging. Anything needing that should open the file in the editor,
 * which is one click away.
 */

import { useMemo } from "react";

/** The kind of each line, which is the whole of the parsing. */
function classify(line) {
  if (line.startsWith("+++") || line.startsWith("---")) return "file";
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  if (line.startsWith("\\")) return "meta"; // "\ No newline at end of file"
  return "context";
}

const STYLE = {
  add: "bg-[color-mix(in_srgb,var(--good)_14%,transparent)] text-[var(--text-primary)]",
  del: "bg-[color-mix(in_srgb,var(--critical)_14%,transparent)] text-[var(--text-primary)]",
  hunk: "bg-[var(--surface-2)] text-[var(--text-muted)]",
  file: "bg-[var(--surface-2)] font-medium text-[var(--text-secondary)]",
  meta: "text-[var(--text-muted)] italic",
  context: "text-[var(--text-secondary)]",
};

/** Added and removed line counts, for the header. Cheap, and the first thing
 *  anyone wants to know about a diff they have not read yet. */
export function countChanges(diff) {
  let added = 0;
  let removed = 0;
  for (const line of (diff || "").split("\n")) {
    const kind = classify(line);
    if (kind === "add") added += 1;
    else if (kind === "del") removed += 1;
  }
  return { added, removed };
}

export default function DiffView({ diff, className = "" }) {
  const lines = useMemo(() => {
    const raw = (diff || "").replace(/\n$/, "");
    if (!raw) return [];
    return raw.split("\n").map((text, index) => ({
      key: index,
      text,
      kind: classify(text),
    }));
  }, [diff]);

  if (!lines.length) {
    return (
      <p className="px-3 py-2 text-xs text-[var(--text-muted)]">
        No changes to show.
      </p>
    );
  }

  return (
    <pre
      className={`overflow-x-auto rounded border border-[var(--border)] bg-[var(--surface-1)] text-[11px] leading-[1.55] ${className}`}
    >
      <code className="block font-mono">
        {lines.map((line) => (
          <span
            key={line.key}
            className={`block whitespace-pre px-3 ${STYLE[line.kind]}`}
          >
            {line.text || " "}
          </span>
        ))}
      </code>
    </pre>
  );
}
