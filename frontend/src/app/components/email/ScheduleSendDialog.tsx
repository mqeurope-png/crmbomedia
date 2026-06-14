"use client";

import { Clock } from "lucide-react";
import { useState } from "react";
import { Modal } from "../Modal";

type Props = {
  open: boolean;
  onSchedule: (iso: string) => void;
  onClose: () => void;
};

/** "Programar envío" picker. Presets recompute their target on
 *  every click so "mañana 9:00" doesn't drift if the dialog stays
 *  open over midnight; "próximo lunes" jumps a full week when
 *  today IS Monday so the operator never gets a same-day "next
 *  Monday". Custom takes a datetime-local; backend validates the
 *  date is in the future. */
export function ScheduleSendDialog({ open, onSchedule, onClose }: Props) {
  const [custom, setCustom] = useState("");

  const apply = (offset: () => Date) => () =>
    onSchedule(offset().toISOString());

  const inOneHour = () => {
    const d = new Date();
    d.setHours(d.getHours() + 1);
    return d;
  };
  const tomorrowMorning = () => {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    d.setHours(9, 0, 0, 0);
    return d;
  };
  const nextMonday = () => {
    const d = new Date();
    const day = d.getDay();
    const delta = ((1 + 7 - day) % 7) || 7;
    d.setDate(d.getDate() + delta);
    d.setHours(9, 0, 0, 0);
    return d;
  };

  return (
    <Modal open={open} onClose={onClose} title="Programar envío" size="small">
      <p className="muted small">
        Elige cuándo quieres que se envíe el email. Puedes cancelar o
        editar el envío desde la carpeta &quot;Programados&quot;.
      </p>
      <ul className="email-schedule-presets">
        <li>
          <button
            type="button"
            className="btn"
            onClick={apply(inOneHour)}
          >
            <Clock size={12} aria-hidden /> En 1 hora
          </button>
        </li>
        <li>
          <button
            type="button"
            className="btn"
            onClick={apply(tomorrowMorning)}
          >
            <Clock size={12} aria-hidden /> Mañana 9:00
          </button>
        </li>
        <li>
          <button
            type="button"
            className="btn"
            onClick={apply(nextMonday)}
          >
            <Clock size={12} aria-hidden /> Próximo lunes 9:00
          </button>
        </li>
      </ul>
      <div className="email-schedule-custom">
        <label>
          Personalizado
          <input
            type="datetime-local"
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
          />
        </label>
        <div className="form-actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancelar
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!custom}
            onClick={() => {
              if (custom) onSchedule(new Date(custom).toISOString());
            }}
          >
            Programar
          </button>
        </div>
      </div>
    </Modal>
  );
}
