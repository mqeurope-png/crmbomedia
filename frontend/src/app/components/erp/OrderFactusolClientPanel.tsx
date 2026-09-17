"use client";

import { useCallback, useEffect, useState } from "react";
import { getCurrentUser, type User } from "../../lib/api";
import { getCompany, mergeCompanies, type Company } from "../../lib/companiesApi";
import { extractErrorMessage } from "../../lib/errors";
import {
  alreadyLinkedHolder,
  completeOrderFactusolCustomer,
  ERP_EDIT_ROLES,
  getOrderFactusolCustomer,
  linkOrderFactusolCompany,
  type AlreadyLinkedHolder,
  type OrderFactusolCustomer,
  type OrderFactusolCustomerFields,
  type OrderFactusolCustomerSource,
} from "../../lib/erpApi";
import { CompanyFactusolPanel } from "./CompanyFactusolPanel";

/** Lote 4 / Lote 6 · ficha — el cliente FACTUSOL del pedido WEB (este panel solo
 *  se monta en pedidos web: la ficha lo envuelve en `isWeb`).
 *
 *  Para CUALQUIER pedido web el cliente FACTUSOL SIEMPRE existe: la app externa
 *  (FesteWeb) lo crea/asocia y carga el pedido como F_PCL al entrar en
 *  WooCommerce. Por eso este panel NUNCA ofrece «Crear en FACTUSOL» (sería un
 *  duplicado). Dos caminos:
 *  - con la EMPRESA CRM ya VINCULADA al cliente FACTUSOL: se reutiliza tal cual
 *    el `CompanyFactusolPanel` de la ficha de empresa (nº F_CLI, «Traer datos»,
 *    régimen…), resuelto por `company_id` (no ofrece crear porque ya está
 *    vinculada);
 *  - sin empresa, o con la empresa SIN vincular, el cliente se resuelve por el
 *    CLIPCL del F_PCL (o, en su defecto, el CLIFAC de la factura / el CLIALB del
 *    albarán); se muestra la ficha F_CLI, se ofrece «Vincular empresa a este
 *    cliente» (al CODCLI exacto) y se deja completar lo que falte (NIF). Nunca
 *    inventa datos del cliente. */
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
    return (
      <OrderFactusolCompanyPanel orderId={orderId} companyId={companyId} onChanged={onChanged} />
    );
  }
  return <OrderFactusolCodePanel orderId={orderId} onChanged={onChanged} />;
}

/** Con empresa CRM: si YA está vinculada al cliente FACTUSOL se reutiliza el
 *  panel de la ficha de empresa; si NO lo está (pedido web sin vincular), se
 *  resuelve por el F_PCL y se ofrece vincular — jamás crear (lo duplicaría). */
function OrderFactusolCompanyPanel({
  orderId,
  companyId,
  onChanged,
}: {
  orderId: string;
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

  // Empresa aún SIN vincular a FACTUSOL: en un pedido web nunca se crea el
  // cliente (ya existe, cargado por FesteWeb) — se resuelve por el F_PCL y se
  // ofrece vincular la empresa a ese CODCLI exacto.
  if (!company.factusol_company_id) {
    return (
      <OrderFactusolCodePanel
        orderId={orderId}
        onChanged={() => { reload(); onChanged?.(); }}
      />
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
  pedido_cliente: "Localizado por el pedido de cliente FACTUSOL (CLIPCL)",
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
  const [linking, setLinking] = useState(false);
  const [form, setForm] = useState<OrderFactusolCustomerFields>({});
  const [completing, setCompleting] = useState(false);
  // Lote 8 · B2 — al vincular, el CODCLI ya lo tiene OTRA empresa: se ofrece
  // fusionar la empresa del pedido EN aquélla (sin duplicar).
  const [mergeOffer, setMergeOffer] = useState<AlreadyLinkedHolder | null>(null);
  const [merging, setMerging] = useState(false);

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

  async function confirmarVincular() {
    setLinking(true);
    setError(null);
    setNotice(null);
    setMergeOffer(null);
    try {
      const r = await linkOrderFactusolCompany(orderId);
      setNotice(`Empresa vinculada al cliente FACTUSOL nº ${r.codcli}.`);
      reload();
      onChanged?.();
    } catch (e) {
      // Lote 8 · B2 — si el CODCLI ya lo tiene OTRA empresa CRM, no se bloquea:
      // se ofrece fusionar la empresa del pedido EN aquélla.
      const holder = alreadyLinkedHolder(e);
      if (holder && holder.company_id !== data?.company_id) {
        setMergeOffer(holder);
      } else {
        setError(extractErrorMessage(e, "No se pudo vincular la empresa al cliente FACTUSOL."));
      }
    } finally {
      setLinking(false);
    }
  }

  /** Lote 8 · B2 — fusiona la empresa del pedido (origen, se archiva) EN la que
   *  ya tiene el vínculo (superviviente): los pedidos —incluido éste— se
   *  reasignan y el CODCLI queda en una sola ficha. Luego se recarga el pedido,
   *  que pasa a estar vinculado. */
  async function fusionarConTitular() {
    if (!mergeOffer?.company_id || !data?.company_id) return;
    setMerging(true);
    setError(null);
    setNotice(null);
    try {
      await mergeCompanies(data.company_id, mergeOffer.company_id);
      setMergeOffer(null);
      setNotice(
        `Empresa fusionada en «${mergeOffer.company_name ?? "la empresa vinculada"}» `
        + "(el CODCLI queda en una sola ficha).",
      );
      reload();
      onChanged?.();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo fusionar con la empresa vinculada."));
    } finally {
      setMerging(false);
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
          Sin cliente FACTUSOL: este pedido no tiene un pedido de cliente (F_PCL),
          ni empresa vinculada en el CRM, ni factura ni albarán con los que
          localizarlo.
        </p>
      </section>
    );
  }

  const c = data.cliente;
  const completable = data.missing.filter((f) => MISSING_LABELS[f]);
  // «Vincular empresa a este cliente»: hay empresa en el pedido pero el CODCLI
  // NO viene de su vínculo CRM (source ≠ company), así que aún no está enlazada
  // a ESTE cliente FACTUSOL. Se vincula al CODCLI exacto (nunca se crea).
  const canLink = canEdit && !!data.company_id && data.source !== "company";

  return (
    <section className="erp-flow-panel" aria-label="Cliente FACTUSOL">
      <h3>Cliente FACTUSOL</h3>
      {error ? <p className="form-error">{error}</p> : null}
      {notice ? <p className="form-info" role="status">{notice}</p> : null}

      <p><span className="badge ok">Cliente FACTUSOL nº {data.codcli}</span></p>
      {data.source ? (
        <p className="muted small">{SOURCE_LABEL[data.source]}.</p>
      ) : null}

      {canLink ? (
        <p className="muted small">
          La empresa del pedido aún no está vinculada a este cliente de FACTUSOL.{" "}
          <button type="button" className="button small" disabled={linking}
                  title="Vincula la empresa del CRM a este cliente FACTUSOL (CODCLI exacto). No crea nada en FACTUSOL."
                  onClick={confirmarVincular}>
            {linking ? "Vinculando…" : "Vincular empresa a este cliente"}
          </button>
        </p>
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

      {mergeOffer ? (
        <div className="modal-overlay" role="dialog" aria-modal="true"
             aria-label="Fusionar con la empresa vinculada">
          <div className="modal-dialog">
            <h2>Ese cliente FACTUSOL ya está vinculado</h2>
            <p>
              El cliente FACTUSOL nº {data.codcli} ya está vinculado a la empresa{" "}
              <strong>{mergeOffer.company_name ?? "otra empresa"}</strong>. Un
              CODCLI solo puede estar en una ficha.
            </p>
            <p className="form-error" role="alert">
              Puedes <strong>fusionar la empresa del pedido en «
              {mergeOffer.company_name ?? "la empresa vinculada"}»</strong>: sus
              pedidos (incluido éste), contactos, tareas y actividad pasan a la
              otra ficha, la empresa del pedido se archiva (reversible: no se
              borra) y el CODCLI queda en una sola empresa.
            </p>
            <div className="modal-actions">
              <button type="button" className="button secondary" disabled={merging}
                      onClick={() => setMergeOffer(null)}>
                Cancelar
              </button>
              <button type="button" className="button danger" disabled={merging}
                      onClick={fusionarConTitular}>
                {merging ? "Fusionando…" : "Fusionar con esa empresa"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
