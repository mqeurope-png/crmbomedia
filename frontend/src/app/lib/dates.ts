/** Normalise an ISO datetime returned by the backend so the
 *  browser interprets it as UTC.
 *
 *  Pydantic + SQLAlchemy + MySQL combo: `DateTime(timezone=True)`
 *  against MySQL stores the UTC value but reads it back as a
 *  *naive* datetime, which Pydantic serializes WITHOUT a `Z` /
 *  offset suffix. `new Date("2026-06-15T00:43:00")` (no suffix)
 *  is interpreted by browsers as LOCAL time, so a UTC value
 *  shipped this way ends up displayed 1-2 hours off depending on
 *  the operator's timezone — exactly the "Programado para 02:43
 *  shows as 00:43 in Madrid" bug reported on v2.4e.
 *
 *  We can't reliably fix the backend serialization without a
 *  schema-wide change; appending `Z` here treats every naive
 *  string as UTC, which matches what the backend actually
 *  stores. Strings that already carry `Z` or a `±HH:MM` offset
 *  are returned untouched.
 */
const HAS_TZ_RE = /[Zz]|[+-]\d{2}:?\d{2}$/;

export function parseBackendDate(iso: string): Date {
  return new Date(HAS_TZ_RE.test(iso) ? iso : iso + "Z");
}

/** Format a backend datetime in the user's locale + timezone. */
export function formatBackendDateTime(
  iso: string | null | undefined,
  options: Intl.DateTimeFormatOptions = {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  },
): string {
  if (!iso) return "—";
  return parseBackendDate(iso).toLocaleString("es-ES", options);
}

/** Convert a backend ISO datetime to the value expected by
 *  `<input type="datetime-local">` — `YYYY-MM-DDTHH:mm` in LOCAL
 *  time (no offset suffix). We shift by the browser's offset so
 *  the picker pre-loads the same wall-clock time the operator
 *  originally saw, not the UTC equivalent.
 */
export function toLocalInputValue(iso: string): string {
  const d = parseBackendDate(iso);
  const off = d.getTimezoneOffset();
  const local = new Date(d.getTime() - off * 60 * 1000);
  return local.toISOString().slice(0, 16);
}
