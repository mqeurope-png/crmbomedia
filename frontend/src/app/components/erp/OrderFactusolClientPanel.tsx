"use client";

import { useCallback, useEffect, useState } from "react";
import { getCurrentUser, type User } from "../../lib/api";
import { getCompany, type Company } from "../../lib/companiesApi";
import { extractErrorMessage } from "../../lib/errors";
import {
  completeOrderFactusolCustomer,
  ERP_EDIT_ROLES,
  getOrderFactusolCustomer,
  type OrderFactusolCustomer,
  type OrderFactusolCustomerFields,
  type OrderFactusolCustomerSource,
} from "../../lib/erpApi";
import { CompanyFactusolPanel } from "./CompanyFactusolPanel";

/** Lote 4 · ficha — el cliente FACTUSOL del pedido, también en los pedidos WEB.
 *
 *  Dos caminos, sin regresar ninguno:
 *  - con EMPRESA CRM vinculada (`companyId`): se reutiliza tal cual el
 *    `CompanyFactusolPanel` de la ficha de empresa (nº F_CLI, «Traer datos»,
 *    régimen…), resuelto por `company_id`;
 *  - sin empresa (típico de un pedido web) el cliente EXISTE igual: el pedido
 *    sabe su CODCLI por el CLIFAC de la factura o el CLIALB del albarán. El
 *    backend lo resuelve y aquí se muestra la ficha F_CLI y se deja completar lo
 *    que falte (NIF) con confirmación. Nunca inventa datos del cliente. */
export function OrderFactusolClientPanel({
  orderId,
  companyId,
  onChanged,
}: {
  orderId: string;
  companyId: string | null;
  /** Tras vincular / traer datos / completar: la ficha recarga el pedido. */
  onChanged?: () => void;
}) {
  if (companyId) {
    return <OrderFactusolCompanyPanel companyId={companyId} onChanged={onChanged} />;
  }
  return <OrderFactusolCodePanel orderId={orderId} onChanged={onChanged} />;
}

/** Con empresa CRM vinculada: envoltorio del panel de la ficha de empresa. */
function OrderFactusolCompanyPanel({
  companyId,
  onChanged,
}: {
  companyId: string;
  onChanged?: () => void;
}) {
  const [company, setCompany] = useState<Company | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    getCompany(companyId)
      .then((c) => { setCompany(c); setError(null); })
      .catch((e) => setError(extractErrorMessage(e, "No se pudo cargar la empresa del pedido.")));
  }, [companyId]);

  useEffect(() => { reload(); }, [reload]);

  if (!company) {
    return (
      <section className="erp-flow-panel" aria-label="Cliente FACTUSOL">
        <h3>Cliente FACTUSOL</h3>
        <p className="muted small">{error ?? "Cargando el cliente FACTUSOL…"}</p>
      </section>
    );
  }

  return (
    <CompanyFactusolPanel
      company={company}
      onLinked={() => { reload(); onChanged?.(); }}
      onPulled={() => { reload(); onChanged?.(); }}
    />
  );
}

const SOURCE_LABEL: Record<OrderFactusolCustomerSource, string> = {
  company: "Vinculado por la empresa del CRM",
  factura: "Localizado por el cliente de la factura (CLIFAC)",
  albaran: "Localizado por el cliente del albarán (CLIALB)",
};

/** Etiquetas de los campos completables (mismo orden que el backend). */
const MISSING_LABELS: Record<string, string> = {
  nombre: "Nombre", nif: "NIF", direccion: "Dirección",
  ciudad: "Ciudad", cp: "CP", provincia: "Provincia",
};

/** Sin empresa CRM: cliente F_CLI resuelto por CODCLI/CLIFAC, con opción de
 *  completar lo que falte (NIF). */
function OrderFactusolCodePanel({
  orderId,
  onChanged,
}: {
  orderId: string;
  onChanged?: () => void;
}) {
  const [user, setUser] = useState<User | null>(null);
  const [data, setData] = useState<OrderFactusolCustomer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<OrderFactusolCustomerFields>({});
  const [completing, setCompleting] = useState(false);

  const canEdit = !!user && (ERP_EDIT_ROLES as readonly string[]).includes(user.role);

  const reload = useCallback(() => {
    getOrderFactusolCustomer(orderId)
      .then((d) => { setData(d); setError(null); })
      .catch((e) => setError(extractErrorMessage(e, "No se pudo leer el cliente FACTUSOL del pedido.")));
  }, [orderId]);

  useEffect(() => { reload(); }, [reload]);
  useEffect(() => { getCurrentUser().then(setUser).catch(() => undefined); }, []);

  async function confirmarCompletar() {
    const entered: OrderFactusolCustomerFields = Object.fromEntries(
      Object.entries(form)
        .map(([k, v]) => [k, (v ?? "").trim()])
        .filter(([, v]) => v),
    );
    if (Object.keys(entered).length === 0) {
      setError("Escribe al menos un dato para completar.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await completeOrderFactusolCustomer(orderId, entered);
      const cols = Object.keys(r.written).join(", ");
      setNotice(r.changed
        ? `Datos completados en FACTUSOL cliente nº ${r.codcli} (${cols}).`
        : `La ficha de FACTUSOL nº ${r.codcli} ya estaba completa: nada que escribir.`);
      setCompleting(false);
      setForm({});
      reload();
      onChanged?.();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron completar los datos en FACTUSOL."));
    } finally {
      setBusy(false);
    }
  }

  if (!data) {
    return (
      <section className="erp-flow-panel" aria-label="Cliente FACTUSOL">
        <h3>Cliente FACTUSOL</h3>
        <p className="muted small">{error ?? "Cargando el cliente FACTUSOL…"}</p>
      </section>
    );
  }

  if (!data.found || !data.cliente) {
    return (
      <section className="erp-flow-panel" aria-label="Cliente FACTUSOL">
        <h3>Cliente FACTUSOL</h3>
        {error ? <p className="form-error">{error}</p> : null}
        <p className="muted small">
          Sin cliente FACTUSOL: este pedido no tiene empresa vinculada en el CRM,
          ni factura ni albarán con los que localizarlo.
        </p>
      </section>
    );
  }

  const c = data.cliente;
  const completable = data.missing.filter((f) => MISSING_LABELS[f]);

  return (
    <section className="erp-flow-panel" aria-label="Cliente FACTUSOL">
      <h3>Cliente FACTUSOL</h3>
      {error ? <p className="form-error">{error}</p> : null}
      {notice ? <p className="form-info" role="status">{notice}</p> : null}

      <p><span className="badge ok">Cliente FACTUSOL nº {data.codcli}</span></p>
      {data.source ? (
        <p className="muted small">{SOURCE_LABEL[data.source]}.</p>
      ) : null}

      <table className="data-table">
        <tbody>
          <tr><td>Nombre</td><td>{c.nombre || c.nofcli || "—"}</td></tr>
          <tr><td>NIF</td><td>{c.nif || "—"}</td></tr>
          {c.regime_label ? (
            <tr><td>Régimen de IVA</td><td>{c.regime_label}</td></tr>
          ) : null}
        </tbody>
      </table>

      {completable.length > 0 ? (
        <p className="muted small">
          Faltan datos en la ficha de FACTUSOL:{" "}
          <strong>{completable.map((f) => MISSING_LABELS[f]).join(", ")}</strong>.
          {canEdit && !completing ? (
            <>
              {" "}
              <button type="button" className="button small" disabled={busy}
                      title="Completa en FACTUSOL (F_CLI) solo los datos que escribas; pide confirmación"
                      onClick={() => setCompleting(true)}>
                Completar datos
              </button>
            </>
          ) : null}
        </p>
      ) : null}

      {completing ? (
        <div className="modal-overlay" role="dialog" aria-modal="true"
             aria-label="Completar datos del cliente FACTUSOL">
          <div className="modal-dialog">
            <h2>Completar datos en FACTUSOL</h2>
            <p className="form-error" role="alert">
              Esto escribirá en FACTUSOL (cliente nº {data.codcli}) SOLO los datos
              que rellenes aquí. No se inventa ni se borra nada.
            </p>
            {completable.map((field) => (
              <label key={field} className="field">
                <span>{MISSING_LABELS[field]}</span>
                <input
                  type="text"
                  value={(form as Record<string, string>)[field] ?? ""}
                  onChange={(e) => setForm((f) => ({ ...f, [field]: e.target.value }))}
                />
              </label>
            ))}
            <div className="modal-actions">
              <button type="button" className="button secondary" disabled={busy}
                      onClick={() => { setCompleting(false); setForm({}); }}>
                Cancelar
              </button>
              <button type="button" className="button" disabled={busy}
                      onClick={confirmarCompletar}>
                {busy ? "Guardando…" : "Guardar en FACTUSOL"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
