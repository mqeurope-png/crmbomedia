"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { SatIncidenciasTab } from "../../components/erp/SatIncidenciasTab";
import { SatPreparingCard } from "../../components/erp/SatPreparingCard";
import { satDateTime, SatQueueTable } from "../../components/erp/SatQueueTable";
import { SatReadyCard, SatShippedCard, satShippedLabel } from "../../components/erp/SatReadyCard";
import { getCurrentUser } from "../../lib/api";
import { Cap, can } from "../../lib/capabilities";
import { extractErrorMessage } from "../../lib/errors";
import {
  bulkNoShipping,
  customerLabel,
  findSatOrderByNumber,
  getErpSettings,
  getSatOrderItem,
  getSatQueue,
  getSatShipped,
  satEnqueueOrder,
  type SatQueueCounts,
  type SatQueueFilters,
  type SatQueueItem,
  type SatTab,
} from "../../lib/erpApi";

type View = "cards" | "list";

/** Pestañas de la cola, en el orden del flujo del taller:
 *  Por embalar → En preparación → Embalados → (Todos pendientes) → Sin
 *  seguimiento → Pendiente de recogida → Enviados, e Incidencias. El backend
 *  decide la pestaña de cada pedido y sus contadores. */
type PendingTab = "por_embalar" | "en_preparacion" | "embalados" | "pendiente_recogida";
type Tab = PendingTab | "pendientes" | "sin_seguimiento" | "enviados" | "incidencias";

const PENDING_TABS: PendingTab[] = [
  "por_embalar", "en_preparacion", "embalados", "pendiente_recogida",
];
const EMPTY_LISTS: Record<PendingTab, SatQueueItem[]> = {
  por_embalar: [], en_preparacion: [], embalados: [], pendiente_recogida: [],
};
const EMPTY_COUNTS: SatQueueCounts = {
  por_embalar: 0, en_preparacion: 0, embalados: 0, pendiente_recogida: 0,
  pendientes: 0, sin_seguimiento: 0, enviados: 0,
};

function isPendingTab(t: string | null | undefined): t is PendingTab {
  return !!t && (PENDING_TABS as string[]).includes(t);
}

/** La card que toca según el estado ACTUAL del pedido (tras avanzarlo en su
 *  sitio, la card cambia a la del paso siguiente sin moverse). */
function SatCard({
  order, onChanged, canEdit, canShip,
}: {
  order: SatQueueItem; onChanged: () => void; canEdit: boolean; canShip: boolean;
}) {
  const t = order.sat_tab;
  if (t === "embalados" || t === "pendiente_recogida") {
    return <SatReadyCard order={order} onChanged={onChanged} canEdit={canEdit} canShip={canShip} />;
  }
  if (t === "enviados" || t === "sin_seguimiento") return <SatShippedCard order={order} />;
  return <SatPreparingCard order={order} onChanged={onChanged} canEdit={canEdit} />;
}

/** Tabla de enviados («Enviados» / «Sin seguimiento»). */
function SatShippedTable({
  items, ariaLabel, selectable = false, selected, onToggle,
}: {
  items: SatQueueItem[]; ariaLabel: string;
  selectable?: boolean; selected?: Set<string>; onToggle?: (id: string) => void;
}) {
  return (
    <div className="table-wrapper sat-history-wrap">
      <table className="data-table data-table--responsive sat-history-table" aria-label={ariaLabel}>
        <thead>
          <tr>
            {selectable ? <th className="sat-td-select" aria-label="Selección" /> : null}
            <th>Nº</th>
            <th>Cliente</th>
            <th>Fecha</th>
            <th>Envío</th>
            <th>Seguimiento</th>
            <th>Agencia</th>
          </tr>
        </thead>
        <tbody>
          {items.map((o) => (
            <tr key={o.id}>
              {selectable ? (
                <td className="sat-td-select">
                  <input type="checkbox" aria-label={`Seleccionar ${o.order_number}`}
                         checked={!!selected?.has(o.id)} onChange={() => onToggle?.(o.id)} />
                </td>
              ) : null}
              <td data-label="Nº" className="mono">
                <Link href={`/erp/orders/${o.id}`}>{o.order_number}</Link>
              </td>
              <td data-label="Cliente" className="sat-td-cliente">{customerLabel(o) || "—"}</td>
              <td data-label="Fecha" className="mono">{satDateTime(o.placed_at)}</td>
              <td data-label="Envío">
                <span className={`badge ${o.sin_seguimiento ? "muted" : "ok"}`}>
                  {satShippedLabel(o)}
                </span>
              </td>
              <td data-label="Seguimiento" className="mono">
                {o.tracking_number || o.genei?.tracking || (o.sin_seguimiento ? "—" : "—")}
              </td>
              <td data-label="Agencia">{o.genei?.courier ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Preferencia de vista (tarjetas / lista) por dispositivo: la tablet del
 *  taller quiere tarjetas; el escritorio de oficina, lista. */
const VIEW_KEY = "bohub.sat.queue.view";

function readStoredView(): View {
  try {
    return window.localStorage.getItem(VIEW_KEY) === "list" ? "list" : "cards";
  } catch {
    return "cards";
  }
}

function storeView(view: View): void {
  try {
    window.localStorage.setItem(VIEW_KEY, view);
  } catch {
    // sin storage (modo privado, etc.): la preferencia dura la sesión
  }
}

/** Cola SAT táctil. Pestañas por paso del taller (las decide el backend, con
 *  sus contadores): «Por embalar» · «En preparación» · «Embalados» · «Todos
 *  pendientes» · «Sin seguimiento» · «Pendiente de recogida» · «Enviados» ·
 *  «Incidencias».
 *
 *  Avanzar un pedido (empezar, embalar, crear envío, recogido…) se hace EN SU
 *  SITIO: solo se refresca su card, que se queda donde estaba aunque ya
 *  pertenezca a otra pestaña (los contadores se actualizan al momento). Al
 *  cambiar de pestaña, cada pedido pasa a la suya.
 *
 *  Lote B6: filtros (fechas, tienda, texto, orden), vista tarjetas/lista y
 *  «Añadir pedido a la cola» a mano por nº de pedido. Lote 2 · PR-2: diseño
 *  móvil primero; en escritorio, la misma card en rejilla. */
export default function SatQueuePage() {
  const [lists, setLists] = useState<Record<PendingTab, SatQueueItem[]>>(EMPTY_LISTS);
  const [counts, setCounts] = useState<SatQueueCounts>(EMPTY_COUNTS);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // --- filtros ---------------------------------------------------------------
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");
  const [store, setStore] = useState("");
  // C4: orden por fecha del pedido. Por defecto, los más recientes primero.
  const [sort, setSort] = useState<"fecha_desc" | "fecha_asc">("fecha_desc");
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [stores, setStores] = useState<{ slug: string; label: string }[]>([]);

  // El buscador se aplica con un pequeño retardo para no pedir la cola en
  // cada tecla (la tablet del taller va por wifi).
  useEffect(() => {
    const t = setTimeout(() => setQ(qInput.trim()), 300);
    return () => clearTimeout(t);
  }, [qInput]);

  const filters = useMemo<SatQueueFilters>(() => {
    const f: SatQueueFilters = {};
    if (desde) f.desde = desde;
    if (hasta) f.hasta = hasta;
    if (store) f.store_slug = store;
    if (q) f.q = q;
    // El orden siempre viaja (por defecto `fecha_desc`); no cuenta como
    // «filtro activo» para el botón «Limpiar».
    if (sort !== "fecha_desc") f.sort = sort;
    return f;
  }, [desde, hasta, store, q, sort]);
  const hasFilters = Object.keys(filters).length > 0;

  function limpiar() {
    setDesde(""); setHasta(""); setStore("");
    setSort("fecha_desc"); setQInput(""); setQ("");
  }

  // --- vista -----------------------------------------------------------------
  const [view, setView] = useState<View>("cards");
  useEffect(() => { setView(readStoredView()); }, []);
  function changeView(v: View) {
    setView(v);
    storeView(v);
  }

  // --- permisos --------------------------------------------------------------
  // Trabajo de taller (empezar/embalar/técnicos): `erp.sat.prepare`.
  const [canEdit, setCanEdit] = useState(false);
  // «Añadir a mano a la cola» es aprobar/meter en cola (oficina).
  const [canEnqueue, setCanEnqueue] = useState(false);
  // Genei (crear/ver envío) y «Resolver» una incidencia de ENVÍO.
  const [canShip, setCanShip] = useState(false);
  // Resolver una incidencia de PEDIDO (cerrar la excepción) es de oficina.
  const [canResolvePedido, setCanResolvePedido] = useState(false);

  useEffect(() => {
    getCurrentUser()
      .then((u) => {
        setCanEdit(can(u, Cap.SAT_PREPARE));
        setCanEnqueue(can(u, Cap.ORDERS_APPROVE));
        setCanShip(can(u, Cap.SAT_SHIPPING));
        setCanResolvePedido(can(u, Cap.ORDERS_CREATE));
      })
      .catch(() => {
        setCanEdit(false);
        setCanEnqueue(false);
        setCanShip(false);
        setCanResolvePedido(false);
      });
    // Tiendas (filtro) best-effort: sin ellas el filtro no sale.
    getErpSettings()
      .then((s) => {
        setStores((s.woocommerce_stores ?? []).map((w) => ({ slug: w.slug, label: w.label })));
      })
      .catch(() => { setStores([]); });
  }, []);

  // --- cola (pestañas de pendientes + contadores) ------------------------------
  // Pedidos que han cambiado de paso EN SU SITIO: siguen donde estaban hasta
  // cambiar de pestaña; entonces pasan a la suya (`aplicarMovimientos`).
  const [moves, setMoves] = useState<Record<string, SatTab | null>>({});

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    getSatQueue(filters)
      .then((r) => {
        setLists({
          por_embalar: r.por_embalar ?? [],
          en_preparacion: r.en_preparacion ?? [],
          embalados: r.embalados ?? [],
          pendiente_recogida: r.pendiente_recogida ?? [],
        });
        setCounts(r.counts ?? EMPTY_COUNTS);
        setMoves({});
      })
      .catch((e) => setError(extractErrorMessage(e, "No se pudo cargar la cola.")))
      .finally(() => setLoading(false));
  }, [filters]);

  useEffect(() => { load(); }, [load]);

  // --- «Enviados» / «Sin seguimiento» (carga al abrir la pestaña) --------------
  const [shipped, setShipped] = useState<{ items: SatQueueItem[]; total: number } | null>(null);
  const [sinSeg, setSinSeg] = useState<{ items: SatQueueItem[]; total: number } | null>(null);
  const [shippedLoading, setShippedLoading] = useState(false);

  const [tab, setTab] = useState<Tab>("por_embalar");

  const loadShipped = useCallback((kind: "enviados" | "sin_seguimiento") => {
    setShippedLoading(true);
    getSatShipped(filters, kind === "sin_seguimiento")
      .then((r) => {
        const data = { items: r.items, total: r.total };
        if (kind === "enviados") setShipped(data); else setSinSeg(data);
      })
      .catch((e) => setError(extractErrorMessage(e, "No se pudieron cargar los enviados.")))
      .finally(() => setShippedLoading(false));
  }, [filters]);

  useEffect(() => {
    if (tab === "enviados" || tab === "sin_seguimiento") loadShipped(tab);
  }, [tab, loadShipped]);

  // --- «Incidencias» (carga en el componente) --------------------------------
  const [incidenciasCount, setIncidenciasCount] = useState<number | null>(null);
  const [incidenciasReload, setIncidenciasReload] = useState(0);

  const refreshAll = useCallback(() => {
    load();
    if (tab === "enviados" || tab === "sin_seguimiento") loadShipped(tab);
    if (tab === "incidencias") setIncidenciasReload((n) => n + 1);
  }, [load, loadShipped, tab]);

  /** Refresca UN pedido tras avanzarlo, sin recargar la cola: su card cambia
   *  al paso siguiente en el mismo sitio y los contadores se ajustan ya. */
  const refreshItem = useCallback(async (id: string) => {
    let item: SatQueueItem;
    try {
      item = await getSatOrderItem(id);
    } catch {
      load();
      return;
    }
    const previo = [
      ...PENDING_TABS.flatMap((t) => lists[t]),
      ...(shipped?.items ?? []), ...(sinSeg?.items ?? []),
    ].find((o) => o.id === id);
    setLists((prev) => {
      const next = { ...prev };
      for (const t of PENDING_TABS) {
        if (next[t].some((o) => o.id === id)) {
          next[t] = next[t].map((o) => (o.id === id ? item : o));
        }
      }
      return next;
    });
    const sustituir = (d: { items: SatQueueItem[]; total: number } | null) =>
      d ? { ...d, items: d.items.map((o) => (o.id === id ? item : o)) } : d;
    setShipped(sustituir);
    setSinSeg(sustituir);
    const antes = previo?.sat_tab ?? null;
    const ahora = item.sat_tab ?? null;
    if (antes !== ahora) {
      setMoves((m) => ({ ...m, [id]: ahora }));
      setCounts((c) => {
        const n = { ...c };
        const ajusta = (k: SatTab | null, d: number) => {
          if (k && k in n) n[k as keyof SatQueueCounts] = Math.max(0, n[k as keyof SatQueueCounts] + d);
        };
        ajusta(antes, -1);
        ajusta(ahora, +1);
        // «Sin seguimiento» también está en «Enviados».
        if (ahora === "sin_seguimiento" && antes !== "enviados") ajusta("enviados", +1);
        if (antes === "sin_seguimiento" && ahora !== "enviados") ajusta("enviados", -1);
        n.pendientes = n.por_embalar + n.en_preparacion + n.embalados + n.pendiente_recogida;
        return n;
      });
    }
  }, [lists, shipped, sinSeg, load]);

  /** Al cambiar de pestaña, cada pedido avanzado pasa a la suya. */
  function aplicarMovimientos() {
    const ids = Object.keys(moves);
    if (ids.length === 0) return;
    setLists((prev) => {
      const next = { ...prev };
      const movidos: SatQueueItem[] = [];
      for (const t of PENDING_TABS) {
        next[t] = next[t].filter((o) => {
          if (!ids.includes(o.id)) return true;
          movidos.push(o);
          return false;
        });
      }
      for (const o of movidos) {
        if (isPendingTab(o.sat_tab)) next[o.sat_tab] = [o, ...next[o.sat_tab]];
      }
      return next;
    });
    if (ids.some((id) => moves[id] === "enviados" || moves[id] === "sin_seguimiento")) {
      setShipped(null);
      setSinSeg(null);
    }
    setMoves({});
  }

  function changeTab(t: Tab) {
    aplicarMovimientos();
    setTab(t);
  }

  // --- selección múltiple («Sin seguimiento» en lote) --------------------------
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  /** Pedidos visibles y seleccionables de la pestaña actual. */
  const visibleItems = useMemo<SatQueueItem[]>(() => {
    if (isPendingTab(tab)) return lists[tab];
    if (tab === "sin_seguimiento") return sinSeg?.items ?? [];
    return [];
  }, [tab, lists, sinSeg]);

  // Cambiar de pestaña limpia la selección.
  useEffect(() => { setSelected(new Set()); }, [tab]);

  function toggleSel(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }
  function toggleAllSel() {
    setSelected((prev) =>
      prev.size === visibleItems.length && visibleItems.length > 0
        ? new Set()
        : new Set(visibleItems.map((o) => o.id)),
    );
  }

  // En las pestañas de pendientes el lote MARCA «Sin seguimiento» (enviado sin
  // tracking); en «Sin seguimiento», lo QUITA (vuelven al taller).
  const bulkValue = tab !== "sin_seguimiento";
  const selectable = canEdit && (isPendingTab(tab) || tab === "sin_seguimiento");

  async function applyBulk() {
    if (selected.size === 0 || bulkBusy) return;
    setBulkBusy(true);
    setError(null);
    try {
      const r = await bulkNoShipping([...selected], bulkValue);
      setNotice(
        bulkValue
          ? `${r.changed} pedido(s) marcados «Sin seguimiento»: enviados, sin nº de tracking.`
          : `${r.changed} pedido(s) vuelven a los pendientes del taller.`,
      );
      setSelected(new Set());
      setConfirmOpen(false);
      setShipped(null);
      setSinSeg(null);
      refreshAll();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo aplicar el cambio."));
    } finally {
      setBulkBusy(false);
    }
  }

  /** Envuelve una card con su casilla de selección (solo si `selectable`). */
  function withSelect(o: SatQueueItem, card: React.ReactNode): React.ReactNode {
    if (!selectable) return <div key={o.id}>{card}</div>;
    return (
      <div key={o.id} className="sat-select-item">
        <label className="sat-select-check">
          <input
            type="checkbox" aria-label={`Seleccionar ${o.order_number}`}
            checked={selected.has(o.id)} onChange={() => toggleSel(o.id)}
          />
        </label>
        <div className="sat-select-body">{card}</div>
      </div>
    );
  }

  function card(o: SatQueueItem): React.ReactNode {
    return (
      <SatCard order={o} onChanged={() => void refreshItem(o.id)}
               canEdit={canEdit} canShip={canShip} />
    );
  }

  // --- añadir a mano ---------------------------------------------------------
  const [addNumber, setAddNumber] = useState("");
  const [addBusy, setAddBusy] = useState(false);
  const [addNotice, setAddNotice] = useState<string | null>(null);
  const [addError, setAddError] = useState<string | null>(null);

  async function addByNumber(e: FormEvent) {
    e.preventDefault();
    const num = addNumber.trim();
    if (!num || addBusy) return;
    setAddBusy(true);
    setAddNotice(null);
    setAddError(null);
    try {
      const found = await findSatOrderByNumber(num);
      const r = await satEnqueueOrder(found.id);
      const who = customerLabel(found);
      const label = who ? `${r.order_number} (${who})` : r.order_number;
      setAddNotice(
        r.already_queued
          ? `${label} ya estaba en la Cola SAT.`
          : r.approved
            ? `${label} añadido a la Cola SAT (aprobado).`
            : `${label} añadido a la Cola SAT.`,
      );
      setAddNumber("");
      refreshAll();
    } catch (err) {
      setAddError(extractErrorMessage(err, "No se pudo añadir el pedido a la cola."));
    } finally {
      setAddBusy(false);
    }
  }

  const TABS: { key: Tab; label: string; count: number | null }[] = [
    { key: "por_embalar", label: "Por embalar", count: counts.por_embalar },
    { key: "en_preparacion", label: "En preparación", count: counts.en_preparacion },
    { key: "embalados", label: "Embalados", count: counts.embalados },
    { key: "pendientes", label: "Todos pendientes", count: counts.pendientes },
    { key: "sin_seguimiento", label: "Sin seguimiento", count: counts.sin_seguimiento },
    { key: "pendiente_recogida", label: "Pendiente de recogida", count: counts.pendiente_recogida },
    { key: "enviados", label: "Enviados", count: counts.enviados },
    { key: "incidencias", label: "Incidencias", count: incidenciasCount },
  ];

  const EMPTY_TEXT: Record<PendingTab, string> = {
    por_embalar: "Nada por embalar",
    en_preparacion: "Nada en preparación",
    embalados: "Nada embalado pendiente de envío",
    pendiente_recogida: "Nada pendiente de recogida",
  };
  const TAB_HINT: Partial<Record<Tab, string>> = {
    en_preparacion: "Preparación empezada, aún sin embalar: mete peso y medidas y embala aquí mismo.",
    embalados: "Embalados, sin etiqueta tramitada: crea el envío con Genei o sube la etiqueta.",
    pendiente_recogida: "Con el envío tramitado y la etiqueta lista: esperando al transportista. "
      + "Cuando la agencia lo recoge (Genei) o marcas «Recogido», pasa a «Enviados».",
    sin_seguimiento: "Enviados SIN nº de tracking (recogida en tienda, transporte sin "
      + "seguimiento…). Cuentan como enviados. Selecciónalos para devolverlos al taller.",
    enviados: "Pedidos que ya han salido: recogidos, en tránsito o entregados, y los "
      + "enviados sin seguimiento.",
  };

  const TABLE_LABEL: Record<PendingTab, string> = {
    por_embalar: "Pedidos por embalar",
    en_preparacion: "Pedidos en preparación",
    embalados: "Pedidos embalados",
    pendiente_recogida: "Pedidos pendientes de recogida",
  };

  function pendingPanel(
    t: PendingTab, items: SatQueueItem[], single = false, ariaLabel = TABLE_LABEL[t],
  ) {
    if (items.length === 0) {
      return (
        <p className="sat-empty">
          {EMPTY_TEXT[t]}{hasFilters ? " con estos filtros." : "."}
        </p>
      );
    }
    if (view === "list") {
      return (
        <SatQueueTable items={items} variant="auto" onChanged={refreshAll}
                       onItemChanged={(id) => void refreshItem(id)}
                       ariaLabel={ariaLabel}
                       selectable={selectable && !single} selected={selected}
                       onToggle={toggleSel} />
      );
    }
    return (
      <div className={`sat-cards${single ? " sat-cards--single" : ""}`}>
        {items.map((o) => (single ? <div key={o.id}>{card(o)}</div> : withSelect(o, card(o))))}
      </div>
    );
  }

  const shippedData = tab === "sin_seguimiento" ? sinSeg : shipped;

  return (
    <div className={`sat-queue-wrap ${view === "list" ? "sat-view-list" : "sat-view-cards"}`}>
      <div className="sat-queue-head">
        <h1>Cola SAT</h1>
        <span className="muted small sat-queue-count">
          {loading ? "Cargando…" : `${counts.pendientes} en el taller`}
        </span>
        <div className="sat-queue-tools">
          <div className="sat-view-toggle" role="group" aria-label="Vista">
            <button type="button" aria-pressed={view === "cards"}
                    onClick={() => changeView("cards")}>
              Tarjetas
            </button>
            <button type="button" aria-pressed={view === "list"}
                    onClick={() => changeView("list")}>
              Lista
            </button>
          </div>
          <button type="button" className="button secondary small" onClick={refreshAll}>
            Actualizar
          </button>
        </div>
      </div>

      <div className="erp-flow-filters" role="search" aria-label="Filtros de la Cola SAT">
        <label className="field erp-flow-filter-grow">
          <span className="sr-only">Buscar pedido o cliente</span>
          <input
            type="search" value={qInput} placeholder="Nº de pedido o cliente…"
            aria-label="Buscar pedido o cliente"
            onChange={(e) => setQInput(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Desde</span>
          <input type="date" aria-label="Fecha desde" value={desde}
                 onChange={(e) => setDesde(e.target.value)} />
        </label>
        <label className="field">
          <span>Hasta</span>
          <input type="date" aria-label="Fecha hasta" value={hasta}
                 onChange={(e) => setHasta(e.target.value)} />
        </label>
        {stores.length > 0 ? (
          <label className="field">
            <span>Tienda</span>
            <select value={store} aria-label="Tienda" onChange={(e) => setStore(e.target.value)}>
              <option value="">Todas</option>
              {stores.map((s) => <option key={s.slug} value={s.slug}>{s.label}</option>)}
            </select>
          </label>
        ) : null}
        <label className="field">
          <span>Orden</span>
          <select value={sort} aria-label="Orden por fecha del pedido"
                  onChange={(e) => setSort(e.target.value as "fecha_desc" | "fecha_asc")}>
            <option value="fecha_desc">Fecha: recientes primero</option>
            <option value="fecha_asc">Fecha: antiguos primero</option>
          </select>
        </label>
        {hasFilters || qInput ? (
          <button type="button" className="button small secondary" onClick={limpiar}>
            Limpiar filtros
          </button>
        ) : null}
      </div>

      {canEnqueue ? (
        <form className="sat-add-form" onSubmit={addByNumber} aria-label="Añadir pedido a la cola">
          <label className="field">
            <span>Añadir pedido a la cola</span>
            <input
              type="text" value={addNumber} placeholder="Nº de pedido (p. ej. BOP-1234)"
              aria-label="Número de pedido a añadir"
              onChange={(e) => setAddNumber(e.target.value)}
            />
          </label>
          <button type="submit" className="button small" disabled={addBusy || !addNumber.trim()}>
            {addBusy ? "Añadiendo…" : "Añadir"}
          </button>
          {addNotice ? <span className="form-info small" role="status">{addNotice}</span> : null}
          {addError ? <span className="form-error small" role="alert">{addError}</span> : null}
        </form>
      ) : null}

      {error ? <p className="form-error">{error}</p> : null}

      <div className="sat-tabs" role="tablist" aria-label="Secciones de la Cola SAT">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            id={`sat-tab-${t.key}`}
            aria-selected={tab === t.key}
            aria-controls={`sat-panel-${t.key}`}
            className="sat-tab"
            onClick={() => changeTab(t.key)}
          >
            {t.label}{" "}
            {t.count !== null ? <span className="sat-tab-count">{t.count}</span> : null}
          </button>
        ))}
      </div>

      {/* Barra de acción en lote «Sin seguimiento» (marcar / quitar). */}
      {selectable && visibleItems.length > 0 ? (
        <div className="sat-bulk-bar" role="group" aria-label="Acciones en lote">
          <label className="sat-bulk-all">
            <input
              type="checkbox" aria-label="Seleccionar todo"
              checked={selected.size === visibleItems.length && visibleItems.length > 0}
              onChange={toggleAllSel}
            />
            <span className="small">Seleccionar todo</span>
          </label>
          <span className="small muted">{selected.size} seleccionado(s)</span>
          <button
            type="button" className="button small"
            disabled={selected.size === 0}
            onClick={() => setConfirmOpen(true)}
          >
            {bulkValue ? "Marcar enviado sin seguimiento" : "Quitar «Sin seguimiento»"}
          </button>
        </div>
      ) : null}
      {notice ? <p className="form-info small" role="status">{notice}</p> : null}
      {TAB_HINT[tab] ? <p className="muted small sat-history-hint">{TAB_HINT[tab]}</p> : null}

      {isPendingTab(tab) ? (
        <section
          className="sat-section" role="tabpanel" id={`sat-panel-${tab}`}
          aria-label={TABS.find((x) => x.key === tab)?.label}
        >
          {/* Lote 4 · #1 — cada vista scrollea en su propio contenedor. */}
          <div className="sat-scroll">{pendingPanel(tab, lists[tab])}</div>
        </section>
      ) : null}

      {tab === "pendientes" ? (
        <section
          className="sat-section sat-global" role="tabpanel" id="sat-panel-pendientes"
          aria-label="Todos pendientes"
        >
          {/* Lote 3/4 — dos columnas: por hacer en el banco y ya embalados. */}
          <div className="sat-global-cols">
            <div className="sat-global-col" aria-label="Por embalar y en preparación">
              <h2 className="sat-global-title">
                📦 Por embalar · en preparación{" "}
                <span className="sat-tab-count">{counts.por_embalar + counts.en_preparacion}</span>
              </h2>
              <div className="sat-scroll">
                {pendingPanel("por_embalar", [...lists.por_embalar, ...lists.en_preparacion], true,
                              "Pedidos por embalar y en preparación")}
              </div>
            </div>
            <div className="sat-global-col" aria-label="Embalados y pendientes de recogida">
              <h2 className="sat-global-title">
                🚚 Embalados · pendiente de recogida{" "}
                <span className="sat-tab-count">{counts.embalados + counts.pendiente_recogida}</span>
              </h2>
              <div className="sat-scroll">
                {pendingPanel("embalados", [...lists.embalados, ...lists.pendiente_recogida], true,
                              "Pedidos embalados y pendientes de recogida")}
              </div>
            </div>
          </div>
        </section>
      ) : null}

      {tab === "enviados" || tab === "sin_seguimiento" ? (
        <section
          className="sat-section sat-history" role="tabpanel" id={`sat-panel-${tab}`}
          aria-label={tab === "enviados" ? "Enviados" : "Sin seguimiento"}
        >
          <div className="sat-scroll">
            {shippedLoading && !shippedData ? (
              <p className="muted">Cargando…</p>
            ) : !shippedData || shippedData.items.length === 0 ? (
              <p className="sat-empty">
                {tab === "enviados" ? "Ningún pedido enviado" : "Ningún pedido sin seguimiento"}
                {hasFilters ? " con estos filtros." : "."}
              </p>
            ) : (
              <>
                {shippedData.total > shippedData.items.length ? (
                  <p className="muted small">
                    Mostrando los {shippedData.items.length} más recientes de {shippedData.total}
                    {" "}(usa los filtros de fecha para ver otros).
                  </p>
                ) : null}
                <SatShippedTable
                  items={shippedData.items}
                  ariaLabel={tab === "enviados" ? "Pedidos enviados" : "Pedidos enviados sin seguimiento"}
                  selectable={selectable && tab === "sin_seguimiento"}
                  selected={selected} onToggle={toggleSel}
                />
              </>
            )}
          </div>
        </section>
      ) : null}

      {tab === "incidencias" ? (
        <SatIncidenciasTab
          filters={filters}
          canResolveEnvio={canShip}
          canResolvePedido={canResolvePedido}
          reloadKey={incidenciasReload}
          onCount={setIncidenciasCount}
          onResolved={refreshAll}
        />
      ) : null}

      {confirmOpen ? (
        <div className="modal-overlay" role="dialog" aria-modal="true"
             aria-label="Confirmar Sin seguimiento">
          <div className="modal-dialog erp-modal">
            <h2>{bulkValue ? "Marcar enviado sin seguimiento" : "Quitar «Sin seguimiento»"}</h2>
            <p>
              {bulkValue
                ? `Vas a marcar ${selected.size} pedido(s) como ENVIADOS sin nº de tracking `
                  + "(recogida en tienda, transporte sin seguimiento…). Pasan a «Enviados» "
                  + "y «Sin seguimiento». No afecta a la factura ni al cobro. Es reversible."
                : `Vas a devolver ${selected.size} pedido(s) al taller: volverán a su `
                  + "pestaña según su estado de preparación."}
            </p>
            <div className="modal-actions">
              <button type="button" className="button secondary" disabled={bulkBusy}
                      onClick={() => setConfirmOpen(false)}>
                Cancelar
              </button>
              <button type="button" className="button" disabled={bulkBusy}
                      onClick={() => void applyBulk()}>
                {bulkBusy ? "Aplicando…" : bulkValue ? "Marcar" : "Devolver"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
