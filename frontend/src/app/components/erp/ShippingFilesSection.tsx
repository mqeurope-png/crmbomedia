"use client";

import { useCallback, useEffect, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  fetchAlbaranFromWoo,
  listShippingFiles,
  openShippingFile,
  uploadShippingFile,
  type ShipmentFile,
  type ShipmentFileKind,
} from "../../lib/erpApi";
import { FileUploadButton } from "./FileUploadButton";

/** Sección «Documentos de envío» (Fase D · D-1): albarán + etiqueta con su
 *  render condicional (presente → Ver/Reemplazar; ausente → Descargar de Woo /
 *  Subir según el origen del pedido). */
export function ShippingFilesSection({
  orderId,
  isWooOrder,
}: {
  orderId: string;
  isWooOrder: boolean;
}) {
  const [files, setFiles] = useState<ShipmentFile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [fetchingAlbaran, setFetchingAlbaran] = useState(false);

  const load = useCallback(() => {
    listShippingFiles(orderId).then(setFiles).catch(() => undefined);
  }, [orderId]);
  useEffect(() => load(), [load]);

  const albaran = files.find((f) => f.kind === "albaran") ?? null;
  const etiqueta = files.find((f) => f.kind === "etiqueta") ?? null;

  async function upload(kind: ShipmentFileKind, file: File) {
    await uploadShippingFile(orderId, kind, file);
    load();
  }

  async function descargarWoo() {
    setFetchingAlbaran(true);
    setError(null);
    try {
      await fetchAlbaranFromWoo(orderId);
      load();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo descargar el albarán de Woo."));
    } finally {
      setFetchingAlbaran(false);
    }
  }

  return (
    <section className="erp-card">
      <h3>Documentos de envío</h3>
      {error ? <p className="form-error">{error}</p> : null}
      <div className="erp-shipping-docs">
        <div className="erp-shipping-doc" aria-label="Albarán">
          <h4>Albarán</h4>
          {albaran ? (
            <>
              <button type="button" className="button small"
                      onClick={() => openShippingFile(albaran)}>
                Ver albarán
              </button>
              <FileUploadButton label="Reemplazar albarán"
                                onFile={(f) => upload("albaran", f)} />
            </>
          ) : isWooOrder ? (
            <>
              <button type="button" className="button small"
                      disabled={fetchingAlbaran} onClick={descargarWoo}>
                {fetchingAlbaran ? "Descargando…" : "Descargar albarán de Woo"}
              </button>
              <FileUploadButton label="Subir albarán"
                                onFile={(f) => upload("albaran", f)} />
            </>
          ) : (
            <FileUploadButton label="Subir albarán"
                              onFile={(f) => upload("albaran", f)} />
          )}
        </div>

        <div className="erp-shipping-doc" aria-label="Etiqueta">
          <h4>Etiqueta</h4>
          {etiqueta ? (
            <>
              <button type="button" className="button small"
                      onClick={() => openShippingFile(etiqueta)}>
                Ver etiqueta
              </button>
              <FileUploadButton label="Reemplazar etiqueta"
                                onFile={(f) => upload("etiqueta", f)} />
            </>
          ) : (
            <FileUploadButton label="Subir etiqueta"
                              onFile={(f) => upload("etiqueta", f)} />
          )}
        </div>
      </div>
    </section>
  );
}
