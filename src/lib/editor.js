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

/**
 * Chrome: the surface, the gutter, the cursor and the selection.
 *
 * Built per mode rather than once, because CodeMirror needs to be *told*
 * whether it is dark -- the `dark` flag drives its own contrast decisions
 * in extensions this theme does not reach. The colours themselves are still
 * custom properties, so the two themes differ only in that flag.
 *
 * It must also be passed as `theme={...}` with the wrapper's own
 * `theme="none"`. `@uiw/react-codemirror` defaults to `theme="light"`,
 * which appends `{"&": {backgroundColor: "#fff"}}` *after* everything
 * else -- which is why this editor was white in dark mode however many
 * times the background was set here.
 */
function chrome(dark) {
  return EditorView.theme(
    {
      "&": {
        backgroundColor: "var(--surface-1)",
        color: "var(--text-primary)",
        fontSize: "13px",
      },
      ".cm-scroller": {
        backgroundColor: "var(--surface-1)",
        fontFamily:
          "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace",
        lineHeight: "1.5",
      },
      ".cm-content": { caretColor: "var(--text-primary)", padding: "6px 0" },

      // No border down the gutter: VS Code has none, and the line is the
      // single thing that most makes an embedded editor look embedded.
      ".cm-gutters": {
        backgroundColor: "var(--surface-1)",
        color: "var(--editor-line-number)",
        border: "none",
        paddingRight: "4px",
      },
      ".cm-lineNumbers .cm-gutterElement": { padding: "0 8px 0 16px" },
      ".cm-activeLineGutter": {
        backgroundColor: "transparent",
        color: "var(--editor-line-number-active)",
      },
      ".cm-foldGutter .cm-gutterElement": { color: "var(--text-muted)" },

      ".cm-activeLine": { backgroundColor: "var(--editor-active-line)" },

      // The selection has to be set on all four of these or it comes out
      // as the browser default in one state or another.
      "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection, .cm-line ::selection":
        { backgroundColor: "var(--syn-selection)" },
      // Other occurrences of whatever is selected, as VS Code shows them.
      ".cm-selectionMatch": {
        backgroundColor: "var(--editor-selection-match)",
      },

      ".cm-cursor, .cm-dropCursor": {
        borderLeftColor: "var(--text-primary)",
        borderLeftWidth: "2px",
      },

      // A box rather than a highlight, which is what VS Code draws.
      "&.cm-focused .cm-matchingBracket, .cm-matchingBracket": {
        backgroundColor: "transparent",
        outline: "1px solid var(--editor-bracket)",
      },
      ".cm-nonmatchingBracket": { outline: "1px solid var(--syn-invalid)" },

      ".cm-panels": {
        backgroundColor: "var(--editor-panel)",
        color: "var(--text-primary)",
        border: "none",
      },
      ".cm-panels input, .cm-panels button": {
        backgroundColor: "var(--surface-1)",
        color: "var(--text-primary)",
        border: "1px solid var(--border)",
        borderRadius: "3px",
      },
      ".cm-searchMatch": {
        backgroundColor: "color-mix(in srgb, var(--warning) 35%, transparent)",
      },
      ".cm-searchMatch.cm-searchMatch-selected": {
        backgroundColor: "color-mix(in srgb, var(--warning) 60%, transparent)",
      },

      ".cm-foldPlaceholder": {
        backgroundColor: "var(--surface-2)",
        color: "var(--text-muted)",
        border: "1px solid var(--border)",
      },
      "&.cm-editor.cm-focused": { outline: "none" },
    },
    { dark },
  );
}

export const lightChrome = chrome(false);
export const darkChrome = chrome(true);

/** Everything the editor needs, for the mode it is being shown in. */
export function themeFor(isDark) {
  return [isDark ? darkChrome : lightChrome, syntaxHighlighting(syntaxTheme)];
}

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
