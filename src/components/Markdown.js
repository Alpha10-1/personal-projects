"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Model prose, rendered.
 *
 * Every answer the assistant writes comes back as Markdown and used to be
 * shown as raw text, so a file path, a task title and an ordinary sentence
 * all looked identical and `**bold**` appeared with its asterisks. This is
 * the one place that turns it into something readable, so chat, a brainstorm
 * and an answer about a repository all look the same.
 *
 * Colour is used for meaning and nothing else. There is one accent, and it
 * marks the things you would want to pick out of a paragraph at a glance --
 * code, identifiers, file paths, links. Letting a model choose colours
 * freely would produce a different scheme in every answer, none of them
 * legible in both themes, and none of them meaning anything the next time.
 *
 * HTML in the source is not rendered. react-markdown ignores it unless you
 * add a plugin, and nothing here adds one: the text comes from a model, and
 * a model can be talked into emitting a tag.
 */

const COMPONENTS = {
  // Paragraphs carry the spacing rather than the container, so a one-line
  // answer has no dangling margin.
  p: ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,

  strong: ({ children }) => (
    <strong className="font-semibold text-[var(--text-primary)]">{children}</strong>
  ),
  em: ({ children }) => <em className="italic">{children}</em>,
  del: ({ children }) => (
    <del className="text-[var(--text-muted)] line-through">{children}</del>
  ),

  // The one thing worth spotting mid-sentence: a path, an identifier, a
  // command. Tinted rather than boxed, so a sentence with four of them still
  // reads as a sentence.
  //
  // Every `code` is styled as inline and `pre` undoes it for its own
  // descendants. Deciding here instead would mean asking whether this node
  // is inline, and there is no reliable way to: react-markdown dropped the
  // `inline` prop, and a fenced block with no language has no className
  // either -- so a plain ``` block would come out looking like a mid-sentence
  // identifier. Letting the block container reset its children cannot get
  // that wrong.
  code: ({ children }) => (
    <code className="rounded bg-[var(--accent-soft)] px-1 py-px font-mono text-[0.85em] text-[var(--accent)]">
      {children}
    </code>
  ),
  pre: ({ children }) => (
    <pre className="mb-2 overflow-x-auto rounded-lg border border-[var(--border)] bg-[var(--surface-0)] p-2.5 text-[var(--text-primary)] last:mb-0 [&_code]:bg-transparent [&_code]:p-0 [&_code]:text-inherit">
      {children}
    </pre>
  ),

  ul: ({ children }) => (
    <ul className="mb-2 ml-4 list-disc space-y-0.5 last:mb-0">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="mb-2 ml-4 list-decimal space-y-0.5 last:mb-0">{children}</ol>
  ),
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,

  // An answer is not a document: even an h1 stays at the size of the text
  // around it and earns its place by weight instead.
  h1: ({ children }) => <p className="mb-1 mt-2 font-semibold first:mt-0">{children}</p>,
  h2: ({ children }) => <p className="mb-1 mt-2 font-semibold first:mt-0">{children}</p>,
  h3: ({ children }) => <p className="mb-1 mt-2 font-semibold first:mt-0">{children}</p>,
  h4: ({ children }) => <p className="mb-1 mt-2 font-semibold first:mt-0">{children}</p>,

  blockquote: ({ children }) => (
    <blockquote className="mb-2 border-l-2 border-[var(--border-strong)] pl-2.5 text-[var(--text-secondary)] last:mb-0">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-2.5 border-[var(--border)]" />,

  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-[var(--accent)] underline underline-offset-2"
    >
      {children}
    </a>
  ),

  table: ({ children }) => (
    <div className="mb-2 overflow-x-auto last:mb-0">
      <table className="w-full border-collapse text-[0.9em]">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-b border-[var(--border-strong)] px-2 py-1 text-left font-semibold">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border-b border-[var(--border)] px-2 py-1 align-top">{children}</td>
  ),
};

export default function Markdown({ children, className = "" }) {
  if (!children) return null;
  return (
    <div className={`break-words ${className}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
        {String(children)}
      </ReactMarkdown>
    </div>
  );
}
