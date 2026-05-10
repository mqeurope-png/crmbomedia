// Mirror of backend/app/core/observability.scrub_pii. Keeps the redaction
// logic identical on both sides so events emitted from the browser look
// the same as events emitted from FastAPI.

import type { ErrorEvent, EventHint } from "@sentry/nextjs";

const SENSITIVE_KEY_NEEDLES = [
  "password",
  "passwd",
  "token",
  "secret",
  "api_key",
  "apikey",
  "authorization",
  "cookie",
  "session",
];

const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;
const REDACTED = "[REDACTED]";
const REDACTED_EMAIL = "[REDACTED EMAIL]";

function isSensitiveKey(key: string): boolean {
  const lower = key.toLowerCase();
  return SENSITIVE_KEY_NEEDLES.some((needle) => lower.includes(needle));
}

function redactEmails(value: string): string {
  return value.replace(EMAIL_RE, REDACTED_EMAIL);
}

function scrub(node: unknown): unknown {
  if (node == null) return node;
  if (typeof node === "string") return redactEmails(node);
  if (Array.isArray(node)) return node.map(scrub);
  if (typeof node === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
      out[key] = isSensitiveKey(key) ? REDACTED : scrub(value);
    }
    return out;
  }
  return node;
}

/** Sentry beforeSend hook. Returns the event with PII removed in place. */
export function scrubSentryEvent(event: ErrorEvent, hint?: EventHint): ErrorEvent | null {
  void hint;
  return scrub(event) as ErrorEvent;
}
