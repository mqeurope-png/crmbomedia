"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import { printShippingFile } from "../../lib/erpApi";
import {
  geneiCreateShipment,
  geneiDeleteShipment,
  geneiFetchLabel,
  geneiPay,
  geneiPrefill,
  geneiPrices,
  geneiRefresh,
  geneiStateTone,
  carrierDate,
  carrierStepTone,
  getCustomerEmailPreview,
  sendCustomerEmail,
  type CustomerEmailPreview,
  type CustomerEmailStatus,
  type GeneiAgencyOption,
  type GeneiDestination,
  type GeneiPackage,
  type GeneiPrefill,
  type CarrierEvent,
  type GeneiState,
} from "../../lib/geneiApi";

/** Envío con Genei (Cola SAT y ficha). Si NO hay envío, propone crearlo
 *  (comparador de agencias); si ya lo hay, NO vuelve a ofrecer «Crear»:
 *  enseña el envío (estado, agencia, seguimiento), «Pagar y tramitar» si está
 *  pendiente de pago, y la etiqueta —descargar e imprimir en un clic— solo
 *  cuando el envío ya está tramitado (Genei estado 1+). */
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
  // La etiqueta solo existe con el envío TRAMITADO (Genei estado 1+); antes,
  // Genei responde con un error: no se ofrece.
  const labelReady = !!state.label_available;

  /** Etiqueta: la trae de Genei (queda adjunta en «Documentos de envío») y
   *  lanza la impresión en el MISMO clic. */
  async function onLabel() {
    setBusy(true); setError(null); setNotice(null);
    try {
      const r = await geneiFetchLabel(orderId);
      setState(r.state);
      await printShippingFile(r.file);
      setNotice("Etiqueta enviada a imprimir (queda en «Documentos de envío»).");
      onChanged?.();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo traer la etiqueta de Genei."));
    } finally {
      setBusy(false);
    }
  }

  /** «Crear envío»: antes de abrir el modal se completa el destino (dirección,
   *  teléfono, email) — si falta algo, el backend lo lee de FACTUSOL. */
  async function onOpenCreate() {
    setBusy(true); setError(null); setNotice(null);
    try {
      const p = await geneiPrefill(orderId, { completar: true });
      setPrefill(p);
      setState(p.state ?? {});
      if (p.state?.shipment_code) return;   // alguien lo creó entretanto
      setCreating(true);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron preparar los datos del envío."));
    } finally {
      setBusy(false);
    }
  }

  async function onRefresh() {
    setBusy(true); setError(null); setNotice(null);
    try {
      const r = await geneiRefresh(orderId);
      setState(r.state);
      // Manda el último escaneo real del transportista, si Genei ya lo tiene.
      setNotice(`Estado actualizado: ${r.state.carrier_status ?? r.summary.state_label}.`);
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
              <span className="v mono">
                {state.tracking_url ? (
                  <a href={state.tracking_url} target="_blank" rel="noopener noreferrer">
                    {state.tracking}
                  </a>
                ) : state.tracking}
              </span></div>
          ) : null}
          {state.carrier_status ? (
            /* Último escaneo REAL del transportista (Genei `/tracking`), tal cual. */
            <div className="erp-flow-kv"><span className="k">Transportista</span>
              <span className="v">
                <span className={`badge ${carrierStepTone(state.carrier_step)}`}>
                  {state.carrier_status}
                </span>
                {state.carrier_status_at ? (
                  <span className="muted small"> · {carrierDate(state.carrier_status_at)}</span>
                ) : null}
              </span></div>
          ) : null}
          <CarrierHistory events={state.carrier_events ?? []} />
          <CustomerEmailBlock
            orderId={orderId}
            status={state.customer_email ?? null}
            hasTracking={Boolean(state.tracking)}
            canManage={canManage}
            onSent={(next) => { setState(next); onChanged?.(); }}
          />
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
          {canManage && !labelReady ? (
            <p className="muted small" role="note">
              La etiqueta estará disponible tras pagar y tramitar el envío.
            </p>
          ) : null}
          {canManage ? (
            <div className="erp-genei-actions">
              <button type="button" className="button small" disabled={busy || !labelReady}
                      title={labelReady ? "Descarga la etiqueta y abre el diálogo de imprimir"
                        : "La etiqueta estará disponible tras pagar y tramitar el envío"}
                      onClick={() => void onLabel()}>
                🖨 Imprimir etiqueta
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
                    onClick={() => void onOpenCreate()}>
              Crear envío con Genei
            </button>
          ) : canManage ? (
            /* Regla de negocio: el envío solo se crea con el pedido embalado. */
            <p className="muted small" role="note">
              Embala el pedido primero (debe estar en «Embalados») para crear el envío.
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
          pendiente de pago: págalo con «Pagar y tramitar».
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

/** Historial del transportista (más reciente arriba), plegado. */
function CarrierHistory({ events }: { events: CarrierEvent[] }) {
  if (!events.length) return null;
  const recientes = [...events].reverse();
  return (
    <details className="erp-genei-events">
      <summary>Historial del transportista ({events.length})</summary>
      <ol aria-label="Historial del transportista">
        {recientes.map((e, i) => (
          <li key={`${e.fecha}-${i}`}>
            <span className="mono muted">{carrierDate(e.fecha)}</span>{" "}
            <span className={`badge ${carrierStepTone(e.step)}`}>{e.descripcion}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}

const EMAIL_LANGS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "es", label: "Español" }, { value: "en", label: "English" },
  { value: "de", label: "Deutsch" }, { value: "fr", label: "Français" },
  { value: "nl", label: "Nederlands" },
];
const LANG_NAME: Record<string, string> = {
  es: "español", en: "inglés", de: "alemán", fr: "francés", nl: "neerlandés",
};

/** Texto del estado del aviso de envío al cliente. */
export function customerEmailLine(st: CustomerEmailStatus | null, hasTracking: boolean): string {
  if (!st || !st.status) {
    return "Aviso al cliente: no se envía solo en este envío (creado antes o con el aviso apagado).";
  }
  switch (st.status) {
    case "sent":
      return `Aviso al cliente enviado${st.sent_at ? ` el ${carrierDate(st.sent_at)}` : ""}`
        + `${st.to ? ` a ${st.to}` : ""}${st.lang ? ` en ${LANG_NAME[st.lang] ?? st.lang}` : ""}`
        + `${st.from ? ` desde ${st.from}` : ""}.`;
    case "pending":
      return hasTracking
        ? "Aviso al cliente: se enviará en un momento."
        : "Aviso al cliente: se enviará solo en cuanto el envío tenga nº de seguimiento.";
    case "sending":
      return "Aviso al cliente: enviándose…";
    case "error":
      return `Aviso al cliente: no se pudo enviar${st.error ? ` (${st.error})` : ""}. Puedes enviarlo a mano.`;
    case "disabled":
      return "Aviso al cliente: desactivado (Ajustes → Envíos). Puedes enviarlo a mano.";
    default:
      return "";
  }
}

/** Aviso de envío al cliente (lo manda BoHub, en su idioma): estado y
 *  «Enviar / Reenviar aviso al cliente» con la vista previa. Lo usan el envío
 *  de Genei y el de OTRO courier (`ExternalShipmentSection`). */
export function CustomerEmailBlock({
  orderId, status, hasTracking, canManage, onSent,
}: {
  orderId: string;
  status: CustomerEmailStatus | null;
  hasTracking: boolean;
  canManage: boolean;
  onSent: (state: GeneiState) => void;
}) {
  const [open, setOpen] = useState(false);
  const sent = status?.status === "sent";
  return (
    <div className="erp-genei-customer-email">
      <p className={`small ${status?.status === "error" ? "form-error" : "muted"}`}
         role="status" aria-label="Aviso de envío al cliente">
        ✉ {customerEmailLine(status, hasTracking)}
      </p>
      {canManage ? (
        <button type="button" className="button small secondary" disabled={!hasTracking}
                title={hasTracking ? undefined : "Aún no hay nº de seguimiento"}
                onClick={() => setOpen(true)}>
          {sent ? "Reenviar aviso al cliente" : "Enviar aviso al cliente"}
        </button>
      ) : null}
      {open ? (
        <CustomerEmailModal orderId={orderId} onClose={() => setOpen(false)}
                            onSent={(next) => { setOpen(false); onSent(next); }} />
      ) : null}
    </div>
  );
}

function CustomerEmailModal({
  orderId, onClose, onSent,
}: {
  orderId: string;
  onClose: () => void;
  onSent: (state: GeneiState) => void;
}) {
  const [preview, setPreview] = useState<CustomerEmailPreview | null>(null);
  const [lang, setLang] = useState<string | undefined>(undefined);
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    getCustomerEmailPreview(orderId, lang)
      .then((p) => {
        if (!alive) return;
        setPreview(p);
        setTo((prev) => prev || p.to);
      })
      .catch((e) => { if (alive) setError(extractErrorMessage(e, "No se pudo preparar el aviso.")); });
    return () => { alive = false; };
  }, [orderId, lang]);

  async function send() {
    setBusy(true); setError(null);
    try {
      const r = await sendCustomerEmail(orderId, { to: to.trim() || undefined, lang });
      onSent(r.state);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo enviar el aviso."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="Aviso de envío al cliente">
      <div className="modal-dialog erp-modal">
        <h2>Aviso de envío al cliente</h2>
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        {!preview ? <p className="muted">Preparando…</p> : (
          <>
            <label className="field">
              <span>Para</span>
              <input type="email" value={to} aria-label="Para"
                     onChange={(e) => setTo(e.target.value)} />
            </label>
            <label className="field">
              <span>Idioma</span>
              <select aria-label="Idioma del aviso" value={lang ?? preview.lang}
                      onChange={(e) => setLang(e.target.value)}>
                {EMAIL_LANGS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
              </select>
            </label>
            <p className="muted small">
              Desde <span className="mono">{preview.from_alias}</span>
              {preview.from_alias_source === "tienda" ? " (remitente de la tienda)" : " (por idioma)"}
            </p>
            <p><strong>{preview.subject}</strong></p>
            <pre className="erp-genei-email-body">{preview.body_text}</pre>
          </>
        )}
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose} disabled={busy}>
            Cancelar
          </button>
          <button type="button" className="button" onClick={() => void send()}
                  disabled={busy || !preview || !to.trim() || (preview?.missing ?? []).length > 0}>
            {busy ? "Enviando…" : "Enviar aviso"}
          </button>
        </div>
      </div>
    </div>
  );
}

