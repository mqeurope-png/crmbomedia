"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../../components/PageHeader";
import { EmbalarModal } from "../../../components/erp/EmbalarModal";
import { PDF_LANGS } from "../../../components/erp/FactusolDocumentDetailModal";
import { InvoiceEmailModal } from "../../../components/erp/InvoiceEmailModal";
import { EmitFactusolButton } from "../../../components/erp/EmitFactusolButton";
import { FactusolAlbaranPdfButton } from "../../../components/erp/FactusolAlbaranPdfButton";
import { OrderStatusMachine } from "../../../components/erp/OrderStatusMachine";
import { ShippingFilesSection } from "../../../components/erp/ShippingFilesSection";
import { getCurrentUser, type User } from "../../../lib/api";
import { extractErrorMessage } from "../../../lib/errors";
import {
  completeOrder,
  createOrderAlbaran,
  customerLabel,
  downloadOrderFactusolPedidoPdf,
  getErpSettings,
  getOrder,
  getOrderFactusolInvoiceRef,
  getOrderTimeline,
  getFactusolStatus,
  getQuoteJobStatus,
  fireTransition,
  saveBlob,
  uncompleteOrder,
  updateOrderLanguage,
  updateOrderSeguimiento,
  ERP_EDIT_ROLES,
  type AvailableTransition,
  type FactusolInvoiceRef,
  type FactusolPdfLang,
  type FactusolStatus,
  type OrderDetail,
  type StatusDomain,
  type TimelineEvent,
} from "../../../lib/erpApi";

const INVOICED_STATUSES = new Set(["generated", "invoiced_by_erp", "already_invoiced_externally"]);

/** Etiqueta del botón de PDF según el documento de ORIGEN del pedido en
 *  FACTUSOL (presupuesto para los creados desde proforma; pedido de cliente
 *  para el resto). Sin documento, la etiqueta genérica y el botón deshabilitado. */
function pdfDocumentLabel(doc: OrderDetail["factusol_document"] | undefined): string {
  return doc?.doc_type === "presupuestos"
    ? "PDF del presupuesto (FACTUSOL)"
    : "PDF del pedido (FACTUSOL)";
}

function pdfFilePrefix(doc: OrderDetail["factusol_document"] | undefined): string {
  return doc?.doc_type === "presupuestos" ? "Presupuesto" : "Pedido";
}
function isInvoiced(o: { invoice_status: string; factusol_invoice_number: string | null }): boolean {
  return INVOICED_STATUSES.has(o.invoice_status) || !!o.factusol_invoice_number;
}

export default function ErpOrderDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [order, setOrder] = useState<OrderDetail | null>(null);
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [user, setUser] = useState<User | null>(null);
  const [factusolStatus, setFactusolStatus] = useState<FactusolStatus | null>(null);
  const [embalarOpen, setEmbalarOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // E4 — PDF del pedido de cliente (F_PCL) vinculado en FACTUSOL.
  const [pdfLang, setPdfLang] = useState<FactusolPdfLang>("es");
  const [pdfBusy, setPdfBusy] = useState(false);
  // Aviso DISCRETO del PDF (404 controlado: «aún no existe en FACTUSOL»);
  // nunca el banner rojo por un caso esperado.
  const [pdfNotice, setPdfNotice] = useState<string | null>(null);
  // ERP-F1 — envío de la factura por email: primero se resuelve la factura
  // FACTUSOL del pedido (serie+número) y luego se abre el modal de preview.
  const [invoiceRef, setInvoiceRef] = useState<FactusolInvoiceRef | null>(null);
  const [emailBusy, setEmailBusy] = useState(false);
  // «Marcar completado» (solo BoHub, reversible).
  const [completeBusy, setCompleteBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    getOrder(id)
      .then((o) => {
        setOrder(o);
        // E4-fix1: el idioma del PDF arranca en el del pedido si se conoce.
        if (o.language) setPdfLang(o.language as FactusolPdfLang);
      })
      .catch((e) => setError(extractErrorMessage(e, "No se pudo cargar el pedido.")));
    getOrderTimeline(id).then((r) => setTimeline(r.items)).catch(() => undefined);
    // C-2-fix2: consulta en vivo si ya hay factura/albarán en FACTUSOL. Si el
    // backend auto-vincula una factura existente, releemos el pedido para que
    // el badge de facturación quede al día.
    getFactusolStatus(id)
      .then((st) => {
        setFactusolStatus(st);
        if (st.status === "invoiced") {
          getOrder(id).then(setOrder).catch(() => undefined);
        }
      })
      .catch(() => setFactusolStatus(null));
  }, [id]);

  useEffect(() => {
    getCurrentUser().then(setUser).catch(() => undefined);
    load();
  }, [load]);

  const canEmit = !!user && (ERP_EDIT_ROLES as readonly string[]).includes(user.role);
  // Señal para abrir el modal de emisión desde la tarjeta FACTURACIÓN.
  const [emitSignal, setEmitSignal] = useState(0);

  async function onFire(domain: StatusDomain, t: AvailableTransition) {
    // Fase D: «Embalado» abre el modal multi-bulto en vez de la transición
    // directa (el backend exige ≥1 bulto medido antes de pasar a packed).
    if (domain === "preparation" && t.to_status === "packed") {
      setEmbalarOpen(true);
      return;
    }
    // ERP-E2-fix2: «Solicitar factura» abre el MISMO modal de emisión que el
    // botón azul. Antes solo movía el estado a «pending» sin encolar nada en
    // FACTUSOL, y el pedido se quedaba ahí para siempre.
    if (domain === "invoice" && t.to_status === "pending") {
      setEmitSignal((n) => n + 1);
      return;
    }
    const evidence: Record<string, unknown> = {};
    for (const key of t.required_evidence) {
      const val = window.prompt(`«${t.label}» requiere: ${key}`);
      if (val === null) return; // cancelado
      evidence[key] = val;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await fireTransition(id, {
        domain, to_status: t.to_status, evidence,
        reason: (evidence.reason as string) ?? undefined,
      });
      setOrder(updated);
      const tl = await getOrderTimeline(id);
      setTimeline(tl.items);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo aplicar la transición."));
    } finally {
      setBusy(false);
    }
  }

  // «Marcar completado» / «Desmarcar» (solo BoHub, reversible): estado final
  // del pedido. No exige envío ni factura (si no está facturado, avisa y deja
  // continuar). Nunca toca WooCommerce.
  async function onToggleComplete() {
    if (!order) return;
    if (
      !order.completed && !isInvoiced(order)
      && !window.confirm("Este pedido aún no está facturado. ¿Marcarlo completado igualmente?")
    ) {
      return;
    }
    setCompleteBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (order.completed) {
        setOrder(await uncompleteOrder(order.id));
        setNotice("Ya no está marcado como completado.");
      } else {
        const r = await completeOrder(order.id);
        setOrder(r);
        setNotice(
          "Marcado como completado (solo en BoHub; WooCommerce no cambia)."
          + (r.completion_avisos.length ? ` Aviso: ${r.completion_avisos.join("; ")}.` : ""),
        );
      }
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo cambiar el estado de completado."));
    } finally {
      setCompleteBusy(false);
    }
  }

  if (!order) {
    return <main className="shell"><p className="muted">{error ?? "Cargando…"}</p></main>;
  }

  return (
    <main className="shell shell-wide">
      <PageHeader
        title={`Pedido ${order.order_number}`}
        eyebrow="ERP"
        description={[
          customerLabel(order) ? `Cliente: ${customerLabel(order)}` : null,
          `${order.external_source} · ${order.total_amount.toFixed(2)} ${order.currency}`,
        ].filter(Boolean).join(" — ")}
        crumbs={[
          { label: "ERP" },
          { label: "Pedidos", href: "/erp/orders" },
          { label: order.order_number },
        ]}
      />
      {error ? <p className="form-error">{error}</p> : null}
      {notice ? <p className="form-success" role="status">{notice}</p> : null}
      <div className="erp-factusol-row" style={{ margin: "0 0 14px" }}>
        <span className="erp-doc-pdf">
          <select
            value={pdfLang}
            aria-label="Idioma del PDF"
            onChange={(e) => setPdfLang(e.target.value as FactusolPdfLang)}
          >
            {PDF_LANGS.map((l) => (
              <option key={l.value} value={l.value}>{l.label}</option>
            ))}
          </select>
          <button
            type="button"
            className="button small secondary"
            disabled={pdfBusy || !order.factusol_document}
            title={order.factusol_document
              ? `Genera el PDF del ${order.factusol_document.label} de origen en FACTUSOL`
              : "Sin documento en FACTUSOL: este pedido no procede de un presupuesto ni de un pedido de cliente"}
            onClick={async () => {
              if (!order.factusol_document) return;
              setPdfBusy(true);
              setError(null);
              setPdfNotice(null);
              try {
                const blob = await downloadOrderFactusolPedidoPdf(order.id, pdfLang);
                saveBlob(blob, `${pdfFilePrefix(order.factusol_document)}_${order.order_number}.pdf`);
              } catch (e) {
                // 404 controlado (el documento no está en FACTUSOL) → aviso
                // discreto; cualquier otra cosa sí es un error.
                if ((e as { status?: number } | null)?.status === 404) {
                  setPdfNotice(extractErrorMessage(e, "Sin documento en FACTUSOL."));
                } else {
                  setError(extractErrorMessage(
                    e, "No se pudo generar el PDF del documento FACTUSOL.",
                  ));
                }
              } finally {
                setPdfBusy(false);
              }
            }}
          >
            {pdfBusy ? "Generando…" : pdfDocumentLabel(order.factusol_document)}
          </button>
          {pdfNotice ? (
            <span className="muted small" role="status">{pdfNotice}</span>
          ) : null}
        </span>
        {/* E4-fix1 — idioma del pedido: dato persistente (detectado en la
            importación Woo) editable a mano; alimenta la cascada de los PDF. */}
        {canEmit ? (
          <label className="erp-doc-pdf" style={{ marginLeft: 12 }}>
            <span className="muted small">Idioma del pedido</span>
            <select
              aria-label="Idioma del pedido"
              value={order.language ?? ""}
              onChange={async (e) => {
                const value = (e.target.value || null) as FactusolPdfLang | null;
                setError(null);
                try {
                  await updateOrderLanguage(order.id, value);
                  setOrder({ ...order, language: value });
                  if (value) setPdfLang(value);
                } catch (err) {
                  setError(extractErrorMessage(
                    err, "No se pudo guardar el idioma del pedido.",
                  ));
                }
              }}
            >
              <option value="">— sin detectar —</option>
              {PDF_LANGS.map((l) => (
                <option key={l.value} value={l.value}>{l.label}</option>
              ))}
            </select>
          </label>
        ) : null}
      </div>
      {canEmit ? (
        <div className="erp-factusol-row" style={{ margin: "0 0 14px" }}>
          <EmitFactusolButton
            orderId={order.id}
            invoiceStatus={order.invoice_status}
            factusolInvoiceNumber={order.factusol_invoice_number}
            totalAmount={order.total_amount}
            currency={order.currency}
            companyId={order.company_id}
            factusolStatus={factusolStatus}
            enableOptions
            openSignal={emitSignal}
            onInvoiced={() => load()}
          />
          {/* ERP-F1 — enviar la factura por email cuando el pedido ya está
              facturado en FACTUSOL. Resuelve la factura (serie+número) y abre
              la previsualización obligatoria. */}
          {order.factusol_invoice_number
           || factusolStatus?.status === "invoiced"
           || order.invoice_status === "generated"
           || order.invoice_status === "invoiced_by_erp" ? (
            <button
              type="button"
              className="button small"
              disabled={emailBusy}
              onClick={async () => {
                setEmailBusy(true);
                setError(null);
                try {
                  const ref = await getOrderFactusolInvoiceRef(order.id);
                  setInvoiceRef(ref);
                } catch (e) {
                  setError(extractErrorMessage(
                    e, "No se pudo localizar la factura en FACTUSOL.",
                  ));
                } finally {
                  setEmailBusy(false);
                }
              }}
            >
              {emailBusy ? "Localizando…" : "Enviar factura por email"}
            </button>
          ) : null}
          {/* «Marcar completado»: estado final del pedido, solo en BoHub. */}
          <button
            type="button"
            className={`button small ${order.completed ? "secondary" : ""}`}
            disabled={completeBusy}
            title={order.completed
              ? "Quitar la marca de completado (solo BoHub)"
              : "Estado final del pedido (facturado y enviado), solo en BoHub; no toca WooCommerce"}
            onClick={() => void onToggleComplete()}
          >
            {completeBusy
              ? "Guardando…"
              : order.completed ? "Desmarcar completado" : "Marcar completado"}
          </button>
        </div>
      ) : null}
      {invoiceRef ? (
        <InvoiceEmailModal
          serie={invoiceRef.serie}
          codigo={invoiceRef.codigo}
          numero={invoiceRef.numero}
          onClose={() => setInvoiceRef(null)}
          onSent={() => { setInvoiceRef(null); load(); }}
        />
      ) : null}
      {order.completed ? (
        <p className="form-info">
          <span className="badge ok">Completado</span>{" "}
          Marcado como completado el{" "}
          {order.completed_at ? new Date(order.completed_at).toLocaleString("es-ES") : "—"}
          {order.completed_by_name ? ` por ${order.completed_by_name}` : ""} (solo BoHub;
          WooCommerce no cambia).
        </p>
      ) : null}
      {order.externally_processed_at ? (
        <p className="form-info" role="status">
          <span className="badge muted">Externalizado</span>{" "}
          Gestionado fuera del ERP el{" "}
          {new Date(order.externally_processed_at).toLocaleString("es-ES")}
          {order.externally_processed_note ? ` — ${order.externally_processed_note}` : ""}
        </p>
      ) : null}
      {order.blockers.length > 0 ? (
        <ul className="erp-blockers" aria-label="Bloqueos del pedido">
          {order.blockers.map((b) => (
            <li key={b.code} className="erp-blocker">
              <span className="badge bad">{b.code}</span> {b.detail}
            </li>
          ))}
        </ul>
      ) : null}
      {order.warnings.length > 0 ? (
        <ul className="erp-warnings" aria-label="Avisos del pedido">
          {order.warnings.map((w) => (
            <li key={w.code} className="erp-warning">
              <span className="badge warn">{w.code}</span> {w.detail}
            </li>
          ))}
        </ul>
      ) : null}

      <OrderStatusMachine order={order} onFire={onFire} busy={busy} />

      <ShippingFilesSection
        orderId={order.id}
        isWooOrder={order.external_source === "woocommerce"}
        factusolAlbaranNumber={order.factusol_albaran_number ?? null}
        pdfLang={pdfLang}
      />

      {embalarOpen ? (
        <EmbalarModal
          orderId={order.id}
          onCancel={() => setEmbalarOpen(false)}
          onDone={() => { setEmbalarOpen(false); load(); }}
        />
      ) : null}

      {/* Fase 2 — albarán FACTUSOL creado al convertir + pago apuntado (opción B). */}
      {order.external_source.startsWith("factusol_")
       || order.factusol_albaran_number || order.factusol_payment ? (
        <AlbaranPagoCard
          order={order}
          canEdit={canEmit}
          pdfLang={pdfLang}
          onChanged={() => load()}
          onError={setError}
        />
      ) : null}

      {/* ERP-F6 — campos del Excel de seguimiento, editables desde la ficha. */}
      <SeguimientoFieldsCard
        order={order}
        canEdit={canEmit}
        onSaved={(patch) => setOrder({ ...order, ...patch })}
        onError={setError}
      />

      <div className="erp-detail-grid">
        <section className="erp-card">
          <h3>Líneas</h3>
          <table className="data-table">
            <thead>
              <tr><th>SKU</th><th>Artículo</th><th>Cant.</th><th>Total</th><th>Mapping</th></tr>
            </thead>
            <tbody>
              {order.lines.map((l) => (
                <tr key={l.id}>
                  <td><code>{l.product_sku}</code></td>
                  <td>{l.description}</td>
                  <td>{l.quantity}</td>
                  <td>{l.line_total.toFixed(2)}</td>
                  <td>{l.product_codart
                    ? <span className="badge ok">{l.product_codart}</span>
                    : <span className="badge bad">sin mapear</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="erp-card">
          <h3>Timeline</h3>
          {timeline.length === 0 ? (
            <p className="muted small">Sin eventos.</p>
          ) : (
            <ul className="erp-timeline">
              {timeline.map((e, i) => (
                <li key={i} className={`erp-timeline-item is-${e.type}`}>
                  <span className="erp-timeline-when">
                    {new Date(e.at).toLocaleString("es-ES")}
                  </span>
                  <span className="erp-timeline-title">{e.title}</span>
                  {typeof e.detail.reason === "string" && e.detail.reason ? (
                    <span className="muted small"> — {e.detail.reason}</span>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {order.exceptions.length > 0 ? (
        <section className="erp-card">
          <h3>Excepciones</h3>
          <ul>
            {order.exceptions.map((e) => (
              <li key={e.id}>
                <span className="badge warn">{e.type}{e.subtype ? `:${e.subtype}` : ""}</span>{" "}
                <span className="badge muted">{e.status}</span>
              </li>
            ))}
          </ul>
          <Link href="/erp/exceptions" className="link-button small">Ver bandeja de excepciones</Link>
        </section>
      ) : null}
    </main>
  );
}

/** ERP-F6 — nº de serie, licencia WhiteRIP y origen del envío (OFI-TER-SAT),
 *  editables desde la ficha. El campo «orden» del Excel no existe como campo:
 *  lo que se escriba ahí se AÑADE a las observaciones del pedido. */
function SeguimientoFieldsCard({
  order, canEdit, onSaved, onError,
}: {
  order: OrderDetail;
  canEdit: boolean;
  onSaved: (patch: Partial<OrderDetail>) => void;
  onError: (msg: string | null) => void;
}) {
  const [serial, setSerial] = useState(order.serial_number ?? "");
  const [whiterip, setWhiterip] = useState(order.whiterip_license ?? "");
  const [origin, setOrigin] = useState(order.shipping_origin ?? "");
  const [orden, setOrden] = useState("");
  const [origins, setOrigins] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getErpSettings()
      .then((cfg) => setOrigins(cfg.shipping_origins ?? []))
      .catch(() => setOrigins([]));
  }, []);

  async function save() {
    setSaving(true);
    setSaved(false);
    onError(null);
    try {
      const r = await updateOrderSeguimiento(order.id, {
        serial_number: serial,
        whiterip_license: whiterip,
        shipping_origin: origin,
        ...(orden.trim() ? { orden: orden.trim() } : {}),
      });
      onSaved({
        serial_number: r.serial_number,
        whiterip_license: r.whiterip_license,
        shipping_origin: r.shipping_origin,
        notes: r.notes,
      });
      setOrden("");
      setSaved(true);
    } catch (e) {
      onError(extractErrorMessage(e, "No se pudieron guardar los datos de seguimiento."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="erp-card">
      <h3>Seguimiento</h3>
      <div className="erp-doc-filters">
        <label className="field">
          <span>Nº de serie</span>
          <input
            aria-label="Número de serie"
            value={serial}
            disabled={!canEdit}
            placeholder="FBAP12613200249 (texto libre)"
            onChange={(e) => setSerial(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Licencia WhiteRIP</span>
          <input
            aria-label="Licencia WhiteRIP"
            value={whiterip}
            disabled={!canEdit}
            placeholder="4829"
            onChange={(e) => setWhiterip(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Origen del envío (OFI-TER-SAT)</span>
          <input
            aria-label="Origen del envío"
            value={origin}
            disabled={!canEdit}
            list="erp-shipping-origins"
            placeholder="SAT"
            onChange={(e) => setOrigin(e.target.value)}
          />
          <datalist id="erp-shipping-origins">
            {origins.map((o) => <option key={o} value={o} />)}
          </datalist>
        </label>
        {canEdit ? (
          <label className="field">
            <span>Añadir a observaciones (columna «Orden» del Excel)</span>
            <input
              aria-label="Añadir a observaciones"
              value={orden}
              placeholder="RECOGE EL CLIENTE - SIN ENVÍO"
              onChange={(e) => setOrden(e.target.value)}
            />
          </label>
        ) : null}
        {canEdit ? (
          <button type="button" className="button small" disabled={saving} onClick={save}>
            {saving ? "Guardando…" : "Guardar seguimiento"}
          </button>
        ) : null}
        {saved ? <span className="muted small" role="status">Guardado.</span> : null}
      </div>
      {order.notes ? (
        <p className="muted small" style={{ whiteSpace: "pre-wrap" }}>
          <strong>Observaciones:</strong> {order.notes}
        </p>
      ) : null}
    </section>
  );
}

const ALBARAN_POLL_MS = 2000;
const ALBARAN_POLL_MAX_TRIES = 30;  // ~60 s: el worker es serie

/** Job del albarán con el que llega el alta (`?albaran_job=`), sin depender de
 *  `useSearchParams` (la ficha no lo usa). */
function albaranJobFromLocation(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return new URLSearchParams(window.location.search).get("albaran_job");
  } catch {
    return null;
  }
}

/** Fase 2 — «Albarán y pago FACTUSOL» del pedido creado desde una proforma /
 *  pedido de cliente de FACTUSOL:
 *
 *  - Albarán: el nº creado por BoHub al convertir (`F_ALB`+`F_LAL`, idempotente);
 *    si aún no lo tiene, botón para (re)crearlo. Los pedidos web nunca lo
 *    tienen aquí: su albarán lo crea WooCommerce.
 *  - Pago (opción B): «pagado» queda APUNTADO (cuenta, fecha) sin emitir
 *    factura; el cobro F-4-B se registra solo cuando exista la factura del
 *    pedido. «Sin pago» → pendiente, sin cobro. */
function AlbaranPagoCard({
  order, canEdit, pdfLang, onChanged, onError,
}: {
  order: OrderDetail;
  canEdit: boolean;
  pdfLang: FactusolPdfLang;
  onChanged: () => void;
  onError: (msg: string | null) => void;
}) {
  // El alta redirige aquí con el job del albarán recién encolado (solo
  // importa si el pedido aún no tiene su nº). Estado inicial perezoso: la
  // tarjeta se monta tras cargar el pedido, ya en el cliente.
  const [jobId, setJobId] = useState<string | null>(
    () => (order.factusol_albaran_number ? null : albaranJobFromLocation()),
  );
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // Polling del job hasta que termine; al terminar se recarga el pedido.
  useEffect(() => {
    if (!jobId) return;
    let alive = true;
    let tries = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = () => {
      getQuoteJobStatus(jobId)
        .then((st) => {
          if (!alive) return;
          if (st.status === "finished") {
            const numero = (st.result as { numero?: string } | undefined)?.numero;
            setNotice(numero ? `Albarán FACTUSOL ${numero} creado.` : "Albarán creado.");
            setJobId(null);
            onChanged();
          } else if (st.status === "failed") {
            setJobId(null);
            onError(`El albarán no se creó en FACTUSOL: ${st.error ?? "error"}`);
          } else if (++tries >= ALBARAN_POLL_MAX_TRIES) {
            setJobId(null);
            setNotice("El albarán sigue en cola; recarga la ficha en unos segundos.");
          } else {
            timer = setTimeout(tick, ALBARAN_POLL_MS);
          }
        })
        .catch(() => {
          if (!alive) return;
          if (++tries >= ALBARAN_POLL_MAX_TRIES) setJobId(null);
          else timer = setTimeout(tick, ALBARAN_POLL_MS);
        });
    };
    tick();
    return () => { alive = false; if (timer) clearTimeout(timer); };
    // onChanged/onError son estables a efectos prácticos (setters / load).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  async function crearAlbaran() {
    setBusy(true);
    onError(null);
    setNotice(null);
    try {
      const r = await createOrderAlbaran(order.id);
      setNotice("Creando el albarán en FACTUSOL…");
      setJobId(r.job_id);
    } catch (e) {
      onError(extractErrorMessage(e, "No se pudo encolar el albarán en FACTUSOL."));
    } finally {
      setBusy(false);
    }
  }

  const pago = order.factusol_payment ?? null;
  const isWeb = order.external_source === "woocommerce";

  return (
    <section className="erp-card" aria-label="Albarán y pago FACTUSOL">
      <h3>Albarán y pago FACTUSOL</h3>
      {notice ? <p className="form-info" role="status">{notice}</p> : null}
      <p>
        {order.factusol_albaran_number ? (
          <>
            <span className="badge ok">Albarán FACTUSOL {order.factusol_albaran_number}</span>
            {" "}
            <FactusolAlbaranPdfButton
              orderId={order.id}
              numero={order.factusol_albaran_number}
              lang={pdfLang}
              onError={onError}
            />
          </>
        ) : jobId ? (
          <span className="badge warn">Creando el albarán en FACTUSOL…</span>
        ) : isWeb ? (
          <span className="muted small">
            Pedido web: el albarán lo crea WooCommerce, no BoHub.
          </span>
        ) : (
          <>
            <span className="badge muted">Sin albarán en FACTUSOL</span>
            {canEdit ? (
              <>
                {" "}
                <button type="button" className="button small" disabled={busy}
                        title="Crea el albarán (F_ALB + líneas) a partir del documento de origen; idempotente"
                        onClick={() => void crearAlbaran()}>
                  {busy ? "Encolando…" : "Crear albarán en FACTUSOL"}
                </button>
              </>
            ) : null}
          </>
        )}
      </p>
      {pago ? (
        <div className="erp-payment-summary">
          {pago.paid ? (
            <>
              <p>
                <span className="badge ok">Pagado (apuntado)</span>{" "}
                {pago.forma_pago_nombre || pago.forma_pago
                  ? `forma de pago ${pago.forma_pago_nombre ?? pago.forma_pago} · ` : ""}
                cuenta {pago.contrapartida_nombre ?? pago.contrapartida ?? "—"}
                {pago.fecha ? ` · fecha ${pago.fecha}` : ""}.
              </p>
              <p className="muted small">
                {pago.cobro?.registered ? (
                  <>
                    Cobro registrado en FACTUSOL para la factura {pago.cobro.numero}
                    {pago.cobro.linlco != null ? ` (línea ${pago.cobro.linlco}` : ""}
                    {pago.cobro.importe != null ? `${pago.cobro.linlco != null ? ", " : " ("}${pago.cobro.importe.toFixed(2)} €)` : (pago.cobro.linlco != null ? ")" : "")}.
                  </>
                ) : pago.cobro && !pago.cobro.registered ? (
                  <>
                    Cobro FACTUSOL <strong>pendiente</strong>: no se pudo registrar
                    {pago.cobro.motivo ? ` (${pago.cobro.motivo})` : ""}. Regístralo desde
                    ERP · Documentos o el script de cobros.
                  </>
                ) : (
                  <>
                    Cobro FACTUSOL <strong>pendiente de factura</strong>: se registrará
                    solo (F-4-B) al emitir la factura del pedido. No se ha emitido
                    ninguna factura.
                  </>
                )}
              </p>
            </>
          ) : (
            <p>
              <span className="badge muted">Sin pago</span>{" "}
              pendiente
              {pago.forma_pago_nombre || pago.forma_pago
                ? ` · forma de pago ${pago.forma_pago_nombre ?? pago.forma_pago}` : ""}.
              No se registra ningún cobro.
            </p>
          )}
        </div>
      ) : null}
    </section>
  );
}
