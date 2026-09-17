/** Vocabularies shared by the forms, filters and badges. Kept in one place so
 *  the UI can never offer a value the API would reject. */

export const PROJECT_STATUSES = [
  { value: "idea", label: "Idea" },
  { value: "planning", label: "Planning" },
  { value: "active", label: "Active" },
  { value: "on_hold", label: "On hold" },
  { value: "done", label: "Done" },
  { value: "archived", label: "Archived" },
];

export const PROJECT_CATEGORIES = [
  { value: "research", label: "Research" },
  { value: "build", label: "Build" },
  { value: "analysis", label: "Analysis" },
  { value: "learning", label: "Learning" },
  { value: "ops", label: "Ops" },
  { value: "other", label: "Other" },
];

export const PRIORITIES = [
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
];

export const TASK_STATUSES = [
  { value: "todo", label: "To do" },
  { value: "in_progress", label: "In progress" },
  { value: "blocked", label: "Blocked" },
  { value: "done", label: "Done" },
];

export const MILESTONE_STATUSES = [
  { value: "pending", label: "Pending" },
  { value: "done", label: "Done" },
];

// The order here is the order of the categorical palette slots, so a category
// keeps the same colour in every chart it appears in.
export const TIME_CATEGORIES = [
  { value: "research", label: "Research" },
  { value: "build", label: "Build" },
  { value: "analysis", label: "Analysis" },
  { value: "meeting", label: "Meetings" },
  { value: "learning", label: "Learning" },
  { value: "admin", label: "Admin" },
  { value: "other", label: "Other" },
];

export const NOTE_KINDS = [
  { value: "note", label: "Note" },
  { value: "decision", label: "Decision" },
  { value: "result", label: "Result" },
  { value: "blocker", label: "Blocker" },
  { value: "idea", label: "Idea" },
];

export const LINK_KINDS = [
  { value: "paper", label: "Paper" },
  { value: "repo", label: "Repo" },
  { value: "doc", label: "Doc" },
  { value: "dataset", label: "Dataset" },
  { value: "tool", label: "Tool" },
  { value: "other", label: "Other" },
];

export function labelFor(options, value) {
  return options.find((option) => option.value === value)?.label ?? value ?? "";
}

/** Badge tone per status. Status colours are reserved for state and are always
 *  paired with the label text, never carrying the meaning on their own. */
export const PROJECT_STATUS_TONE = {
  idea: "neutral",
  planning: "info",
  active: "good",
  on_hold: "warning",
  done: "neutral",
  archived: "neutral",
};

export const TASK_STATUS_TONE = {
  todo: "neutral",
  in_progress: "info",
  blocked: "critical",
  done: "good",
};

export const PRIORITY_TONE = {
  high: "serious",
  medium: "neutral",
  low: "neutral",
};
