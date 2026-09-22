"use client";

/**
 * A wall around one panel, so a bad render does not take the page.
 *
 * This exists because of a real failure: the model was asked for a list of
 * consequences and answered with one paragraph, the panel called `.map` on
 * a string, and the whole file view went white. The shape is fixed at the
 * source now -- but the lesson is that anything rendering model output is
 * rendering something nobody wrote by hand, and the next surprise will be
 * a different one.
 *
 * So the rule here is narrow: wrap the panels that display generated
 * content, not the page. A boundary around everything would turn a small
 * rendering fault into a blank screen, which is the thing being avoided.
 */

import { Component } from "react";
import { AlertTriangle } from "lucide-react";

export default class ErrorBoundary extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <p className="flex items-start gap-1.5 rounded border border-[var(--border)] bg-[var(--surface-2)] px-3 py-2 text-xs text-[var(--text-secondary)]">
        <AlertTriangle size={13} className="mt-px shrink-0 text-[var(--warning)]" />
        <span>
          <strong>{this.props.label || "This panel"} could not be shown.</strong>{" "}
          Nothing else was affected, and nothing was changed.{" "}
          <span className="text-[var(--text-muted)]">{String(error.message || error)}</span>
        </span>
      </p>
    );
  }
}
