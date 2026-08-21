"use client";

import { useEffect, useState } from "react";
import {
  getFactusolDocument,
  type FactusolDocType,
  type FactusolDocumentDetail,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

const TYPE_LABELS: Record<FactusolDocType, string> = {
  pedidos: "Pedido de cliente",
  presupuestos: "Presupuesto",
  albaranes: "Albarán",
  facturas: "Factura",
};

/** ERP-E3-A — detalle READ-ONLY de un documento FACTUSOL: cabecera +
 *  líneas, leído en vivo por su clave compuesta (serie, código). */
export function FactusolDocumentDetailModal({
  docType,
  serie,
  codigo,
  onClose,
}: {
  docType: FactusolDocType;
  serie: number;
  codigo: number | string;
  onClose: () => void;
}) {
  const [doc, setDoc] = useState<FactusolDocumentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setDoc(null);
    setError(null);
    getFactusolDocument(docType, serie, codigo)
      .then((d) => { if (alive) setDoc(d); })
      .catch((e) => {
        if (alive) setError(extractErrorMessage(e, "No se pudo cargar el documento."));
      });
    return () => { alive = false; };
  }, [docType, serie, codigo]);

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label={`Detalle ${TYPE_LABELS[docType]}`}>
      <div className="modal-dialog erp-emit-modal erp-doc-detail">
        <h2>
          {TYPE_LABELS[docType]}{" "}
          <span className="muted">{doc?.numero ?? `${serie}-${codigo}`}</span>
        </h2>

        {error ? <p className="form-error">{error}</p> : null}
        {!doc && !error ? <p className="muted">Cargando…</p> : null}

        {doc ? (
          <>
            <dl className="erp-doc-detail-head">
              <dt>Cliente</dt>
              <dd>{doc.cliente_nombre ?? doc.cliente_codigo ?? "—"}</dd>
              <dt>Fecha</dt>
              <dd>{doc.fecha ?? "—"}</dd>
              <dt>Estado</dt>
              <dd>{doc.estado_label}</dd>
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
        </div>
      </div>
    </div>
  );
}
