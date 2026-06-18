/**
 * PR-Aperturas-Falsas. Defense-in-depth helper that strips the CRM's
 * own open-tracking pixel from saved email HTML before we hand it to
 * an iframe (or `dangerouslySetInnerHTML`).
 *
 * The backend already strips it on the wire — this fallback covers
 * any code path that fetches a raw `body_html` (cached drafts, future
 * endpoints) and renders it without going through the new API
 * contract. Belt-and-suspenders only; if we hit a regression, we can
 * grep for `stripTrackingPixel` in the frontend and confirm every
 * preview site is calling it.
 *
 * Third-party pixels (Mailchimp, Sendgrid) are intentionally left
 * intact so quoted replies render correctly — the regex anchors on
 * our own `/api/email-track/open/` path.
 */
const TRACKING_PIXEL_REGEX =
  /<img[^>]*src=["'][^"']*?\/api\/email-track\/open\/[^"']*?["'][^>]*?\/?>/gi;

export function stripTrackingPixel(html: string): string;
export function stripTrackingPixel(html: null): null;
export function stripTrackingPixel(html: undefined): undefined;
export function stripTrackingPixel(
  html: string | null | undefined,
): string | null | undefined;
export function stripTrackingPixel(
  html: string | null | undefined,
): string | null | undefined {
  if (!html) return html;
  return html.replace(TRACKING_PIXEL_REGEX, "");
}
