"use client";

import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { cycleBadge, FactusolDocumentDetailModal } from
  "../../components/erp/FactusolDocumentDetailModal";
import {
  getFactusolSeries,
  listFactusolDocuments,
  type FactusolDocType,
  type FactusolDocument,
  type FactusolDocumentFilters,
  type FactusolDocumentSort,
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

/** E3-B — estados del ciclo por los que se puede filtrar en cada pestaña
 *  (una factura no tiene «siguiente paso», así que no filtra). */
const CICLO_OPTIONS: Partial<Record<
  FactusolDocType,
  { value: NonNullable<FactusolDocumentFilters["ciclo"]>; label: string }[]
>> = {
  presupuestos: [
    { value: "pendiente", label: "Sin albarán ni factura" },
    { value: "con_albaran", label: "Con albarán (sin factura)" },
    { value: "facturado", label: "Facturados" },
  ],
  pedidos: [
    { value: "pendiente", label: "Sin albarán ni factura" },
    { value: "con_albaran", label: "Con albarán (sin factura)" },
    { value: "facturado", label: "Facturados" },
  ],
  albaranes: [
    { value: "pendiente", label: "Sin factura" },
    { value: "facturado", label: "Facturados" },
  ],
};

/** Celda «Ciclo» (E3-B): las facturas enseñan su origen; el resto, el badge
 *  del estado del ciclo PRE→ALB→FAC. Sin anotación (el backend la sirve
 *  best-effort) → «—». */
function renderCiclo(d: FactusolDocument) {
  const ciclo = d.ciclo;
  if (!ciclo) return <span className="muted">—</span>;
  if (d.doc_type === "facturas") {
    return ciclo.origen.length > 0 ? (
      <span className="muted small">
        de {ciclo.origen.map((o) => o.numero).join(", ")}
      </span>
    ) : (
      <span className="muted">—</span>
    );
  }
  const badge = cycleBadge(ciclo.estado);
  return badge ? (
    <span className={badge.className}>{badge.label}</span>
  ) : (
    <span className="muted">—</span>
  );
}

/** ERP-E3-A/E3-B — explorador de documentos FACTUSOL. Lectura EN VIVO (sin
 *  cache): cada listado consulta la contabilidad real, igual que la vista
 *  de proformas de C-4. Desde E3-B el detalle permite crear el siguiente
 *  documento del ciclo (albarán/factura). */
export default function FactusolDocumentosPage() {
  const [tab, setTab] = useState<FactusolDocType>("facturas");
  const [items, setItems] = useState<FactusolDocument[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [series, setSeries] = useState<FactusolSerie[]>([]);
  const [detail, setDetail] = useState<FactusolDocument | null>(null);

  // Filtros. `clienteQ` viaja tal cual: el backend lo resuelve contra
  // F_CLI por nombre, CIF o email (E3-A-fix1).
  const [serie, setSerie] = useState<string>("");
  const [clienteInput, setClienteInput] = useState("");
  const [clienteQ, setClienteQ] = useState("");
  const [fechaDesde, setFechaDesde] = useState("");
  const [fechaHasta, setFechaHasta] = useState("");
  const [q, setQ] = useState("");
  // E3-B — filtro por estado del ciclo PRE→ALB→FAC.
  const [ciclo, setCiclo] = useState<string>("");
  // Orden (E3-A-fix1): sobre el conjunto completo filtrado, en el backend.
  const [sort, setSort] = useState<FactusolDocumentSort>("numero");
  const [dir, setDir] = useState<"asc" | "desc">("desc");

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
        cliente_q: clienteQ.trim() || undefined,
        fecha_desde: fechaDesde || undefined,
        fecha_hasta: fechaHasta || undefined,
        q: q.trim() || undefined,
        ciclo: (ciclo || undefined) as FactusolDocumentFilters["ciclo"],
        sort,
        dir,
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
  }, [tab, serie, clienteQ, fechaDesde, fechaHasta, q, ciclo, sort, dir]);

  useEffect(() => {
    void load(0);
  }, [load]);

  function toggleSort(column: FactusolDocumentSort) {
    if (sort === column) {
      setDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSort(column);
      // Texto asc primero (cliente); numérico/fecha desc primero, como el
      // orden por defecto.
      setDir(column === "cliente" ? "asc" : "desc");
    }
  }

  function limpiar() {
    setSerie("");
    setClienteInput("");
    setClienteQ("");
    setFechaDesde("");
    setFechaHasta("");
    setQ("");
    setCiclo("");
  }

  const hasFilters =
    serie !== "" || clienteQ !== "" || fechaDesde !== "" ||
    fechaHasta !== "" || q.trim() !== "" || ciclo !== "";
  const cicloOptions = CICLO_OPTIONS[tab];

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
            onClick={() => { setTab(t.key); setDetail(null); setCiclo(""); }}
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
          <span>Cliente, CIF o email</span>
          <span className="erp-doc-cliente-buscar">
            <input
              type="text"
              placeholder="Nombre, B12345678 o email@…"
              value={clienteInput}
              aria-label="Cliente, CIF o email"
              onChange={(e) => setClienteInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") setClienteQ(clienteInput);
              }}
            />
            <button
              type="button"
              className="button small secondary"
              onClick={() => setClienteQ(clienteInput)}
            >
              Buscar
            </button>
          </span>
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
        {cicloOptions ? (
          <label className="field">
            <span>Ciclo</span>
            <select
              value={ciclo}
              aria-label="Estado del ciclo"
              onChange={(e) => setCiclo(e.target.value)}
            >
              <option value="">Todos</option>
              {cicloOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </label>
        ) : null}
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
                {([
                  ["numero", "Número"],
                  ["cliente", "Cliente"],
                  ["fecha", "Fecha"],
                  ["total", "Total"],
                ] as [FactusolDocumentSort, string][]).map(([col, label]) => (
                  <th
                    key={col}
                    aria-sort={
                      sort === col
                        ? dir === "asc" ? "ascending" : "descending"
                        : "none"
                    }
                  >
                    <button
                      type="button"
                      className="erp-doc-sort"
                      onClick={() => toggleSort(col)}
                    >
                      {label}
                      {sort === col ? (dir === "asc" ? " ▲" : " ▼") : ""}
                    </button>
                  </th>
                ))}
                <th>Estado</th>
                <th>Ciclo</th>
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
                  <td>{renderCiclo(d)}</td>
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
          onChanged={() => void load(offset)}
        />
      ) : null}
    </main>
  );
}
