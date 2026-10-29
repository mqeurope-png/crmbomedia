"use client";

import { useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import { ModalCloseButton } from "../ModalCloseButton";
import { useModalBehaviour } from "../useModalBehaviour";
import {
  crearClienteNuevo,
  previewClienteNuevo,
  previewRevincularCliente,
  revincularCliente,
  searchFactusolCustomers,
  type ClienteNuevoDatos,
  type ClienteNuevoPlan,
  type FactusolCustomer,
  type RevincularPlan,
} from "../../lib/erpApi";

const REGIMEN: Record<string, string> = {
  nacional: "nacional (con IVA)",
  intracomunitario: "intracomunitario (sin IVA)",
  exportacion: "exportación (sin IVA)",
};

type Paso = "buscar" | "nuevo" | "previa";

const NUEVO_VACIO: ClienteNuevoDatos = {
  nombre: "", nif: "", direccion: "", cp: "", ciudad: "", provincia: "", pais: "ES", email: "",
};

/** «Cambiar cliente» del pedido: buscar el cliente en FACTUSOL por nombre o
 *  CIF y revincular, o crearlo (comprobando ANTES por CIF que no exista: un
 *  duplicado en FACTUSOL dura años). Siempre con vista previa: de qué cliente
 *  a cuál, la ficha del CRM que queda, y —si el pedido ya tiene factura
 *  emitida— los campos exactos que cambian en ella, o por qué no se puede.
 *  Queda en Auditoría. */
export function RevincularClienteModal({
  orderId, orderNumber, factura, clienteActual, onClose, onDone,
}: {
  orderId: string;
  orderNumber: string;
  /** Nº de la factura emitida, si la hay (se avisa desde el principio). */
  factura: string | null;
  clienteActual: string | null;
  onClose: () => void;
  onDone?: () => void;
}) {
  const [paso, setPaso] = useState<Paso>("buscar");
  const [q, setQ] = useState("");
  const [by, setBy] = useState<"name" | "nif">("name");
  const [resultados, setResultados] = useState<FactusolCustomer[] | null>(null);
  const [plan, setPlan] = useState<RevincularPlan | null>(null);
  const [nuevo, setNuevo] = useState<ClienteNuevoDatos>(NUEVO_VACIO);
  const [planNuevo, setPlanNuevo] = useState<ClienteNuevoPlan | null>(null);
  const [confirmo, setConfirmo] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hecho, setHecho] = useState<string | null>(null);
  const { overlayProps, requestClose } = useModalBehaviour({ onClose, disabled: busy });

  async function run<T>(fn: () => Promise<T>, fallo: string): Promise<T | null> {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(extractErrorMessage(e, fallo));
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function buscar() {
    const r = await run(() => searchFactusolCustomers(q.trim(), by), "No se pudo buscar en FACTUSOL.");
    if (r) setResultados(r);
  }

  async function elegir(codcli: string) {
    setConfirmo(false);
    const r = await run(() => previewRevincularCliente(orderId, codcli), "No se pudo preparar el cambio.");
    if (r) { setPlan(r); setPaso("previa"); }
  }

  async function comprobarNuevo() {
    setConfirmo(false);
    const r = await run(() => previewClienteNuevo(orderId, nuevo), "No se pudo comprobar el CIF en FACTUSOL.");
    if (r) setPlanNuevo(r);
  }

  async function crear() {
    const r = await run(() => crearClienteNuevo(orderId, nuevo), "No se pudo crear el cliente en FACTUSOL.");
    if (r) await elegir(r.codcli);
  }

  async function aplicar() {
    if (!plan?.cliente.codcli) return;
    const campos = plan.factura?.cambios.map((c) => c.campo) ?? [];
    const r = await run(
      () => revincularCliente(orderId, plan.cliente.codcli as string, campos),
      "No se pudo cambiar el cliente.",
    );
    if (r) {
      setHecho(`El pedido ${orderNumber} queda con «${r.company_name}» (${r.codcli})`
        + (r.escrito_en_factura ? ` y la factura ${r.factura} también, en FACTUSOL.` : ".")
        + (r.empresa_creada ? " Se ha creado su ficha en BoHub." : ""));
      onDone?.();
    }
  }

  const fac = plan?.factura ?? null;
  const facCambia = !!fac && fac.cambios.length > 0;
  const bloqueado = (facCambia && (!!fac?.bloqueo || fac?.puedes === false))
    || !!plan?.documento?.bloqueo;
  const nada = !!plan && plan.sin_cambios_crm && !facCambia;

  const title = `Cambiar el cliente · ${orderNumber}`;
  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label={title} {...overlayProps}>
      <div className="modal-dialog wide">
        <div className="modal-header">
          <h2>{title}</h2>
          <ModalCloseButton onClose={requestClose} disabled={busy} placement="header" />
        </div>
        <div className="modal-body">
          <p className="muted small">
            Ahora: <strong>{clienteActual || "sin cliente"}</strong>.
          </p>
          {factura ? (
            <p className="form-info small" role="alert">
              ⚠ Este pedido ya tiene la factura {factura} emitida: si el cliente cambia, también
              se cambia en la factura en FACTUSOL (lo verás campo a campo antes de confirmar).
            </p>
          ) : null}

          {paso === "buscar" ? (
            <div className="erp-revincular-buscar">
              <form
                className="erp-revincular-form"
                onSubmit={(e) => { e.preventDefault(); if (q.trim()) void buscar(); }}
              >
                <select aria-label="Buscar por" value={by} disabled={busy}
                        onChange={(e) => setBy(e.target.value as "name" | "nif")}>
                  <option value="name">Nombre</option>
                  <option value="nif">CIF / NIF</option>
                </select>
                <input
                  type="search" aria-label="Cliente en FACTUSOL" value={q} disabled={busy}
                  placeholder={by === "nif" ? "B12345678" : "Razón social"}
                  onChange={(e) => setQ(e.target.value)}
                />
                <button type="submit" className="button small" disabled={busy || !q.trim()}>
                  {busy ? "Buscando…" : "Buscar en FACTUSOL"}
                </button>
              </form>
              {resultados ? (
                resultados.length ? (
                  <ul className="erp-revincular-lista" aria-label="Clientes de FACTUSOL">
                    {resultados.map((c) => (
                      <li key={c.codcli ?? c.nombre ?? ""}>
                        <span>
                          <strong>{c.nombre || c.nofcli || "—"}</strong>
                          <span className="muted small">
                            {" "}· {c.codcli} · {c.nif || "sin NIF"}
                            {c.pobcli ? ` · ${c.pobcli}` : ""}
                            {c.regime_label ? ` · ${c.regime_label}` : ""}
                            {c.crm_link ? ` · en BoHub: ${c.crm_link.name}` : ""}
                          </span>
                        </span>
                        <button
                          type="button" className="button small secondary" disabled={busy || !c.codcli}
                          onClick={() => void elegir(String(c.codcli))}
                        >
                          Elegir
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted small">Ningún cliente de FACTUSOL con eso.</p>
                )
              ) : null}
              <p className="small">
                ¿No está?{" "}
                <button type="button" className="link-button" disabled={busy}
                        onClick={() => { setPaso("nuevo"); setPlanNuevo(null); setError(null); }}>
                  Crear el cliente en FACTUSOL
                </button>
              </p>
            </div>
          ) : null}

          {paso === "nuevo" ? (
            <div className="erp-revincular-nuevo">
              <p className="muted small">
                Antes de crear nada se comprueba por CIF que no exista ya en FACTUSOL.
              </p>
              <div className="modal-form erp-revincular-campos">
                {([
                  ["nombre", "Razón social"], ["nif", "CIF / NIF"], ["direccion", "Dirección"],
                  ["cp", "CP"], ["ciudad", "Población"], ["provincia", "Provincia"],
                  ["pais", "País (ES, FR, BE…)"], ["email", "Email (opcional)"],
                ] as [keyof ClienteNuevoDatos, string][]).map(([k, label]) => (
                  <label key={k}>
                    <span>{label}</span>
                    <input
                      type="text" aria-label={label} value={String(nuevo[k] ?? "")} disabled={busy}
                      onChange={(e) => { setNuevo({ ...nuevo, [k]: e.target.value }); setPlanNuevo(null); }}
                    />
                  </label>
                ))}
              </div>
              {planNuevo ? (
                planNuevo.existe ? (
                  <div className="form-info" role="alert">
                    <p>Ya hay un cliente con ese CIF en FACTUSOL: no se crea otro.</p>
                    <ul className="erp-revincular-lista">
                      {planNuevo.factusol.map((c) => (
                        <li key={c.codcli ?? ""}>
                          <span><strong>{c.nombre}</strong> · {c.codcli} · {c.nif}</span>
                          <button type="button" className="button small" disabled={busy || !c.codcli}
                                  onClick={() => void elegir(String(c.codcli))}>
                            Usar este
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : (
                  <div className="erp-revincular-previa" aria-label="Cliente que se creará">
                    {planNuevo.crm.length ? (
                      <p className="form-info small">
                        En BoHub ya hay una empresa con ese CIF ({planNuevo.crm.map((c) => c.nombre).join(", ")}):
                        al revincular se usará esa ficha.
                      </p>
                    ) : null}
                    <p>Se creará en FACTUSOL (F_CLI) con estos campos, tal cual
                      {planNuevo.regime_label ? <> · régimen de IVA <strong>{planNuevo.regime_label}</strong></> : null}:</p>
                    <table className="erp-revincular-tabla">
                      <tbody>
                        {planNuevo.campos.map((c) => (
                          <tr key={c.campo}><th scope="row">{c.campo}</th><td>{c.valor}</td></tr>
                        ))}
                      </tbody>
                    </table>
                    <label className="field-toggle">
                      <input type="checkbox" aria-label="Confirmo crear el cliente" checked={confirmo}
                             disabled={busy} onChange={(e) => setConfirmo(e.target.checked)} />
                      <span>Confirmo que quiero crear este cliente en FACTUSOL.</span>
                    </label>
                  </div>
                )
              ) : null}
            </div>
          ) : null}

          {paso === "previa" && plan ? (
            <div className="erp-revincular-previa" aria-label="Vista previa del cambio">
              <p>
                De <strong>{plan.actual?.nombre ?? "sin cliente"}</strong>
                {plan.actual?.codcli ? ` (${plan.actual.codcli})` : ""} a{" "}
                <strong>{plan.cliente.nombre}</strong> ({plan.cliente.codcli} · {plan.cliente.nif || "sin NIF"}
                {plan.cliente.regime ? ` · ${REGIMEN[plan.cliente.regime] ?? plan.cliente.regime}` : ""}).
              </p>
              <p className="small">
                {plan.empresa.crear
                  ? `En BoHub se creará su ficha «${plan.empresa.nombre}» con sus datos de FACTUSOL.`
                  : `En BoHub queda vinculado a la ficha «${plan.empresa.nombre}».`}
              </p>
              {fac ? (
                facCambia ? (
                  <>
                    <p>En la factura <strong>{fac.numero}</strong> de FACTUSOL cambiará:</p>
                    <table className="erp-revincular-tabla">
                      <thead><tr><th>Campo</th><th>Ahora</th><th>Quedará</th></tr></thead>
                      <tbody>
                        {fac.cambios.map((c) => (
                          <tr key={c.campo}>
                            <th scope="row">{c.label}</th><td>{c.antes || "—"}</td><td>{c.despues || "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {fac.bloqueo ? (
                      <p className="form-error" role="alert">No se puede cambiar la factura: {fac.bloqueo}</p>
                    ) : null}
                    {fac.puedes === false ? (
                      <p className="form-error" role="alert">
                        Cambiar el cliente de una factura emitida es de quien emite facturas.
                      </p>
                    ) : null}
                    {fac.enviada ? (
                      <p className="form-info small" role="alert">
                        ⚠ La factura ya se envió al cliente: el PDF que tiene lleva el cliente anterior.
                      </p>
                    ) : null}
                    <ul className="erp-revincular-avisos">
                      {fac.avisos.map((a) => <li key={a}>{a}</li>)}
                    </ul>
                  </>
                ) : (
                  <p className="muted small">La factura {fac.numero} ya va a nombre de este cliente: no cambia.</p>
                )
              ) : null}
              {plan.documento?.bloqueo ? (
                <p className="form-error" role="alert">{plan.documento.bloqueo}</p>
              ) : null}
              {plan.avisos.map((a) => <p key={a} className="form-info small">{a}</p>)}
              {nada ? <p className="form-info">El pedido ya está con ese cliente.</p> : null}
              {!bloqueado && !nada && !hecho ? (
                <label className="field-toggle">
                  <input type="checkbox" aria-label="Confirmo el cambio de cliente" checked={confirmo}
                         disabled={busy} onChange={(e) => setConfirmo(e.target.checked)} />
                  <span>
                    {facCambia
                      ? `Confirmo que quiero cambiar el cliente del pedido y de la factura ${fac?.numero}.`
                      : "Confirmo que quiero cambiar el cliente del pedido."}
                  </span>
                </label>
              ) : null}
            </div>
          ) : null}

          {error ? <p className="form-error">{error}</p> : null}
          {hecho ? <p className="form-success" role="status">{hecho}</p> : null}
        </div>
        <div className="modal-actions">
          {paso !== "buscar" && !hecho ? (
            <button type="button" className="button secondary" disabled={busy}
                    onClick={() => { setPaso("buscar"); setPlan(null); setConfirmo(false); setError(null); }}>
              Volver a buscar
            </button>
          ) : null}
          <button type="button" className="button secondary" onClick={onClose} disabled={busy}>
            {hecho ? "Cerrar" : "Cancelar"}
          </button>
          {paso === "nuevo" && !planNuevo ? (
            <button type="button" className="button" onClick={() => void comprobarNuevo()}
                    disabled={busy || nuevo.nombre.trim().length < 2 || nuevo.nif.trim().length < 3}>
              {busy ? "Comprobando…" : "Comprobar CIF"}
            </button>
          ) : null}
          {paso === "nuevo" && planNuevo && !planNuevo.existe ? (
            <button type="button" className="button" onClick={() => void crear()} disabled={busy || !confirmo}>
              {busy ? "Creando…" : "Crear cliente"}
            </button>
          ) : null}
          {paso === "previa" && plan && !bloqueado && !nada && !hecho ? (
            <button type="button" className="button" onClick={() => void aplicar()} disabled={busy || !confirmo}>
              {busy ? "Cambiando…" : "Cambiar el cliente"}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
