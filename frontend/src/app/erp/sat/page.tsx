"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { SatIncidenciasTab } from "../../components/erp/SatIncidenciasTab";
import { SatPreparingCard } from "../../components/erp/SatPreparingCard";
import { satDateTime, SatQueueTable } from "../../components/erp/SatQueueTable";
import { SatReadyCard } from "../../components/erp/SatReadyCard";
import { getCurrentUser } from "../../lib/api";
import { Cap, can } from "../../lib/capabilities";
import { extractErrorMessage } from "../../lib/errors";
import {
  bulkNoShipping,
  customerLabel,
  findSatOrderByNumber,
  getErpSettings,
  getSatHistory,
  getSatQueue,
  satEnqueueOrder,
  STATUS_LABELS,
  type SatHistoryRow,
  type SatQueue,
  type SatQueueEstado,
  type SatQueueFilters,
  type SatQueueItem,
} from "../../lib/erpApi";

type View = "cards" | "list";

/** Lote 2 · PR-2: las pestañas de la cola. «Enviados» es el antiguo historial
 *  plegable (email al SAT o aprobación), ahora al mismo nivel que las otras
 *  para que se vea a qué hora se envió cada pedido y quién.
 *  Lote 3: «Global» muestra «Por embalar» y «Listos» a la vez (50/50).
 *  Lote 2 · PR-2 A5: «Incidencias» reúne los pedidos que salen de «Enviados»
 *  por una incidencia (de envío del webhook de Genei o de pedido/excepción). */
type Tab =
  | "por_embalar" | "listos" | "global" | "no_shipping" | "enviados" | "incidencias";

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

const ESTADO_OPTIONS: { value: "" | SatQueueEstado; label: string }[] = [
  { value: "", label: "Todos" },
  { value: "por_embalar", label: "Por embalar" },
  { value: "blocked", label: "Bloqueados" },
  { value: "in_queue", label: "En cola" },
  { value: "preparing", label: "Preparando" },
  { value: "ready", label: "Listos" },
];

const KIND_LABEL: Record<SatHistoryRow["kind"], string> = {
  email_sat: "Email SAT",
  aprobado: "Aprobado",
};

/** Cola SAT táctil (D-1-fix1): «Por embalar» (con acceso al modo trabajo) y
 *  «Listos para envío» (imprimir albarán/etiqueta + marcar recogido).
 *
 *  Lote B6: filtros (fechas, tienda, estado, texto), vista tarjetas/lista con
 *  las mismas acciones, historial de «enviados al taller» (email al SAT o
 *  aprobación) y «Añadir pedido a la cola» a mano por nº de pedido.
 *
 *  Lote 2 · PR-2 (revisión de diseño §8): la pantalla se diseña primero para
 *  móvil. Tres pestañas con contador — «Por embalar» · «Listos» · «Enviados»
 *  (el historial, con hora y responsable) —, y en las cards: observaciones
 *  del comercial arriba en ámbar, datos técnicos grandes con «copiar» y tres
 *  acciones de 48 px en dos filas. En escritorio, la misma card en rejilla. */
export default function SatQueuePage() {
  const [queue, setQueue] = useState<SatQueue>({ preparing: [], ready_for_pickup: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // --- filtros ---------------------------------------------------------------
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");
  const [store, setStore] = useState("");
  const [estado, setEstado] = useState<"" | SatQueueEstado>("");
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
    if (estado) f.estado = estado;
    if (q) f.q = q;
    // El orden siempre viaja (por defecto `fecha_desc`); no cuenta como
    // «filtro activo» para el botón «Limpiar».
    if (sort !== "fecha_desc") f.sort = sort;
    return f;
  }, [desde, hasta, store, estado, q, sort]);
  const hasFilters = Object.keys(filters).length > 0;

  function limpiar() {
    setDesde(""); setHasta(""); setStore(""); setEstado("");
    setSort("fecha_desc"); setQInput(""); setQ("");
  }

  // --- vista -----------------------------------------------------------------
  const [view, setView] = useState<View>("cards");
  useEffect(() => { setView(readStoredView()); }, []);
  function changeView(v: View) {
    setView(v);
    storeView(v);
  }

  // --- pestañas --------------------------------------------------------------
  const [tab, setTab] = useState<Tab>("por_embalar");

  /** El filtro «Estado» solo llena una sección: al elegirlo se salta a su
   *  pestaña para no dejar al operario mirando una cola vacía. */
  function changeEstado(v: "" | SatQueueEstado) {
    setEstado(v);
    if (v === "ready") setTab("listos");
    else if (v) setTab("por_embalar");
  }

  // --- permisos: añadir a mano es de oficina (admin / pedidos) ---------------
  const [canEdit, setCanEdit] = useState(false);
  // Roles y permisos: «Añadir a mano a la cola» es aprobar/meter en cola
  // (capacidad `erp.orders.approve` — oficina), NO trabajo de taller: el SAT no
  // lo ve. Va aparte de `canEdit` (trabajo de taller).
  const [canEnqueue, setCanEnqueue] = useState(false);
  // Genei (crear envío): capacidad `erp.sat.shipping`. El botón vive en la card
  // de «Listos»; el backend revalida. También habilita «Resolver» una incidencia
  // de ENVÍO en la pestaña «Incidencias».
  const [canShip, setCanShip] = useState(false);
  // Resolver una incidencia de PEDIDO (cerrar la excepción) es de oficina:
  // capacidad `erp.orders.create` (mismo gate que la bandeja de excepciones).
  const [canResolvePedido, setCanResolvePedido] = useState(false);

  useEffect(() => {
    getCurrentUser()
      // Roles y permisos: el trabajo de taller (preparar/embalar/técnicos) exige
      // la capacidad `erp.sat.prepare` (SAT/Pedidos/Admin). El Comercial solo ve
      // la cola; sube la etiqueta desde la ficha del pedido (envío). El backend
      // revalida cada acción (403 si falta la capacidad).
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

  // --- cola ------------------------------------------------------------------
  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    getSatQueue(filters)
      .then(setQueue)
      .catch((e) => setError(extractErrorMessage(e, "No se pudo cargar la cola.")))
      .finally(() => setLoading(false));
  }, [filters]);

  useEffect(() => { load(); }, [load]);

  // --- «Enviados»: historial de enviados al taller (carga perezosa) ----------
  const historyOpen = tab === "enviados";
  const [history, setHistory] = useState<SatHistoryRow[]>([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);

  const loadHistory = useCallback(() => {
    setHistoryLoading(true);
    setHistoryError(null);
    getSatHistory(filters)
      .then((r) => { setHistory(r.items); setHistoryLoaded(true); })
      .catch((e) => setHistoryError(extractErrorMessage(e, "No se pudo cargar el historial.")))
      .finally(() => setHistoryLoading(false));
  }, [filters]);

  useEffect(() => {
    if (historyOpen) loadHistory();
  }, [historyOpen, loadHistory]);

  // --- «Incidencias»: pedidos que salen de «Enviados» (carga en el componente).
  // Aquí solo llevamos el contador (chip) y una señal de recarga para que
  // «Actualizar» refresque la pestaña; el componente hace el fetch.
  const [incidenciasCount, setIncidenciasCount] = useState<number | null>(null);
  const [incidenciasReload, setIncidenciasReload] = useState(0);

  const refreshAll = useCallback(() => {
    load();
    if (historyOpen) loadHistory();
    if (tab === "incidencias") setIncidenciasReload((n) => n + 1);
  }, [load, loadHistory, historyOpen, tab]);

  // --- «No requiere envío»: vista de marcados (carga perezosa) ---------------
  const noShipOpen = tab === "no_shipping";
  const [noShip, setNoShip] = useState<SatQueueItem[]>([]);
  const [noShipLoaded, setNoShipLoaded] = useState(false);
  const [noShipLoading, setNoShipLoading] = useState(false);

  const loadNoShip = useCallback(() => {
    setNoShipLoading(true);
    getSatQueue({ ...filters, no_shipping: true })
      .then((r) => { setNoShip([...r.preparing, ...r.ready_for_pickup]); setNoShipLoaded(true); })
      .catch((e) => setError(extractErrorMessage(e, "No se pudieron cargar los marcados.")))
      .finally(() => setNoShipLoading(false));
  }, [filters]);

  useEffect(() => { if (noShipOpen) loadNoShip(); }, [noShipOpen, loadNoShip]);

  // --- selección múltiple (marcar/desmarcar «No requiere envío» en lote) -----
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  /** Pedidos visibles y seleccionables de la pestaña actual. */
  const visibleItems = useMemo<SatQueueItem[]>(() => {
    if (tab === "no_shipping") return noShip;
    if (tab === "listos") return queue.ready_for_pickup;
    if (tab === "global") return [...queue.preparing, ...queue.ready_for_pickup];
    if (tab === "por_embalar") return queue.preparing;
    return [];
  }, [tab, noShip, queue]);

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

  // En las pestañas normales el lote MARCA «No requiere envío»; en la pestaña
  // «No requieren envío», DESMARCA (vuelven a la Cola SAT).
  const bulkValue = tab !== "no_shipping";
  // Selección solo en las colas de una sección (no en «Global» ni «Enviados»).
  const selectable = canEdit && (
    tab === "por_embalar" || tab === "listos" || tab === "no_shipping"
  );

  async function applyBulk() {
    if (selected.size === 0 || bulkBusy) return;
    setBulkBusy(true);
    setError(null);
    try {
      const r = await bulkNoShipping([...selected], bulkValue);
      setNotice(
        bulkValue
          ? `${r.changed} pedido(s) marcados «No requiere envío».`
          : `${r.changed} pedido(s) vuelven a requerir envío.`,
      );
      setSelected(new Set());
      setConfirmOpen(false);
      refreshAll();
      if (noShipLoaded || noShipOpen) loadNoShip();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo aplicar el cambio."));
    } finally {
      setBulkBusy(false);
    }
  }

  /** Envuelve una card con su casilla de selección (solo si `selectable`). */
  function withSelect(o: SatQueueItem, card: React.ReactNode): React.ReactNode {
    if (!selectable) return card;
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

  const { preparing, ready_for_pickup: ready } = queue;

  const TABS: { key: Tab; label: string; count: number | null }[] = [
    { key: "por_embalar", label: "Por embalar", count: preparing.length },
    { key: "listos", label: "Listos", count: ready.length },
    { key: "global", label: "Global", count: preparing.length + ready.length },
    { key: "no_shipping", label: "No requieren envío",
      count: noShipLoaded ? noShip.length : null },
    { key: "enviados", label: "Enviados", count: historyLoaded ? history.length : null },
    { key: "incidencias", label: "Incidencias", count: incidenciasCount },
  ];

  return (
    <div className={`sat-queue-wrap ${view === "list" ? "sat-view-list" : "sat-view-cards"}`}>
      <div className="sat-queue-head">
        <h1>Cola SAT</h1>
        <span className="muted small sat-queue-count">
          {loading ? "Cargando…" : `${preparing.length + ready.length} en el taller`}
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
          <span>Estado</span>
          <select value={estado} aria-label="Estado"
                  onChange={(e) => changeEstado(e.target.value as "" | SatQueueEstado)}>
            {ESTADO_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
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
            onClick={() => setTab(t.key)}
          >
            {t.label}{" "}
            {t.count !== null ? <span className="sat-tab-count">{t.count}</span> : null}
          </button>
        ))}
      </div>

      {/* Barra de acción en lote «No requiere envío» (marcar / desmarcar). */}
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
            {bulkValue ? "Marcar «No requiere envío»" : "Volver a requerir envío"}
          </button>
        </div>
      ) : null}
      {notice ? <p className="form-info small" role="status">{notice}</p> : null}

      {tab === "por_embalar" ? (
        <section
          className="sat-section" role="tabpanel" id="sat-panel-por_embalar"
          aria-label="Por embalar"
        >
          {/* Lote 4 · #1 — el contenido de la vista vive en un contenedor con
              scroll vertical propio, así se alcanza el último pedido aunque la
              cola sea larga (el body es overflow:hidden). */}
          <div className="sat-scroll">
            {preparing.length === 0 ? (
              <p className="sat-empty">{hasFilters ? "Nada por embalar con estos filtros." : "Nada por embalar."}</p>
            ) : view === "list" ? (
              <SatQueueTable items={preparing} variant="preparing" onChanged={refreshAll}
                             ariaLabel="Pedidos por embalar"
                             selectable={selectable} selected={selected} onToggle={toggleSel} />
            ) : (
              <div className="sat-cards">
                {preparing.map((o) => withSelect(o,
                  <SatPreparingCard key={o.id} order={o} onChanged={refreshAll}
                                    canEdit={canEdit} />,
                ))}
              </div>
            )}
          </div>
        </section>
      ) : null}

      {tab === "listos" ? (
        <section
          className="sat-section" role="tabpanel" id="sat-panel-listos"
          aria-label="Listos para envío"
        >
          <div className="sat-scroll">
            {ready.length === 0 ? (
              <p className="sat-empty">{hasFilters ? "Nada listo para enviar con estos filtros." : "Nada listo para enviar."}</p>
            ) : view === "list" ? (
              <SatQueueTable items={ready} variant="ready" onChanged={refreshAll}
                             ariaLabel="Pedidos listos para envío"
                             selectable={selectable} selected={selected} onToggle={toggleSel} />
            ) : (
              <div className="sat-cards">
                {ready.map((o) => withSelect(o,
                  <SatReadyCard key={o.id} order={o} onChanged={refreshAll}
                                canEdit={canEdit} canShip={canShip} />,
                ))}
              </div>
            )}
          </div>
        </section>
      ) : null}

      {tab === "global" ? (
        <section
          className="sat-section sat-global" role="tabpanel" id="sat-panel-global"
          aria-label="Por embalar y listos"
        >
          {/* Lote 4 · #2 — la vista global también respeta el toggle
              Tarjetas/Lista: cada columna pinta su tabla compacta o sus
              tarjetas. #1 — cada columna scrollea por su cuenta (o la página,
              en móvil) para llegar a todos los pedidos de ambas colas. */}
          <div className="sat-global-cols">
            <div className="sat-global-col" aria-label="Por embalar">
              <h2 className="sat-global-title">
                📦 Por embalar <span className="sat-tab-count">{preparing.length}</span>
              </h2>
              <div className="sat-scroll">
                {preparing.length === 0 ? (
                  <p className="sat-empty">{hasFilters ? "Nada por embalar con estos filtros." : "Nada por embalar."}</p>
                ) : view === "list" ? (
                  <SatQueueTable items={preparing} variant="preparing" onChanged={refreshAll}
                                 ariaLabel="Pedidos por embalar" />
                ) : (
                  <div className="sat-cards sat-cards--single">
                    {preparing.map((o) => (
                      <SatPreparingCard key={o.id} order={o} onChanged={refreshAll}
                                        canEdit={canEdit} />
                    ))}
                  </div>
                )}
              </div>
            </div>
            <div className="sat-global-col" aria-label="Listos para envío">
              <h2 className="sat-global-title">
                🚚 Listos <span className="sat-tab-count">{ready.length}</span>
              </h2>
              <div className="sat-scroll">
                {ready.length === 0 ? (
                  <p className="sat-empty">{hasFilters ? "Nada listo para enviar con estos filtros." : "Nada listo para enviar."}</p>
                ) : view === "list" ? (
                  <SatQueueTable items={ready} variant="ready" onChanged={refreshAll}
                                 ariaLabel="Pedidos listos para envío" />
                ) : (
                  <div className="sat-cards sat-cards--single">
                    {ready.map((o) => (
                      <SatReadyCard key={o.id} order={o} onChanged={refreshAll}
                                    canEdit={canEdit} canShip={canShip} />
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        </section>
      ) : null}

      {tab === "no_shipping" ? (
        <section
          className="sat-section" role="tabpanel" id="sat-panel-no_shipping"
          aria-label="No requieren envío"
        >
          <p className="muted small sat-history-hint">
            Pedidos marcados «No requiere envío»: fuera de la Cola SAT y de «Por
            enviar». Selecciónalos para devolverlos al taller. No afecta a la
            factura ni al cobro.
          </p>
          <div className="sat-scroll">
            {noShipLoading && noShip.length === 0 ? (
              <p className="muted">Cargando…</p>
            ) : noShip.length === 0 ? (
              <p className="sat-empty">
                {hasFilters ? "Ninguno con estos filtros." : "Ningún pedido marcado «No requiere envío»."}
              </p>
            ) : view === "list" ? (
              <SatQueueTable items={noShip} variant="preparing" onChanged={refreshAll}
                             ariaLabel="Pedidos que no requieren envío"
                             selectable={selectable} selected={selected} onToggle={toggleSel} />
            ) : (
              <div className="sat-cards">
                {noShip.map((o) => withSelect(o,
                  <SatPreparingCard key={o.id} order={o} onChanged={refreshAll}
                                    canEdit={canEdit} />,
                ))}
              </div>
            )}
          </div>
        </section>
      ) : null}

      {tab === "enviados" ? (
        <section
          className="sat-section sat-history" role="tabpanel" id="sat-panel-enviados"
          aria-label="Enviados al taller"
        >
          <p className="muted small sat-history-hint">
            Cada envío al taller (email al SAT o aprobación), con su hora y quién lo hizo:
            así no se manda dos veces.
          </p>
          <div className="sat-scroll">
          {historyError ? (
            <p className="form-error">{historyError}</p>
          ) : historyLoading && history.length === 0 ? (
            <p className="muted">Cargando historial…</p>
          ) : history.length === 0 ? (
            <p className="muted">Sin envíos al taller{hasFilters ? " con estos filtros" : ""}.</p>
          ) : (
            <div className="table-wrapper sat-history-wrap">
              <table className="data-table data-table--responsive sat-history-table"
                     aria-label="Enviados al taller">
                <thead>
                  <tr>
                    <th>Nº</th>
                    <th>Cliente</th>
                    <th>Cuándo</th>
                    <th>Quién</th>
                    <th>Tipo</th>
                    <th>Destinatario</th>
                    <th>Estado actual</th>
                    <th>Estado envío</th>
                    <th>Albarán</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((h, i) => (
                    <tr key={`${h.kind}-${h.order_id}-${h.at}-${i}`}>
                      <td data-label="Nº" className="mono">
                        <Link href={`/erp/orders/${h.order_id}`}>{h.order_number}</Link>
                      </td>
                      <td data-label="Cliente" className="sat-td-cliente">{customerLabel(h) || "—"}</td>
                      <td data-label="Cuándo" className="mono">{satDateTime(h.at)}</td>
                      <td data-label="Quién">{h.actor_name ?? "—"}</td>
                      <td data-label="Tipo" title={h.reason ?? h.subject ?? undefined}>
                        <span className={`badge ${h.kind === "email_sat" ? "active" : "ok"}`}>
                          {KIND_LABEL[h.kind]}
                        </span>
                        {h.reason ? <span className="muted small sat-history-reason"> {h.reason}</span> : null}
                      </td>
                      <td data-label="Destinatario">{h.to.length > 0 ? h.to.join(", ") : "—"}</td>
                      <td data-label="Estado actual">
                        <span className={`badge ${STATUS_LABELS[h.preparation_status]?.tone ?? "muted"}`}>
                          {STATUS_LABELS[h.preparation_status]?.label ?? h.preparation_status}
                        </span>
                        {h.cancelled ? <span className="badge bad"> Anulado</span> : null}
                        {h.excluded ? <span className="badge muted"> Quitado</span> : null}
                      </td>
                      <td data-label="Estado envío">
                        <span className={`badge ${STATUS_LABELS[h.transport_status]?.tone ?? "muted"}`}>
                          {STATUS_LABELS[h.transport_status]?.label ?? h.transport_status}
                        </span>
                      </td>
                      <td data-label="Albarán" className="mono">{h.factusol_albaran_number ?? (h.has_albaran ? "Subido" : "—")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
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
             aria-label="Confirmar No requiere envío">
          <div className="modal-dialog erp-modal">
            <h2>{bulkValue ? "Marcar «No requiere envío»" : "Volver a requerir envío"}</h2>
            <p>
              {bulkValue
                ? `Vas a marcar ${selected.size} pedido(s) como «No requiere envío». `
                  + "Saldrán de la Cola SAT. No afecta a la factura ni al cobro. Es reversible."
                : `Vas a devolver ${selected.size} pedido(s) al taller: volverán a `
                  + "requerir envío según su estado de preparación."}
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
