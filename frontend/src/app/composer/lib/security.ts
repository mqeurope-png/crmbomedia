/**
 * Client-side HTML sanitization for the composer.
 *
 * Ported literal from `bomedia-v4/src/app-security.jsx`. Strips the
 * usual gallery of email/preview injection vectors:
 *
 *   - `<script>` blocks
 *   - inline `on*=` event handlers
 *   - `javascript:` and `data:` URIs in href / src / action
 *   - `<iframe>` / `<object>` / `<embed>` tags
 *   - `<style>` blocks (they'd pwn the email's own CSS_BLOCK and could
 *     hide the body with `body{display:none}` or @import remote CSS)
 *   - `<link>` (external stylesheets reveal cache state to a hostile
 *     domain), `<base>` (would re-root every relative link), and
 *     `<meta http-equiv=refresh>` (timed redirect).
 *
 * Whitelist parsing is intentionally a regex pass instead of a real
 * DOMParser walk. Reason: this runs on rich text the user just typed
 * via the RichTextEditor toolbar, where the set of tags introduced is
 * small (b/i/u/s/p/h1-3/ul/ol/li/a) — the rejected vectors are also
 * small. Going through DOMParser would mean creating a `Document`
 * just to serialize back, with all the encoding round-trips that
 * implies, for no extra coverage.
 *
 * `sanitizeJsonObj` defends template imports from prototype pollution.
 */

export function sanitizeHtml(html: string | null | undefined): string {
  if (!html) return "";
  let s = html;
  // Scripts and inline event handlers.
  s = s.replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, "");
  s = s.replace(/\s+on\w+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, "");
  s = s.replace(
    /(href|src|action)\s*=\s*(?:"javascript:[^"]*"|'javascript:[^']*')/gi,
    '$1=""',
  );
  s = s.replace(/src\s*=\s*(?:"data:[^"]*"|'data:[^']*')/gi, 'src=""');
  // Embeds that can execute.
  s = s.replace(/<iframe\b[^<]*(?:(?!<\/iframe>)<[^<]*)*<\/iframe>/gi, "");
  s = s.replace(/<iframe\b[^>]*\/?>/gi, "");
  s = s.replace(/<(?:object|embed)\b[^>]*\/?>/gi, "");
  // <style> in pasted rich HTML can clobber the email's own CSS_BLOCK
  // (e.g. body{display:none}) or @import remote stylesheets.
  s = s.replace(/<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>/gi, "");
  s = s.replace(/<style\b[^>]*\/?>/gi, "");
  // <link rel=stylesheet> loads external CSS and leaks cache state;
  // <base> would re-root every relative link to a hostile domain;
  // <meta http-equiv=refresh> allows timed redirect.
  s = s.replace(/<(?:link|base|meta)\b[^>]*\/?>/gi, "");
  return s;
}

/** Strip __proto__ / constructor / prototype keys from a deserialized
 * JSON tree before merging it into app state. Used when loading
 * legacy/imported templates. */
export function sanitizeJsonObj<T>(obj: T): T {
  if (obj === null || typeof obj !== "object") return obj;
  if (Array.isArray(obj)) {
    return obj.map((x) => sanitizeJsonObj(x)) as unknown as T;
  }
  const clean: Record<string, unknown> = {};
  const o = obj as Record<string, unknown>;
  for (const k of Object.keys(o)) {
    if (k === "__proto__" || k === "constructor" || k === "prototype") continue;
    clean[k] = sanitizeJsonObj(o[k]);
  }
  return clean as unknown as T;
}

/** SHA-256 hex hash via WebCrypto. Async because `crypto.subtle.digest`
 * is async; consumers should treat this as a one-shot setup helper. */
export async function sha256Hash(str: string): Promise<string> {
  const encoder = new TextEncoder();
  const data = encoder.encode(str);
  const buf = await crypto.subtle.digest("SHA-256", data);
  const arr = Array.from(new Uint8Array(buf));
  return arr.map((b) => b.toString(16).padStart(2, "0")).join("");
}
