"use client";

import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { FactusolDocumentDetailModal } from
  "../../components/erp/FactusolDocumentDetailModal";
import {
  getFactusolSeries,
  listFactusolDocuments,
  searchFactusolCustomers,
  type FactusolCustomer,
  type FactusolDocType,
  type FactusolDocument,
  type FactusolSerie,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

const PAGE_SIZE = 100;

const TABS: { key: FactusolDocType; label: string }[] = [
  { key: "pedidos", label: "Pedidos" },
  { key: "presupuestos", label: "Presupuestos" },
  { key: "albaranes", label: "Albaranes" },
  { key: "facturas", label: "Facturas" },
];

/** ERP-E3-A — explorador de documentos FACTUSOL. Lectura EN VIVO (sin
 *  cache): cada listado consulta la contabilidad real, igual que la vista
 *  de proformas de C-4. Solo lectura — las acciones de crear llegan en
 *  E3-B. */
export default function FactusolDocumentosPage() {
  const [tab, setTab] = useState<FactusolDocType>("facturas");
  const [items, setItems] = useState<FactusolDocument[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [series, setSeries] = useState<FactusolSerie[]>([]);
  const [detail, setDetail] = useState<FactusolDocument | null>(null);

  // Filtros. `cliente` guarda el CODCLI resuelto por el buscador.
  const [serie, setSerie] = useState<string>("");
  const [cliente, setCliente] = useState<FactusolCustomer | null>(null);
  const [clienteQuery, setClienteQuery] = useState("");
  const [clienteOpts, setClienteOpts] = useState<FactusolCustomer[]>([]);
  const [fechaDesde, setFechaDesde] = useState("");
  const [fechaHasta, setFechaHasta] = useState("");
  const [q, setQ] = useState("");

  useEffect(() => {
    getFactusolSeries()
      .then((r) => setSeries(r.items.filter((s) => s.is_known)))
      .catch(() => setSeries([]));
  }, []);

  const load = useCallback(async (nextOffset: number) => {
    setLoading(true);
    setError(null);
    try {
      const r = await listFactusolDocuments(tab, {
        serie: serie ? Number(serie) : undefined,
        codcli: cliente?.codcli ?? undefined,
        fecha_desde: fechaDesde || undefined,
        fecha_hasta: fechaHasta || undefined,
        q: q.trim() || undefined,
        limit: PAGE_SIZE,
        offset: nextOffset,
      });
      setItems(r.items);
      setTotal(r.total);
      setOffset(nextOffset);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo consultar FACTUSOL."));
      setItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [tab, serie, cliente, fechaDesde, fechaHasta, q]);

  useEffect(() => {
    void load(0);
  }, [load]);

  async function buscarCliente() {
    const query = clienteQuery.trim();
    if (!query) return;
    try {
      setClienteOpts(await searchFactusolCustomers(query, "name"));
    } catch {
      setClienteOpts([]);
    }
  }

  function limpiar() {
    setSerie("");
    setCliente(null);
    setClienteQuery("");
    setClienteOpts([]);
    setFechaDesde("");
    setFechaHasta("");
    setQ("");
  }

  const hasFilters =
    serie !== "" || cliente !== null || fechaDesde !== "" ||
    fechaHasta !== "" || q.trim() !== "";

  return (
    <main className="shell">
      <PageHeader
        title="Documentos FACTUSOL"
        description="Lectura en vivo de la contabilidad — pedidos, presupuestos, albaranes y facturas."
      />

      <div className="erp-doc-tabs" role="tablist" aria-label="Tipo de documento">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            className={`pill-toggle ${tab === t.key ? "is-active" : ""}`}
            onClick={() => { setTab(t.key); setDetail(null); }}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="erp-doc-filters">
        <label className="field">
          <span>Serie / empresa</span>
          <select
            value={serie}
            aria-label="Serie / empresa"
            onChange={(e) => setSerie(e.target.value)}
          >
            <option value="">Todas</option>
            {series.map((s) => (
              <option key={s.serie} value={s.serie}>
                {s.serie} · {s.nombre}
              </option>
            ))}
          </select>
        </label>

        <label className="field erp-doc-filter-cliente">
          <span>Cliente</span>
          {cliente ? (
            <span className="badge info erp-doc-cliente-activo">
              {cliente.nombre || cliente.codcli}
              <button
                type="button"
                aria-label="Quitar filtro de cliente"
                onClick={() => setCliente(null)}
              >
                ×
              </button>
            </span>
          ) : (
            <span className="erp-doc-cliente-buscar">
              <input
                type="text"
                placeholder="Nombre del cliente…"
                value={clienteQuery}
                aria-label="Buscar cliente"
                onChange={(e) => setClienteQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void buscarCliente();
                }}
              />
              <button
                type="button"
                className="button small secondary"
                onClick={() => void buscarCliente()}
              >
                Buscar
              </button>
            </span>
          )}
          {!cliente && clienteOpts.length > 0 ? (
            <ul className="erp-doc-cliente-opts">
              {clienteOpts.slice(0, 8).map((c) => (
                <li key={c.codcli ?? c.nombre}>
                  <button
                    type="button"
                    onClick={() => {
                      setCliente(c);
                      setClienteOpts([]);
                    }}
                  >
                    {c.nombre || "(sin nombre)"}{" "}
                    <span className="muted small">#{c.codcli}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </label>

        <label className="field">
          <span>Desde</span>
          <input
            type="date"
            value={fechaDesde}
            aria-label="Fecha desde"
            onChange={(e) => setFechaDesde(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Hasta</span>
          <input
            type="date"
            value={fechaHasta}
            aria-label="Fecha hasta"
            onChange={(e) => setFechaHasta(e.target.value)}
          />
        </label>
        <label className="field erp-doc-filter-q">
          <span>Nº / referencia</span>
          <input
            type="search"
            placeholder="5-260066, BOP-099917…"
            value={q}
            aria-label="Buscar por número o referencia"
            onChange={(e) => setQ(e.target.value)}
          />
        </label>
        {hasFilters ? (
          <button
            type="button"
            className="button small secondary erp-doc-clear"
            onClick={limpiar}
          >
            Limpiar filtros
          </button>
        ) : null}
      </div>

      {error ? <p className="form-error">{error}</p> : null}

      {loading ? (
        <p className="muted">Consultando FACTUSOL…</p>
      ) : items.length === 0 ? (
        <p className="muted">Sin documentos que casen los filtros.</p>
      ) : (
        <>
          <table className="data-table erp-doc-table">
            <thead>
              <tr>
                <th>Número</th>
                <th>Cliente</th>
                <th>Fecha</th>
                <th>Total</th>
                <th>Estado</th>
                <th>Referencia</th>
              </tr>
            </thead>
            <tbody>
              {items.map((d) => (
                <tr
                  key={`${d.serie}-${d.codigo}`}
                  className="erp-doc-row"
                  onClick={() => setDetail(d)}
                >
                  <td><strong>{d.numero}</strong></td>
                  <td>{d.cliente_nombre ?? d.cliente_codigo ?? "—"}</td>
                  <td>{d.fecha ?? "—"}</td>
                  <td>
                    {d.total !== null && d.total !== undefined
                      ? `${d.total.toFixed(2)} €` : "—"}
                  </td>
                  <td>{d.estado_label}</td>
                  <td className="muted small">{d.referencia ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="erp-doc-pager">
            <span className="muted small">
              {offset + 1}–{Math.min(offset + items.length, total)} de {total}
            </span>
            <button
              type="button"
              className="button small secondary"
              disabled={offset === 0 || loading}
              onClick={() => void load(Math.max(0, offset - PAGE_SIZE))}
            >
              ← Anteriores
            </button>
            <button
              type="button"
              className="button small secondary"
              disabled={offset + items.length >= total || loading}
              onClick={() => void load(offset + PAGE_SIZE)}
            >
              Siguientes →
            </button>
          </div>
        </>
      )}

      {detail && detail.serie !== null && detail.codigo !== null ? (
        <FactusolDocumentDetailModal
          docType={tab}
          serie={detail.serie}
          codigo={detail.codigo}
          onClose={() => setDetail(null)}
        />
      ) : null}
    </main>
  );
}
