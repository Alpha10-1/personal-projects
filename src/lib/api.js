"use client";

/**
 * Thin fetch wrapper around the local FastAPI backend.
 *
 * There is no auth layer because there is no second user: the API is bound to
 * localhost and the browser talks to it directly.
 */

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://localhost:8000";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function buildUrl(path, params) {
  const url = new URL(`${API_URL}${path}`);
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    url.searchParams.set(key, value);
  });
  return url.toString();
}

async function request(method, path, { params, body, isForm } = {}) {
  let response;
  try {
    response = await fetch(buildUrl(path, params), {
      method,
      headers: isForm || body === undefined ? undefined : { "Content-Type": "application/json" },
      body: isForm ? body : body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    // A network-level failure here almost always means the API isn't running,
    // which is worth saying plainly instead of surfacing "Failed to fetch".
    throw new ApiError(
      `Can't reach the API at ${API_URL}. Is the backend running?`,
      0,
    );
  }

  if (response.status === 204) return null;

  const text = await response.text();
  const payload = text ? safeJson(text) : null;

  if (!response.ok) {
    throw new ApiError(detailToMessage(payload) || response.statusText, response.status);
  }
  return payload;
}

function safeJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function detailToMessage(payload) {
  const detail = payload?.detail;
  if (!detail) return null;
  if (typeof detail === "string") return detail;
  // FastAPI validation errors arrive as a list of {loc, msg}; show the first
  // one with its field rather than dumping the raw array.
  if (Array.isArray(detail)) {
    const first = detail[0];
    const field = Array.isArray(first?.loc) ? first.loc[first.loc.length - 1] : null;
    return [field, first?.msg].filter(Boolean).join(": ") || "Invalid request";
  }
  return "Invalid request";
}

export const api = {
  get: (path, params) => request("GET", path, { params }),
  post: (path, body) => request("POST", path, { body }),
  patch: (path, body) => request("PATCH", path, { body }),
  del: (path) => request("DELETE", path),
  upload: (path, formData) => request("POST", path, { body: formData, isForm: true }),
};

export const downloadUrl = (fileId) => `${API_URL}/files/${fileId}/download`;
