"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { PageHeader } from "../../components/PageHeader";
import { CobroFactusolBadge } from "../../components/erp/CobroFactusolBadge";
import { ExcludeSeguimientoModal } from "../../components/erp/ExcludeSeguimientoModal";
import { OrderStatusBadge } from "../../components/erp/OrderStatusBadge";
import { isInvoiced, OrderStatusPills } from "../../components/erp/OrderStatusPills";
import { RegistrarCobroModal } from "../../components/erp/RegistrarCobroModal";
import {
  QUEUE_COLOR,
  QUEUE_HINT,
  QUEUE_LABEL,
  WorkflowQueueCards,
} from "../../components/erp/flow/WorkflowQueueCards";
import { WorkflowAlerts } from "../../components/erp/flow/WorkflowAlerts";
import { regimeLabel } from "../../components/erp/flow/RegimePill";
import { ActionsMenu } from "../../components/erp/flow/ActionsMenu";
import { getCurrentUser, type User } from "../../lib/api";
import { extractErrorMessage } from "../../lib/errors";
import {
  approveOrder,
  approveOrdersBulk,
  completeOrder,
  completeOrdersBulk,
  customerLabel,
  ERP_EDIT_ROLES,
  excludeSeguimiento,
  type ExclusionReasonCode,
  getErpSettings,
  includeSeguimiento,
  listOrders,
  type OrderCobroInfo,
  type OrderSummary,
  refreshOrdersFactusolCobro,
  uncompleteOrder,
  type WorkflowQueue,
} from "../../lib/erpApi";

function d(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, day] = iso.slice(0, 10).split("-");
  return `${Number(day)}/${Number(m)}/${y}`;
}

// --- filtros de refinamiento (Lote B7) ---------------------------------------

type SortDir = "desc" | "asc";
type Vista = "cards" | "list";

/** Vista de revisión (Lote 2 A2): la bandeja normal («activos»), SOLO los
 *  quitados a mano («ocultados») o SOLO los anulados («anulados»). Son
 *  EXCLUYENTES: un pedido anulado nunca se mezcla con los ocultados, y al
 *  backend va un solo flag (o ninguno). No se persiste: son vistas de
 *  revisión, no tiene sentido volver a entrar en ellas sin querer. */
type Revision = "activos" | "ocultados" | "anulados";
const REVISION_OPTIONS: [Revision, string, string][] = [
  ["activos", "Activos", "La bandeja: los pedidos en curso"],
  ["ocultados", "Ocultados", "Solo los quitados a mano de la bandeja, con su motivo y «Reincluir»"],
  ["anulados", "Anulados", "Solo los anulados (se restauran desde la ficha)"],
];

/** Cola válida en `?queue=` (Lote 2 D: el inicio del ERP y los enlaces a la
 *  antigua Cola PEDIDOS entran por `/erp/orders?queue=por_revisar`). */
function queueFromParam(value: string | null | undefined): WorkflowQueue | null {
  return value && value in QUEUE_LABEL ? (value as WorkflowQueue) : null;
}

/** Los filtros que se PERSISTEN (últimos usados). Los «Ver …» (procesados
 *  externamente / ocultados / anulados) no: son vistas de revisión y no tiene
 *  sentido volver a entrar en ellas sin querer. */
type Filtros = {
  prep: string;
  payment: string;
  /** "" = todos, "yes" = solo completados, "no" = sin completar. */
  completed: string;
  cobro: string;
  /** "" = todos, "yes" = facturados, "no" = sin facturar. */
  invoiced: string;
  /** "" = todas, "enviada" / "no_enviada" (factura enviada al cliente). */
  invoiceEmail: string;
  /** Slug de la tienda Woo (artisjet / boprint / fluxlasers…). */
  store: string;
  from: string;
  to: string;
  sortDir: SortDir;
};

/** Al entrar por primera vez: pagados y sin completar (lo que toca trabajar).
 *  Es reversible: «Limpiar filtros» lo deja todo en «todos» y «Por defecto»
 *  vuelve a esto. */
const DEFAULT_FILTROS: Filtros = {
  prep: "", payment: "paid", completed: "no", cobro: "", invoiced: "",
  invoiceEmail: "", store: "", from: "", to: "", sortDir: "desc",
};
/** «Limpiar filtros»: todo a «todos», sin ningún valor por defecto. */
const EMPTY_FILTROS: Filtros = { ...DEFAULT_FILTROS, payment: "", completed: "" };

const LS_FILTROS = "erp.bandeja.filtros";
const LS_VISTA = "erp.bandeja.vista";
/** Tope de filas por carga (el del backend). */
const PAGE_LIMIT = 100;

const PREP_OPTIONS: [string, string][] = [
  ["pending_review", "Pend. revisión"], ["in_queue", "En cola"], ["preparing", "Preparando"],
  ["packed", "Embalado"], ["blocked", "Bloqueado"],
];
const PAYMENT_OPTIONS: [string, string][] = [
  ["pending", "Pendiente"], ["paid", "Pagado"], ["failed", "Fallido"], ["refunded", "Reembolsado"],
];
const COMPLETED_OPTIONS: [string, string][] = [["yes", "Solo completados"], ["no", "Sin completar"]];
const COBRO_OPTIONS: [string, string][] = [
  ["cobrada", "Cobrado en FACTUSOL"], ["pendiente", "Pendiente de cobro"],
  ["sin_comprobar", "Con factura, sin comprobar"],
];
const INVOICED_OPTIONS: [string, string][] = [["yes", "Facturado"], ["no", "Sin facturar"]];
const INVOICE_EMAIL_OPTIONS: [string, string][] = [
  ["enviada", "Enviada"], ["no_enviada", "No enviada"],
];

function optionLabel(options: [string, string][], value: string): string {
  return options.find(([v]) => v === value)?.[1] ?? value;
}

/** Lo guardado en localStorage puede ser de otra versión: solo se aceptan las
 *  claves conocidas y valores de texto. Sin nada guardado → los defaults. */
function sanitizeFiltros(raw: unknown): Filtros {
  if (!raw || typeof raw !== "object") return DEFAULT_FILTROS;
  const src = raw as Record<string, unknown>;
  const out: Filtros = { ...EMPTY_FILTROS };
  for (const key of Object.keys(EMPTY_FILTROS) as (keyof Filtros)[]) {
    const v = src[key];
    if (key === "sortDir") {
      if (v === "asc" || v === "desc") out.sortDir = v;
    } else if (typeof v === "string") {
      out[key] = v;
    }
  }
  return out;
}

function leerFiltros(): Filtros {
  try {
    const raw = window.localStorage.getItem(LS_FILTROS);
    return raw ? sanitizeFiltros(JSON.parse(raw)) : DEFAULT_FILTROS;
  } catch {
    return DEFAULT_FILTROS;
  }
}

function leerVista(): Vista {
  try {
    return window.localStorage.getItem(LS_VISTA) === "list" ? "list" : "cards";
  } catch {
    return "cards";
  }
}

function guardar(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Sin localStorage (modo privado, cuota…): la bandeja funciona igual.
  }
}

function mismosFiltros(a: Filtros, b: Filtros): boolean {
  return (Object.keys(EMPTY_FILTROS) as (keyof Filtros)[]).every((k) => a[k] === b[k]);
}

/** ERP · Pedidos — la BANDEJA DE TRABAJO (rediseño de flujo, Fase 1; Lote B7).
 *
 *  Ya no es una tabla de cuatro estados que hay que interpretar: arriba están
 *  las colas con su contador (por revisar / por facturar / por cobrar / por
 *  enviar / incidencias / listo) y cada pedido enseña su SIGUIENTE ACCIÓN como
 *  botón principal, su alerta si la tiene y el importe con el régimen de IVA
 *  del cliente. Quién decide todo eso es el backend (`workflow`), el mismo
 *  bloque que consume la ficha.
 *
 *  Lote B7: al entrar se ven los pagados sin completar (reversible con
 *  «Limpiar filtros»), se puede acotar por facturado / tienda / fechas y
 *  ordenar por fecha, cada pedido lleva las cuatro pastillas de estado
 *  (Pagado · Facturado · Cobro registrado · Completado) y hay una vista de
 *  LISTA (tabla) además de las tarjetas, con las mismas acciones.
 *
 *  No se ha perdido ninguna acción: los filtros de siempre quedan como
 *  refinamiento y las acciones por fila (Completar/Desmarcar, Registrar cobro,
 *  Quitar/Reincluir) viven en el menú «⋯» de cada tarjeta; las de bloque
 *  (Aprobar seleccionados, Completar seleccionados, Quitar de la bandeja,
 *  Reincluir) siguen sobre la lista con la selección múltiple.
 *
 *  Lote 2 (A2 + D): «Activos / Ocultados / Anulados» es un solo control
 *  excluyente; la Cola PEDIDOS ya no es una pantalla aparte — es la cola
 *  «Por revisar» (`?queue=por_revisar` la preselecciona) y «Aprobar» se hace
 *  aquí mismo, uno a uno o en bloque. */
function ErpOrdersScreen() {
  const searchParams = useSearchParams();
  // `?queue=` manda sobre lo recordado (solo para la cola; los filtros de
  // refinamiento siguen siendo los últimos usados).
  const urlQueue = queueFromParam(searchParams?.get("queue"));
  const [user, setUser] = useState<User | null>(null);
  const [rows, setRows] = useState<OrderSummary[]>([]);
  const [counts, setCounts] = useState<Partial<Record<WorkflowQueue, number>>>({});
  // Cola de trabajo elegida (organización primaria); null = todas.
  const [queue, setQueue] = useState<WorkflowQueue | null>(null);
  // Filtros de refinamiento (persistidos) + vista. Se leen de localStorage al
  // montar (no en el render inicial, para no desincronizar la hidratación) y
  // hasta entonces no se pide nada al backend: una sola carga, con lo bueno.
  const [filtros, setFiltros] = useState<Filtros>(DEFAULT_FILTROS);
  const [vista, setVista] = useState<Vista>("cards");
  const [ready, setReady] = useState(false);
  // B-2-fix4: por defecto la bandeja esconde los procesados externamente.
  const [showExternal, setShowExternal] = useState(false);
  // Vista de revisión (A2): activos / SOLO ocultados / SOLO anulados.
  const [revision, setRevision] = useState<Revision>("activos");
  const showExcluded = revision === "ocultados";
  const showCancelled = revision === "anulados";
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Selección múltiple de filas (para quitar / reincluir en bloque).
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Pedidos a QUITAR (abre el diálogo de motivo + avisos): una fila o la selección.
  const [excludeTarget, setExcludeTarget] = useState<OrderSummary[] | null>(null);
  // Cobro FACTUSOL (estado contable, no el «Pagado» del CRM): la fila con el
  // modal «Registrar cobro» abierto y el refresco en bloque «Actualizar
  // cobros FACTUSOL». El filtro vive en `filtros.cobro`.
  const [cobroTarget, setCobroTarget] = useState<OrderSummary | null>(null);
  const [refreshingCobros, setRefreshingCobros] = useState(false);
  // Tiendas Woo dadas de alta (para el filtro «Tienda» y la pastilla de origen).
  const [stores, setStores] = useState<{ slug: string; label: string }[]>([]);

  const canEdit = !!user && (ERP_EDIT_ROLES as readonly string[]).includes(user.role);
  // En «Ver anulados» no hay acciones de bloque (se reactivan desde la ficha).
  const selectable = canEdit && !showCancelled;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await listOrders({
        preparation: filtros.prep || undefined,
        payment: filtros.payment || undefined,
        show_external: showExternal,
        show_excluded: showExcluded,
        show_cancelled: showCancelled,
        completed: filtros.completed === "" ? undefined : filtros.completed === "yes",
        invoiced: filtros.invoiced === "" ? undefined : filtros.invoiced === "yes",
        store_slug: filtros.store || undefined,
        placed_from: filtros.from || undefined,
        placed_to: filtros.to || undefined,
        cobro: (filtros.cobro || undefined) as "cobrada" | "pendiente" | "sin_comprobar" | undefined,
        invoice_email: (filtros.invoiceEmail || undefined) as "enviada" | "no_enviada" | undefined,
        queue: queue ?? undefined,
        sort: filtros.sortDir === "asc" ? "placed_asc" : "placed_desc",
        limit: PAGE_LIMIT,
      });
      setRows(r.items);
      // Los contadores llegan de TODO lo filtrado (antes de elegir cola): la
      // cabecera no se vacía al meterse en una cola.
      setCounts(r.queue_counts);
      // Al cambiar de vista/filtros o tras una acción, la selección deja de tener sentido.
      setSelected(new Set());
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron cargar los pedidos."));
    } finally {
      setLoading(false);
    }
  }, [filtros, showExternal, showExcluded, showCancelled, queue]);

  useEffect(() => {
    getCurrentUser().then(setUser).catch(() => undefined);
    getErpSettings()
      .then((s) => setStores((s.woocommerce_stores ?? []).map((t) => ({ slug: t.slug, label: t.label }))))
      .catch(() => undefined);
  }, []);

  // Últimos filtros y vista del usuario (si los hay); luego ya se carga.
  useEffect(() => {
    setFiltros(leerFiltros());
    setVista(leerVista());
    setReady(true);
  }, []);

  // Lote 2 D: la cola de la URL se preselecciona (y se sigue si cambia).
  useEffect(() => { if (urlQueue) setQueue(urlQueue); }, [urlQueue]);

  useEffect(() => { if (ready) void load(); }, [ready, load]);
  useEffect(() => { if (ready) guardar(LS_FILTROS, JSON.stringify(filtros)); }, [ready, filtros]);
  useEffect(() => { if (ready) guardar(LS_VISTA, vista); }, [ready, vista]);
  // La URL refleja la cola elegida (`?queue=`), para que recargar o compartir
  // el enlace vuelva a la misma cola; sin cola, sin parámetro.
  useEffect(() => {
    if (!ready) return;
    try {
      const url = new URL(window.location.href);
      if (queue) url.searchParams.set("queue", queue);
      else url.searchParams.delete("queue");
      if (url.href !== window.location.href) {
        window.history.replaceState(window.history.state, "", url);
      }
    } catch {
      // Sin acceso a location/history: la bandeja funciona igual.
    }
  }, [ready, queue]);

  function setFiltro<K extends keyof Filtros>(key: K, value: Filtros[K]) {
    setFiltros((prev) => ({ ...prev, [key]: value }));
  }

  function toggleSort() {
    setFiltro("sortDir", filtros.sortDir === "desc" ? "asc" : "desc");
  }

  /** «Limpiar filtros»: TODO a «todos» (ni los defaults, ni cola, ni vistas
   *  de revisión). */
  function limpiarFiltros() {
    setFiltros(EMPTY_FILTROS);
    setQueue(null);
    setShowExternal(false);
    setRevision("activos");
  }

  /** Tienda de un pedido web por el prefijo del nº (`BOPRIN-…` → boprint):
   *  el mismo cálculo que hace el backend al numerar (`slug.upper()[:6]`). */
  const storeByPrefix = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of stores) m.set(s.slug.slice(0, 6).toUpperCase(), s.label);
    return m;
  }, [stores]);

  /** Origen del pedido, como pastilla (tienda web / manual / FACTUSOL). */
  function sourcePill(o: OrderSummary) {
    const web = o.external_source === "woocommerce";
    const manual = o.external_source === "manual";
    const tienda = web ? storeByPrefix.get(o.order_number.split("-")[0].toUpperCase()) : null;
    return (
      <span
        className={`erp-flow-src${web ? " is-woo" : manual ? " is-man" : ""}`}
        title={web ? `Pedido web${tienda ? ` · ${tienda}` : ""}` : undefined}
      >
        {web ? tienda ?? "woo" : o.external_source}
      </span>
    );
  }

  function toggleRow(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelected((prev) =>
      prev.size === rows.length ? new Set() : new Set(rows.map((r) => r.id)),
    );
  }

  // «Quitar»: abre el diálogo (motivo + avisos) para una fila o la selección.
  // Cualquier pedido, cualquier estado; con factura/cobro/albarán se AVISA.
  function openExclude(target: OrderSummary[]) {
    if (target.length === 0) return;
    setError(null);
    setNotice(null);
    setExcludeTarget(target);
  }

  function onExcludeSelected() {
    openExclude(rows.filter((r) => selected.has(r.id)));
  }

  async function onConfirmExclude(reason: string, reasonCode?: ExclusionReasonCode) {
    if (!excludeTarget) return;
    setBusy(true);
    setError(null);
    try {
      const r = await excludeSeguimiento(
        excludeTarget.map((x) => x.id), reason || undefined, reasonCode,
      );
      setExcludeTarget(null);
      setNotice(
        `${r.excluded} pedido(s) quitado(s) de la bandeja (y del seguimiento)`
        + (r.already_excluded > 0 ? ` (${r.already_excluded} ya estaban fuera)` : "")
        + ". Se deshace con «Reincluir» en «Ver ocultados».",
      );
      await load();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron quitar los pedidos de la bandeja."));
    } finally {
      setBusy(false);
    }
  }

  // «Reincluir»: deshace la exclusión (una fila o la selección). Idempotente.
  async function onIncludeRows(ids: string[]) {
    if (ids.length === 0) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await includeSeguimiento(ids);
      setNotice(`${r.included} pedido(s) reincluido(s) en la bandeja (y en el seguimiento).`);
      await load();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron reincluir los pedidos."));
    } finally {
      setBusy(false);
    }
  }

  function onIncludeSelected() {
    void onIncludeRows([...selected]);
  }

  // «Marcar completado» / «Desmarcar» (solo BoHub, reversible). No exige envío
  // ni factura: si no está facturado, avisa y deja continuar. Nunca toca
  // WooCommerce.
  async function onToggleComplete(o: OrderSummary) {
    if (
      !o.completed && !isInvoiced(o)
      && !window.confirm(`${o.order_number} aún no está facturado. ¿Marcarlo completado igualmente?`)
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (o.completed) {
        await uncompleteOrder(o.id);
        setNotice(`${o.order_number} ya no está marcado como completado.`);
      } else {
        const r = await completeOrder(o.id);
        setNotice(
          `${o.order_number} marcado como completado (solo en BoHub; WooCommerce no cambia).`
          + (r.completion_avisos.length ? ` Aviso: ${r.completion_avisos.join("; ")}.` : ""),
        );
      }
      await load();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo cambiar el estado de completado."));
    } finally {
      setBusy(false);
    }
  }

  // «Completar seleccionados»: la misma semántica que «Marcar completado»
  // (solo BoHub, reversible, idempotente) aplicada a la selección de una vez.
  // Confirma con el recuento (y cuántos van sin factura: avisa, no bloquea);
  // repinta las filas afectadas sin recargar; los fallos se informan y el
  // resto se completa igualmente.
  async function onCompleteSelected() {
    const target = rows.filter((r) => selected.has(r.id));
    if (target.length === 0) return;
    const pendientes = target.filter((r) => !r.completed);
    const sinFactura = pendientes.filter((r) => !isInvoiced(r)).length;
    const yaCompletados = target.length - pendientes.length;
    const msg = `¿Marcar ${target.length} pedido(s) como completados? Solo en BoHub; WooCommerce no cambia.`
      + (sinFactura ? ` Aviso: ${sinFactura} aún sin facturar.` : "")
      + (yaCompletados ? ` (${yaCompletados} ya estaba(n) completado(s).)` : "");
    if (!window.confirm(msg)) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await completeOrdersBulk(target.map((x) => x.id));
      const byId = new Map(r.items.map((it) => [it.id, it]));
      setRows((prev) => prev.map((row) => {
        const fresh = byId.get(row.id);
        return fresh ? { ...row, ...fresh } : row;
      }));
      const numero = (id: string) => target.find((x) => x.id === id)?.order_number ?? id;
      const fallos = r.failed.map((f) => `${numero(f.order_id)}: ${f.error}`);
      setNotice(
        `${r.completed} pedido(s) marcado(s) como completado(s) (solo en BoHub; WooCommerce no cambia)`
        + (r.already_completed ? `, ${r.already_completed} ya lo estaba(n)` : "")
        + (r.sin_facturar ? `. Aviso: ${r.sin_facturar} sin facturar` : "")
        + (fallos.length ? `. No se pudo completar ${fallos.length}: ${fallos.join("; ")}` : "")
        + ".",
      );
      setSelected(new Set());
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron completar los pedidos seleccionados."));
    } finally {
      setBusy(false);
    }
  }

  // «Aprobar» (Lote 2 D): pending_review → in_queue (cola del taller), aquí
  // mismo, sin pasar por una pantalla aparte. Con bloqueos (excepciones
  // abiertas) el backend responde 409 `blocked` y se enseña el motivo.
  async function onApprove(o: OrderSummary) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await approveOrder(o.id);
      setNotice(`${o.order_number} aprobado: pasa a la cola del taller (SAT).`);
      await load();
    } catch (e) {
      setError(`No se pudo aprobar ${o.order_number}: ${extractErrorMessage(e, "error inesperado")}`);
    } finally {
      setBusy(false);
    }
  }

  /** Los seleccionados que de verdad se pueden aprobar (pendientes de revisión). */
  const aprobables = rows.filter((r) => selected.has(r.id) && r.preparation_status === "pending_review");

  // «Aprobar seleccionados»: la misma semántica que «Aprobar» aplicada a los
  // seleccionados pendientes de revisión, de una vez. Los bloqueados se
  // informan (con su motivo) y el resto se aprueba igualmente; luego se
  // recarga, porque los aprobados cambian de cola.
  async function onApproveSelected() {
    if (aprobables.length === 0) return;
    const otros = selected.size - aprobables.length;
    const msg = `¿Aprobar ${aprobables.length} pedido(s)? Pasan a la cola del taller (SAT).`
      + (otros ? ` (${otros} de los seleccionados no está(n) pendiente(s) de revisión y se deja(n) como está(n).)` : "");
    if (!window.confirm(msg)) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await approveOrdersBulk(aprobables.map((x) => x.id));
      const numero = (id: string) => aprobables.find((x) => x.id === id)?.order_number ?? id;
      const fallos = r.failed.map((f) => `${numero(f.order_id)}: ${f.error}`);
      setNotice(
        `${r.approved} pedido(s) aprobado(s): pasan a la cola del taller (SAT)`
        + (r.already_approved ? `, ${r.already_approved} ya lo estaba(n)` : "")
        + (fallos.length ? `. No se pudo aprobar ${fallos.length}: ${fallos.join("; ")}` : "")
        + ".",
      );
      await load();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron aprobar los pedidos seleccionados."));
    } finally {
      setBusy(false);
    }
  }

  // «Actualizar cobros FACTUSOL»: comprueba en bloque (solo lectura) y
  // repinta las filas en su sitio, sin recargar la bandeja.
  async function onRefreshCobros() {
    setRefreshingCobros(true);
    setError(null);
    setNotice(null);
    try {
      const r = await refreshOrdersFactusolCobro();
      const byId = new Map(r.items.map((it) => [it.id, it]));
      setRows((prev) => prev.map((row) => {
        const fresh = byId.get(row.id);
        return fresh ? { ...row, ...fresh } : row;
      }));
      setNotice(
        r.checked === 0
          ? "Ningún pedido con factura que comprobar."
          : `Cobro FACTUSOL comprobado en ${r.checked} pedido(s) con factura.`,
      );
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo comprobar el cobro en FACTUSOL."));
    } finally {
      setRefreshingCobros(false);
    }
  }

  // Tras registrar (o comprobar) el cobro desde el modal: solo esa fila.
  function onCobroDone(info: OrderCobroInfo) {
    const status = info.status === "cobrada" || info.status === "pendiente" ? info.status : null;
    setRows((prev) => prev.map((row) => (
      row.id !== info.order_id ? row : {
        ...row,
        factusol_cobro_status: status ?? row.factusol_cobro_status ?? null,
        factusol_cobro_checked_at: info.checked_at ?? row.factusol_cobro_checked_at ?? null,
        factusol_invoice_serie: info.invoice?.serie ?? row.factusol_invoice_serie ?? null,
        factusol_cobro: status && info.invoice?.serie != null && info.invoice.codigo != null ? {
          numero: info.invoice.numero, serie: info.invoice.serie, codigo: info.invoice.codigo,
          total: info.total ?? null, total_cobrado: info.total_cobrado ?? null,
          saldo_pendiente: info.saldo_pendiente ?? null, estfac: info.estfac ?? null,
          cobros: info.cobros ?? null, cobrada: status === "cobrada",
          checked_at: info.checked_at ?? new Date().toISOString(), source: "modal",
        } : row.factusol_cobro ?? null,
      }
    )));
    if (status === "cobrada") {
      setNotice(`${info.order_number}: cobro registrado en FACTUSOL (factura ${info.invoice?.numero ?? ""}).`);
    }
  }

  /** Lote 2 · PR-2: el enlace a la ficha lleva la cola actual (`?from=`)
   *  para que su miga «← Bandeja · Por cobrar» devuelva exactamente aquí. */
  const fichaHref = (o: OrderSummary) =>
    queue ? `/erp/orders/${o.id}?from=${queue}` : `/erp/orders/${o.id}`;

  /** El botón principal de la tarjeta: la acción que el backend dice que toca.
   *  Las que la bandeja sabe hacer sin salir (aprobar, cobro, completar) se
   *  disparan aquí mismo; el resto lleva a la ficha, que es donde viven. El
   *  MISMO en tarjetas y en la vista lista. */
  function primaryAction(o: OrderSummary): ReactNode {
    const wf = o.workflow;
    if (!wf || wf.next_action === "ninguna") {
      return (
        <Link href={fichaHref(o)} className="button small secondary">
          Abrir
        </Link>
      );
    }
    const label = wf.next_action_label;
    if (canEdit && wf.next_action === "aprobar") {
      return (
        <button
          type="button" className="button small" disabled={busy}
          aria-label={`${label} ${o.order_number}`} title={wf.next_action_hint}
          onClick={() => void onApprove(o)}
        >
          {label}
        </button>
      );
    }
    if (canEdit && wf.next_action === "registrar_cobro" && o.factusol_invoice_number) {
      return (
        <button
          type="button" className="button small" disabled={busy}
          aria-label={`${label} ${o.order_number}`} title={wf.next_action_hint}
          onClick={() => { setError(null); setNotice(null); setCobroTarget(o); }}
        >
          {label}
        </button>
      );
    }
    if (canEdit && wf.next_action === "marcar_completado") {
      return (
        <button
          type="button" className="button small" disabled={busy}
          aria-label={`${label} ${o.order_number}`} title={wf.next_action_hint}
          onClick={() => void onToggleComplete(o)}
        >
          {label}
        </button>
      );
    }
    return (
      <Link
        href={fichaHref(o)} className="button small"
        aria-label={`${label} ${o.order_number}`} title={wf.next_action_hint}
      >
        {label}
      </Link>
    );
  }

  /** El menú «⋯» de la fila: las acciones que no son la principal. El MISMO
   *  en tarjetas y en la vista lista. */
  function rowMenu(o: OrderSummary): ReactNode {
    if (!canEdit) return null;
    const wf = o.workflow;
    const cobrada = o.factusol_cobro_status === "cobrada";
    return (
      <ActionsMenu label={`Más acciones ${o.order_number}`}>
        <Link href={fichaHref(o)}>Abrir ficha</Link>
        {wf?.next_action === "marcar_completado" ? null : (
          <button
            type="button" disabled={busy || !!o.cancelled}
            title={o.cancelled
              ? "Pedido anulado: se reactiva desde la ficha"
              : o.completed
                ? "Quitar la marca de completado (solo BoHub)"
                : "Marcar como completado: estado final, solo en BoHub (no toca WooCommerce)"}
            aria-label={o.completed
              ? `Desmarcar completado ${o.order_number}`
              : `Marcar completado ${o.order_number}`}
            onClick={() => void onToggleComplete(o)}
          >
            {o.completed ? "Desmarcar completado" : "Marcar completado"}
          </button>
        )}
        {/* Cobro manual sin entrar al pedido: mismo modal que la
            ficha. Solo con factura pendiente de cobro. */}
        {wf?.next_action === "registrar_cobro" && o.factusol_invoice_number ? null : (
          <button
            type="button"
            disabled={busy || !o.factusol_invoice_number || cobrada}
            title={!o.factusol_invoice_number
              ? "Emite la factura primero: el cobro se registra sobre la factura del pedido"
              : cobrada
                ? "La factura ya consta cobrada en FACTUSOL"
                : "Registrar el cobro de la factura en FACTUSOL (F_LCO + ESTFAC=2)"}
            aria-label={`Registrar cobro ${o.order_number}`}
            onClick={() => { setError(null); setNotice(null); setCobroTarget(o); }}
          >
            {cobrada ? "Cobrado" : "Registrar cobro"}
          </button>
        )}
        {o.excluded ? (
          <button
            type="button" disabled={busy}
            title="Vuelve a incluir este pedido en la bandeja y en el seguimiento"
            aria-label={`Reincluir ${o.order_number} en la bandeja`}
            onClick={() => void onIncludeRows([o.id])}
          >
            Reincluir en la bandeja
          </button>
        ) : (
          <button
            type="button" disabled={busy || !!o.cancelled}
            title={o.cancelled
              ? "Pedido anulado: ya está fuera de la bandeja"
              : "Quitar de la bandeja y del seguimiento (reversible; no borra nada)"}
            aria-label={`Quitar ${o.order_number} de la bandeja`}
            onClick={() => openExclude([o])}
          >
            Quitar de la bandeja
          </button>
        )}
      </ActionsMenu>
    );
  }

  /** Los badges pequeños de los OTROS estados (los cuatro principales van en
   *  las pastillas): por qué no está pagado, cobro FACTUSOL pendiente / sin
   *  comprobar, bloqueado, externalizado, oculto, anulado. */
  function smallBadges(o: OrderSummary): ReactNode {
    const wf = o.workflow;
    return (
      <>
        {o.payment_status !== "paid" ? <OrderStatusBadge status={o.payment_status} /> : null}
        {o.factusol_invoice_number && o.factusol_cobro_status !== "cobrada" ? (
          <CobroFactusolBadge
            hasInvoice
            status={o.factusol_cobro_status ?? null}
            cobro={o.factusol_cobro ?? null}
          />
        ) : null}
        {wf?.blocked ? (
          <span className="badge bad" title={wf.next_action_hint}>Bloqueado</span>
        ) : null}
        {o.externally_processed_at ? (
          <span className="badge muted">Externalizado</span>
        ) : null}
        {o.excluded ? (
          <span className="badge muted"
            title="Quitado a mano de las listas de trabajo (reversible)">
            Oculto
          </span>
        ) : null}
        {o.cancelled ? (
          <span className="badge bad"
            title={`Anulado${o.cancelled_at ? ` el ${d(o.cancelled_at)}` : ""}${o.cancelled_by_name ? ` por ${o.cancelled_by_name}` : ""}${o.cancelled_reason ? `: ${o.cancelled_reason}` : ""} (reversible desde la ficha)`}>
            Anulado
          </span>
        ) : null}
      </>
    );
  }

  /** Quién y por qué quitó / anuló el pedido (vistas de revisión). */
  function reviewInfo(o: OrderSummary): ReactNode {
    if (o.excluded) {
      return (
        <p className="erp-flow-item-r2 small">
          <span>
            {d(o.seguimiento_excluded_at)}
            {o.seguimiento_excluded_by_name ? ` · ${o.seguimiento_excluded_by_name}` : ""}
          </span>
          <span className="muted">{o.seguimiento_excluded_reason || "sin motivo"}</span>
        </p>
      );
    }
    if (o.cancelled) {
      return (
        <p className="erp-flow-item-r2 small">
          <span>
            Anulado {d(o.cancelled_at)}
            {o.cancelled_by_name ? ` · ${o.cancelled_by_name}` : ""}
          </span>
          <span className="muted">{o.cancelled_reason || "sin motivo"}</span>
        </p>
      );
    }
    return null;
  }

  // Chips de los filtros activos (cada uno se quita con su ×).
  const chips: { key: keyof Filtros; label: string }[] = [];
  if (filtros.prep) chips.push({ key: "prep", label: `Preparación: ${optionLabel(PREP_OPTIONS, filtros.prep)}` });
  if (filtros.payment) chips.push({ key: "payment", label: `Pago: ${optionLabel(PAYMENT_OPTIONS, filtros.payment)}` });
  if (filtros.completed) chips.push({ key: "completed", label: optionLabel(COMPLETED_OPTIONS, filtros.completed) });
  if (filtros.invoiced) chips.push({ key: "invoiced", label: optionLabel(INVOICED_OPTIONS, filtros.invoiced) });
  if (filtros.cobro) chips.push({ key: "cobro", label: `Cobro FACTUSOL: ${optionLabel(COBRO_OPTIONS, filtros.cobro)}` });
  if (filtros.store) {
    chips.push({ key: "store", label: `Tienda: ${stores.find((s) => s.slug === filtros.store)?.label ?? filtros.store}` });
  }
  if (filtros.from) chips.push({ key: "from", label: `Desde ${d(filtros.from)}` });
  if (filtros.to) chips.push({ key: "to", label: `Hasta ${d(filtros.to)}` });
  const hayFiltros = chips.length > 0 || !!queue || showExternal || revision !== "activos"
    || filtros.sortDir !== "desc";
  const esDefault = mismosFiltros(filtros, DEFAULT_FILTROS);

  const enCola = queue ? QUEUE_LABEL[queue] : "Todos los pedidos";
  const subtitulo = (queue
    ? `· ${rows.length} pedido(s) ${QUEUE_HINT[queue]}`
    : `· ${rows.length} pedido(s)`)
    + (rows.length >= PAGE_LIMIT ? ` (los ${PAGE_LIMIT} primeros: afina los filtros)` : "");

  const vacio = showCancelled
    ? "No hay pedidos anulados."
    : showExcluded
      ? "No hay pedidos ocultados."
      : queue
        ? "Esta cola está vacía con los filtros actuales."
        : "No hay pedidos con este filtro.";

  return (
    <main className="shell shell-wide erp-flow">
      <PageHeader
        title="Pedidos"
        eyebrow="ERP"
        description="Bandeja de trabajo — ordenada por lo que hay que hacer."
        crumbs={[{ label: "ERP" }, { label: "Pedidos" }]}
        actions={
          <Link href="/erp/orders/new" className="button small">
            + Nuevo pedido manual
          </Link>
        }
      />

      <WorkflowQueueCards counts={counts} active={queue} onSelect={setQueue} />

      {/* Los filtros de siempre: ahora REFINAN la cola elegida. */}
      <div className="wf-list-filters erp-flow-filters" role="search" aria-label="Filtros de la bandeja">
        <span className="erp-flow-filters-label">Refinar</span>
        <select value={filtros.prep} onChange={(e) => setFiltro("prep", e.target.value)} aria-label="Filtro preparación">
          <option value="">Preparación: todas</option>
          {PREP_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <select value={filtros.payment} onChange={(e) => setFiltro("payment", e.target.value)} aria-label="Filtro pago">
          <option value="">Pago: todos</option>
          {PAYMENT_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <select value={filtros.completed} onChange={(e) => setFiltro("completed", e.target.value)} aria-label="Filtro completado">
          <option value="">Completado: todos</option>
          {COMPLETED_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        {/* Cobro FACTUSOL (contable): distinto del filtro «Pago» del CRM. */}
        <select value={filtros.cobro} onChange={(e) => setFiltro("cobro", e.target.value)} aria-label="Filtro cobro FACTUSOL">
          <option value="">Cobro FACTUSOL: todos</option>
          {COBRO_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        {/* Lote B7: facturado, tienda, fechas y orden. */}
        <select value={filtros.invoiced} onChange={(e) => setFiltro("invoiced", e.target.value)} aria-label="Filtro facturado">
          <option value="">Facturado: todos</option>
          {INVOICED_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        {/* «Factura enviada» al cliente por email (dato ya registrado). */}
        <select value={filtros.invoiceEmail} onChange={(e) => setFiltro("invoiceEmail", e.target.value)} aria-label="Filtro factura enviada">
          <option value="">Factura enviada: todas</option>
          {INVOICE_EMAIL_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <select value={filtros.store} onChange={(e) => setFiltro("store", e.target.value)} aria-label="Filtro tienda">
          <option value="">Tienda: todas</option>
          {stores.map((s) => <option key={s.slug} value={s.slug}>{s.label}</option>)}
        </select>
        <label className="field">
          <span>Desde</span>
          <input type="date" aria-label="Fecha desde" value={filtros.from}
                 max={filtros.to || undefined}
                 onChange={(e) => setFiltro("from", e.target.value)} />
        </label>
        <label className="field">
          <span>Hasta</span>
          <input type="date" aria-label="Fecha hasta" value={filtros.to}
                 min={filtros.from || undefined}
                 onChange={(e) => setFiltro("to", e.target.value)} />
        </label>
        <button
          type="button" className="button small secondary"
          aria-label={filtros.sortDir === "desc" ? "Orden descendente" : "Orden ascendente"}
          title={filtros.sortDir === "desc"
            ? "Por fecha del pedido, los más recientes primero (pulsa para invertir)"
            : "Por fecha del pedido, los más antiguos primero (pulsa para invertir)"}
          onClick={toggleSort}
        >
          {filtros.sortDir === "desc" ? "Fecha ↓" : "Fecha ↑"}
        </button>
        <button
          type="button" className="button small secondary" disabled={busy || refreshingCobros || loading}
          title="Comprueba en FACTUSOL (solo lectura) el estado de cobro de los pedidos con factura y lo deja guardado en cada fila"
          onClick={() => void onRefreshCobros()}
        >
          {refreshingCobros ? "Comprobando cobros…" : "Actualizar cobros FACTUSOL"}
        </button>
        <label className="checkbox-inline" style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input
            type="checkbox"
            checked={showExternal}
            disabled={revision !== "activos"}
            aria-label="Mostrar procesados externamente"
            onChange={(e) => setShowExternal(e.target.checked)}
          />
          <span className="small">Mostrar procesados externamente</span>
        </label>
        {/* Lote 2 A2: una sola vista de revisión — Activos / SOLO los quitados
            a mano (con motivo y «Reincluir») / SOLO los anulados. Excluyentes:
            nunca se mezclan ni van los dos flags al backend. */}
        <span className="erp-flow-seg" role="group" aria-label="Ver">
          <span className="erp-flow-seg-label small">Ver</span>
          {REVISION_OPTIONS.map(([value, label, hint]) => (
            <button
              key={value} type="button"
              className={`erp-flow-seg-btn${revision === value ? " is-on" : ""}`}
              aria-pressed={revision === value}
              aria-label={`Ver ${label.toLowerCase()}`}
              title={hint}
              onClick={() => setRevision(value)}
            >
              {label}
            </button>
          ))}
        </span>
      </div>

      {/* Filtros activos como chips (cada uno se quita con su ×), «Limpiar
          filtros» (todo a «todos») y «Por defecto» (pagados y sin completar). */}
      <div className="erp-flow-chips" aria-label="Filtros activos">
        {chips.length === 0 ? (
          <span className="muted small">Sin filtros de refinamiento.</span>
        ) : chips.map((c) => (
          <button
            key={c.key} type="button" className="erp-flow-chip"
            aria-label={`Eliminar filtro ${c.label}`} title="Eliminar este filtro"
            onClick={() => setFiltro(c.key, "")}
          >
            {c.label} <span aria-hidden>×</span>
          </button>
        ))}
        {hayFiltros ? (
          <button type="button" className="button small secondary" onClick={limpiarFiltros}
                  title="Todo a «todos»: sin filtros, sin cola, sin vistas de revisión">
            Limpiar filtros
          </button>
        ) : null}
        {!esDefault ? (
          <button type="button" className="button small secondary"
                  title="Los filtros iniciales: pagados y sin completar"
                  onClick={() => setFiltros(DEFAULT_FILTROS)}>
            Por defecto
          </button>
        ) : null}
      </div>

      <div className="erp-flow-qtitle">
        <span>{enCola}</span>
        <span className="cnt">{subtitulo}</span>
        {queue ? (
          <button type="button" className="button small secondary" onClick={() => setQueue(null)}>
            Ver todas las colas
          </button>
        ) : null}
        {/* Lote B7: tarjetas o lista (tabla), a gusto de cada uno; se recuerda. */}
        <span className="erp-flow-view" role="group" aria-label="Vista">
          <button
            type="button" className={`button small${vista === "cards" ? "" : " secondary"}`}
            aria-pressed={vista === "cards"} onClick={() => setVista("cards")}
          >
            Tarjetas
          </button>
          <button
            type="button" className={`button small${vista === "list" ? "" : " secondary"}`}
            aria-pressed={vista === "list"} onClick={() => setVista("list")}
          >
            Lista
          </button>
        </span>
        {selectable && rows.length > 0 ? (
          <label className="checkbox-inline" style={{ display: "flex", alignItems: "center", gap: 6, marginLeft: "auto" }}>
            <input
              type="checkbox"
              aria-label="Seleccionar todo"
              checked={rows.length > 0 && selected.size === rows.length}
              onChange={toggleAll}
            />
            <span className="small muted">Seleccionar todo</span>
          </label>
        ) : null}
        {selectable && selected.size > 0 ? (
          showExcluded ? (
            <button type="button" className="button small" disabled={busy}
              onClick={onIncludeSelected}>
              Reincluir ({selected.size})
            </button>
          ) : (
            <>
              {/* Lote 2 D: aprobar en bloque desde «Por revisar» (solo cuenta
                  los seleccionados que están pendientes de revisión). */}
              {aprobables.length > 0 ? (
                <button type="button" className="button small" disabled={busy}
                  title="Aprueba los seleccionados pendientes de revisión: pasan a la cola del taller (SAT). Los bloqueados se informan y el resto se aprueba igualmente."
                  onClick={() => void onApproveSelected()}>
                  Aprobar seleccionados ({aprobables.length})
                </button>
              ) : null}
              <button type="button" className="button small" disabled={busy}
                title="Marca los seleccionados como completados (estado final, solo en BoHub; no toca WooCommerce; reversible uno a uno con «Desmarcar»)"
                onClick={() => void onCompleteSelected()}>
                Completar seleccionados ({selected.size})
              </button>
              <button type="button" className="button small danger" disabled={busy}
                title="Quita los seleccionados de la bandeja y del seguimiento (reversible; no borra nada)"
                onClick={onExcludeSelected}>
                Quitar de la bandeja ({selected.size})
              </button>
            </>
          )
        ) : null}
      </div>

      {error ? <p className="form-error">{error}</p> : null}
      {notice ? <p className="form-success" role="status">{notice}</p> : null}
      {loading ? (
        <p className="muted">Cargando…</p>
      ) : rows.length === 0 ? (
        <p className="muted">{vacio}</p>
      ) : vista === "list" ? (
        /* --- Vista LISTA: la misma información y las mismas acciones, en tabla. --- */
        <div className="erp-bandeja-table-wrap">
          <table className="data-table erp-bandeja-table">
            <thead>
              <tr>
                {selectable ? <th className="bulk-checkbox-cell"><span className="sr-only">Selección</span></th> : null}
                <th>Nº</th>
                <th>Cliente</th>
                <th>Tienda</th>
                <th className="sortable" aria-sort={filtros.sortDir === "asc" ? "ascending" : "descending"}>
                  <button
                    type="button" className="erp-bandeja-sort"
                    aria-label={`Ordenar por fecha (${filtros.sortDir === "asc" ? "ascendente" : "descendente"})`}
                    title="Pulsa para invertir el orden"
                    onClick={toggleSort}
                  >
                    Fecha <span className="sort-arrow" aria-hidden>{filtros.sortDir === "asc" ? "↑" : "↓"}</span>
                  </button>
                </th>
                <th className="num">Importe</th>
                <th>Estado</th>
                <th>Cola · siguiente paso</th>
                <th>Acciones</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((o) => {
                const wf = o.workflow;
                return (
                  <tr
                    key={o.id}
                    data-order-row={o.order_number}
                    className={`${wf?.blocked ? "is-alert" : ""}${o.excluded || o.cancelled ? " is-muted" : ""}${selected.has(o.id) ? " is-selected" : ""}`}
                  >
                    {selectable ? (
                      <td className="bulk-checkbox-cell">
                        <input
                          type="checkbox"
                          aria-label={`Seleccionar ${o.order_number}`}
                          checked={selected.has(o.id)}
                          onChange={() => toggleRow(o.id)}
                        />
                      </td>
                    ) : null}
                    <td>
                      <Link href={fichaHref(o)}><strong>{o.order_number}</strong></Link>
                      <div className="erp-bandeja-badges">{smallBadges(o)}</div>
                      {reviewInfo(o)}
                    </td>
                    <td>{customerLabel(o) || "—"}</td>
                    <td>{sourcePill(o)}</td>
                    <td>
                      <time className="erp-flow-date" dateTime={o.placed_at ?? undefined}>{d(o.placed_at)}</time>
                    </td>
                    <td className="num erp-flow-amount">
                      {o.total_amount.toFixed(2)} {o.currency}
                      {regimeLabel(wf?.regime) ? <small>{regimeLabel(wf?.regime)}</small> : null}
                    </td>
                    <td><OrderStatusPills order={o} size="sm" /></td>
                    <td>
                      {wf ? (
                        <>
                          <span className="erp-bandeja-queue" style={{ ["--qc" as string]: QUEUE_COLOR[wf.queue] }}>
                            <span className="erp-flow-dot" aria-hidden />
                            {wf.queue_label}
                          </span>
                          {wf.next_action !== "ninguna" ? (
                            <span className="muted small erp-bandeja-next" title={wf.next_action_hint}>
                              {wf.next_action_label}
                            </span>
                          ) : null}
                        </>
                      ) : "—"}
                    </td>
                    <td>
                      <div className="erp-flow-item-actions">
                        {primaryAction(o)}
                        {rowMenu(o)}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="erp-flow-list">
          {rows.map((o) => {
            const wf = o.workflow;
            return (
              <article
                key={o.id}
                data-order-row={o.order_number}
                className={`erp-flow-item${wf?.blocked ? " is-alert" : ""}${o.excluded || o.cancelled ? " is-muted" : ""}`}
              >
                {selectable ? (
                  <input
                    type="checkbox"
                    aria-label={`Seleccionar ${o.order_number}`}
                    checked={selected.has(o.id)}
                    onChange={() => toggleRow(o.id)}
                  />
                ) : <span />}
                <div className="erp-flow-item-main">
                  <div className="erp-flow-item-r1">
                    <Link href={fichaHref(o)}><strong>{o.order_number}</strong></Link>
                    {sourcePill(o)}
                    <time className="erp-flow-date" dateTime={o.placed_at ?? undefined} title="Fecha del pedido">
                      {d(o.placed_at)}
                    </time>
                    {smallBadges(o)}
                  </div>
                  <OrderStatusPills order={o} className="erp-flow-item-pills" />
                  <p className="erp-flow-item-r2">
                    <span>{customerLabel(o) || "—"}</span>
                    {wf ? <span>{wf.queue_label}</span> : null}
                  </p>
                  {reviewInfo(o)}
                  {wf ? <WorkflowAlerts alerts={wf.alerts} variant="inline" max={2} /> : null}
                </div>
                <div className="erp-flow-item-side">
                  <p className="erp-flow-amount">
                    {o.total_amount.toFixed(2)} {o.currency}
                    {regimeLabel(wf?.regime) ? <small>{regimeLabel(wf?.regime)}</small> : null}
                  </p>
                  <div className="erp-flow-item-actions">
                    {primaryAction(o)}
                    {rowMenu(o)}
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {cobroTarget ? (
        <RegistrarCobroModal
          orderId={cobroTarget.id}
          orderNumber={cobroTarget.order_number}
          onClose={() => setCobroTarget(null)}
          onDone={onCobroDone}
        />
      ) : null}
      {excludeTarget ? (
        <ExcludeSeguimientoModal
          context="bandeja"
          rows={excludeTarget.map((o) => ({
            id: o.id, order_number: o.order_number, cliente: customerLabel(o) || null,
          }))}
          busy={busy}
          onConfirm={onConfirmExclude}
          onCancel={() => setExcludeTarget(null)}
        />
      ) : null}
    </main>
  );
}

/** `useSearchParams` exige Suspense en el app router (`?queue=`). */
export default function ErpOrdersPage() {
  return (
    <Suspense fallback={<main className="shell shell-wide erp-flow"><p className="muted">Cargando…</p></main>}>
      <ErpOrdersScreen />
    </Suspense>
  );
}
