"use client";

import { useMemo } from "react";

/** Un contacto de la empresa candidato a destinatario (viene en la
 *  previsualización del envío). */
export type PickContact = {
  id: string;
  name: string;
  email: string | null;
  has_email: boolean;
  /** Es el contacto ligado al pedido (se marca por defecto y se distingue). */
  is_order_contact?: boolean;
};

/** Canal al que se asigna un contacto elegido. */
export type ContactChannel = "to" | "cc";

/** Selector de destinatarios entre los CONTACTOS de la empresa del pedido.
 *  `value` es un mapa `email → canal` (Para/CC). Los contactos sin email se
 *  muestran deshabilitados con un aviso. El campo de emails libres va aparte,
 *  en el modal que lo usa. */
export function CompanyContactsPicker({
  contacts,
  value,
  onChange,
  disabled,
}: {
  contacts: PickContact[];
  value: Record<string, ContactChannel>;
  onChange: (next: Record<string, ContactChannel>) => void;
  disabled?: boolean;
}) {
  const withEmail = useMemo(
    () => contacts.filter((c) => c.has_email && c.email),
    [contacts],
  );
  const withoutEmail = useMemo(
    () => contacts.filter((c) => !c.has_email),
    [contacts],
  );

  function set(email: string, channel: ContactChannel | null) {
    const next = { ...value };
    if (channel === null) delete next[email];
    else next[email] = channel;
    onChange(next);
  }

  if (contacts.length === 0) {
    return (
      <p className="muted small">
        Esta empresa no tiene contactos guardados. Escribe la dirección a mano
        en «Para».
      </p>
    );
  }

  return (
    <div className="erp-contacts-picker">
      <span className="erp-contacts-title">Contactos de la empresa</span>
      <div className="erp-contacts-list">
        {withEmail.map((c) => {
          const email = c.email as string;
          const channel = value[email] ?? null;
          return (
            <div key={c.id} className="erp-contact-row">
              <label className="erp-contact-check">
                <input
                  type="checkbox"
                  checked={channel !== null}
                  disabled={disabled}
                  aria-label={`Enviar a ${c.name} (${email})`}
                  onChange={(e) => set(email, e.target.checked ? "to" : null)}
                />
                <span className="erp-contact-info">
                  <span className="erp-contact-name">
                    {c.name}
                    {c.is_order_contact ? (
                      <span className="badge muted" title="Contacto del pedido">
                        {" "}pedido
                      </span>
                    ) : null}
                  </span>
                  <span className="erp-contact-email muted small">{email}</span>
                </span>
              </label>
              {channel !== null ? (
                <span
                  className="erp-contact-channel"
                  role="group"
                  aria-label={`Canal de ${c.name}`}
                >
                  <button
                    type="button"
                    className={`button small ${channel === "to" ? "" : "secondary"}`}
                    disabled={disabled}
                    aria-pressed={channel === "to"}
                    onClick={() => set(email, "to")}
                  >
                    Para
                  </button>
                  <button
                    type="button"
                    className={`button small ${channel === "cc" ? "" : "secondary"}`}
                    disabled={disabled}
                    aria-pressed={channel === "cc"}
                    onClick={() => set(email, "cc")}
                  >
                    CC
                  </button>
                </span>
              ) : null}
            </div>
          );
        })}
        {withoutEmail.map((c) => (
          <div key={c.id} className="erp-contact-row is-disabled">
            <span className="erp-contact-info">
              <span className="erp-contact-name muted">{c.name}</span>
              <span className="erp-contact-email muted small">
                sin email — no se puede seleccionar
              </span>
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Reparte un mapa `email → canal` en las listas `to` / `cc`. */
export function splitContactChannels(
  value: Record<string, ContactChannel>,
): { to: string[]; cc: string[] } {
  const to: string[] = [];
  const cc: string[] = [];
  for (const [email, channel] of Object.entries(value)) {
    (channel === "cc" ? cc : to).push(email);
  }
  return { to, cc };
}
