"use client";

import { ArrowDownLeft, ArrowUpRight, Mail } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { formatRelative } from "../../lib/dates";
import {
  getEmailActivity,
  type EmailActivityItem,
} from "../../lib/emailsApi";
import { extractErrorMessage } from "../../lib/errors";

// PR-Timezone-Fix. La función local hacía `new Date(value)` directo;
// con ISO sin offset (caso real) el browser lo interpretaba como
// hora local y restaba 2 h del diff. Ahora delegamos en la util.
const relativeTime = (value: string) => formatRelative(value);

export function EmailActivityWidget() {
  const [scope, setScope] = useState<"mine" | "all">("all");
  const [items, setItems] = useState<EmailActivityItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    getEmailActivity(scope, 5)
      .then(setItems)
      .catch((err) =>
        setError(extractErrorMessage(err, "No se pudieron cargar los eventos.")),
      )
      .finally(() => setLoading(false));
  }, [scope]);

  return (
    <article className="card widget widget-email">
      <header className="section-title">
        <h2>
          <Mail size={14} aria-hidden /> Actividad email
        </h2>
        <div className="widget-toolbar">
          <button
            type="button"
            className={`pill-toggle ${scope === "all" ? "is-active" : ""}`}
            onClick={() => setScope("all")}
          >
            Todo
          </button>
          <button
            type="button"
            className={`pill-toggle ${scope === "mine" ? "is-active" : ""}`}
            onClick={() => setScope("mine")}
          >
            Míos
          </button>
        </div>
      </header>
      {loading ? (
        <p className="muted small">Cargando…</p>
      ) : error ? (
        <p className="form-error">{error}</p>
      ) : items.length === 0 ? (
        <p className="muted small">Sin actividad reciente.</p>
      ) : (
        <ul className="widget-list">
          {items.map((evt) => {
            const isOutbound = evt.direction === "outbound";
            const counterparty =
              evt.contact_name ??
              (isOutbound ? evt.from_email : evt.from_email);
            const subjectLine = evt.subject || "(sin asunto)";
            return (
              <li key={evt.message_id} className="widget-row">
                <div className="widget-row-main">
                  <p className="widget-row-title">
                    {isOutbound ? (
                      <ArrowUpRight size={11} aria-hidden />
                    ) : (
                      <ArrowDownLeft size={11} aria-hidden />
                    )}{" "}
                    <Link href={`/emails/${evt.thread_id}`}>
                      {isOutbound ? `Tú → ${counterparty}` : `${counterparty} → Tú`}
                    </Link>
                    {": "}
                    <span className="muted">{subjectLine}</span>
                  </p>
                  <p className="widget-row-meta muted small">
                    {relativeTime(evt.occurred_at)}
                  </p>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </article>
  );
}
