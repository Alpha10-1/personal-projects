"use client";

/**
 * A file, syntax-coloured and editable.
 *
 * Saving does not write the file. It raises a change that the project's
 * leader approves, and the button says so -- "Save for review" rather than
 * "Save" -- because a button labelled Save that does not save is the
 * cruellest thing an editor can do.
 *
 * The Explain button appears in the bottom corner only while something is
 * selected. It asks the backend, which answers from the repository rather
 * than from a model: free, instant, and the same answer twice.
 */

import { useCallback, useMemo, useState } from "react";
import CodeMirror from "@uiw/react-codemirror";
import {
  ExternalLink,
  HelpCircle,
  RotateCcw,
  Save,
  ShieldCheck,
} from "lucide-react";

import { api } from "@/lib/api";
import { useAsync, useIsDark } from "@/lib/hooks";
import { languageFor, languageName, selectedLines, themeFor } from "@/lib/editor";
import { Badge, Button, Card, ErrorNote, Spinner } from "@/components/ui";
import ExplainPanel from "@/components/ExplainPanel";
import ErrorBoundary from "@/components/ErrorBoundary";

export default function CodeEditor({ project, path, onRaised, onOpenInEditor }) {
  // Mounted with `key={path}` by the caller, so every piece of state here
  // belongs to one file and switching files starts clean. That is what
  // lets this component have no effects at all: there is nothing to reset,
  // because the component itself is replaced.
  const file = useAsync(
    () => api.get(`/projects/${project.id}/workspace/file`, { path }),
    [project.id, path],
  );

  // `null` means untouched. Distinct from an empty string, which is a file
  // the user has deliberately emptied.
  const [draft, setDraft] = useState(null);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [raised, setRaised] = useState(null);

  const [selection, setSelection] = useState(null);
  const [explain, setExplain] = useState({ result: null, loading: false, error: null });

  const meta = file.data;
  const onDisk = meta?.content ?? "";
  const value = draft ?? onDisk;
  const dirty = draft !== null && draft !== onDisk;

  const language = useMemo(() => languageFor(path), [path]);
  // The editor has to be told which mode it is in: the colours come from
  // custom properties and follow the cascade, but CodeMirror's own `dark`
  // flag does not, and extensions this theme does not reach read it.
  const isDark = useIsDark();
  const theme = useMemo(() => themeFor(isDark), [isDark]);

  const onUpdate = useCallback((viewUpdate) => {
    if (!viewUpdate.selectionSet && !viewUpdate.docChanged) return;
    setSelection(selectedLines(viewUpdate.state));
  }, []);

  // Holds the lines the open explanation is about, so "ask again" does not
  // depend on the selection still being there -- reading the answer moves
  // the cursor, which would otherwise clear it.
  const [asked, setAsked] = useState(null);

  const askExplain = async (lines, refresh = false) => {
    if (!lines) return;
    setAsked(lines);
    setExplain({ result: null, loading: true, error: null });
    try {
      const result = await api.post(`/projects/${project.id}/code/explain`, {
        path,
        start_line: lines.from,
        end_line: lines.to,
        refresh,
      });
      setExplain({ result, loading: false, error: null });
    } catch (err) {
      setExplain({ result: null, loading: false, error: err });
    }
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const change = await api.post(`/projects/${project.id}/code/changes`, {
        path,
        content: value,
        note: note.trim() || null,
      });
      setRaised(change);
      setNote("");
      if (onRaised) onRaised(change);
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  };

  if (file.loading && !file.data) return <Spinner label={`Reading ${path}`} />;
  if (file.error) return <ErrorNote error={file.error} />;
  if (meta?.binary) {
    return (
      <p className="p-4 text-xs text-[var(--text-muted)]">
        {path} is a binary file ({meta.size.toLocaleString()} bytes).
      </p>
    );
  }

  const readOnly = Boolean(meta?.truncated);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="flex min-w-0 items-center gap-2 text-xs">
          <code className="truncate text-[var(--text-secondary)]">{path}</code>
          <Badge tone="neutral">{languageName(path) || "plain text"}</Badge>
          {dirty ? <Badge tone="warning">unsaved</Badge> : null}
        </p>
        <div className="flex items-center gap-1.5">
          {dirty ? (
            <Button variant="ghost" onClick={() => setDraft(null)}>
              <RotateCcw size={12} /> Discard
            </Button>
          ) : null}
          <Button variant="ghost" onClick={() => onOpenInEditor?.(path)}>
            <ExternalLink size={12} /> VS Code
          </Button>
        </div>
      </div>

      {meta?.truncated ? (
        <p className="text-[11px] text-[var(--warning)]">
          This file is {meta.size.toLocaleString()} bytes and has been cut
          short, so it cannot be edited here — saving would lose the rest.
        </p>
      ) : null}

      <div className="relative">
        <CodeMirror
          value={value}
          height="30rem"
          editable={!readOnly}
          theme="none"
          extensions={[...theme, ...language]}
          onChange={setDraft}
          onUpdate={onUpdate}
          basicSetup={{
            lineNumbers: true,
            foldGutter: true,
            highlightActiveLine: true,
            autocompletion: false,
            // The repository is the source of truth for what is correct
            // here; a linter guessing at it would be noise.
            lintKeymap: false,
          }}
          className="overflow-hidden rounded border border-[var(--border)]"
        />

        {selection ? (
          <button
            type="button"
            onClick={() => askExplain(selection)}
            className="absolute bottom-3 right-4 z-10 flex items-center gap-1.5 rounded-full border border-[var(--border-strong)] bg-[var(--surface-1)] px-3 py-1.5 text-xs font-medium shadow-lg hover:bg-[var(--accent-soft)] hover:text-[var(--accent)]"
          >
            <HelpCircle size={13} />
            Explain lines {selection.from}
            {selection.to !== selection.from ? `–${selection.to}` : ""}
          </button>
        ) : null}
      </div>

      {explain.result || explain.loading || explain.error ? (
        // Boundaried because this is the one panel here rendering something
        // a model wrote, and a surprise in its shape should cost you the
        // explanation, not the editor you were in the middle of using.
        <ErrorBoundary
          key={`${asked?.from}-${asked?.to}`}
          label="The explanation"
        >
          <ExplainPanel
            {...explain}
            onRefresh={() => askExplain(asked, true)}
            onClose={() => setExplain({ result: null, loading: false, error: null })}
          />
        </ErrorBoundary>
      ) : null}

      {error ? <ErrorNote error={error} onDismiss={() => setError(null)} /> : null}

      {raised ? (
        <Card className="space-y-1 p-3 text-xs">
          <p className="flex items-center gap-1.5 font-medium text-[var(--good)]">
            <ShieldCheck size={13} /> Saved as change #{raised.id}, waiting for
            approval.
          </p>
          <p className="text-[var(--text-muted)]">
            The file on disk has not changed. It will when the change is
            approved, under Changes below.
          </p>
        </Card>
      ) : null}

      {dirty && !readOnly ? (
        <Card className="space-y-2 p-3">
          <label className="block text-xs text-[var(--text-secondary)]">
            Why this change
            <input
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Optional, but the reviewer reads it first"
              className="mt-1 w-full rounded border border-[var(--border)] bg-[var(--surface-1)] px-2 py-1 text-xs"
            />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={save} disabled={saving}>
              <Save size={13} /> Save for review
            </Button>
            <span className="text-[11px] text-[var(--text-muted)]">
              This does not write the file. It raises a change for the
              project&apos;s leader to approve.
            </span>
          </div>
        </Card>
      ) : null}
    </div>
  );
}
