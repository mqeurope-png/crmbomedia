"use client";

import { useEffect, useState } from "react";
import {
  getAlbaranesCandidatos,
  vincularAlbaranPedido,
  type AlbaranCandidato,
  type OrderDetail,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

/** Vincular un albarán que ya existe, desde la ficha del pedido (30/10/2026),
 *  sin ir a ERP · Documentos: los del cliente que no están en otro pedido,
 *  los más recientes primero (el de la factura de origen delante), cada uno
 *  con el documento del que sale, y un buscador por número. No escribe en
 *  FACTUSOL; se deshace con «Desvincular» en el bloque FACTUSOL. */

const NF = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function fecha(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("es-ES");
}

export function VincularAlbaranPanel({
  orderId, aviso, onLinked,
}: {
  orderId: string;
  /** Por qué no tiene albarán (p. ej. la factura no lo apunta). */
  aviso?: string | null;
  onLinked: (order: OrderDetail) => void;
}) {
  const [abierto, setAbierto] = useState(false);
  const [q, setQ] = useState("");
  const [buscado, setBuscado] = useState<string | undefined>(undefined);
  const [items, setItems] = useState<AlbaranCandidato[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState<string | null>(null);

  useEffect(() => {
    if (!abierto) return;
    let vivo = true;
    setItems(null);
    getAlbaranesCandidatos(orderId, buscado)
      .then((r) => { if (vivo) setItems(r.items); })
      .catch((e) => { if (vivo) setError(extractErrorMessage(e, "No se han podido leer los albaranes.")); });
    return () => { vivo = false; };
  }, [abierto, orderId, buscado]);

  async function vincular(a: AlbaranCandidato) {
    setOcupado(a.numero);
    setError(null);
    try {
      onLinked(await vincularAlbaranPedido(orderId, a.serie, a.codigo));
      setAbierto(false);
    } catch (e) {
      setError(extractErrorMessage(e, "No se ha podido vincular el albarán."));
    } finally {
      setOcupado(null);
    }
  }

  return (
    <div className="erp-vincular-albaran" aria-label="Vincular un albarán">
      {aviso ? <p className="form-info" role="status">{aviso}</p> : null}
      {!abierto ? (
        <button type="button" className="button small secondary" onClick={() => setAbierto(true)}>
          Vincular un albarán que ya existe
        </button>
      ) : (
        <>
          <form className="erp-historico-acciones" onSubmit={(e) => {
            e.preventDefault();
            setBuscado(q.trim() || undefined);
          }}>
            <label className="field">
              <span>Buscar por número</span>
              <input value={q} placeholder="p. ej. 5-500004" maxLength={40}
                     onChange={(e) => setQ(e.target.value)} />
            </label>
            <button type="submit" className="button small secondary">Buscar</button>
            {buscado ? (
              <button type="button" className="button small secondary"
                      onClick={() => { setQ(""); setBuscado(undefined); }}>
                Los del cliente
              </button>
            ) : null}
            <button type="button" className="button small secondary" onClick={() => setAbierto(false)}>
              Cerrar
            </button>
          </form>
          {error ? <p className="form-error" role="alert">{error}</p> : null}
          {items === null ? <p className="muted">Buscando…</p> : items.length === 0 ? (
            <p className="muted">
              {buscado ? `Ningún albarán libre con el número ${buscado}.`
                : "Este cliente no tiene albaranes sin pedido."}
            </p>
          ) : (
            <table className="data-table data-table--responsive">
              <thead>
                <tr><th>Albarán</th><th>Fecha</th><th>Sale de</th><th className="num">Total</th><th aria-label="Acción" /></tr>
              </thead>
              <tbody>
                {items.map((a) => (
                  <tr key={`${a.ejercicio}-${a.numero}`}>
                    <td data-label="Albarán">
                      <span className="mono">{a.numero}</span>
                      {a.de_la_factura ? <span className="badge ok"> el de su factura</span> : null}
                      {buscado && a.cliente_nombre ? <div className="muted">{a.cliente_nombre}</div> : null}
                    </td>
                    <td data-label="Fecha">{fecha(a.fecha)}</td>
                    <td data-label="Sale de">{a.viene_de ?? <span className="muted">—</span>}</td>
                    <td data-label="Total" className="num">{a.total == null ? "—" : `${NF.format(a.total)} €`}</td>
                    <td>
                      <button type="button" className="button small" disabled={ocupado !== null}
                              onClick={() => void vincular(a)}>
                        {ocupado === a.numero ? "Vinculando…" : "Vincular"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  );
}
