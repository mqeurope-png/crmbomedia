"use client";

import moment from "moment";
import "moment/locale/es";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Calendar,
  momentLocalizer,
  type View,
} from "react-big-calendar";
import "react-big-calendar/lib/css/react-big-calendar.css";
import {
  getCalendarTasks,
  type Task,
} from "../lib/tasksApi";
import { extractErrorMessage } from "../lib/errors";
import { TaskModal } from "./TaskModal";

moment.locale("es");
const localizer = momentLocalizer(moment);

type CalendarEvent = {
  id: string;
  title: string;
  start: Date;
  end: Date;
  allDay: boolean;
  resource: Task;
};

const VIEWS: View[] = ["month", "week", "day"];

const MESSAGES = {
  date: "Fecha",
  time: "Hora",
  event: "Tarea",
  allDay: "Todo el día",
  week: "Semana",
  work_week: "Semana laboral",
  day: "Día",
  month: "Mes",
  previous: "Anterior",
  next: "Siguiente",
  yesterday: "Ayer",
  tomorrow: "Mañana",
  today: "Hoy",
  agenda: "Agenda",
  noEventsInRange: "Sin tareas en este rango.",
  showMore: (total: number) => `+${total} más`,
};

/** Calendar view of `/tasks` — month / week / day picker, click an
 *  event to edit, drag (where the lib allows) to reschedule. */
export function TasksCalendar() {
  const [view, setView] = useState<View>("month");
  const [date, setDate] = useState<Date>(new Date());
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingTask, setEditingTask] = useState<Task | null>(null);

  // Drag-and-drop is a spec-listed "deseable but not required" — the
  // base Calendar component handles click-to-edit, the drag addon
  // can land in a follow-up PR. Keeping the shape (CalendarEvent
  // wraps the Task) so wiring it up is local.

  const range = useMemo(() => {
    // Pad the visible window by a week on each side so dragging an
    // event a few days off-screen still has fresh data.
    const start = moment(date).subtract(1, view as moment.unitOfTime.Base).toDate();
    const end = moment(date).add(1, view as moment.unitOfTime.Base).toDate();
    return { start, end };
  }, [date, view]);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const items = await getCalendarTasks(
        range.start.toISOString(),
        range.end.toISOString(),
      );
      setTasks(items);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo cargar el calendario."));
    } finally {
      setLoading(false);
    }
  }, [range.start, range.end]);

  useEffect(() => {
    reload();
  }, [reload]);

  const events = useMemo<CalendarEvent[]>(() => {
    return tasks
      .filter((t) => t.due_at)
      .map((t) => {
        const start = new Date(t.due_at as string);
        // Tasks have no end time today — block out a 30-min slot so
        // they're not invisible single points on the time grid.
        const end = new Date(start.getTime() + 30 * 60 * 1000);
        return {
          id: t.id,
          title: t.status === "done" ? `✓ ${t.title}` : t.title,
          start,
          end,
          allDay: false,
          resource: t,
        };
      });
  }, [tasks]);

  function eventClassName(event: CalendarEvent) {
    const t = event.resource;
    if (t.status === "done") return "rbc-event rbc-event-done";
    if (t.priority === "urgent") return "rbc-event rbc-event-urgent";
    if (t.priority === "high") return "rbc-event rbc-event-high";
    return "rbc-event";
  }

  return (
    <section className="tasks-calendar-wrapper">
      {error ? <p className="form-error">{error}</p> : null}
      {loading ? <p className="muted small">Cargando…</p> : null}
      <Calendar
        localizer={localizer}
        events={events}
        view={view}
        date={date}
        views={VIEWS}
        onView={setView}
        onNavigate={setDate}
        messages={MESSAGES}
        culture="es"
        eventPropGetter={(event) => ({ className: eventClassName(event as CalendarEvent) })}
        onSelectEvent={(event) => setEditingTask((event as CalendarEvent).resource)}
        style={{ minHeight: 600 }}
      />
      {editingTask ? (
        <TaskModal
          task={editingTask}
          onClose={() => setEditingTask(null)}
          onUpdated={async () => {
            setEditingTask(null);
            await reload();
          }}
        />
      ) : null}
    </section>
  );
}
