"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  geneiCreateShipment,
  geneiDeleteShipment,
  geneiFetchLabel,
  geneiPay,
  geneiPrefill,
  geneiPrices,
  geneiRefresh,
  geneiStateTone,
  type GeneiAgencyOption,
  type GeneiDestination,
  type GeneiPackage,
  type GeneiPrefill,
  type GeneiState,
} from "../../lib/geneiApi";

/** Envío con Genei desde la Cola SAT (PR-1). Vive dentro del cajón «Envío y
 *  seguimiento»: si no hay envío, propone crearlo (comparador de agencias); si
 *  ya lo hay, enseña el estado y permite traer la etiqueta, actualizar el estado
 *  y eliminarlo. El PAGO se hace a mano en Genei (PR-2 añade el botón). */
export function GeneiShipmentSection({
  orderId,
  canManage,
  onChanged,
}: {
  orderId: string;
  /** Rol de taller/oficina puede crear/gestionar el envío. */
  canManage: boolean;
  /** Tras crear / etiqueta / eliminar, la ficha recarga el pedido. */
  onChanged?: () => void;
}) {
  const [prefill, setPrefill] = useState<GeneiPrefill | null>(null);
  const [state, setState] = useState<GeneiState>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const p = await geneiPrefill(orderId);
      setPrefill(p);
      setState(p.state ?? {});
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo cargar el envío de Genei."));
    } finally {
      setLoading(false);
    }
  }, [orderId]);

  useEffect(() => { void load(); }, [load]);

  const hasShipment = !!state.shipment_code;

  async function onLabel() {
    setBusy(true); setError(null); setNotice(null);
    try {
      const r = await geneiFetchLabel(orderId);
      setState(r.state);
      setNotice("Etiqueta descargada y adjunta en «Documentos de envío».");
      onChanged?.();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo traer la etiqueta de Genei."));
    } finally {
      setBusy(false);
    }
  }

  async function onRefresh() {
    setBusy(true); setError(null); setNotice(null);
    try {
      const r = await geneiRefresh(orderId);
      setState(r.state);
      setNotice(`Estado actualizado: ${r.summary.state_label}.`);
      onChanged?.();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo actualizar el estado en Genei."));
    } finally {
      setBusy(false);
    }
  }

  async function onPay() {
    setBusy(true); setError(null); setNotice(null);
    try {
      const r = await geneiPay(orderId);
      setState(r.state);
      setNotice(`Pagado y tramitado en Genei: ${r.summary.state_label}.`);
      onChanged?.();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo pagar el envío en Genei."));
    } finally {
      setBusy(false);
    }
  }

  async function onDelete() {
    setBusy(true); setError(null); setNotice(null);
    try {
      await geneiDeleteShipment(orderId);
      setState({});
      setNotice("Envío eliminado en Genei.");
      onChanged?.();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo eliminar el envío en Genei."));
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <p className="muted small">Cargando Genei…</p>;
  if (!prefill) return null;

  if (!prefill.configured) {
    return (
      <p className="muted small" role="note">
        Genei no está configurado. Añade las credenciales en <strong>Ajustes → Envíos (Genei)</strong>.
      </p>
    );
  }

  return (
    <section className="erp-genei" aria-label="Envío con Genei">
      <div className="erp-genei-head">
        <h4>Envío con Genei</h4>
        {hasShipment ? (
          <span className={`badge ${geneiStateTone(state.state_bucket)}`}>
            {state.state_label || "En Genei"}
          </span>
        ) : null}
      </div>

      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {notice ? <p className="form-success" role="status">{notice}</p> : null}

      {hasShipment ? (
        <div className="erp-genei-state">
          <div className="erp-flow-kv"><span className="k">Envío</span>
            <span className="v mono">{state.shipment_code}</span></div>
          {state.courier ? (
            <div className="erp-flow-kv"><span className="k">Agencia</span>
              <span className="v">{state.courier}</span></div>
          ) : null}
          {state.tracking ? (
            <div className="erp-flow-kv"><span className="k">Seguimiento</span>
              <span className="v mono">{state.tracking}</span></div>
          ) : null}
          {canManage && state.state_bucket === "created" ? (
            /* PR-2: se paga por API contra el saldo de la cuenta, sin popup. Lo
               dispara SIEMPRE una persona con este botón (nunca automático). */
            <div className="erp-genei-pay">
              <p className="muted small">
                Pendiente de pago. Se paga contra el saldo de Genei, sin salir de BoHub.
              </p>
              <button type="button" className="button small" disabled={busy}
                      onClick={() => void onPay()}>
                Pagar y tramitar
              </button>
            </div>
          ) : null}
          {canManage ? (
            <div className="erp-genei-actions">
              <button type="button" className="button small" disabled={busy} onClick={() => void onLabel()}>
                Descargar etiqueta
              </button>
              <button type="button" className="button small secondary" disabled={busy}
                      onClick={() => void onRefresh()}>
                Actualizar estado
              </button>
              <button type="button" className="button small danger" disabled={busy}
                      onClick={() => void onDelete()}>
                Eliminar envío
              </button>
            </div>
          ) : null}
        </div>
      ) : (
        <div className="erp-genei-empty">
          <p className="muted small">Aún no hay envío en Genei para este pedido.</p>
          {canManage && prefill.is_packed ? (
            <button type="button" className="button small" disabled={busy}
                    onClick={() => setCreating(true)}>
              Crear envío con Genei
            </button>
          ) : canManage ? (
            /* Regla de negocio: el envío solo se crea con el pedido embalado. */
            <p className="muted small" role="note">
              Empaqueta el pedido primero (debe estar en «Listos») para crear el envío.
            </p>
          ) : null}
        </div>
      )}

      {creating ? (
        <CreateGeneiShipmentModal
          orderId={orderId}
          prefill={prefill}
          onCancel={() => setCreating(false)}
          onCreated={(newState) => {
            setState(newState);
            setCreating(false);
            setNotice("Envío creado en Genei (pendiente de pago).");
            onChanged?.();
          }}
        />
      ) : null}
    </section>
  );
}

/** Modal de creación: destino editable (prellenado), bulto y comparador de
 *  agencias (propone el preferido factible más barato del país, a domicilio). */
function CreateGeneiShipmentModal({
  orderId, prefill, onCancel, onCreated,
}: {
  orderId: string;
  prefill: GeneiPrefill;
  onCancel: () => void;
  onCreated: (state: GeneiState) => void;
}) {
  const [dest, setDest] = useState<GeneiDestination>(prefill.destination);
  // Bultos REALES del pedido (medidos por el SAT al embalar); si el pedido no
  // los trae, se cae al bulto por defecto de la config (editable).
  const [pkgs, setPkgs] = useState<GeneiPackage[]>(
    prefill.packages.length > 0 ? prefill.packages : [prefill.default_package],
  );
  const [options, setOptions] = useState<GeneiAgencyOption[] | null>(null);
  const [agencyId, setAgencyId] = useState<string | null>(null);
  const [homeOnly, setHomeOnly] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const didAutoCompare = useRef(false);

  const missing = destMissing(dest);

  // Cambiar los bultos invalida la comparativa: la agencia elegida era factible
  // para las medidas anteriores, así que se fuerza a volver a comparar.
  function invalidateComparison() {
    setOptions(null);
    setAgencyId(null);
  }
  function updatePkg(idx: number, patch: Partial<GeneiPackage>) {
    setPkgs((prev) => prev.map((p, i) => (i === idx ? { ...p, ...patch } : p)));
    invalidateComparison();
  }
  function addPkg() {
    setPkgs((prev) => [...prev, prefill.default_package]);
    invalidateComparison();
  }
  function removePkg(idx: number) {
    setPkgs((prev) => (prev.length > 1 ? prev.filter((_, i) => i !== idx) : prev));
    invalidateComparison();
  }

  const compare = useCallback(async () => {
    setBusy(true); setError(null);
    try {
      const r = await geneiPrices(orderId, { destination: dest, packages: pkgs, home_only: homeOnly });
      setOptions(r.all_options);
      setAgencyId(r.default?.agency_id ?? r.home_options[0]?.agency_id ?? null);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron consultar las agencias en Genei."));
      setOptions([]);
    } finally {
      setBusy(false);
    }
  }, [orderId, dest, pkgs, homeOnly]);

  // Autocompara al abrir si el destino ya está completo (comparador por defecto).
  useEffect(() => {
    if (!didAutoCompare.current && missing.length === 0) {
      didAutoCompare.current = true;
      void compare();
    }
  }, [missing.length, compare]);

  async function create() {
    if (!agencyId) return;
    setBusy(true); setError(null);
    try {
      const r = await geneiCreateShipment(orderId, {
        agency_id: agencyId, destination: dest, packages: pkgs,
        observations: dest.observations || null,
      });
      onCreated(r.state);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo crear el envío en Genei."));
    } finally {
      setBusy(false);
    }
  }

  const shown = homeOnly ? (options ?? []).filter((o) => o.home_delivery) : (options ?? []);

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="Crear envío con Genei">
      <div className="modal-dialog erp-modal">
        <h2>Crear envío con Genei</h2>
        <p className="muted small">
          Revisa el destino y el bulto, compara agencias y crea el envío. Nace
          pendiente de pago; el pago se hace a mano en Genei.
        </p>

        <fieldset className="erp-genei-dest" disabled={busy}>
          <legend>Destino</legend>
          <div className="form-row">
            <Field label="Nombre" value={dest.name} onChange={(v) => setDest({ ...dest, name: v })} />
            <Field label="NIF/DNI" value={dest.dni} onChange={(v) => setDest({ ...dest, dni: v })} />
          </div>
          <Field label="Dirección" value={dest.address} onChange={(v) => setDest({ ...dest, address: v })} />
          <div className="form-row">
            <Field label="Código postal" value={dest.postalCode}
                   onChange={(v) => setDest({ ...dest, postalCode: v })} />
            <Field label="Población" value={dest.town} onChange={(v) => setDest({ ...dest, town: v })} />
            <Field label="País (ISO2)" value={dest.isoCountry}
                   onChange={(v) => setDest({ ...dest, isoCountry: v.toUpperCase().slice(0, 2) })} />
          </div>
          <div className="form-row">
            <Field label="Teléfono" value={dest.phone} onChange={(v) => setDest({ ...dest, phone: v })} />
            <Field label="Email" value={dest.email} onChange={(v) => setDest({ ...dest, email: v })} />
          </div>
        </fieldset>

        <fieldset className="erp-genei-pkg" disabled={busy}>
          <legend>Bultos {pkgs.length > 1 ? `(${pkgs.length})` : ""}</legend>
          {prefill.packages.length > 0 ? (
            <p className="muted small">Prellenado con las medidas reales del embalaje; ajústalas si hace falta.</p>
          ) : null}
          {pkgs.map((p, i) => (
            <div className="form-row erp-genei-pkg-row" key={i}>
              <NumField label="Peso (kg)" value={p.weight} onChange={(v) => updatePkg(i, { weight: v })} />
              <NumField label="Alto (cm)" value={p.height} onChange={(v) => updatePkg(i, { height: v })} />
              <NumField label="Ancho (cm)" value={p.width} onChange={(v) => updatePkg(i, { width: v })} />
              <NumField label="Largo (cm)" value={p.length} onChange={(v) => updatePkg(i, { length: v })} />
              {pkgs.length > 1 ? (
                <button type="button" className="button small danger" aria-label={`Quitar bulto ${i + 1}`}
                        onClick={() => removePkg(i)}>
                  Quitar
                </button>
              ) : null}
            </div>
          ))}
          <button type="button" className="button small secondary" onClick={addPkg}>
            + Añadir bulto
          </button>
        </fieldset>

        {missing.length > 0 ? (
          <p className="form-error" role="alert">Faltan datos del destino: {missing.join(", ")}.</p>
        ) : null}
        {error ? <p className="form-error" role="alert">{error}</p> : null}

        <div className="erp-genei-compare">
          <div className="erp-genei-compare-head">
            <button type="button" className="button small secondary" disabled={busy || missing.length > 0}
                    onClick={() => void compare()}>
              {options ? "Volver a comparar" : "Comparar agencias"}
            </button>
            <label className="field-toggle">
              <input type="checkbox" checked={homeOnly}
                     onChange={(e) => setHomeOnly(e.target.checked)} />
              <span>Solo a domicilio</span>
            </label>
          </div>
          {options === null ? null : shown.length === 0 ? (
            <p className="muted small">Ninguna agencia factible para esos datos.</p>
          ) : (
            <ul className="erp-genei-agencies" aria-label="Agencias">
              {shown.map((o) => (
                <li key={o.agency_id}>
                  <label className="field-toggle">
                    <input type="radio" name="genei-agency" checked={agencyId === o.agency_id}
                           onChange={() => setAgencyId(o.agency_id)} />
                    <span>
                      <strong>{o.name || `Agencia ${o.agency_id}`}</strong>
                      {o.price != null ? ` · ${o.price.toFixed(2)} €` : ""}
                      {o.home_delivery ? "" : " · oficina"}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onCancel} disabled={busy}>
            Cancelar
          </button>
          <button type="button" className="button" disabled={busy || !agencyId || missing.length > 0}
                  onClick={() => void create()}>
            Crear envío
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, value, onChange }: {
  label: string; value: string; onChange: (v: string) => void;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input value={value} aria-label={label} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

function NumField({ label, value, onChange }: {
  label: string; value: number; onChange: (v: number) => void;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type="number" min={0} step="0.01" value={value} aria-label={label}
             onChange={(e) => onChange(Number(e.target.value) || 0)} />
    </label>
  );
}

function destMissing(d: GeneiDestination): string[] {
  const req: [keyof GeneiDestination, string][] = [
    ["name", "nombre"], ["address", "dirección"], ["postalCode", "código postal"],
    ["town", "población"], ["isoCountry", "país"],
  ];
  return req.filter(([k]) => !String(d[k] ?? "").trim()).map(([, l]) => l);
}
