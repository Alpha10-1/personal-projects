/** Date and number formatting. Dates from the API are plain ISO `YYYY-MM-DD`
 *  strings with no timezone, so they are parsed as local calendar dates -- using
 *  `new Date(str)` would read them as UTC and shift a due date by a day. */

export function parseDate(value) {
  if (!value) return null;
  const [datePart] = String(value).split("T");
  const [year, month, day] = datePart.split("-").map(Number);
  if (!year || !month || !day) return null;
  return new Date(year, month - 1, day);
}

export function todayIso() {
  const now = new Date();
  return toIso(now);
}

export function toIso(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function formatDate(value, { withYear = true } = {}) {
  const date = parseDate(value);
  if (!date) return "—";
  return date.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    ...(withYear ? { year: "numeric" } : {}),
  });
}

export function formatWeek(value) {
  const date = parseDate(value);
  if (!date) return "";
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** "3 days overdue", "due today", "in 5 days" -- the phrasing a to-do list
 *  actually needs, relative to local midnight. */
export function relativeDue(value) {
  const date = parseDate(value);
  if (!date) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.round((date - today) / 86400000);
  if (days === 0) return { text: "Due today", tone: "warning", days };
  if (days < 0) {
    const n = Math.abs(days);
    return { text: `${n} day${n === 1 ? "" : "s"} overdue`, tone: "critical", days };
  }
  if (days === 1) return { text: "Due tomorrow", tone: "warning", days };
  return { text: `In ${days} days`, tone: "neutral", days };
}

export function formatHours(value) {
  const hours = Number(value || 0);
  if (!hours) return "0h";
  return Number.isInteger(hours) ? `${hours}h` : `${hours.toFixed(1)}h`;
}

export function formatBytes(bytes) {
  const size = Number(bytes || 0);
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}
