"use client";

/**
 * The Code tab: what the checkout looks like right now.
 *
 * "Right now" is the whole point, so this polls rather than loading once.
 * An edit made in VS Code should appear here without a refresh, because the
 * moment it does not, the view stops being trustworthy and you go back to
 * looking at the editor instead.
 *
 * Nothing here writes. Opening a file in the editor is the only side effect,
 * and proposing changes lives in the panel below.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  ExternalLink,
  File as FileIcon,
  FolderClosed,
  FolderOpen,
  GitBranch,
  RefreshCw,
  Search,
} from "lucide-react";

import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNote,
  Spinner,
} from "@/components/ui";
import DiffView from "@/components/DiffView";

// Often enough to feel live while you alt-tab, rare enough that it is one
// `git status` every few seconds on a local repository -- which costs
// nothing and never leaves the machine.
const POLL_MS = 4000;

const STATE_TONE = {
  modified: "warning",
  added: "success",
  created: "success",
  untracked: "info",
  deleted: "critical",
  renamed: "info",
  conflicted: "critical",
};

/** One folder of the tree, expanded on demand.
 *
 *  Lazy because a real repository has tens of thousands of entries and
 *  almost none of them will be looked at. */
function TreeNode({ projectId, entry, depth, onOpenFile, selected }) {
  const [open, setOpen] = useState(false);
  const [children, setChildren] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const toggle = async () => {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (children) return;
    setLoading(true);
    try {
      const body = await api.get(`/projects/${projectId}/workspace/tree`, {
        path: entry.path,
      });
      setChildren(body.entries);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  };

  const pad = { paddingLeft: `${depth * 12 + 8}px` };

  if (entry.type === "file") {
    const isSelected = selected === entry.path;
    return (
      <button
        type="button"
        onClick={() => onOpenFile(entry.path)}
        style={pad}
        className={`flex w-full items-center gap-1.5 py-[3px] pr-2 text-left text-xs hover:bg-[var(--surface-2)] ${
          isSelected
            ? "bg-[var(--accent-soft)] font-medium text-[var(--accent)]"
            : "text-[var(--text-secondary)]"
        }`}
      >
        <FileIcon size={12} className="shrink-0 opacity-60" />
        <span className="truncate">{entry.name}</span>
      </button>
    );
  }

  return (
    <>
      <button
        type="button"
        onClick={toggle}
        style={pad}
        className="flex w-full items-center gap-1.5 py-[3px] pr-2 text-left text-xs text-[var(--text-primary)] hover:bg-[var(--surface-2)]"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {open ? (
          <FolderOpen size={12} className="shrink-0 opacity-70" />
        ) : (
          <FolderClosed size={12} className="shrink-0 opacity-70" />
        )}
        <span className="truncate">{entry.name}</span>
      </button>
      {open ? (
        <>
          {loading ? (
            <p style={{ paddingLeft: `${depth * 12 + 28}px` }} className="py-1 text-[11px] text-[var(--text-muted)]">
              Loading…
            </p>
          ) : null}
          {error ? (
            <p style={{ paddingLeft: `${depth * 12 + 28}px` }} className="py-1 text-[11px] text-[var(--critical)]">
              {error.message}
            </p>
          ) : null}
          {(children || []).map((child) => (
            <TreeNode
              key={child.path}
              projectId={projectId}
              entry={child}
              depth={depth + 1}
              onOpenFile={onOpenFile}
              selected={selected}
            />
          ))}
        </>
      ) : null}
    </>
  );
}

/** The file currently being looked at, with its line numbers. */
function FileView({ projectId, path, onOpenInEditor }) {
  const file = useAsync(
    () => api.get(`/projects/${projectId}/workspace/file`, { path }),
    [projectId, path],
  );

  const lines = useMemo(
    () => (file.data?.content ?? "").replace(/\n$/, "").split("\n"),
    [file.data],
  );

  if (file.loading) return <Spinner label={`Reading ${path}`} />;
  if (file.error) return <ErrorNote error={file.error} />;
  if (file.data?.binary) {
    return (
      <p className="p-4 text-xs text-[var(--text-muted)]">
        {path} is a binary file ({file.data.size.toLocaleString()} bytes).
      </p>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <code className="truncate text-xs text-[var(--text-secondary)]">{path}</code>
        <Button variant="ghost" onClick={() => onOpenInEditor(path)}>
          <ExternalLink size={12} /> Open in VS Code
        </Button>
      </div>
      {file.data?.truncated ? (
        <p className="text-[11px] text-[var(--warning)]">
          Showing the first part only — the file is{" "}
          {file.data.size.toLocaleString()} bytes.
        </p>
      ) : null}
      <pre className="max-h-[32rem] overflow-auto rounded border border-[var(--border)] bg-[var(--surface-1)] text-[11px] leading-[1.55]">
        <code className="block font-mono">
          {lines.map((line, index) => (
            <span key={index} className="flex whitespace-pre">
              <span className="sticky left-0 w-12 shrink-0 select-none bg-[var(--surface-1)] pr-3 text-right text-[var(--text-muted)]">
                {index + 1}
              </span>
              <span className="text-[var(--text-secondary)]">{line || " "}</span>
            </span>
          ))}
        </code>
      </pre>
    </div>
  );
}

function SearchPanel({ projectId, onOpenFile }) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (event) => {
    event.preventDefault();
    if (query.trim().length < 2) return;
    setBusy(true);
    setError(null);
    try {
      const body = await api.get(`/projects/${projectId}/workspace/search`, {
        q: query.trim(),
      });
      setHits(body.hits);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2">
      <form onSubmit={submit} className="flex gap-1.5">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Find in tracked files"
          className="min-w-0 flex-1 rounded border border-[var(--border)] bg-[var(--surface-1)] px-2 py-1 text-xs"
        />
        <Button type="submit" variant="ghost" disabled={busy}>
          <Search size={12} />
        </Button>
      </form>
      {error ? <ErrorNote error={error} /> : null}
      {hits && !hits.length ? (
        <p className="text-[11px] text-[var(--text-muted)]">No matches.</p>
      ) : null}
      {hits?.length ? (
        <ul className="max-h-48 space-y-px overflow-auto">
          {hits.map((hit, index) => (
            <li key={`${hit.path}:${hit.line}:${index}`}>
              <button
                type="button"
                onClick={() => onOpenFile(hit.path)}
                className="w-full truncate rounded px-1 py-[2px] text-left text-[11px] hover:bg-[var(--surface-2)]"
              >
                <span className="text-[var(--accent)]">
                  {hit.path}:{hit.line}
                </span>{" "}
                <span className="text-[var(--text-muted)]">{hit.text.trim()}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export default function CodeWorkspace({ project }) {
  const [view, setView] = useState({ kind: "changes", path: null });
  const [notice, setNotice] = useState(null);

  const state = useAsync(
    () => api.get(`/projects/${project.id}/workspace`),
    [project.id],
  );
  const { reload } = state;

  // Poll quietly: a spinner every four seconds would be worse than no
  // polling at all.
  useEffect(() => {
    const timer = setInterval(() => reload({ quiet: true }), POLL_MS);
    return () => clearInterval(timer);
  }, [reload]);

  const available = state.data?.available;

  const tree = useAsync(
    () =>
      available
        ? api.get(`/projects/${project.id}/workspace/tree`, { path: "" })
        : Promise.resolve({ entries: [] }),
    [project.id, available],
  );

  const diff = useAsync(
    () =>
      available
        ? api.get(`/projects/${project.id}/workspace/diff`)
        : Promise.resolve({ diff: "" }),
    [project.id, available, state.data?.changes?.length],
  );

  const openInEditor = useCallback(
    async (path) => {
      setNotice(null);
      try {
        await api.post(`/projects/${project.id}/workspace/open`, { path });
      } catch (err) {
        // A 503 means no CLI. The deep link is the fallback and needs no
        // server at all, so offer it rather than just reporting failure.
        setNotice({
          message: err.message,
          url: `vscode://file/${(state.data?.root || "").replace(/\\/g, "/")}/${path}`,
        });
      }
    },
    [project.id, state.data?.root],
  );

  if (state.loading && !state.data) return <Spinner label="Reading the checkout" />;
  if (state.error) return <ErrorNote error={state.error} />;

  if (!available) {
    return (
      <Card>
        <EmptyState
          icon={GitBranch}
          title="No local folder for this project"
          description={
            state.data?.reason ||
            "Set the project's local folder in the brief to see the working copy here."
          }
        />
      </Card>
    );
  }

  const changes = state.data.changes || [];

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title={
            <span className="flex items-center gap-1.5">
              <GitBranch size={13} />
              {state.data.detached ? "detached HEAD" : state.data.branch}
            </span>
          }
          subtitle={
            state.data.last_commit
              ? `${state.data.last_commit.sha} · ${state.data.last_commit.subject}`
              : "No commits yet"
          }
          action={
            <div className="flex items-center gap-1.5">
              <Badge tone={changes.length ? "warning" : "success"}>
                {changes.length
                  ? `${changes.length} uncommitted`
                  : "clean"}
              </Badge>
              <Button variant="ghost" onClick={() => reload()}>
                <RefreshCw size={12} />
              </Button>
              <Button variant="ghost" onClick={() => openInEditor("")}>
                <ExternalLink size={12} /> Open repo
              </Button>
            </div>
          }
        />
        <p className="px-4 pb-3 text-[11px] text-[var(--text-muted)]">
          <code>{state.data.root}</code> — read live from disk, updated every{" "}
          {POLL_MS / 1000}s.
        </p>
      </Card>

      {notice ? (
        <Card className="space-y-2 p-3 text-xs">
          <p className="text-[var(--warning)]">{notice.message}</p>
          <a
            href={notice.url}
            className="inline-flex items-center gap-1 text-[var(--accent)] underline"
          >
            <ExternalLink size={12} /> Try the vscode:// link instead
          </a>
        </Card>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,17rem)_minmax(0,1fr)]">
        <Card className="space-y-3 p-3">
          <SearchPanel
            projectId={project.id}
            onOpenFile={(path) => setView({ kind: "file", path })}
          />
          <div className="max-h-[32rem] overflow-auto border-t border-[var(--border)] pt-1">
            <button
              type="button"
              onClick={() => setView({ kind: "changes", path: null })}
              className={`mb-1 flex w-full items-center gap-1.5 px-2 py-1 text-left text-xs ${
                view.kind === "changes"
                  ? "font-medium text-[var(--accent)]"
                  : "text-[var(--text-secondary)]"
              }`}
            >
              Uncommitted changes
              {changes.length ? (
                <span className="text-[var(--text-muted)]">({changes.length})</span>
              ) : null}
            </button>
            {tree.loading ? <Spinner label="Listing files" /> : null}
            {(tree.data?.entries || []).map((entry) => (
              <TreeNode
                key={entry.path}
                projectId={project.id}
                entry={entry}
                depth={0}
                onOpenFile={(path) => setView({ kind: "file", path })}
                selected={view.path}
              />
            ))}
          </div>
        </Card>

        <Card className="p-3">
          {view.kind === "file" ? (
            <FileView
              projectId={project.id}
              path={view.path}
              onOpenInEditor={openInEditor}
            />
          ) : (
            <div className="space-y-3">
              {changes.length ? (
                <ul className="flex flex-wrap gap-1.5">
                  {changes.map((change) => (
                    <li key={change.path}>
                      <button
                        type="button"
                        onClick={() => setView({ kind: "file", path: change.path })}
                        className="flex items-center gap-1.5"
                      >
                        <Badge tone={STATE_TONE[change.state] || "neutral"}>
                          {change.state}
                        </Badge>
                        <code className="text-[11px] text-[var(--text-secondary)] underline-offset-2 hover:underline">
                          {change.path}
                        </code>
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-[var(--text-muted)]">
                  Nothing uncommitted. The working copy matches the last commit.
                </p>
              )}
              {diff.data?.diff ? <DiffView diff={diff.data.diff} /> : null}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
