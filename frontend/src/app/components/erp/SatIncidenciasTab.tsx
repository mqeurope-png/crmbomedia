"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  customerLabel,
  getSatIncidencias,
  resolveException,
  resolveShippingIncidencia,
  STATUS_LABELS,
  type SatIncidenciaRow,
  type SatQueueFilters,
} from "../../lib/erpApi";

/** Pestaña «Incidencias» de la Cola SAT (Lote 2 · PR-2 A5). Reúne los pedidos
 *  que SALEN de «Enviados» por una incidencia, de dos orígenes:
 *
 *   - **Envío** (transporte): `transport_status = incident`, lo pone el webhook
 *     de Genei o «Actualizar estado». Se resuelve devolviendo a «en tránsito»
 *     (arco abierto al SAT), y el pedido vuelve a «Enviados».
 *   - **Pedido** (excepción abierta del taller/stock/VIES…): se resuelve
 *     cerrando la excepción (mismo endpoint que la bandeja de excepciones).
 *
 *  Autocargable y con su propio scroll, como «Enviados». Informa del número de
 *  filas al contenedor (chip de la pestaña) por `onCount`. */
export function SatIncidenciasTab({
  filters,
  canResolveEnvio,
  canResolvePedido,
  reloadKey = 0,
  onCount,
  onResolved,
}: {
  filters: SatQueueFilters;
  /** `erp.sat.shipping`: resolver incidencia de ENVÍO (vuelve a «en tránsito»). */
  canResolveEnvio: boolean;
  /** `erp.orders.create` (oficina): cerrar la excepción de PEDIDO. */
  canResolvePedido: boolean;
  /** Cambia (p. ej. al pulsar «Actualizar») para forzar recarga. */
  reloadKey?: number;
  /** Nº de incidencias tras cargar (para el contador de la pestaña). */
  onCount?: (n: number) => void;
  /** Tras resolver una incidencia (el pedido vuelve a «Enviados»). */
  onResolved?: () => void;
}) {
  const [items, setItems] = useState<SatIncidenciaRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<{ id: string; msg: string } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    getSatIncidencias(filters)
      .then((r) => {
        setItems(r.items);
        setLoaded(true);
        onCount?.(r.items.length);
      })
      .catch((e) => setError(extractErrorMessage(e, "No se pudieron cargar las incidencias.")))
      .finally(() => setLoading(false));
    // onCount es estable en el uso real (useCallback en el padre); no lo metemos
    // en deps para no recargar en cada render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters, reloadKey]);

  useEffect(() => { load(); }, [load]);

  async function resolve(row: SatIncidenciaRow) {
    if (busyId) return;
    setBusyId(row.order_id);
    setRowError(null);
    setNotice(null);
    try {
      if (row.tipo === "envio") {
        await resolveShippingIncidencia(row.order_id);
        setNotice(`Incidencia de envío de ${row.order_number} resuelta: vuelve a «Enviados».`);
      } else {
        if (!row.exception_id) throw new Error("Sin excepción que resolver.");
        await resolveException(row.exception_id);
        setNotice(`Incidencia de ${row.order_number} resuelta.`);
      }
      // El pedido sale de esta pestaña; refrescamos «Enviados» en el padre.
      setItems((prev) => {
        const next = prev.filter((it) => it.order_id !== row.order_id);
        onCount?.(next.length);
        return next;
      });
      onResolved?.();
    } catch (e) {
      setRowError({
        id: row.order_id,
        msg: extractErrorMessage(e, "No se pudo resolver la incidencia."),
      });
    } finally {
      setBusyId(null);
    }
  }

  function canResolve(row: SatIncidenciaRow): boolean {
    return row.tipo === "envio" ? canResolveEnvio : canResolvePedido && !!row.exception_id;
  }

  return (
    <section
      className="sat-section sat-incidencias" role="tabpanel" id="sat-panel-incidencias"
      aria-label="Incidencias"
    >
      <p className="muted small sat-history-hint">
        Pedidos con incidencia (salen de «Enviados» hasta resolverse): de{" "}
        <strong>envío</strong> (transporte, del seguimiento de Genei) o de{" "}
        <strong>pedido</strong> (taller, stock, VIES…). Al resolver, el pedido
        vuelve a «Enviados».
      </p>
      {notice ? <p className="form-info small" role="status">{notice}</p> : null}
      <div className="sat-scroll">
        {error ? (
          <p className="form-error" role="alert">{error}</p>
        ) : loading && !loaded ? (
          <p className="muted">Cargando incidencias…</p>
        ) : items.length === 0 ? (
          <p className="sat-empty">
            {hasFilters(filters) ? "Ninguna incidencia con estos filtros." : "Sin incidencias. 👌"}
          </p>
        ) : (
          <div className="table-wrapper sat-history-wrap">
            <table className="data-table data-table--responsive sat-incidencias-table"
                   aria-label="Incidencias">
              <thead>
                <tr>
                  <th>Nº</th>
                  <th>Cliente</th>
                  <th>Tipo</th>
                  <th>Motivo</th>
                  <th>Seguimiento</th>
                  <th>Estado envío</th>
                  <th>Acción</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => {
                  const st = STATUS_LABELS[row.transport_status];
                  return (
                    <tr key={`${row.tipo}-${row.order_id}`}>
                      <td data-label="Nº" className="mono">
                        <Link href={`/erp/orders/${row.order_id}`}>{row.order_number}</Link>
                      </td>
                      <td data-label="Cliente" className="sat-td-cliente">
                        {customerLabel(row) || "—"}
                      </td>
                      <td data-label="Tipo">
                        <span className={`badge ${row.tipo === "envio" ? "active" : "warn"}`}>
                          {row.tipo === "envio" ? "Envío" : "Pedido"}
                        </span>
                        {row.tipo === "pedido" && row.exception_type ? (
                          <span className="muted small sat-history-reason"> {row.exception_type}</span>
                        ) : null}
                      </td>
                      <td data-label="Motivo">{row.motivo || "—"}</td>
                      <td data-label="Seguimiento" className="mono">{row.tracking_number || "—"}</td>
                      <td data-label="Estado envío">
                        <span className={`badge ${st?.tone ?? "muted"}`}>
                          {st?.label ?? row.transport_status}
                        </span>
                      </td>
                      <td data-label="Acción">
                        {canResolve(row) ? (
                          <button
                            type="button" className="button small"
                            disabled={busyId === row.order_id}
                            onClick={() => void resolve(row)}
                          >
                            {busyId === row.order_id ? "Resolviendo…" : "Resolver"}
                          </button>
                        ) : (
                          <span className="muted small">Sin permiso</span>
                        )}
                        {rowError && rowError.id === row.order_id ? (
                          <span className="form-error small" role="alert"> {rowError.msg}</span>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}

function hasFilters(f: SatQueueFilters): boolean {
  return Object.keys(f).length > 0;
}
