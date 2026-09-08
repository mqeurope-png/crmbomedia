"use client";

import { useCallback, useEffect, useState } from "react";
import { getCurrentUser, type User } from "../../lib/api";
import {
  convertFactusolDocument,
  downloadFactusolDocumentPdf,
  ERP_EDIT_ROLES,
  getErpSettings,
  getFactusolConvertStatus,
  getFactusolDocument,
  getFactusolSeries,
  markInvoicePayment,
  saveBlob,
  waitForInvoicePaymentJob,
  type FactusolBankAccount,
  type FactusolConvertTarget,
  type FactusolCycle,
  type FactusolCycleRef,
  type FactusolDocType,
  type FactusolDocumentDetail,
  type FactusolPdfLang,
  type FactusolPdfOptions,
  type FactusolSerie,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";
import { InvoiceEmailModal } from "./InvoiceEmailModal";

const TYPE_LABELS: Record<FactusolDocType, string> = {
  pedidos: "Pedido de cliente",
  presupuestos: "Presupuesto",
  albaranes: "Albarán",
  facturas: "Factura",
};

const TARGET_LABELS: Record<FactusolConvertTarget, string> = {
  albaranes: "albarán",
  facturas: "factura",
};

/** Conversiones que ofrece cada tipo (espejo de `chain.ALLOWED_CONVERSIONS`). */
const CONVERSIONS: Partial<Record<FactusolDocType, FactusolConvertTarget[]>> = {
  presupuestos: ["albaranes", "facturas"],
  albaranes: ["facturas"],
};

/** E3-B-fix1 — etiquetas del ciclo POR TIPO, como fallback si el backend no
 *  mandó `estado_label` (espejo de `documents.CICLO_ESTADO_LABELS`). Un
 *  albarán «pendiente» está SIN FACTURAR — nunca «sin albarán». */
const CYCLE_FALLBACK_LABELS: Partial<
  Record<FactusolDocType, Record<string, string>>
> = {
  presupuestos: {
    pendiente: "Sin albarán ni factura",
    con_albaran: "Con albarán",
    facturado: "Facturado",
  },
  pedidos: {
    pendiente: "Sin albarán ni factura",
    con_albaran: "Con albarán",
    facturado: "Facturado",
  },
  albaranes: { pendiente: "Sin facturar", facturado: "Facturado" },
};

export function cycleBadge(
  docType: FactusolDocType,
  ciclo: FactusolCycle,
): { label: string; className: string } | null {
  if (!ciclo || !ciclo.estado) return null;
  const label =
    ciclo.estado_label ?? CYCLE_FALLBACK_LABELS[docType]?.[ciclo.estado];
  if (!label) return null;
  const className =
    ciclo.estado === "facturado"
      ? "badge ok"
      : ciclo.estado === "con_albaran"
        ? "badge warn"
        : "badge muted";
  return { label, className };
}

/** Acciones de conversión disponibles como BOTÓN (E3-B-fix2): SOLO cuando
 *  el documento hijo aún no existe. Si ya existe, el botón DESAPARECE —
 *  duplicar por un clic de más es inaceptable en la contabilidad. La única
 *  vía legítima de un segundo albarán es la acción discreta de «entrega
 *  parcial» (`partialDeliveryAvailable`); para facturas no hay vía: una
 *  segunda factura es un error contable, se haría en FACTUSOL escritorio. */
function availableActions(
  docType: FactusolDocType,
  ciclo: FactusolCycle,
): FactusolConvertTarget[] {
  const targets = CONVERSIONS[docType] ?? [];
  if (targets.length === 0) return [];
  if (!ciclo) {
    // Sin anotación del ciclo (best-effort del backend): se ofrecen las
    // acciones normales — el 409 anti-duplicado del backend sigue de red.
    return [...targets];
  }
  const tieneAlbaran = ciclo.albaranes.length > 0;
  const facturado = ciclo.facturas.length > 0;
  if (docType === "albaranes") {
    // Albarán facturado: nada (el aviso enlaza a la factura).
    return facturado ? [] : [...targets];
  }
  // Presupuesto/pedido: cualquier hijo retira los botones — facturado no
  // deja vía; con albarán, la factura se genera DESDE el albarán y el
  // segundo albarán solo existe como «entrega parcial».
  return tieneAlbaran || facturado ? [] : [...targets];
}

/** Enlace discreto de «entrega parcial»: presupuesto/pedido que ya tiene
 *  albarán y AÚN no está facturado (un pedido entregado en varios envíos
 *  genera varios albaranes del mismo presupuesto — caso real). */
function partialDeliveryAvailable(
  docType: FactusolDocType,
  ciclo: FactusolCycle,
): boolean {
  if (docType !== "presupuestos" && docType !== "pedidos") return false;
  if (!ciclo) return false;
  return ciclo.albaranes.length > 0 && ciclo.facturas.length === 0;
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/** E4 — idioma por defecto del PDF: el del país del cliente si se puede
 *  deducir; español si no; inglés para el resto de extranjeros. */
export function defaultPdfLang(pais: string | null | undefined): FactusolPdfLang {
  const p = (pais ?? "").trim().toLowerCase();
  if (!p || /espa|spain|^es$/.test(p)) return "es";
  if (/alem|german|deutsch|österreich|osterreich|austria/.test(p)) return "de";
  if (/franc/.test(p)) return "fr";
  if (/nederland|netherland|holanda|holland|pa[íi]ses bajos/.test(p)) return "nl";
  return "en";
}

/** Opciones del selector de idioma del PDF. */
export const PDF_LANGS: { value: FactusolPdfLang; label: string }[] = [
  { value: "es", label: "ES" },
  { value: "en", label: "EN" },
  { value: "de", label: "DE" },
  { value: "fr", label: "FR" },
  { value: "nl", label: "NL" },
];

/** E4-fix1 — de dónde viene el idioma propuesto, para que Bart sepa si es
 *  dato real o suposición. */
const PDF_LANG_SOURCE_LABELS: Record<string, string> = {
  pedido: "del pedido",
  cliente: "del cliente",
  // F1-fix2: país del cliente EN EL DOCUMENTO (CPA*) — el dato más fiable
  // tras el idioma explícito.
  pais_documento: "del país en el documento",
  // E4-fix2: deducción por el país del cliente — se distingue del idioma
  // que el cliente tiene puesto a mano («del cliente»).
  pais_cliente: "del país del cliente",
  empresa: "de la empresa emisora",
  defecto: "por defecto",
};

/** E4-fix1 — variantes de impresión por tipo (espejo de
 *  `factusol_pdf.VARIANTS_BY_TYPE`). El valor "" es el documento normal. */
const PDF_VARIANTS: Partial<Record<FactusolDocType, { value: string; label: string }[]>> = {
  facturas: [
    { value: "", label: "Factura" },
    { value: "anticipo", label: "Factura de anticipo" },
  ],
  presupuestos: [
    { value: "", label: "Presupuesto" },
    { value: "proforma", label: "Factura proforma" },
  ],
  albaranes: [
    { value: "", label: "Albarán (sin importes)" },
    { value: "valorado", label: "Albarán valorado" },
    { value: "devolucion", label: "Albarán de devolución" },
  ],
};

/** Divisas que ofrece el selector de descarga (cambian la presentación, no
 *  los importes). */
const PDF_CURRENCIES = ["EUR", "SEK", "DKK", "NOK", "USD", "GBP", "CHF"];

/** ERP-E3-A/E3-B — detalle de un documento FACTUSOL: cabecera + líneas +
 *  posición en el ciclo PRE→ALB→FAC, con las acciones de crear el siguiente
 *  documento de la cadena (albarán/factura). Los enlaces del ciclo navegan
 *  DENTRO del modal (el listado de fondo no cambia de pestaña). */
export function FactusolDocumentDetailModal({
  docType,
  serie,
  codigo,
  onClose,
  onChanged,
}: {
  docType: FactusolDocType;
  serie: number;
  codigo: number | string;
  onClose: () => void;
  /** Se llama cuando el modal CREÓ un documento (para refrescar el listado). */
  onChanged?: () => void;
}) {
  const [current, setCurrent] = useState({ docType, serie, codigo });
  const [doc, setDoc] = useState<FactusolDocumentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [convertTarget, setConvertTarget] =
    useState<FactusolConvertTarget | null>(null);
  // E3-B-fix2: la conversión abierta desde el enlace de «entrega parcial»
  // (el único camino para un segundo albarán) — cambia el texto del modal
  // de confirmación y manda `force`.
  const [convertPartial, setConvertPartial] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [created, setCreated] = useState<FactusolCycleRef | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  // E3-B-fix3: el hijo se creó pero el ORIGEN no quedó marcado como
  // convertido (ESTPRE/ESTALB) — es un AVISO, no un error de la conversión.
  const [originWarning, setOriginWarning] = useState<string | null>(null);
  // E4 — descarga de PDF: idioma (preseleccionado por la cascada del
  // backend) + su origen, variante, banco y divisa (E4-fix1).
  const [pdfLang, setPdfLang] = useState<FactusolPdfLang>("es");
  const [pdfLangSource, setPdfLangSource] = useState<string | null>(null);
  const [pdfVariant, setPdfVariant] = useState<string>("");
  const [pdfBank, setPdfBank] = useState<number>(0);
  const [pdfCurrency, setPdfCurrency] = useState<string>("EUR");
  const [pdfBusy, setPdfBusy] = useState(false);
  // Cuentas bancarias de la empresa emisora (serie) — para el selector de
  // banco de la descarga. Se cargan una vez de los ajustes del ERP.
  const [banks, setBanks] = useState<FactusolBankAccount[]>([]);
  // ERP-F1 — modal de envío de la factura por email (solo facturas).
  const [emailOpen, setEmailOpen] = useState(false);
  // ERP-F3 — marcado del cobro: confirmación (paid) + estado de la operación.
  const [payConfirm, setPayConfirm] = useState<boolean | null>(null);
  const [payBusy, setPayBusy] = useState(false);
  const [payError, setPayError] = useState<string | null>(null);
  const [payMsg, setPayMsg] = useState<string | null>(null);

  useEffect(() => {
    // Mantiene la referencia si las props no cambiaron: un objeto nuevo
    // idéntico re-dispararía la carga del detalle en cada montaje.
    setCurrent((prev) =>
      prev.docType === docType && prev.serie === serie && prev.codigo === codigo
        ? prev
        : { docType, serie, codigo },
    );
  }, [docType, serie, codigo]);

  useEffect(() => {
    getCurrentUser().then(setUser).catch(() => undefined);
  }, []);

  // Cuentas bancarias de la empresa emisora de este documento (por serie).
  useEffect(() => {
    getErpSettings()
      .then((cfg) => {
        const cuentas = cfg.factusol_companies?.[String(current.serie)]?.bancos
          ?? [];
        setBanks(cuentas);
        const def = cuentas.findIndex((b) => b.defecto);
        setPdfBank(def >= 0 ? def : 0);
      })
      .catch(() => setBanks([]));
  }, [current.serie]);

  const load = useCallback((fresh = false) => {
    setDoc(null);
    setError(null);
    let alive = true;
    getFactusolDocument(
      current.docType, current.serie, current.codigo,
      fresh ? { fresh: true } : undefined,
    )
      .then((d) => {
        if (!alive) return;
        setDoc(d);
        // E4-fix1: el idioma llega preseleccionado por la cascada del
        // backend (pedido → cliente → empresa → español), con su origen.
        // Fallback al país del cliente si el backend no lo mandó.
        if (d.pdf_lang) {
          setPdfLang(d.pdf_lang.lang);
          setPdfLangSource(d.pdf_lang.source);
        } else {
          setPdfLang(defaultPdfLang(d.cliente_pais));
          setPdfLangSource(null);
        }
        setPdfVariant("");
        setPdfBank(0);
        setPdfCurrency("EUR");
      })
      .catch((e) => {
        if (alive) setError(extractErrorMessage(e, "No se pudo cargar el documento."));
      });
    return () => { alive = false; };
  }, [current]);

  useEffect(() => load(), [load]);

  // Polling del job de conversión (E2-fix2: un job muerto NUNCA deja el
  // modal en «Generando…» — failed llega por aquí y se enseña).
  useEffect(() => {
    if (!jobId) return;
    const timer = setInterval(async () => {
      try {
        const st = await getFactusolConvertStatus(jobId);
        if (st.status === "finished") {
          setJobId(null);
          setCreated({
            doc_type: st.result.target_type,
            serie: st.result.serie,
            codigo: st.result.codigo,
            numero: st.result.numero,
          });
          setOriginWarning(
            st.result.origin_marked === false
              ? st.result.origin_mark_warning
                ?? "El documento se creó, pero el origen no quedó marcado como convertido."
              : null,
          );
          // E3-B-fix1: recarga saltando el cache del índice del ciclo —
          // badge, avisos y botones se repintan AL MOMENTO, sin reabrir.
          load(true);
          onChanged?.();   // y la fila del listado de fondo
        } else if (st.status === "failed") {
          setJobId(null);
          setCreateError(st.error || "La creación falló en FACTUSOL.");
        }
      } catch {
        // Polling best-effort: un fallo puntual de red no aborta el job.
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [jobId, load, onChanged]);

  const canEdit =
    !!user && (ERP_EDIT_ROLES as readonly string[]).includes(user.role);
  const ciclo = doc?.ciclo ?? null;
  const badge = cycleBadge(current.docType, ciclo);
  const actions = doc ? availableActions(current.docType, ciclo) : [];
  const isSourceType = current.docType !== "facturas";
  // El selector de banco solo aparece si la empresa tiene más de una cuenta;
  // el índice por defecto es la marcada (o la primera).
  const pdfBankOptions = banks;

  function navigate(ref: FactusolCycleRef) {
    setCreated(null);
    setCreateError(null);
    setOriginWarning(null);
    setConvertTarget(null);
    setConvertPartial(false);
    setCurrent({ docType: ref.doc_type, serie: ref.serie, codigo: ref.codigo });
  }

  /** ERP-F3 — marca la factura como cobrada/pendiente (tras confirmar). */
  async function doMarkPayment(paid: boolean) {
    setPayBusy(true);
    setPayError(null);
    setPayMsg(null);
    setPayConfirm(null);
    try {
      const res = await markInvoicePayment(current.serie, current.codigo, {
        confirm: true, paid,
      });
      if (res.status === "already") {
        setPayMsg(`La factura ya estaba ${paid ? "cobrada" : "pendiente"}.`);
      } else if (res.job_id) {
        const st = await waitForInvoicePaymentJob(res.job_id);
        if (st.status === "failed" || (st.status === "finished" && !st.result.marked)) {
          const motivo = st.status === "finished"
            ? (st.result.motivo ?? "")
            : (st.error ?? "");
          setPayError(
            `No se pudo marcar el cobro en FACTUSOL. ${motivo}`.trim(),
          );
        } else {
          setPayMsg(`Factura marcada como ${paid ? "cobrada" : "pendiente"}.`);
          load(true);           // repinta el estado desde FACTUSOL
          onChanged?.();
        }
      }
    } catch (e) {
      setPayError(extractErrorMessage(e, "No se pudo marcar el cobro."));
    } finally {
      setPayBusy(false);
    }
  }

  /** Hijos que ya existen del tipo destino — el aviso anti-duplicado. */
  function existingChildren(target: FactusolConvertTarget): FactusolCycleRef[] {
    if (!ciclo) return [];
    return target === "albaranes" ? ciclo.albaranes : ciclo.facturas;
  }

  function refLinks(refs: FactusolCycleRef[]) {
    return refs.map((ref) => (
      <button
        key={`${ref.doc_type}-${ref.numero}`}
        type="button"
        className="erp-doc-ciclo-link"
        onClick={() => navigate(ref)}
      >
        {ref.numero}
      </button>
    ));
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label={`Detalle ${TYPE_LABELS[current.docType]}`}>
      <div className="modal-dialog erp-emit-modal erp-doc-detail">
        <h2>
          {TYPE_LABELS[current.docType]}{" "}
          <span className="muted">
            {doc?.numero ?? `${current.serie}-${current.codigo}`}
          </span>
        </h2>

        {error ? <p className="form-error">{error}</p> : null}
        {!doc && !error ? <p className="muted">Cargando…</p> : null}

        {created ? (
          <p className="form-success">
            Creado {TARGET_LABELS[created.doc_type as FactusolConvertTarget]
              ?? created.doc_type}{" "}
            <button
              type="button"
              className="erp-doc-ciclo-link"
              onClick={() => navigate(created)}
            >
              {created.numero}
            </button>{" "}
            en FACTUSOL.
          </p>
        ) : null}
        {createError ? <p className="form-error">{createError}</p> : null}
        {payError ? <p className="form-error">{payError}</p> : null}
        {payMsg ? <p className="form-success" role="status">{payMsg}</p> : null}
        {payBusy ? <p className="muted">Marcando el cobro en FACTUSOL…</p> : null}
        {originWarning ? (
          <p className="erp-doc-ciclo-aviso">{originWarning}</p>
        ) : null}
        {jobId ? <p className="muted">Creando el documento en FACTUSOL…</p> : null}

        {doc ? (
          <>
            <dl className="erp-doc-detail-head">
              <dt>Cliente</dt>
              <dd>{doc.cliente_nombre ?? doc.cliente_codigo ?? "—"}</dd>
              <dt>Fecha</dt>
              <dd>{doc.fecha ?? "—"}</dd>
              <dt>Estado</dt>
              <dd>
                {doc.estado_label}
                {current.docType === "facturas" && canEdit ? (
                  <button
                    type="button"
                    className="button secondary small"
                    style={{ marginLeft: 8 }}
                    disabled={payBusy}
                    onClick={() => {
                      setPayError(null);
                      setPayMsg(null);
                      // Cobrada (estado 2) → ofrecer marcar pendiente; en
                      // cualquier otro caso → marcar cobrada.
                      setPayConfirm(String(doc.estado) !== "2");
                    }}
                  >
                    {String(doc.estado) === "2"
                      ? "Marcar como pendiente"
                      : "Marcar como cobrada"}
                  </button>
                ) : null}
              </dd>
              <dt>Forma de pago</dt>
              <dd>
                {doc.forma_pago_nombre
                  ?? (doc.forma_pago ? `Código ${doc.forma_pago}` : "—")}
              </dd>
              {doc.referencia ? (
                <>
                  <dt>Referencia</dt>
                  <dd>{doc.referencia}</dd>
                </>
              ) : null}
              <dt>Total</dt>
              <dd>
                <strong>
                  {doc.total !== null ? `${doc.total.toFixed(2)} €` : "—"}
                </strong>
              </dd>
            </dl>

            {ciclo ? (
              <div className="erp-doc-ciclo">
                {badge ? <span className={badge.className}>{badge.label}</span> : null}
                {ciclo.origen.length > 0 ? (
                  <span>
                    {current.docType === "facturas" ? "Creada" : "Creado"} desde{" "}
                    {ciclo.origen.map((ref) => (
                      <button key={ref.numero} type="button"
                              className="erp-doc-ciclo-link"
                              onClick={() => navigate(ref)}>
                        {TYPE_LABELS[ref.doc_type].toLowerCase()} {ref.numero}
                      </button>
                    ))}
                  </span>
                ) : null}
              </div>
            ) : null}

            {/* Avisos de la regla de negocio (E3-B-fix1). */}
            {ciclo && isSourceType &&
             (current.docType === "presupuestos" || current.docType === "pedidos") &&
             ciclo.albaranes.length > 0 ? (
              <p className="erp-doc-ciclo-aviso">
                Este {TYPE_LABELS[current.docType].toLowerCase()} ya tiene el
                albarán {refLinks(ciclo.albaranes)}. La factura se genera
                desde el albarán.
              </p>
            ) : null}
            {ciclo && isSourceType && ciclo.facturas.length > 0 ? (
              <p className="erp-doc-ciclo-aviso">
                Ya facturado en {refLinks(ciclo.facturas)}.
              </p>
            ) : null}
            {/* E3-B-fix2: la ÚNICA vía de un segundo albarán — un enlace
                discreto explícitamente etiquetado como entrega parcial. */}
            {canEdit && partialDeliveryAvailable(current.docType, ciclo) ? (
              <p className="muted small">
                <button
                  type="button"
                  className="erp-doc-ciclo-link"
                  disabled={!!jobId}
                  onClick={() => {
                    setCreateError(null);
                    setConvertPartial(true);
                    setConvertTarget("albaranes");
                  }}
                >
                  ¿Entrega parcial? Crear otro albarán
                </button>
              </p>
            ) : null}

            {doc.lines.length > 0 ? (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Artículo</th>
                    <th>Descripción</th>
                    <th>Cant.</th>
                    <th>Precio</th>
                    <th>Total</th>
                  </tr>
                </thead>
                <tbody>
                  {doc.lines.map((ln) => (
                    <tr key={`${ln.position}-${ln.description}`}>
                      <td>{ln.position}</td>
                      <td className="muted small">{ln.codart ?? "—"}</td>
                      <td>{ln.description}</td>
                      <td>{ln.quantity}</td>
                      <td>{ln.unit_price.toFixed(2)}</td>
                      <td>{ln.line_total.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="muted">Sin líneas.</p>
            )}
          </>
        ) : null}

        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose}>
            Cerrar
          </button>
          {doc ? (
            <span className="erp-doc-pdf">
              {(PDF_VARIANTS[current.docType]?.length ?? 0) > 1 ? (
                <select
                  value={pdfVariant}
                  aria-label="Variante del documento"
                  onChange={(e) => setPdfVariant(e.target.value)}
                >
                  {PDF_VARIANTS[current.docType]!.map((v) => (
                    <option key={v.value} value={v.value}>{v.label}</option>
                  ))}
                </select>
              ) : null}
              {pdfBankOptions.length > 1 ? (
                <select
                  value={pdfBank}
                  aria-label="Cuenta bancaria"
                  onChange={(e) => setPdfBank(Number(e.target.value))}
                >
                  {pdfBankOptions.map((b, i) => (
                    <option key={i} value={i}>{b.nombre || b.iban}</option>
                  ))}
                </select>
              ) : null}
              <select
                value={pdfCurrency}
                aria-label="Divisa"
                onChange={(e) => setPdfCurrency(e.target.value)}
              >
                {PDF_CURRENCIES.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
              <select
                value={pdfLang}
                aria-label="Idioma del PDF"
                title={pdfLangSource
                  ? `Idioma ${PDF_LANG_SOURCE_LABELS[pdfLangSource] ?? ""}`
                  : undefined}
                onChange={(e) => {
                  setPdfLang(e.target.value as FactusolPdfLang);
                  setPdfLangSource(null);  // elección manual: ya no es sugerido
                }}
              >
                {PDF_LANGS.map((l) => (
                  <option key={l.value} value={l.value}>{l.label}</option>
                ))}
              </select>
              {pdfLangSource ? (
                <span className="muted small erp-doc-pdf-langsrc">
                  {PDF_LANGS.find((l) => l.value === pdfLang)?.label}{" "}
                  · {PDF_LANG_SOURCE_LABELS[pdfLangSource]}
                </span>
              ) : null}
              <button
                type="button"
                className="button secondary"
                disabled={pdfBusy}
                onClick={async () => {
                  setPdfBusy(true);
                  setCreateError(null);
                  try {
                    const blob = await downloadFactusolDocumentPdf(
                      current.docType, current.serie, current.codigo, pdfLang,
                      {
                        variant: (pdfVariant || undefined) as
                          FactusolPdfOptions["variant"],
                        bank: pdfBankOptions.length > 1 ? pdfBank : undefined,
                        currency: pdfCurrency !== "EUR" ? pdfCurrency : undefined,
                      },
                    );
                    saveBlob(
                      blob,
                      `${TYPE_LABELS[current.docType].replace(/ /g, "_")}_${doc.numero}.pdf`,
                    );
                  } catch (e) {
                    setCreateError(extractErrorMessage(
                      e, "No se pudo generar el PDF.",
                    ));
                  } finally {
                    setPdfBusy(false);
                  }
                }}
              >
                {pdfBusy ? "Generando…" : "Descargar PDF"}
              </button>
              {/* ERP-F1 — enviar la factura por email en el idioma del
                  cliente (previsualización obligatoria en el modal). Solo
                  facturas y con permiso de edición. */}
              {current.docType === "facturas" && canEdit ? (
                <button
                  type="button"
                  className="button"
                  onClick={() => setEmailOpen(true)}
                >
                  Enviar factura por email
                </button>
              ) : null}
            </span>
          ) : null}
          {doc && canEdit
            ? actions.map((target) => (
                <button
                  key={target}
                  type="button"
                  className="button"
                  disabled={!!jobId}
                  onClick={() => {
                    setCreateError(null);
                    setConvertPartial(false);
                    setConvertTarget(target);
                  }}
                >
                  Crear {TARGET_LABELS[target]}
                </button>
              ))
            : null}
        </div>
      </div>

      {doc && convertTarget ? (
        <ConvertConfirmModal
          doc={doc}
          docType={current.docType}
          target={convertTarget}
          existing={existingChildren(convertTarget)}
          partial={convertPartial}
          submitting={!!jobId}
          onCancel={() => { setConvertTarget(null); setConvertPartial(false); }}
          onSubmit={async (opts) => {
            setCreateError(null);
            setCreated(null);
            try {
              const r = await convertFactusolDocument(
                current.docType, current.serie, current.codigo,
                { target: convertTarget, ...opts },
              );
              setConvertTarget(null);
              setConvertPartial(false);
              setJobId(r.job_id);
            } catch (e) {
              // 409 anti-duplicado (carrera: alguien lo creó después de abrir
              // el modal) u otro error — se enseña DENTRO del diálogo.
              throw new Error(
                extractErrorMessage(e, "No se pudo encolar la creación."),
              );
            }
          }}
        />
      ) : null}

      {doc && emailOpen && current.docType === "facturas" ? (
        <InvoiceEmailModal
          serie={current.serie}
          codigo={Number(current.codigo)}
          numero={doc.numero}
          bank={pdfBankOptions.length > 1 ? pdfBank : null}
          variant={pdfVariant === "anticipo" ? "anticipo" : null}
          onClose={() => setEmailOpen(false)}
        />
      ) : null}

      {doc && payConfirm !== null ? (
        <div className="modal-overlay" role="dialog" aria-modal="true"
             aria-label="Confirmar marcado de cobro">
          <div className="modal-dialog erp-emit-modal">
            <h2>
              Marcar como {payConfirm ? "cobrada" : "pendiente"}
            </h2>
            <p className="form-error">
              Marcar una factura como {payConfirm ? "cobrada" : "pendiente"} es
              una afirmación contable. Revisa los datos antes de confirmar.
            </p>
            <dl className="erp-doc-detail-head">
              <dt>Factura</dt><dd><strong>{doc.numero}</strong></dd>
              <dt>Cliente</dt>
              <dd>{doc.cliente_nombre ?? doc.cliente_codigo ?? "—"}</dd>
              <dt>Importe</dt>
              <dd>{doc.total !== null ? `${doc.total.toFixed(2)} €` : "—"}</dd>
            </dl>
            <div className="modal-actions">
              <button type="button" className="button secondary"
                      onClick={() => setPayConfirm(null)} disabled={payBusy}>
                Cancelar
              </button>
              <button type="button" className="button"
                      onClick={() => doMarkPayment(payConfirm)} disabled={payBusy}>
                {payBusy
                  ? "Marcando…"
                  : `Confirmar ${payConfirm ? "cobrada" : "pendiente"}`}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** Confirmación de conversión — mismo patrón que el modal de emisión E2:
 *  total, aviso de irreversibilidad, serie heredada con override y fecha.
 *
 *  E3-B-fix2: el modo `partial` (entrega parcial — la ÚNICA vía de un
 *  segundo albarán) cambia el título, el aviso y el botón («Crear albarán
 *  parcial», NUNCA «de todos modos») y manda `force`. Un 409 por carrera se
 *  enseña como error, sin ofrecer forzar desde aquí. */
function ConvertConfirmModal({
  doc,
  docType,
  target,
  existing,
  partial,
  submitting,
  onCancel,
  onSubmit,
}: {
  doc: FactusolDocumentDetail;
  docType: FactusolDocType;
  target: FactusolConvertTarget;
  existing: FactusolCycleRef[];
  partial: boolean;
  submitting: boolean;
  onCancel: () => void;
  onSubmit: (opts: {
    serie?: number | null; fecha?: string | null; force?: boolean;
  }) => Promise<void>;
}) {
  const [serie, setSerie] = useState<number | null>(null);
  const [fecha, setFecha] = useState(today());
  const [series, setSeries] = useState<FactusolSerie[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const tipo = TYPE_LABELS[docType].toLowerCase();
  const targetLabel = partial
    ? `${TARGET_LABELS[target]} parcial`
    : TARGET_LABELS[target];

  useEffect(() => {
    getFactusolSeries()
      .then((r) => setSeries(
        [...r.items].sort(
          (a, b) => Number(b.is_known) - Number(a.is_known) || a.serie - b.serie,
        ),
      ))
      .catch(() => undefined);
  }, []);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await onSubmit({
        serie,
        fecha: fecha || null,
        force: partial,
      });
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo crear el documento."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label={`Crear ${targetLabel}`}>
      <div className="modal-dialog erp-emit-modal">
        <h2>
          Crear {targetLabel} desde {tipo} {doc.numero}
        </h2>
        <p>
          Total:{" "}
          <strong>
            {doc.total !== null ? `${doc.total.toFixed(2)} €` : "—"}
          </strong>
        </p>
        <p className="form-error">
          Se creará un documento <strong>real</strong> en FACTUSOL, enlazado a
          este {tipo}. Esta acción no es reversible desde el CRM.
        </p>
        {partial ? (
          <p className="form-error">
            Este {tipo} ya tiene el albarán{" "}
            <strong>{existing.map((r) => r.numero).join(", ")}</strong>. Úsalo
            solo si estás entregando el pedido en varios envíos — de lo
            contrario duplicarás el documento en la contabilidad.
          </p>
        ) : null}
        {error ? <p className="form-error">{error}</p> : null}

        <label className="field">
          <span>Empresa emisora / Serie</span>
          <select
            value={serie ?? ""}
            aria-label="Serie del documento nuevo"
            onChange={(e) =>
              setSerie(e.target.value ? Number(e.target.value) : null)
            }
          >
            <option value="">
              Heredar la del origen{doc.serie !== null ? ` (serie ${doc.serie})` : ""}
            </option>
            {series.map((s) => (
              <option key={s.serie} value={s.serie}>
                {s.serie} · {s.nombre}
              </option>
            ))}
          </select>
        </label>
        <span className="muted small">
          {serie === null
            ? "El documento nuevo se numera en la serie del origen."
            : `Se fuerza la serie ${serie}, ignorando la del origen.`}
        </span>

        <label className="field">
          <span>Fecha del documento</span>
          <input
            type="date"
            value={fecha}
            aria-label="Fecha del documento nuevo"
            onChange={(e) => setFecha(e.target.value)}
          />
        </label>

        <div className="modal-actions">
          <button type="button" className="button secondary"
                  onClick={onCancel} disabled={busy || submitting}>
            Cancelar
          </button>
          <button type="button" className="button"
                  onClick={submit} disabled={busy || submitting}>
            {busy || submitting ? "Creando…" : `Crear ${targetLabel}`}
          </button>
        </div>
      </div>
    </div>
  );
}
