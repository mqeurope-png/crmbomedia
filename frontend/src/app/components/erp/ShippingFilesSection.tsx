"use client";

import { useCallback, useEffect, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  fetchAlbaranFromWoo,
  listShippingFiles,
  openShippingFile,
  uploadShippingFile,
  type FactusolPdfLang,
  type ShipmentFile,
  type ShipmentFileKind,
} from "../../lib/erpApi";
import { FactusolAlbaranPdfButton } from "./FactusolAlbaranPdfButton";
import { FileUploadButton } from "./FileUploadButton";

/** Sección «Documentos de envío» (Fase D · D-1): albarán + etiqueta con su
 *  render condicional (presente → Ver/Reemplazar; ausente → Descargar de Woo /
 *  Subir según el origen del pedido).
 *
 *  Fase 2: si el pedido tiene albarán en FACTUSOL (`factusolAlbaranNumber`),
 *  se ofrece además «PDF del albarán (FACTUSOL)». Conviven (decisión de
 *  Bart): «Subir albarán» sigue disponible para los albaranes externos / SAT
 *  y como alternativa. */
export function ShippingFilesSection({
  orderId,
  isWooOrder,
  factusolAlbaranNumber = null,
  pdfLang = "es",
}: {
  orderId: string;
  isWooOrder: boolean;
  /** Nº del albarán creado por BoHub en FACTUSOL (`5-500008`), si lo hay. */
  factusolAlbaranNumber?: string | null;
  /** Idioma del PDF (el selector de la ficha). */
  pdfLang?: FactusolPdfLang;
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
          {factusolAlbaranNumber ? (
            /* Fase 2: el albarán vive en FACTUSOL → PDF desde allí. */
            <>
              <span className="badge ok">Albarán FACTUSOL {factusolAlbaranNumber}</span>
              <FactusolAlbaranPdfButton
                orderId={orderId}
                numero={factusolAlbaranNumber}
                lang={pdfLang}
                className="button small"
                onError={setError}
              />
            </>
          ) : null}
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
