// Centralized error-message extraction so the UI never renders "[object
// Object]" when an API call fails. Two layers of defense:
//
//   1. The fetch wrappers (lib/api.ts, lib/integrationSettings.ts) call
//      formatFastApiDetail on the response body before throwing, so the
//      Error.message is always a sensible string even when FastAPI returns
//      a 422 with `detail: [{ msg, loc, ... }]` (Pydantic validation list).
//
//   2. Page-level catch handlers call extractErrorMessage(err, fallback),
//      which also handles network errors (`TypeError: Failed to fetch`),
//      raw strings, and unknown shapes with a friendly default.

const DEFAULT_MESSAGE = "Ocurrió un error inesperado.";
const NETWORK_MESSAGE = "No se pudo conectar con el servidor.";

type FastApiValidationItem = {
  msg?: unknown;
  loc?: unknown;
};

function locToLabel(loc: unknown): string {
  if (!Array.isArray(loc)) return "";
  // FastAPI loc looks like ["body", "new_password"]; "body" is noise for users.
  return loc
    .filter((part) => part !== "body" && part !== "query" && part !== "path")
    .map((part) => String(part))
    .join(".");
}

/** Format the `detail` field of a FastAPI error response into a human string. */
export function formatFastApiDetail(detail: unknown, fallback = DEFAULT_MESSAGE): string {
  if (typeof detail === "string") {
    const trimmed = detail.trim();
    if (trimmed) return trimmed;
  }
  if (Array.isArray(detail)) {
    const lines: string[] = [];
    for (const item of detail) {
      if (typeof item === "string" && item.trim()) {
        lines.push(item.trim());
        continue;
      }
      if (item && typeof item === "object") {
        const candidate = item as FastApiValidationItem;
        if (typeof candidate.msg === "string" && candidate.msg.trim()) {
          const label = locToLabel(candidate.loc);
          lines.push(label ? `${label}: ${candidate.msg}` : candidate.msg);
        }
      }
    }
    if (lines.length > 0) return lines.join("\n");
  }
  return fallback;
}

/** Extract a user-friendly message from anything thrown by an API call. */
export function extractErrorMessage(err: unknown, fallback = DEFAULT_MESSAGE): string {
  if (err == null) return fallback;
  if (typeof err === "string") return err.trim() || fallback;
  if (err instanceof Error) {
    // fetch() raises a TypeError when the request can't reach the server.
    if (err.name === "TypeError" && /fetch|network|failed/i.test(err.message)) {
      return NETWORK_MESSAGE;
    }
    const message = err.message?.trim();
    return message && message !== "[object Object]" ? message : fallback;
  }
  if (typeof err === "object") {
    const obj = err as Record<string, unknown>;
    if ("detail" in obj) return formatFastApiDetail(obj.detail, fallback);
    if (typeof obj.message === "string" && obj.message.trim()) return obj.message.trim();
  }
  return fallback;
}
