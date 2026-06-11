"use client";

import { UserPlus } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { updateContact } from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";
import {
  getDashboardUnattendedLeads,
  type UnattendedLead,
} from "../../lib/dashboardApi";

export function UnattendedLeadsWidget({
  currentUserId,
}: {
  currentUserId: string | null;
}) {
  const [leads, setLeads] = useState<UnattendedLead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLeads(await getDashboardUnattendedLeads());
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudieron cargar los leads."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function assignToMe(lead: UnattendedLead) {
    if (!currentUserId) return;
    try {
      await updateContact(lead.id, { owner_user_id: currentUserId });
      await load();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo asignar el lead."));
    }
  }

  return (
    <article className="card widget widget-leads">
      <header className="section-title">
        <h2>
          <UserPlus size={14} aria-hidden /> Leads sin atender
        </h2>
        <span className="muted small">{leads.length}</span>
      </header>
      {loading ? (
        <p className="muted small">Cargando…</p>
      ) : error ? (
        <p className="form-error">{error}</p>
      ) : leads.length === 0 ? (
        <p className="muted small">
          No hay leads nuevos sin asignar en los últimos 14 días.
        </p>
      ) : (
        <ul className="widget-list">
          {leads.map((lead) => (
            <li key={lead.id} className="widget-row widget-row-lead">
              <div className="widget-row-main">
                <p className="widget-row-title">
                  <Link href={`/contacts/${lead.id}`}>
                    {[lead.first_name, lead.last_name]
                      .filter(Boolean)
                      .join(" ") || lead.email}
                  </Link>
                </p>
                <p className="widget-row-meta muted small">
                  {lead.email ?? lead.phone ?? "(sin contacto)"}
                </p>
              </div>
              {currentUserId ? (
                <button
                  type="button"
                  className="button small secondary"
                  onClick={() => assignToMe(lead)}
                  title="Asignar a mí"
                >
                  Asignarme
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}
