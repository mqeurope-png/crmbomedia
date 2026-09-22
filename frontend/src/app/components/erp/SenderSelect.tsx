"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { getEmailSenders, type EmailSender } from "../../lib/erpApi";

/** Selector «Enviar desde»: los «enviar como» VERIFICADOS de la cuenta de Gmail
 *  conectada del CRM. El remitente propuesto (por tienda/serie) llega en
 *  `defaultAlias` y se garantiza que esté entre las opciones aunque el listado
 *  no lo traiga o Gmail no esté disponible. */
export function SenderSelect({
  defaultAlias,
  value,
  onChange,
  disabled,
  hint,
}: {
  defaultAlias: string;
  value: string;
  onChange: (email: string) => void;
  disabled?: boolean;
  hint?: ReactNode;
}) {
  const [senders, setSenders] = useState<EmailSender[] | null>(null);

  useEffect(() => {
    let alive = true;
    getEmailSenders()
      .then((r) => { if (alive) setSenders(r.senders); })
      .catch(() => { if (alive) setSenders([]); });
    return () => { alive = false; };
  }, []);

  const options = useMemo(() => {
    const list = senders ?? [];
    const seen = new Set(list.map((s) => s.email.toLowerCase()));
    const out = list.map((s) => ({
      email: s.email,
      label: s.name ? `${s.name} <${s.email}>` : s.email,
    }));
    // El remitente propuesto siempre debe poder elegirse (aunque el listado de
    // Gmail no lo traiga o no esté disponible).
    if (defaultAlias && !seen.has(defaultAlias.toLowerCase())) {
      out.unshift({ email: defaultAlias, label: defaultAlias });
    }
    return out;
  }, [senders, defaultAlias]);

  return (
    <>
      <label className="field">
        <span>Enviar desde</span>
        <select
          value={value}
          aria-label="Remitente"
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        >
          {options.length === 0 ? (
            <option value="">—</option>
          ) : (
            options.map((o) => (
              <option key={o.email} value={o.email}>{o.label}</option>
            ))
          )}
        </select>
      </label>
      {hint ? <span className="muted small">{hint}</span> : null}
    </>
  );
}
