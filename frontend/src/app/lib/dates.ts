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
 *  shows as 00:43 in Madrid" bug reported on v2.4e, and the
 *  "sync que termina AHORA muestra 'hace 2 horas'" del PR-Timezone-
 *  Fix.
 *
 *  El backend ahora reattacha UTC vía SQLAlchemy event listener
 *  (`_ensure_utc` en `app/models/crm.py`), así que los timestamps
 *  nuevos deberían llegar con offset. Mantenemos esta función
 *  defensiva por si algún endpoint legacy escapa al hook (consultas
 *  con `select(col)` que devuelven tuplas crudas, p. ej.).
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

/** Render "hace X" / "in X" relative to now. Tolerant input — null /
 *  undefined / empty string → "—". Future timestamps (clock skew or
 *  scheduled events) render as "en X". Buckets:
 *
 *  - < 10 s          : "ahora mismo"
 *  - < 60 s          : "hace 30 s"
 *  - < 60 min        : "hace 5 min"
 *  - < 24 h          : "hace 3 h"
 *  - < 30 d          : "hace 4 d"
 *  - >= 30 d         : fallback a `formatBackendDateTime`.
 *
 *  Pre-PR-Timezone-Fix, varios widgets ("Sincronización Agile", "Email
 *  Activity", "Tareas Próximas") computaban relative time con
 *  `new Date(iso)` directo. Cuando el backend mandaba el ISO sin tz,
 *  el navegador en Madrid (UTC+2) restaba 2 h al diff → "hace 2 h"
 *  recién terminada la sync. Centralizar el parsing aquí asegura que
 *  cualquier futuro widget hereda el fix sin tener que recordar el
 *  hack del sufijo `Z`. */
export function formatRelative(
  iso: string | null | undefined,
  now: Date = new Date(),
): string {
  if (!iso) return "—";
  const target = parseBackendDate(iso);
  const diffMs = now.getTime() - target.getTime();
  const absMs = Math.abs(diffMs);
  const future = diffMs < 0;
  const prefix = future ? "en " : "hace ";

  if (absMs < 10_000) return "ahora mismo";
  const seconds = Math.floor(absMs / 1000);
  if (seconds < 60) return `${prefix}${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${prefix}${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${prefix}${hours} h`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${prefix}${days} d`;
  return formatBackendDateTime(iso, {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
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
