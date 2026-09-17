"use client";

import { cloneElement, isValidElement, useEffect, useId, useRef } from "react";
import { AlertTriangle, Loader2, X } from "lucide-react";

export function Card({ children, className = "", ...rest }) {
  return (
    <section
      className={`rounded-xl border bg-[var(--surface-1)] ${className}`}
      {...rest}
    >
      {children}
    </section>
  );
}

export function CardHeader({ title, subtitle, action }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b px-4 py-3">
      <div className="min-w-0">
        <h2 className="truncate text-sm font-semibold">{title}</h2>
        {subtitle ? (
          <p className="mt-0.5 text-xs text-[var(--text-muted)]">{subtitle}</p>
        ) : null}
      </div>
      {action}
    </div>
  );
}

const TONES = {
  neutral: "bg-[var(--surface-2)] text-[var(--text-secondary)] border-[var(--border)]",
  info: "bg-[var(--accent-soft)] text-[var(--accent)] border-[var(--accent)]/30",
  good: "bg-[var(--good)]/12 text-[var(--good)] border-[var(--good)]/35",
  warning: "bg-[var(--warning)]/15 text-[var(--text-primary)] border-[var(--warning)]/50",
  serious: "bg-[var(--serious)]/15 text-[var(--text-primary)] border-[var(--serious)]/50",
  critical: "bg-[var(--critical)]/12 text-[var(--critical)] border-[var(--critical)]/35",
};

export function Badge({ tone = "neutral", children, className = "" }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium leading-4 ${TONES[tone] || TONES.neutral} ${className}`}
    >
      {children}
    </span>
  );
}

const BUTTON_VARIANTS = {
  primary:
    "bg-[var(--accent)] text-white border-transparent hover:opacity-90 disabled:opacity-50",
  secondary:
    "bg-[var(--surface-1)] border-[var(--border-strong)] hover:bg-[var(--surface-2)] disabled:opacity-50",
  ghost:
    "bg-transparent border-transparent hover:bg-[var(--surface-2)] disabled:opacity-50",
  danger:
    "bg-transparent border-[var(--critical)]/40 text-[var(--critical)] hover:bg-[var(--critical)]/10 disabled:opacity-50",
};

export function Button({
  variant = "secondary",
  size = "md",
  busy = false,
  className = "",
  children,
  ...rest
}) {
  const sizes = {
    sm: "px-2 py-1 text-xs gap-1",
    md: "px-3 py-1.5 text-sm gap-1.5",
  };
  return (
    <button
      type="button"
      {...rest}
      disabled={rest.disabled || busy}
      className={`inline-flex items-center justify-center rounded-lg border font-medium transition-colors ${sizes[size]} ${BUTTON_VARIANTS[variant]} ${className}`}
    >
      {busy ? <Loader2 size={14} className="animate-spin" /> : null}
      {children}
    </button>
  );
}

/**
 * A labelled form control.
 *
 * The label points at the control by id rather than wrapping it. Wrapping is
 * shorter, but a wrapped `<select>` makes its own `<option>` text part of the
 * label's text content, so assistive tech announces the field as
 * "Status Idea Planning Active On hold Done Archived". Associating by id keeps
 * the accessible name to just the label.
 */
export function Field({ label, hint, children, className = "" }) {
  const generatedId = useId();
  const control = isValidElement(children)
    ? cloneElement(children, {
        id: children.props.id || generatedId,
        "aria-describedby": hint
          ? [children.props["aria-describedby"], `${generatedId}-hint`]
              .filter(Boolean)
              .join(" ")
          : children.props["aria-describedby"],
      })
    : children;

  return (
    <div className={`block ${className}`}>
      <label
        htmlFor={control?.props?.id || generatedId}
        className="mb-1 block text-xs font-medium text-[var(--text-secondary)]"
      >
        {label}
      </label>
      {control}
      {hint ? (
        <span id={`${generatedId}-hint`} className="mt-1 block text-[11px] text-[var(--text-muted)]">
          {hint}
        </span>
      ) : null}
    </div>
  );
}

export function Select({ options, includeBlank, blankLabel = "Any", ...rest }) {
  return (
    <select {...rest}>
      {includeBlank ? <option value="">{blankLabel}</option> : null}
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

export function ErrorNote({ error, onDismiss }) {
  if (!error) return null;
  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-lg border border-[var(--critical)]/40 bg-[var(--critical)]/10 px-3 py-2 text-sm"
    >
      <AlertTriangle size={15} className="mt-0.5 shrink-0 text-[var(--critical)]" />
      <span className="min-w-0 flex-1">{String(error.message || error)}</span>
      {onDismiss ? (
        <button type="button" onClick={onDismiss} aria-label="Dismiss">
          <X size={14} />
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({ icon: Icon, title, description, action }) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-10 text-center">
      {Icon ? <Icon size={22} className="text-[var(--text-muted)]" /> : null}
      <p className="text-sm font-medium">{title}</p>
      {description ? (
        <p className="max-w-sm text-xs text-[var(--text-muted)]">{description}</p>
      ) : null}
      {action}
    </div>
  );
}

export function Spinner({ label = "Loading" }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-[var(--text-muted)]">
      <Loader2 size={15} className="animate-spin" />
      {label}
    </div>
  );
}

export function ProgressBar({ value, tone = "accent" }) {
  const pct = Math.max(0, Math.min(100, Math.round(value || 0)));
  const color = tone === "good" ? "var(--good)" : "var(--accent)";
  return (
    <div
      className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--surface-2)]"
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div className="h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
    </div>
  );
}

export function Modal({ open, title, onClose, children, width = "max-w-lg" }) {
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    // Focus moves into the dialog so keyboard users aren't left behind on the
    // page underneath.
    ref.current?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/45 p-4 sm:p-8"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={ref}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`w-full ${width} rounded-xl border bg-[var(--surface-1)] shadow-xl outline-none`}
      >
        <div className="flex items-center justify-between border-b px-4 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          <button type="button" onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}
