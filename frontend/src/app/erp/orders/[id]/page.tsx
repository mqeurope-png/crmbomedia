"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import {
  Suspense, useCallback, useEffect, useState, useSyncExternalStore, type ReactNode,
} from "react";
import { PageHeader } from "../../../components/PageHeader";
import { CancelOrderModal } from "../../../components/erp/CancelOrderModal";
import { EmbalarModal } from "../../../components/erp/EmbalarModal";
import { PDF_LANGS } from "../../../components/erp/FactusolDocumentDetailModal";
import { InvoiceEmailModal } from "../../../components/erp/InvoiceEmailModal";
import { OrderEmailModal } from "../../../components/erp/OrderEmailModal";
import { CobroFactusolBadge } from "../../../components/erp/CobroFactusolBadge";
import { OrderFactusolClientPanel } from "../../../components/erp/OrderFactusolClientPanel";
import { EmitFactusolButton } from "../../../components/erp/EmitFactusolButton";
import { PrimaryActionBar } from "../../../components/erp/PrimaryActionBar";
import { RegistrarCobroModal } from "../../../components/erp/RegistrarCobroModal";
import { OrderStatusMachine } from "../../../components/erp/OrderStatusMachine";
import { ShippingFilesSection } from "../../../components/erp/ShippingFilesSection";
import { ActionsMenu } from "../../../components/erp/flow/ActionsMenu";
import { NextActionBar } from "../../../components/erp/flow/NextActionBar";
import { RegimePill } from "../../../components/erp/flow/RegimePill";
import { WorkflowAlerts } from "../../../components/erp/flow/WorkflowAlerts";
import { QUEUE_LABEL } from "../../../components/erp/flow/WorkflowQueueCards";
import { WorkflowProgress, WorkflowSteps } from "../../../components/erp/flow/WorkflowSteps";
import { getCurrentUser, type User } from "../../../lib/api";
import { extractErrorMessage } from "../../../lib/errors";
import { usePersistentState } from "../../../lib/usePersistentState";
import {
  completeOrder,
  customerLabel,
  downloadFactusolDocumentPdf,
  downloadOrderFactusolPedidoPdf,
  getErpSettings,
  getOrder,
  getOrderFactusolInvoiceRef,
  getOrderTimeline,
  getFactusolStatus,
  getOrderFactusolCobro,
  resolveOrderCobroStatus,
  type OrderCobroInfo,
  fireTransition,
  saveBlob,
  uncancelOrder,
  uncompleteOrder,
  updateOrderLanguage,
  updateOrderSeguimiento,
  ERP_EDIT_ROLES,
  type AvailableTransition,
  type FactusolCobroStatus,
  type FactusolInvoiceRef,
  type FactusolPdfLang,
  type FactusolStatus,
  type OrderDetail,
  type StatusDomain,
  type TimelineEvent,
  type WorkflowAction,
  type WorkflowAlert,
  type WorkflowQueue,
} from "../../../lib/erpApi";

const INVOICED_STATUSES = new Set(["generated", "invoiced_by_erp", "already_invoiced_externally"]);

/** Lote 2 · PR-2 — la cola de la bandeja de la que se llegó (`?from=`), solo
 *  si es una cola real: la miga «← Bandeja · Por cobrar» devuelve a ella. */
function queueFromParam(value: string | null | undefined): WorkflowQueue | null {
  return value && value in QUEUE_LABEL ? (value as WorkflowQueue) : null;
}

/** Paneles plegables de la ficha y la clave con la que cada uno recuerda si
 *  el usuario lo dejó abierto o cerrado (localStorage, por navegador). */
type FichaPanelId = "lineas" | "envio" | "historial";
const PANEL_LS_KEY: Record<FichaPanelId, string> = {
  lineas: "erp.ficha.panel.lineas",
  envio: "erp.ficha.panel.envio",
  historial: "erp.ficha.panel.historial",
};

/** El panel que interesa en el paso actual (se abre por defecto; el resto
 *  se pliega por debajo de 1280 px). Sale de `next_action`, la única fuente. */
function relevantPanel(action: WorkflowAction | undefined): FichaPanelId | null {
  switch (action) {
    case "aprobar":
    case "vincular_empresa":
    case "revalidar_vies":
    case "emitir_factura":
    case "crear_albaran":
      return "lineas";
    case "crear_envio":
    case "enviar_sat":
      return "envio";
    case "marcar_completado":
    case "revisar_incidencia":
    case "ninguna":
      return "historial";
    default:
      // «Registrar cobro»: lo que hay que mirar es el resumen económico, que
      // siempre está a la vista.
      return null;
  }
}

/** Estado de transporte en palabras, para el resumen del panel de envío. */
const TRANSPORT_TEXT: Record<string, string> = {
  not_shipped: "Sin expedición",
  label_created: "Etiqueta creada",
  in_transit: "En tránsito",
  delivered: "Entregado",
  incident: "Incidencia en el envío",
  returned: "Devuelto",
  already_shipped_externally: "Enviado fuera del ERP",
};

/** «hace 2 h» / «hace 3 días» para el resumen del historial. */
function hace(iso: string, now: number = Date.now()): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return "ahora mismo";
  const m = Math.round(s / 60);
  if (m < 60) return `hace ${m} min`;
  const h = Math.round(m / 60);
  if (h < 24) return `hace ${h} h`;
  const d = Math.round(h / 24);
  if (d < 30) return d === 1 ? "hace 1 día" : `hace ${d} días`;
  return `el ${new Date(iso).toLocaleDateString("es-ES")}`;
}

/** Fecha DD/MM/AAAA a partir de un ISO (se lee el tramo de fecha directamente,
 *  sin zona horaria: el mismo día que muestra el backend). */
function formatDMY(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : "—";
}

/** Media query como estado (SSR: false). El molde de la ficha decide con
 *  esto qué paneles abre por defecto y si la acción principal va pegada
 *  abajo (móvil). */
function useMediaQuery(query: string): boolean {
  const subscribe = useCallback((onChange: () => void) => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return () => undefined;
    }
    const mq = window.matchMedia(query);
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, [query]);
  return useSyncExternalStore(
    subscribe,
    () => (typeof window.matchMedia === "function" ? window.matchMedia(query).matches : false),
    () => false,
  );
}

/** Tooltip del botón «PDF del pedido (FACTUSOL)» — la ETIQUETA es siempre
 *  la misma (decisión de Bart), aunque por debajo el documento de origen sea
 *  un presupuesto (creado desde proforma) o un pedido de cliente. Sin
 *  documento, el botón se deshabilita con el motivo. */
function pdfDocumentTitle(doc: OrderDetail["factusol_document"] | undefined): string {
  if (!doc) {
    return "Sin documento en FACTUSOL: este pedido no procede de un presupuesto ni de un pedido de cliente";
  }
  const where = doc.by_ref
    ? ` (pedido web: se localiza por su referencia ${doc.ref ?? "REFPCL"})`
    : doc.numero ? ` ${doc.numero}` : "";
  return `Genera el PDF del ${doc.label} de origen en FACTUSOL${where}`;
}
function isInvoiced(o: { invoice_status: string; factusol_invoice_number: string | null }): boolean {
  return INVOICED_STATUSES.has(o.invoice_status) || !!o.factusol_invoice_number;
}

/** `useSearchParams` exige Suspense en el app router (`?from=`). */
export default function ErpOrderDetailPage() {
  return (
    <Suspense fallback={<main className="shell shell-wide erp-flow"><p className="muted">Cargando…</p></main>}>
      <ErpOrderDetailScreen />
    </Suspense>
  );
}

function ErpOrderDetailScreen() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const searchParams = useSearchParams();
  // Lote 2 · PR-2: la cola de la bandeja de la que se llegó (miga de vuelta).
  const fromQueue = queueFromParam(searchParams?.get("from"));
  // Molde responsive: por debajo de 1280 solo se abre el panel del paso
  // actual; en móvil (< 768) la acción principal va pegada abajo.
  const isWide = useMediaQuery("(min-width: 1280px)");
  const isMobile = useMediaQuery("(max-width: 767px)");
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
  const [invoicePdfBusy, setInvoicePdfBusy] = useState(false);
  // «Anular pedido» (manual / FACTUSOL): modal con aviso previo; «Restaurar».
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelBusy, setCancelBusy] = useState(false);
  // ERP · envío del PEDIDO por email (SAT / taller): modal + petición de crear
  // el albarán cuando el aviso del modal lo ofrece.
  const [orderEmailOpen, setOrderEmailOpen] = useState(false);
  const [albaranSignal, setAlbaranSignal] = useState(0);
  // Lote 2 C: «Subir etiqueta» desde «Siguiente paso» abre el selector de la
  // etiqueta de «Documentos de envío» (la subida es el antiguo «Crear envío»).
  const [etiquetaSignal, setEtiquetaSignal] = useState(0);
  // «Marcar completado» (solo BoHub, reversible).
  const [completeBusy, setCompleteBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  // Cobro manual (F-4-B desde la app): estado de cobro EN VIVO de la factura
  // del pedido (best-effort al cargar; el persistido viene en `order`) y el
  // modal compartido «Registrar cobro en FACTUSOL».
  const [cobroLive, setCobroLive] = useState<OrderCobroInfo | null>(null);
  const [cobroOpen, setCobroOpen] = useState(false);

  const load = useCallback(() => {
    getOrder(id)
      .then((o) => {
        setOrder(o);
        // E4-fix1: el idioma del PDF arranca en el del pedido si se conoce.
        if (o.language) setPdfLang(o.language as FactusolPdfLang);
        // Cobro FACTUSOL en vivo solo si hay factura (best-effort: sin
        // FACTUSOL se queda el estado persistido del pedido).
        if (o.factusol_invoice_number) {
          Promise.resolve()
            .then(() => getOrderFactusolCobro(o.id))
            .then(setCobroLive)
            .catch(() => undefined);
        }
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
  // Señal para abrir el modal de emisión desde «Siguiente paso» / «Solicitar
  // factura», y fase de la emisión (para no ofrecer dos veces «Emitir»).
  const [emitSignal, setEmitSignal] = useState(0);
  const [emitPhase, setEmitPhase] = useState<"idle" | "confirm" | "working" | "done" | "error">("idle");

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

  /** Estado de cobro FACTUSOL (contable) de la factura del pedido: FUENTE
   *  ÚNICA de la ficha (helper compartido). El estado en vivo manda sobre el
   *  persistido; todo indicador de cobro de la ficha deriva de aquí. */
  function cobroStatusOf(o: OrderDetail): FactusolCobroStatus | null {
    return resolveOrderCobroStatus(o, cobroLive);
  }

  /** Botón de la barra «Siguiente paso»: la acción que el backend dice que
   *  toca, enganchada a lo que YA hace esta ficha (el modal de emisión, el de
   *  cobro, el albarán, el email al SAT…). Cuando la acción vive en otra
   *  pantalla (aprobar, mapear líneas, vincular la empresa) lleva hasta allí
   *  en vez de inventarse un flujo nuevo. */
  function nextStepAction(): ReactNode {
    const wf = order?.workflow;
    if (!wf || !order) return null;
    const label = wf.next_action_label;
    switch (wf.next_action) {
      case "emitir_factura":
        return canEmit ? (
          <button
            type="button" className="button small"
            disabled={emitPhase === "working"}
            onClick={() => setEmitSignal((n) => n + 1)}
          >
            {emitPhase === "working" ? "Generando…" : label}
          </button>
        ) : null;
      case "registrar_cobro": {
        // Misma lógica que el botón del panel FACTUSOL: el estado en vivo
        // manda (cobrada fuera de BoHub → no ofrecer un segundo cobro).
        if (!canEmit || !order.factusol_invoice_number) return null;
        const cobrada = cobroStatusOf(order) === "cobrada";
        return (
          <button
            type="button" className="button small"
            disabled={cobrada}
            title={cobrada
              ? "La factura ya consta cobrada en FACTUSOL (no se registra un segundo cobro)"
              : "Registra el cobro de la factura en FACTUSOL (F_LCO + ESTFAC=2) con cuenta, fecha y forma de pago"}
            onClick={() => setCobroOpen(true)}
          >
            {cobrada ? "Cobrado en FACTUSOL" : label}
          </button>
        );
      }
      case "crear_envio": {
        // Lote 2 C: sin envío todavía, el paso es SUBIR LA ETIQUETA — abre el
        // selector de «Documentos de envío»; al guardarla, el backend pasa el
        // transporte a «Etiqueta creada» y la ficha se recarga. El arco
        // «Crear envío» a secas ya no se pinta en ningún sitio.
        if (order.transport_status === "not_shipped") {
          return (
            <button type="button" className="button small"
                    onClick={() => setEtiquetaSignal((n) => n + 1)}>
              {label}
            </button>
          );
        }
        // Con la etiqueta ya subida: la transición de transporte disponible
        // (recogida…); la fila de estados la omite para no repetirla.
        const t = order.available_transitions?.transport?.[0];
        return t ? (
          <button type="button" className="button small" disabled={busy}
                  onClick={() => void onFire("transport", t)}>
            {t.label}
          </button>
        ) : null;
      }
      case "marcar_completado":
        return canEmit ? (
          <button type="button" className="button small" disabled={completeBusy}
                  onClick={() => void onToggleComplete()}>
            {label}
          </button>
        ) : null;
      case "crear_albaran":
        return canEmit ? (
          <button type="button" className="button small" onClick={() => setAlbaranSignal((n) => n + 1)}>
            {label}
          </button>
        ) : null;
      case "enviar_sat":
        // El «Enviar por email» de la cabecera se esconde: es este.
        return canEmit ? (
          <button type="button" className="button small" onClick={() => setOrderEmailOpen(true)}>
            Enviar por email
          </button>
        ) : null;
      case "aprobar":
        return (
          <Link href="/erp/orders/pending-approval" className="button small">
            Ir a la Cola PEDIDOS
          </Link>
        );
      case "vincular_empresa":
        return order.company_id ? (
          <Link href={`/companies/${order.company_id}`} className="button small">
            Abrir ficha de empresa
          </Link>
        ) : null;
      case "revisar_incidencia":
        return <Link href="/erp/exceptions" className="button small">Ver excepciones</Link>;
      default:
        return null;
    }
  }

  /** Botón que resuelve cada alerta de la barra (la alerta dice QUÉ pasa;
   *  esto lleva a dónde se arregla). */
  function alertAction(a: WorkflowAlert): ReactNode {
    if (!order) return null;
    if (
      a.code === "empresa_sin_vincular" || a.code === "cliente_intracomunitario"
      || a.code === "cliente_exportacion" || a.code === "vat_no_valido_vies"
    ) {
      // VIES (Fase VIES): «Revalidar en VIES» vive en la ficha de la empresa
      // (ahí está el NIF-IVA para corregirlo si hace falta).
      return order.company_id ? (
        <Link href={`/companies/${order.company_id}`} className="button small secondary">
          {a.action === "revalidar_vies" ? "Revalidar en VIES" : "Ver ficha cliente"}
        </Link>
      ) : null;
    }
    // «cobro_no_registrado»: el botón de cobro ya está en «Siguiente paso» o
    // en el panel FACTUSOL; la alerta solo avisa.
    if (a.code === "excepcion_abierta") {
      return <Link href="/erp/exceptions" className="button small secondary">Ver excepciones</Link>;
    }
    return null;
  }

  if (!order) {
    return <main className="shell"><p className="muted">{error ?? "Cargando…"}</p></main>;
  }

  const wf = order.workflow ?? null;
  const isWeb = order.external_source === "woocommerce";
  const hasInvoice = !!order.factusol_invoice_number;
  const invoiced = hasInvoice
    || factusolStatus?.status === "invoiced"
    || order.invoice_status === "generated"
    || order.invoice_status === "invoiced_by_erp";
  // Cobro FACTUSOL (contable, distinto del «Pagado» del CRM).
  const cobroStatus = cobroStatusOf(order);
  // Transición que ya es el botón principal (no repetirla en la fila de
  // estados). Con el transporte «Sin enviar» el botón principal es «Subir
  // etiqueta», que no es una transición: nada que omitir.
  const primaryTransition = wf?.next_action === "crear_envio"
    && order.transport_status !== "not_shipped"
    && order.available_transitions?.transport?.[0]
    ? { domain: "transport" as const, to_status: order.available_transitions.transport[0].to_status }
    : null;
  const cobroTitle = !hasInvoice
    ? "Emite la factura primero: el cobro se registra sobre la factura del pedido en FACTUSOL"
    : cobroStatus === "cobrada"
      ? "La factura ya consta cobrada en FACTUSOL (no se registra un segundo cobro)"
      : "Registra el cobro de la factura en FACTUSOL (F_LCO + ESTFAC=2) con cuenta, fecha y forma de pago";
  // Lote 2 · PR-2: la miga vuelve a la cola de la que se llegó (`?from=`).
  const bandejaHref = fromQueue ? `/erp/orders?queue=${fromQueue}` : "/erp/orders";
  const bandejaLabel = fromQueue ? `Bandeja · ${QUEUE_LABEL[fromQueue]}` : "Bandeja";
  // La acción del paso actual. En móvil vive en la barra pegada abajo (una
  // sola vez); en escritorio, dentro de la tarjeta del paso actual.
  const stepAction = wf ? nextStepAction() : null;
  const stickyAction = isMobile && stepAction ? stepAction : null;
  const relevant = relevantPanel(wf?.next_action);
  const panelDefault = (p: FichaPanelId) => isWide || relevant === p;
  // Cobro de la factura: el saldo en vivo manda; si no, el persistido.
  const saldoPendiente = cobroLive?.saldo_pendiente ?? order.factusol_cobro?.saldo_pendiente ?? null;
  const totalCobrado = cobroLive?.total_cobrado ?? order.factusol_cobro?.total_cobrado ?? null;
  const eur = (n: number) => `${n.toFixed(2)} ${order.currency}`;
  const sinMapear = order.lines.filter((l) => !l.product_codart).length;
  const otrosCargos = order.total_amount - lineasBase(order) - lineasIva(order);
  const ultimoEvento = timeline.reduce<string | null>(
    (max, e) => (max == null || e.at > max ? e.at : max), null,
  );

  return (
    <main className={`shell shell-wide erp-flow erp-ficha${stickyAction ? " has-sticky-action" : ""}`}>
      {/* Lote 2 · PR-2: la miga recuerda la cola de la bandeja de la que se
          llegó («← Bandeja · Por cobrar»); sin `?from=`, la bandeja a secas. */}
      <p className="erp-ficha-back">
        <Link href={bandejaHref}>← {bandejaLabel}</Link>
      </p>
      {/* Cabecera: nº, cliente y las acciones de documento del pedido (el
          selector de idioma del PDF, el PDF del pedido, el envío por email y
          el completado) + «⋯» con el resto. Las acciones de ESTADO viven en
          «Siguiente paso» y en «Otras acciones de estado», no aquí. */}
      <PageHeader
        title={`Pedido ${order.order_number}`}
        eyebrow="ERP"
        description={`${order.external_source} · ${order.total_amount.toFixed(2)} ${order.currency}`}
        crumbs={[
          { label: "ERP" },
          { label: bandejaLabel, href: bandejaHref },
          { label: order.order_number },
        ]}
        actions={
          <>
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
              title={pdfDocumentTitle(order.factusol_document)}
              onClick={async () => {
                if (!order.factusol_document) return;
                setPdfBusy(true);
                setError(null);
                setPdfNotice(null);
                try {
                  const blob = await downloadOrderFactusolPedidoPdf(order.id, pdfLang);
                  saveBlob(blob, `Pedido_${order.order_number}.pdf`);
                } catch (e) {
                  // 404 controlado (el documento no está en FACTUSOL: pedido
                  // web aún no replicado, o prefijo de referencia de la tienda
                  // sin configurar — el aviso lo dice) → aviso discreto;
                  // cualquier otra cosa sí es un error.
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
              {pdfBusy ? "Generando…" : "PDF del pedido (FACTUSOL)"}
            </button>
            {/* Bloque 2a: el PDF de la FACTURA desde la ficha (además del
                pedido y del albarán). Se localiza la factura del pedido en
                FACTUSOL y se descarga en el idioma elegido. */}
            <button
              type="button"
              className="button small secondary"
              disabled={!invoiced || invoicePdfBusy}
              title={invoiced
                ? "Descarga el PDF de la factura de este pedido (FACTUSOL)"
                : "Emite la factura en FACTUSOL primero"}
              onClick={async () => {
                setInvoicePdfBusy(true);
                setError(null);
                try {
                  const ref = await getOrderFactusolInvoiceRef(order.id);
                  const blob = await downloadFactusolDocumentPdf(
                    "facturas", ref.serie, ref.codigo, pdfLang,
                  );
                  saveBlob(blob, `Factura_${ref.numero}.pdf`);
                } catch (e) {
                  setError(extractErrorMessage(e, "No se pudo generar el PDF de la factura."));
                } finally {
                  setInvoicePdfBusy(false);
                }
              }}
            >
              {invoicePdfBusy ? "Generando…" : "PDF de la factura"}
            </button>
            {canEmit ? (
              <>
                {/* ERP · enviar el PEDIDO al SAT / taller (y a quien haga
                    falta) con el albarán adjunto por defecto. */}
                {wf?.next_action === "enviar_sat" ? null : (
                  <button
                    type="button"
                    className="button small secondary"
                    title="Envía el pedido por email (Gmail) con el albarán adjunto; el PDF del pedido y la factura son opcionales"
                    onClick={() => setOrderEmailOpen(true)}
                  >
                    Enviar por email
                  </button>
                )}
                {/* «Enviar factura al cliente»: la factura de FACTUSOL en PDF,
                    desde el alias de la TIENDA del pedido (o de la serie), en
                    el idioma del pedido/cliente, con previsualización
                    obligatoria antes de enviar y registro en el timeline.
                    Sin factura emitida queda deshabilitado (no oculto). */}
                <button
                  type="button"
                  className="button small secondary"
                  disabled={!invoiced || emailBusy}
                  title={invoiced
                    ? "Envía la factura al cliente por email (Gmail) con el PDF adjunto; verás remitente, destinatario y asunto antes de enviar"
                    : "Emite la factura en FACTUSOL primero"}
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
                  {emailBusy ? "Localizando…" : "Enviar factura al cliente"}
                </button>
                {/* «Marcar completado»: estado final del pedido, solo en BoHub.
                    Si ya es el «Siguiente paso», el botón está allí (no se repite). */}
                {wf?.next_action === "marcar_completado" ? null : (
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
                )}
                <ActionsMenu label="Más acciones del pedido">
                  {/* E4-fix1 — idioma del pedido: dato persistente (detectado
                      en la importación Woo) editable a mano; alimenta la
                      cascada de los PDF. */}
                  <label className="erp-flow-menu-field">
                    <span>Idioma del pedido</span>
                    <select
                      aria-label="Idioma del pedido"
                      value={order.language ?? ""}
                      onChange={async (e) => {
                        const value = (e.target.value || null) as FactusolPdfLang | null;
                        setError(null);
                        try {
                          await updateOrderLanguage(order.id, value);
                          setOrder((o) => (o ? { ...o, language: value } : o));
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
                  {/* ERP-F1 «Enviar factura por email» vive ahora en la cabecera
                      como «Enviar factura al cliente» (una sola acción, sin
                      duplicarla aquí). */}
                  {/* Lote ERP · «Anular pedido» (solo manuales / FACTUSOL; los
                      web se anulan en WooCommerce): estado final reversible,
                      distinto de «quitar». Con aviso y modal; puede borrar el
                      albarán / presupuesto en FACTUSOL. */}
                  {!isWeb ? (
                    order.cancelled ? (
                      <button
                        type="button"
                        className="button small secondary"
                        disabled={cancelBusy}
                        title="Deshace la anulación en BoHub (lo borrado en FACTUSOL no se recrea)"
                        onClick={async () => {
                          setCancelBusy(true);
                          setError(null);
                          try {
                            await uncancelOrder(order.id);
                            setNotice("Pedido restaurado.");
                            await load();
                          } catch (e) {
                            setError(extractErrorMessage(e, "No se pudo restaurar el pedido."));
                          } finally {
                            setCancelBusy(false);
                          }
                        }}
                      >
                        Restaurar pedido
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="button small danger"
                        title="Anula el pedido (con aviso previo); distinto de quitarlo de la bandeja"
                        onClick={() => setCancelOpen(true)}
                      >
                        Anular pedido
                      </button>
                    )
                  ) : null}
                </ActionsMenu>
              </>
            ) : null}
          </>
        }
      />
      {error ? <p className="form-error">{error}</p> : null}
      {notice ? <p className="form-success" role="status">{notice}</p> : null}
      {pdfNotice ? <p className="muted small" role="status">{pdfNotice}</p> : null}
      {emailBusy ? (
        <p className="muted small" role="status">Localizando la factura en FACTUSOL…</p>
      ) : null}

      {/* Rediseño de flujo (Fase 1) — la «línea de vida» del pedido: quién es
          el cliente y con qué régimen de IVA, qué alertas tiene, por dónde va
          el ciclo y qué toca AHORA. Todo sale del bloque `workflow` que
          calcula el backend (el mismo que ve la bandeja): aquí no se deduce
          ningún estado. */}
      <p className="erp-flow-item-r2" style={{ margin: "0 0 12px" }}>
        <strong>{customerLabel(order) || "Sin cliente"}</strong>
        {wf ? <span className="erp-flow-pill is-n" title="Cola de la bandeja">{wf.queue_label}</span> : null}
        {wf ? <RegimePill regime={wf.regime} country={wf.company?.country} /> : null}
        {wf?.company?.factusol_id ? (
          <span className="badge ok">FACTUSOL nº {wf.company.factusol_id}</span>
        ) : wf?.company ? (
          <span className="badge warn">Empresa sin vincular a FACTUSOL</span>
        ) : null}
      </p>
      {/* Lote 2 · PR-2: la barra de 7 segmentos («Paso 6 de 7 · Cobro»), la
          lectura rápida sin recorrer la línea de vida. */}
      {wf ? <WorkflowProgress steps={wf.steps} /> : null}
      {wf ? (
        <WorkflowAlerts
          alerts={wf.alerts}
          // Con incidencia bloqueante, «Siguiente paso» ya lleva ESA acción:
          // la alerta no la repite.
          renderAction={(a) => (wf.blocked && a.action === wf.next_action ? null : alertAction(a))}
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
      {order.cancelled ? (
        <p className="form-error" role="status">
          <span className="badge muted">Anulado</span>{" "}
          Pedido anulado el{" "}
          {order.cancelled_at ? new Date(order.cancelled_at).toLocaleString("es-ES") : "—"}
          {order.cancelled_by_name ? ` por ${order.cancelled_by_name}` : ""}
          {order.cancelled_reason ? ` — ${order.cancelled_reason}` : ""}. Fuera de la
          bandeja, las colas y el seguimiento; se puede restaurar desde «⋯».
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
      <div className="erp-ficha-cols">
      <div className="erp-ficha-main">
      {/* Lote 2 · PR-2 — la línea de vida VERTICAL: los 7 pasos en columna,
          cada uno con su dato (fecha, importe, nº de albarán/factura) y el
          actual como la única tarjeta azul, con «Siguiente paso» y su acción
          dentro. Si no hay paso actual (todo hecho), «Siguiente paso» va
          debajo de la lista. Todo sale de `workflow` (backend). */}
      {wf ? (
        <section className="erp-flow-panel erp-lifeline" aria-label="Línea de vida del pedido">
          <h3>Línea de vida del pedido</h3>
          <WorkflowSteps
            steps={wf.steps}
            vertical
            renderAction={(s) => {
              const nowBar = s.state === "now" ? (
                <NextActionBar workflow={wf} embedded>{isMobile ? null : stepAction}</NextActionBar>
              ) : null;
              // Bloque 8 — en el paso «Factura», y solo cuando la factura ya
              // existe (si no, el paso es EMITIR, no enviar), se ve de un
              // vistazo si se ha enviado al cliente desde la app y cuándo.
              const enviada = s.key === "factura" && hasInvoice ? (
                <InvoiceEmailedNote order={order} />
              ) : null;
              if (!nowBar && !enviada) return null;
              return <>{nowBar}{enviada}</>;
            }}
          />
          {wf.steps.some((s) => s.state === "now") ? null : (
            <NextActionBar workflow={wf}>{isMobile ? null : stepAction}</NextActionBar>
          )}
        </section>
      ) : null}

      {/* Las transiciones de estado que no son la principal (Reembolso,
          Empezar preparación, Bloquear, Recogido…), en una fila compacta:
          ninguna se pierde. Dos NO se pintan (el arco sigue en el backend):
          «Solicitar factura» (invoice → pending), alias de «Emitir factura
          FACTUSOL», el único botón de factura (Bloque 2b); y «Crear envío»
          (transport → label_created), que ahora es subir la etiqueta
          (Lote 2 C). */}
      <OrderStatusMachine
        order={order}
        onFire={onFire}
        busy={busy}
        omit={primaryTransition}
        hide={[
          { domain: "invoice", to_status: "pending" },
          { domain: "transport", to_status: "label_created" },
        ]}
      />

      {order.exceptions.length > 0 ? (
        <section className="erp-flow-panel" aria-label="Excepciones">
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

      {/* Lote 3 · #1 — reequilibrio de columnas: «Líneas» y «Envío y
          seguimiento» (los dos paneles más altos) bajan a la columna
          izquierda, junto a la línea de vida; la derecha se queda con el
          resumen económico, FACTUSOL, el cliente FACTUSOL (web) y el
          historial. Así las dos columnas quedan parejas en escritorio; por
          debajo de 1280 se apilan igual (todo el contenido se conserva). */}
      <FichaPanel
        id="lineas"
        title="Líneas"
        summary={[
          `${order.lines.length} ${order.lines.length === 1 ? "artículo" : "artículos"}`,
          sinMapear > 0 ? `${sinMapear} sin mapear` : null,
          Math.abs(otrosCargos) >= 0.01 ? `portes ${eur(otrosCargos)}` : null,
        ].filter(Boolean).join(" · ")}
        defaultOpen={panelDefault("lineas")}
      >
        <div className="erp-flow-table">
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
        </div>
      </FichaPanel>

      {/* Envío y seguimiento: el albarán vive AQUÍ y solo aquí (el de
          FACTUSOL —nº, PDF, crearlo si falta— y el fichero subido a mano /
          descargado de Woo), la etiqueta (cuya subida mueve el transporte:
          al subirla, la ficha se recarga para que la línea de vida y el
          «Siguiente paso» lo reflejen) y los campos del Excel de
          seguimiento (ERP-F6). «Crear albarán» / «Subir etiqueta» desde el
          paso actual abren el panel si estaba plegado. */}
      <FichaPanel
        id="envio"
        title="Envío y seguimiento"
        summary={[
          TRANSPORT_TEXT[order.transport_status] ?? order.transport_status,
          order.factusol_albaran_number
            ? `albarán ${order.factusol_albaran_number}`
            : isWeb ? null : "sin albarán",
          order.tracking_number ? `seguimiento ${order.tracking_number}` : null,
        ].filter(Boolean).join(" · ")}
        defaultOpen={panelDefault("envio")}
        openSignal={albaranSignal + etiquetaSignal}
      >
        <ShippingFilesSection
          orderId={order.id}
          isWooOrder={isWeb}
          orderSource={order.external_source}
          factusolAlbaranNumber={order.factusol_albaran_number ?? null}
          pdfLang={pdfLang}
          canCreateAlbaran={canEmit}
          createSignal={albaranSignal}
          onAlbaranCreated={({ error: err }) => { if (err) setError(err); load(); }}
          openEtiquetaSignal={etiquetaSignal}
          onUploaded={() => load()}
        />
        <SeguimientoFieldsCard
          order={order}
          canEdit={canEmit}
          onSaved={(patch) => setOrder((o) => (o ? { ...o, ...patch } : o))}
          onError={setError}
        />
      </FichaPanel>
      </div>

      <div className="erp-ficha-side">
        <EconomicSummary
          order={order}
          hasInvoice={hasInvoice}
          cobroStatus={cobroStatus}
          saldoPendiente={saldoPendiente}
          totalCobrado={totalCobrado}
        />
        {/* Bloque FACTUSOL: lo que hay allí y la acción de cada cosa. El
            albarán (y su PDF) vive en «Documentos de envío». */}
        <section className="erp-flow-panel" aria-label="FACTUSOL">
          <h3>FACTUSOL</h3>
          <div className="erp-flow-kv">
            <span className="k">Cliente</span>
            <span className="v">
              {wf?.company?.factusol_id
                ? `${wf.company.factusol_id} · vinculado`
                : wf?.company ? "sin vincular" : "—"}
            </span>
          </div>
          <div className="erp-flow-kv">
            <span className="k">Albarán</span>
            <span className="v">
              {order.factusol_albaran_number || (isWeb ? "lo crea WooCommerce" : "—")}
            </span>
          </div>
          <div className="erp-flow-kv">
            <span className="k">Factura</span>
            <div className="v erp-flow-kv-actions">
              {canEmit ? (
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
                  // Si emitir ya es el «Siguiente paso», el botón está allí; y
                  // el nº de albarán ya lo dice la fila de arriba.
                  buttonHidden={wf?.next_action === "emitir_factura"}
                  albaranBadgeHidden
                  onPhaseChange={setEmitPhase}
                  onInvoiced={() => load()}
                />
              ) : (
                order.factusol_invoice_number || "pendiente"
              )}
            </div>
          </div>
          {/* Cobro manual (F-4-B desde la app): estado de cobro EN FACTUSOL
              de la factura del pedido y el botón que abre el modal
              compartido. Sin factura → deshabilitado con tooltip, nunca un
              error rojo; ya cobrada → «Cobrado», sin doble cobro. El cobro
              es SIEMPRE manual: nada lo registra por su cuenta. */}
          <div className="erp-flow-kv">
            <span className="k">Cobro</span>
            <div className="v erp-flow-kv-actions">
              {hasInvoice ? (
                <CobroFactusolBadge
                  hasInvoice
                  status={cobroStatus}
                  cobro={order.factusol_cobro}
                />
              ) : (
                <span className="muted small">sin factura</span>
              )}
              {cobroLive?.status === "pendiente" && cobroLive.saldo_pendiente != null ? (
                <span className="muted small">saldo {cobroLive.saldo_pendiente.toFixed(2)} €</span>
              ) : null}
              {canEmit && wf?.next_action !== "registrar_cobro" ? (
                <button
                  type="button"
                  className="button small secondary"
                  disabled={!hasInvoice || cobroStatus === "cobrada"}
                  title={cobroTitle}
                  onClick={() => setCobroOpen(true)}
                >
                  {cobroStatus === "cobrada" ? "Cobrado en FACTUSOL" : "Registrar cobro en FACTUSOL"}
                </button>
              ) : null}
            </div>
          </div>
        </section>

        {/* Lote 3 · #6 — cliente FACTUSOL también en los pedidos WEB. El
            mismo panel de la ficha de empresa (ver el nº F_CLI, «Traer datos»,
            completar el NIF que falte…) resuelto por `company_id`. Sin empresa
            vinculada (raro) avisa discreto; nunca inventa datos del cliente. */}
        {isWeb ? (
          <OrderFactusolClientPanel companyId={order.company_id} onChanged={load} />
        ) : null}

        <FichaPanel
          id="historial"
          title="Historial"
          summary={timeline.length === 0
            ? "Sin eventos"
            : `${timeline.length} ${timeline.length === 1 ? "evento" : "eventos"}${ultimoEvento ? ` · último ${hace(ultimoEvento)}` : ""}`}
          defaultOpen={panelDefault("historial")}
        >
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
        </FichaPanel>
      </div>
      </div>

      {/* Lote 2 · PR-2 (E7) — en móvil la acción del paso actual va pegada
          al borde inferior, una sola vez (la tarjeta del paso solo lleva el
          texto). Va al FINAL del contenido, que es como `sticky` se mantiene
          visible. */}
      {stickyAction && wf ? (
        <PrimaryActionBar hint={wf.next_action_hint}>{stickyAction}</PrimaryActionBar>
      ) : null}

      {cobroOpen ? (
        <RegistrarCobroModal
          orderId={order.id}
          orderNumber={order.order_number}
          onClose={() => setCobroOpen(false)}
          onDone={(info) => { setCobroLive(info); load(); }}
        />
      ) : null}
      {cancelOpen ? (
        <CancelOrderModal
          orderId={order.id}
          orderNumber={order.order_number}
          onClose={() => setCancelOpen(false)}
          onDone={() => { void load(); }}
        />
      ) : null}
      {invoiceRef ? (
        <InvoiceEmailModal
          serie={invoiceRef.serie}
          codigo={invoiceRef.codigo}
          numero={invoiceRef.numero}
          orderId={order.id}
          onClose={() => setInvoiceRef(null)}
          onSent={() => { setInvoiceRef(null); load(); }}
        />
      ) : null}
      {/* ERP · enviar el pedido al SAT / taller con el albarán adjunto. */}
      {orderEmailOpen ? (
        <OrderEmailModal
          orderId={order.id}
          orderNumber={order.order_number}
          onClose={() => setOrderEmailOpen(false)}
          onSent={() => load()}
          onCreateAlbaran={() => { setOrderEmailOpen(false); setAlbaranSignal((n) => n + 1); }}
        />
      ) : null}
      {embalarOpen ? (
        <EmbalarModal
          orderId={order.id}
          onCancel={() => setEmbalarOpen(false)}
          onDone={() => { setEmbalarOpen(false); load(); }}
        />
      ) : null}
    </main>
  );
}

const REGIME_TEXT: Record<string, string> = {
  nacional: "nacional",
  intracomunitario: "intracomunitario",
  exportacion: "exportación",
};

/** Lote 3 · #8 — en el paso «Factura» de la línea de vida, de un vistazo (sin
 *  abrir el historial): si la factura se ha enviado al cliente por email DESDE
 *  la app y cuándo. Solo se pinta si la factura ya existe (el propio paso ya
 *  filtra por eso). Los destinatarios van en el `title`. */
function InvoiceEmailedNote({ order }: { order: OrderDetail }) {
  const at = order.invoice_emailed_at ?? null;
  const to = order.invoice_emailed_to ?? [];
  if (at) {
    return (
      <p
        className="erp-flow-emailed is-sent"
        title={to.length ? `Enviada a ${to.join(", ")}` : undefined}
      >
        Factura enviada al cliente el {formatDMY(at)}
      </p>
    );
  }
  return <p className="erp-flow-emailed is-unsent">Sin enviar al cliente</p>;
}

/** Base imponible del pedido: la suma de sus líneas. */
function lineasBase(order: OrderDetail): number {
  return order.lines.reduce((s, l) => s + (l.line_total ?? 0), 0);
}

/** IVA de las líneas según el régimen del cliente: intracomunitario y
 *  exportación van exentos (0). */
function lineasIva(order: OrderDetail): number {
  const regime = order.workflow?.regime ?? null;
  if (regime === "intracomunitario" || regime === "exportacion") return 0;
  return order.lines.reduce((s, l) => s + (l.line_total ?? 0) * (l.tax_rate ?? 0) / 100, 0);
}

/** Resumen económico del pedido CON SU RÉGIMEN: la base sale de las líneas y
 *  el IVA de su tipo, salvo que el cliente sea intracomunitario o de
 *  exportación — entonces la factura va exenta y aquí se dice, que es donde
 *  se mira antes de emitir. El total es siempre el del pedido: si no cuadra
 *  con base + IVA (portes de la cabecera, descuentos), la diferencia se
 *  enseña en vez de esconderla.
 *
 *  Lote 2 · PR-2: el «pendiente de cobro» es el dato que más se consulta y
 *  va aquí, en bloque ámbar con la cifra grande (verde cuando la factura ya
 *  está cobrada; sin factura, no hay nada que cobrar todavía). El saldo es
 *  el de FACTUSOL: el comprobado en vivo o, si no, el persistido. */
function EconomicSummary({
  order, hasInvoice, cobroStatus, saldoPendiente, totalCobrado,
}: {
  order: OrderDetail;
  hasInvoice: boolean;
  cobroStatus: FactusolCobroStatus | null;
  saldoPendiente: number | null;
  totalCobrado: number | null;
}) {
  const regime = order.workflow?.regime ?? null;
  const exento = regime === "intracomunitario" || regime === "exportacion";
  const base = lineasBase(order);
  const iva = lineasIva(order);
  const otros = order.total_amount - base - iva;
  const pago = order.factusol_payment ?? null;
  const eur = (n: number) => `${n.toFixed(2)} ${order.currency}`;
  const cobrada = cobroStatus === "cobrada";
  const cobroTone = !hasInvoice ? "na" : cobrada ? "done" : "pending";
  const pendiente = !hasInvoice
    ? null
    : saldoPendiente ?? (cobrada ? 0 : null);
  const cobrado = totalCobrado ?? (cobrada ? order.total_amount : hasInvoice ? 0 : null);
  return (
    <section className="erp-flow-panel" aria-label="Resumen económico">
      <h3>Resumen económico</h3>
      <p className="erp-flow-kv">
        <span className="k">Base imponible</span>
        <span className="v">{eur(base)}</span>
      </p>
      <p className="erp-flow-kv">
        <span className="k">IVA{regime ? ` (${REGIME_TEXT[regime] ?? regime})` : ""}</span>
        <span className="v">{exento ? `${eur(0)} · exento` : eur(iva)}</span>
      </p>
      {Math.abs(otros) >= 0.01 ? (
        <p className="erp-flow-kv">
          <span className="k">Portes y otros cargos</span>
          <span className="v">{eur(otros)}</span>
        </p>
      ) : null}
      <p className="erp-flow-kv">
        <span className="k">Forma de pago</span>
        <span className="v">
          {pago?.forma_pago_nombre || pago?.forma_pago || "—"}
          {pago?.contrapartida_nombre ? ` · ${pago.contrapartida_nombre}` : ""}
        </span>
      </p>
      {/* Fase 2 (opción B): lo apuntado al convertir. El cobro en FACTUSOL es
          otra cosa (y siempre manual): lo dice el bloque FACTUSOL. */}
      {pago ? (
        <p className="erp-flow-kv">
          <span className="k">Pago al convertir</span>
          <span className="v">
            {pago.paid ? (
              <>
                <span className="badge ok">Pagado (apuntado)</span>
                {pago.fecha ? ` · fecha ${pago.fecha}` : ""}
              </>
            ) : (
              <>
                <span className="badge muted">Sin pago</span> · pendiente
              </>
            )}
          </span>
        </p>
      ) : null}
      <p className="erp-flow-total">
        <span className="k muted">Total</span>
        <span className="n">{eur(order.total_amount)}</span>
      </p>
      {hasInvoice ? (
        <p className="erp-flow-kv">
          <span className="k">Cobrado</span>
          <span className="v">{cobrado != null ? eur(cobrado) : "—"}</span>
        </p>
      ) : null}
      <div
        className={`erp-flow-cobro is-${cobroTone}`}
        role="group"
        aria-label="Pendiente de cobro"
      >
        <span className="erp-flow-cobro-k">Pendiente de cobro</span>
        <span className="erp-flow-cobro-n">{pendiente != null ? eur(pendiente) : "—"}</span>
        <span className="erp-flow-cobro-s">
          {!hasInvoice
            ? "sin factura emitida"
            : cobrada
              ? "factura cobrada en FACTUSOL"
              : pendiente == null
                ? "saldo sin comprobar en FACTUSOL"
                : "de la factura en FACTUSOL"}
        </span>
      </div>
    </section>
  );
}

/** Lote 2 · PR-2 — panel plegable de la ficha: `<details>` con el título y
 *  un resumen de lo esencial en la cabecera («3 artículos · portes 45,00 €»),
 *  para que plegado no se pierda información. Recuerda por usuario (en el
 *  navegador) si se dejó abierto o cerrado; mientras no haya elegido, manda
 *  `defaultOpen` (el panel del paso actual, o todos desde 1280 px). Un
 *  `openSignal` que sube lo abre desde fuera (p. ej. «Crear albarán» desde
 *  el paso actual abre «Envío y seguimiento»), sin tocar lo recordado. */
function FichaPanel({
  id, title, summary, defaultOpen, openSignal = 0, children,
}: {
  id: FichaPanelId;
  title: string;
  summary: string;
  defaultOpen: boolean;
  openSignal?: number;
  children: ReactNode;
}) {
  const [stored, setStored] = usePersistentState<boolean | null>(PANEL_LS_KEY[id], null);
  // Señal externa ya «consumida»: al cerrarlo a mano se anota la actual para
  // que no lo vuelva a abrir hasta la siguiente.
  const [closedAt, setClosedAt] = useState(0);
  const open = openSignal > closedAt || (stored ?? defaultOpen);
  const bodyId = `erp-ficha-panel-${id}`;
  return (
    <details
      className={`erp-flow-panel erp-ficha-panel${open ? " is-open" : ""}`}
      open={open}
      aria-label={title}
    >
      <summary
        className="erp-ficha-panel-head"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={(e) => {
          // Controlado por React (no por el toggle nativo): el estado se
          // recuerda y sirve en cualquier navegador igual.
          e.preventDefault();
          if (open) setClosedAt(openSignal);
          setStored(!open);
        }}
      >
        <span className="erp-ficha-panel-txt">
          <h3>{title}</h3>
          <span className="erp-ficha-panel-sum">{summary}</span>
        </span>
        <span className="erp-ficha-panel-chev" aria-hidden>{open ? "▴" : "▾"}</span>
      </summary>
      <div id={bodyId} className="erp-ficha-panel-body">{children}</div>
    </details>
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
    <section className="erp-flow-panel" aria-label="Seguimiento">
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
