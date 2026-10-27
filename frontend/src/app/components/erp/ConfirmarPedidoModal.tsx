"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ApiError } from "../../lib/api";
import {
  createOrderFromDocumentType,
  previewOrderFromDocumentType,
  type AlbaranJobExtra,
  type DocumentOrderPreview,
  type OrderDetail,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";
import { ErpModalShell } from "./ErpModalShell";
import { ResumenPedidoView } from "./ResumenPedidoView";

const LABEL: Record<"albaranes" | "facturas", string> = {
  albaranes: "el albarán",
  facturas: "la factura",
};
const LIGADO: Record<"albaranes" | "facturas", string> = {
  albaranes: "al albarán",
  facturas: "a la factura",
};

/** «Crear pedido» desde un albarán o una factura de FACTUSOL (Documentos):
 *  ANTES de crear nada enseña el resumen de lo que se va a crear —empresa,
 *  fecha, conceptos con cantidades, importe y de qué documento sale—, leído
 *  del servidor con el mismo cálculo que la creación. Solo «Crear pedido»
 *  crea (una vez: el botón se bloquea mientras tanto); «Cancelar» no deja
 *  nada a medias. Solo se LEE el documento: FACTUSOL no se toca. */
export function ConfirmarPedidoModal({
  docType,
  serie,
  codigo,
  numero,
  companyId,
  companyName,
  onClose,
  onCreated,
  onUnlinked,
}: {
  docType: "albaranes" | "facturas";
  serie: number;
  codigo: number;
  numero: string;
  /** La empresa del CRM con la que se crea (cruce de la fila o recién
   *  vinculada). Sin ella, la que resuelva el servidor por el cliente. */
  companyId?: string | null;
  companyName?: string | null;
  onClose: () => void;
  onCreated: (order: OrderDetail & AlbaranJobExtra) => void;
  /** El cliente FACTUSOL no tiene empresa en el CRM: hay que vincularla. */
  onUnlinked: (codcli: string, nombre: string | null) => void;
}) {
  const [preview, setPreview] = useState<DocumentOrderPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Un solo alta aunque se pulse dos veces antes de que React repinte.
  const enviado = useRef(false);

  useEffect(() => {
    let alive = true;
    previewOrderFromDocumentType(docType, serie, codigo)
      .then((p) => { if (alive) setPreview(p); })
      .catch((e) => { if (alive) setError(extractErrorMessage(e, "No se pudo leer el documento.")); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [docType, serie, codigo]);

  const empresaId = companyId ?? preview?.company_id ?? null;
  const yaImportado = preview?.already_imported ?? null;
  const puedeCrear = Boolean(preview) && !yaImportado && Boolean(empresaId) && !creating;

  async function confirmar() {
    if (!puedeCrear || enviado.current) return;
    enviado.current = true;
    setCreating(true);
    setError(null);
    try {
      const order = await createOrderFromDocumentType(docType, serie, codigo, { company_id: empresaId });
      onCreated(order);
    } catch (e) {
      enviado.current = false;
      setCreating(false);
      if (e instanceof ApiError && e.code === "factusol_customer_unlinked") {
        const det = (e.detail && typeof e.detail === "object" ? e.detail : {}) as Record<string, unknown>;
        const codcli = typeof det.codcli === "string" ? det.codcli : preview?.cliente_codigo;
        if (codcli) {
          onUnlinked(codcli, typeof det.cliente_nombre === "string" ? det.cliente_nombre : (preview?.cliente_nombre ?? null));
          return;
        }
      }
      setError(extractErrorMessage(e, "No se pudo crear el pedido."));
    }
  }

  return (
    <ErpModalShell
      label={`Crear pedido desde ${LABEL[docType]} ${numero}`}
      title={<>Crear pedido desde {LABEL[docType]} <span className="mono">{numero}</span></>}
      onClose={onClose}
      disabled={creating}
      dialogClassName="erp-modal modal-wide"
    >
      {loading ? <p className="muted">Leyendo el documento…</p> : null}
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {preview ? (
        <>
          {yaImportado ? (
            <p className="form-info" role="status">
              Este documento ya tiene pedido:{" "}
              <Link href={`/erp/orders/${yaImportado.order_id}`}>{yaImportado.order_number}</Link>.
              No se crea otro.
            </p>
          ) : (
            <p className="muted small">
              Revisa lo que se va a crear. Nada se crea hasta pulsar «Crear pedido».
            </p>
          )}
          {/* Ya tiene pedido: solo el enlace (el resumen sería el del documento,
              no el del pedido que existe). */}
          {!yaImportado ? (
            <ResumenPedidoView resumen={preview.resumen} empresa={companyName ?? null} />
          ) : null}
          {!empresaId && !yaImportado ? (
            <div className="form-warning" role="alert">
              El cliente FACTUSOL {preview.cliente_codigo ?? ""} no tiene empresa en el CRM.
              {preview.cliente_codigo ? (
                <button type="button" className="button small secondary"
                        onClick={() => onUnlinked(preview.cliente_codigo as string, preview.cliente_nombre)}>
                  Vincular empresa
                </button>
              ) : null}
            </div>
          ) : null}
          <p className="muted small">
            El pedido queda ligado {LIGADO[docType]}; en FACTUSOL no se escribe nada.
          </p>
        </>
      ) : null}
      <div className="modal-actions">
        <button type="button" className="button secondary" onClick={onClose} disabled={creating}>
          Cancelar
        </button>
        {!yaImportado ? (
          <button type="button" className="button" disabled={!puedeCrear}
                  onClick={() => void confirmar()}>
            {creating ? "Creando…" : "Crear pedido"}
          </button>
        ) : null}
      </div>
    </ErpModalShell>
  );
}
