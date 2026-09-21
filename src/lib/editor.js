"use client";

/**
 * CodeMirror configuration: which language, and what the colours mean.
 *
 * The theme is written entirely in CSS custom properties rather than hex
 * values, which is what lets the editor follow the app's light/dark toggle
 * without being rebuilt or re-mounted. The properties themselves live in
 * `globals.css` beside every other token.
 *
 * Language packs are imported statically rather than loaded on demand.
 * There are nine of them, they are small, and a dynamic import would mean
 * the first paint of a file is unhighlighted and then flickers -- which
 * looks broken in exactly the way syntax colouring is supposed to fix.
 */

import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { EditorView } from "@codemirror/view";
import { tags } from "@lezer/highlight";

import { css } from "@codemirror/lang-css";
import { html } from "@codemirror/lang-html";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { python } from "@codemirror/lang-python";
import { sql } from "@codemirror/lang-sql";
import { yaml } from "@codemirror/lang-yaml";

/** Extension to language, by what is actually in these repositories. */
const BY_EXTENSION = {
  py: () => python(),
  pyi: () => python(),
  js: () => javascript({ jsx: true }),
  jsx: () => javascript({ jsx: true }),
  mjs: () => javascript({ jsx: true }),
  cjs: () => javascript({ jsx: true }),
  ts: () => javascript({ jsx: true, typescript: true }),
  tsx: () => javascript({ jsx: true, typescript: true }),
  json: () => json(),
  md: () => markdown(),
  mdx: () => markdown(),
  css: () => css(),
  html: () => html(),
  htm: () => html(),
  sql: () => sql(),
  yaml: () => yaml(),
  yml: () => yaml(),
};

/** A readable name for the status line. Null where there is no highlighting,
 *  so the UI can say "plain text" rather than implying it failed. */
export function languageName(path) {
  const extension = (path || "").split(".").pop()?.toLowerCase();
  const names = {
    py: "Python",
    pyi: "Python",
    js: "JavaScript",
    jsx: "JavaScript",
    mjs: "JavaScript",
    cjs: "JavaScript",
    ts: "TypeScript",
    tsx: "TypeScript",
    json: "JSON",
    md: "Markdown",
    mdx: "Markdown",
    css: "CSS",
    html: "HTML",
    htm: "HTML",
    sql: "SQL",
    yaml: "YAML",
    yml: "YAML",
  };
  return names[extension] || null;
}

export function languageFor(path) {
  const extension = (path || "").split(".").pop()?.toLowerCase();
  const build = BY_EXTENSION[extension];
  return build ? [build()] : [];
}

/** Token colours. Every value is a custom property, so dark mode is handled
 *  by the cascade rather than by swapping themes. */
export const syntaxTheme = HighlightStyle.define([
  { tag: tags.keyword, color: "var(--syn-keyword)" },
  { tag: tags.controlKeyword, color: "var(--syn-keyword)" },
  { tag: tags.moduleKeyword, color: "var(--syn-keyword)" },
  { tag: tags.definitionKeyword, color: "var(--syn-keyword)" },
  { tag: [tags.string, tags.special(tags.string)], color: "var(--syn-string)" },
  {
    tag: [tags.comment, tags.lineComment, tags.blockComment, tags.docComment],
    color: "var(--syn-comment)",
    fontStyle: "italic",
  },
  { tag: [tags.number, tags.integer, tags.float], color: "var(--syn-number)" },
  { tag: [tags.bool, tags.null, tags.atom], color: "var(--syn-keyword)" },
  {
    tag: [tags.function(tags.variableName), tags.function(tags.propertyName)],
    color: "var(--syn-function)",
  },
  {
    tag: [tags.definition(tags.function(tags.variableName))],
    color: "var(--syn-function)",
  },
  {
    tag: [tags.typeName, tags.className, tags.namespace, tags.tagName],
    color: "var(--syn-type)",
  },
  {
    tag: [tags.variableName, tags.propertyName, tags.attributeName],
    color: "var(--syn-variable)",
  },
  { tag: [tags.constant(tags.variableName), tags.standard(tags.variableName)], color: "var(--syn-constant)" },
  { tag: [tags.operator, tags.punctuation, tags.bracket], color: "var(--syn-operator)" },
  { tag: tags.invalid, color: "var(--syn-invalid)" },
  { tag: tags.heading, color: "var(--syn-keyword)", fontWeight: "600" },
  { tag: tags.link, color: "var(--syn-constant)", textDecoration: "underline" },
  { tag: tags.emphasis, fontStyle: "italic" },
  { tag: tags.strong, fontWeight: "600" },
]);

/** Chrome: gutters, cursor, selection, and the surface it all sits on. */
export const editorTheme = EditorView.theme({
  "&": {
    backgroundColor: "var(--surface-1)",
    color: "var(--text-primary)",
    fontSize: "12px",
  },
  ".cm-content": {
    fontFamily:
      "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace",
    caretColor: "var(--text-primary)",
  },
  ".cm-gutters": {
    backgroundColor: "var(--surface-1)",
    color: "var(--text-muted)",
    border: "none",
    borderRight: "1px solid var(--border)",
  },
  ".cm-activeLine": { backgroundColor: "var(--surface-2)" },
  ".cm-activeLineGutter": {
    backgroundColor: "var(--surface-2)",
    color: "var(--text-secondary)",
  },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": {
    backgroundColor: "var(--syn-selection)",
  },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--text-primary)" },
  ".cm-searchMatch": {
    backgroundColor: "color-mix(in srgb, var(--warning) 35%, transparent)",
  },
  "&.cm-editor.cm-focused": { outline: "none" },
});

export const extensions = [editorTheme, syntaxHighlighting(syntaxTheme)];

/**
 * Turn a CodeMirror selection into 1-based line numbers.
 *
 * Exported and pure so the Explain button's behaviour can be tested without
 * mounting an editor: the interesting part is the arithmetic and the
 * "nothing is selected" case, not the widget.
 */
export function selectedLines(state) {
  const range = state?.selection?.main;
  if (!range || range.empty) return null;
  return {
    from: state.doc.lineAt(range.from).number,
    to: state.doc.lineAt(range.to).number,
    text: state.sliceDoc(range.from, range.to),
  };
}
