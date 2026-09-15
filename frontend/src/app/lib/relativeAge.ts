/** Antigüedad de una fecha en palabras, en español y para leerla de un
 *  vistazo en una fila: «hoy», «ayer», «hace 3 días», «hace 9 semanas»,
 *  «hace 4 meses», «hace 2 años».
 *
 *  Pensado para las fechas `YYYY-MM-DD` de FACTUSOL (sin hora): cuenta días
 *  naturales, no horas, así una proforma de ayer por la tarde es «ayer» y no
 *  «hace 0 días». Acepta también un ISO con hora (se queda con el día) y un
 *  `Date`.
 *
 *  Tramos: los días se mantienen hasta los 60 —el umbral comercial de una
 *  proforma sin respuesta está en 30 y ahí importa la cifra exacta («hace 41
 *  días»)—; de 60 a 89, semanas; de 90 a 364, meses; desde 365, años. Una
 *  fecha futura (reloj desfasado) cuenta como «hoy». */

const DAY_MS = 86_400_000;
const DAYS_AS_DAYS = 60;
const DAYS_AS_WEEKS = 90;
const DAYS_AS_MONTHS = 365;
const DAYS_PER_MONTH = 30.4375;

export type AgeInput = string | Date | null | undefined;

/** Día natural (medianoche UTC) de la fecha, o null si no hay fecha válida.
 *  Una cadena se lee por su `YYYY-MM-DD` inicial; un `Date`, por su día
 *  local (el que ve quien mira la pantalla). */
function dayOf(value: AgeInput): number | null {
  if (!value) return null;
  if (value instanceof Date) {
    return Number.isNaN(value.getTime())
      ? null
      : Date.UTC(value.getFullYear(), value.getMonth(), value.getDate());
  }
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!m) return null;
  const t = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return Number.isNaN(t) ? null : t;
}

/** Días naturales transcurridos desde `date` hasta `now` (hoy = 0, ayer =
 *  1). Null sin fecha válida; nunca negativo (una fecha futura cuenta como
 *  hoy). `now` es `Date.now()` por defecto para poder fijarlo en tests. */
export function ageInDays(date: AgeInput, now: number | Date = Date.now()): number | null {
  const from = dayOf(date);
  if (from === null) return null;
  const today = dayOf(typeof now === "number" ? new Date(now) : now);
  if (today === null) return null;
  return Math.max(0, Math.round((today - from) / DAY_MS));
}

function count(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** «hoy» · «ayer» · «hace N días» · «hace N semanas» · «hace N meses» ·
 *  «hace N años». Null si no hay fecha válida (quien pinta decide qué poner). */
export function relativeAge(date: AgeInput, now: number | Date = Date.now()): string | null {
  const days = ageInDays(date, now);
  if (days === null) return null;
  if (days === 0) return "hoy";
  if (days === 1) return "ayer";
  if (days < DAYS_AS_DAYS) return `hace ${days} días`;
  if (days < DAYS_AS_WEEKS) return `hace ${count(Math.round(days / 7), "semana", "semanas")}`;
  if (days < DAYS_AS_MONTHS) return `hace ${count(Math.round(days / DAYS_PER_MONTH), "mes", "meses")}`;
  return `hace ${count(Math.floor(days / DAYS_AS_MONTHS), "año", "años")}`;
}
