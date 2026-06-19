"use client";

type Props = {
  cfg: Record<string, unknown>;
  setField: (key: string, value: unknown) => void;
};

/**
 * PR-Fixes-Pase-5 Bug 3.
 *
 * Selector de vencimiento de la tarea con 2 modos:
 *
 *  - "relative" (default): cantidad + unidad (minutos/horas/días/
 *    semanas/meses) + hora opcional. Resultado = ahora + cantidad
 *    de unidades, con hora del día sobreescrita si se aporta.
 *  - "weekday": próximo día de la semana (lunes…domingo) + hora
 *    opcional. Resultado = próximo `target_weekday`; si es hoy y
 *    la hora ya pasó (o no se da), sale la semana que viene.
 *
 * El modo "fecha absoluta" no aplica al workflow: la tarea se
 * crea dinámicamente cuando el motor ejecuta el step, no en una
 * fecha fija.
 *
 * Drafts viejos (PR #209) con `due_in_days` siguen funcionando en
 * el backend porque `_resolve_workflow_task_due_at` cae al
 * fallback legacy. Al editar y guardar pasan al nuevo formato.
 */
export function DueDatePicker({ cfg, setField }: Props) {
  const mode = ((cfg.due_mode as string) || "relative").toLowerCase() as
    | "relative"
    | "weekday";

  const switchMode = (next: "relative" | "weekday") => {
    setField("due_mode", next);
  };

  return (
    <div className="workflow-due-picker">
      <fieldset className="workflow-radio-group">
        <legend className="muted small">Vencimiento</legend>
        <label className="workflow-radio">
          <input
            type="radio"
            name="due-mode"
            checked={mode === "relative"}
            onChange={() => switchMode("relative")}
          />
          Relativo desde ahora
        </label>
        <label className="workflow-radio">
          <input
            type="radio"
            name="due-mode"
            checked={mode === "weekday"}
            onChange={() => switchMode("weekday")}
          />
          Próximo día de la semana
        </label>
      </fieldset>

      {mode === "relative" ? (
        <>
          <label>
            Cantidad
            <input
              type="number"
              min={0}
              value={(cfg.duration_amount as number) ?? 1}
              onChange={(e) =>
                setField("duration_amount", Number(e.target.value))
              }
            />
          </label>
          <label>
            Unidad
            <select
              value={(cfg.duration_unit as string) ?? "days"}
              onChange={(e) => setField("duration_unit", e.target.value)}
            >
              <option value="minutes">minutos</option>
              <option value="hours">horas</option>
              <option value="days">días</option>
              <option value="weeks">semanas</option>
              <option value="months">meses</option>
            </select>
          </label>
          <label>
            Hora del día (HH:MM, opcional)
            <input
              type="time"
              value={(cfg.duration_hhmm as string) ?? ""}
              onChange={(e) => setField("duration_hhmm", e.target.value)}
              placeholder="09:00"
            />
            <span className="muted small">
              Si lo dejas vacío, la tarea vence a la hora del día que
              resulte del cálculo (ej. ahora + 2 días = misma hora
              dentro de 2 días).
            </span>
          </label>
        </>
      ) : (
        <>
          <label>
            Día de la semana
            <select
              value={String((cfg.target_weekday as number) ?? 0)}
              onChange={(e) =>
                setField("target_weekday", Number(e.target.value))
              }
            >
              <option value="0">Lunes</option>
              <option value="1">Martes</option>
              <option value="2">Miércoles</option>
              <option value="3">Jueves</option>
              <option value="4">Viernes</option>
              <option value="5">Sábado</option>
              <option value="6">Domingo</option>
            </select>
          </label>
          <label>
            Hora del día (HH:MM, opcional)
            <input
              type="time"
              value={(cfg.weekday_hhmm as string) ?? ""}
              onChange={(e) => setField("weekday_hhmm", e.target.value)}
              placeholder="09:00"
            />
            <span className="muted small">
              Si lo dejas vacío y hoy ya es ese día, la tarea sale la
              semana siguiente para no quedar vencida nada más crearla.
            </span>
          </label>
        </>
      )}
    </div>
  );
}
