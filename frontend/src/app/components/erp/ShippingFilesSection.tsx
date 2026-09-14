"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { extractErrorMessage } from "../../lib/errors";
import {
  createOrderAlbaran,
  fetchAlbaranFromWoo,
  getQuoteJobStatus,
  listShippingFiles,
  openShippingFile,
  uploadShippingFile,
  type FactusolPdfLang,
  type ShipmentFile,
  type ShipmentFileKind,
} from "../../lib/erpApi";
import { FactusolAlbaranPdfButton } from "./FactusolAlbaranPdfButton";
import { FileUploadButton } from "./FileUploadButton";

const ALBARAN_POLL_MS = 2000;
const ALBARAN_POLL_MAX_TRIES = 30;  // ~60 s: el worker es serie

/** Job del albarán con el que llega el alta (`?albaran_job=`), sin depender de
 *  `useSearchParams` (la ficha no lo usa). */
function albaranJobFromLocation(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return new URLSearchParams(window.location.search).get("albaran_job");
  } catch {
    return null;
  }
}

/** Panel «Documentos de envío» (Fase D · D-1 + Fase 2 + rediseño de flujo):
 *  el ÚNICO sitio de la ficha donde vive el albarán.
 *
 *  - Albarán FACTUSOL (`factusolAlbaranNumber`): nº + «PDF del albarán
 *    (FACTUSOL)». Si no lo hay y el pedido no es web, «Crear albarán en
 *    FACTUSOL» (F_ALB + líneas, idempotente; el worker es serie → polling del
 *    job). Un pedido web nunca lo crea (lo hace WooCommerce).
 *  - Fichero de albarán subido a mano / descargado de Woo: Ver / Reemplazar,
 *    o Descargar de Woo / Subir según el origen. Conviven con el de FACTUSOL
 *    (decisión de Bart): el subido sirve para albaranes externos / SAT.
 *  - Etiqueta: Ver / Reemplazar o Subir. */
export function ShippingFilesSection({
  orderId,
  isWooOrder,
  orderSource = null,
  factusolAlbaranNumber = null,
  pdfLang = "es",
  canCreateAlbaran = false,
  createSignal = 0,
  onAlbaranCreated,
}: {
  orderId: string;
  isWooOrder: boolean;
  /** Origen del pedido (`manual`, `factusol_proforma`…): decide el tooltip
   *  de «Crear albarán en FACTUSOL». */
  orderSource?: string | null;
  /** Nº del albarán creado por BoHub en FACTUSOL (`5-500008`), si lo hay. */
  factusolAlbaranNumber?: string | null;
  /** Idioma del PDF (el selector de la ficha). */
  pdfLang?: FactusolPdfLang;
  /** El rol puede crear el albarán en FACTUSOL (roles de edición del ERP). */
  canCreateAlbaran?: boolean;
  /** Contador que, al subir, lanza «Crear albarán en FACTUSOL» desde fuera
   *  (lo usa el aviso del modal de envío por email cuando falta el albarán, y
   *  el «Siguiente paso» de la ficha). */
  createSignal?: number;
  /** Tras crearse el albarán (o fallar): la ficha recarga el pedido y, si
   *  hay error, lo enseña. */
  onAlbaranCreated?: (result: { numero: string | null; error: string | null }) => void;
}) {
  const [files, setFiles] = useState<ShipmentFile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [fetchingAlbaran, setFetchingAlbaran] = useState(false);
  // Albarán FACTUSOL: el alta redirige aquí con el job recién encolado (solo
  // importa si el pedido aún no tiene su nº). Estado inicial perezoso: el
  // panel se monta tras cargar el pedido, ya en el cliente.
  const [jobId, setJobId] = useState<string | null>(
    () => (factusolAlbaranNumber ? null : albaranJobFromLocation()),
  );
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  // Siempre el último `onAlbaranCreated` (la ficha pasa una arrow nueva en
  // cada render): el polling dura hasta ~60 s y no debe quedarse con el viejo.
  const onCreated = useRef(onAlbaranCreated);
  useEffect(() => { onCreated.current = onAlbaranCreated; }, [onAlbaranCreated]);

  /** Al terminar el job (bien o mal) el `?albaran_job=` del alta ya no sirve:
   *  se quita de la URL para que una recarga no vuelva a sondearlo. */
  function dropJobParam() {
    if (typeof window === "undefined") return;
    try {
      const url = new URL(window.location.href);
      if (!url.searchParams.has("albaran_job")) return;
      url.searchParams.delete("albaran_job");
      window.history.replaceState({}, "", url.toString());
    } catch {
      /* sin URL que limpiar */
    }
  }

  const load = useCallback(() => {
    listShippingFiles(orderId).then(setFiles).catch(() => undefined);
  }, [orderId]);
  useEffect(() => load(), [load]);

  // Polling del job del albarán hasta que termine; al terminar avisa a la
  // ficha (que recarga el pedido con el nº).
  useEffect(() => {
    if (!jobId) return;
    let alive = true;
    let tries = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = () => {
      getQuoteJobStatus(jobId)
        .then((st) => {
          if (!alive) return;
          if (st.status === "finished") {
            const numero = (st.result as { numero?: string } | undefined)?.numero ?? null;
            setNotice(numero ? `Albarán FACTUSOL ${numero} creado.` : "Albarán creado.");
            setJobId(null);
            dropJobParam();
            onCreated.current?.({ numero, error: null });
          } else if (st.status === "failed") {
            setJobId(null);
            setNotice(null);
            dropJobParam();
            onCreated.current?.({
              numero: null,
              error: `El albarán no se creó en FACTUSOL: ${st.error ?? "error"}`,
            });
          } else if (++tries >= ALBARAN_POLL_MAX_TRIES) {
            setJobId(null);
            setNotice("El albarán sigue en cola; recarga la ficha en unos segundos.");
          } else {
            timer = setTimeout(tick, ALBARAN_POLL_MS);
          }
        })
        .catch(() => {
          if (!alive) return;
          if (++tries >= ALBARAN_POLL_MAX_TRIES) {
            setJobId(null);
            setNotice("No se pudo consultar el estado del albarán; recarga la ficha en unos segundos.");
          } else {
            timer = setTimeout(tick, ALBARAN_POLL_MS);
          }
        });
    };
    tick();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [jobId]);

  async function crearAlbaran() {
    if (creating || jobId) return;   // ya hay uno en vuelo: no encolar dos
    setCreating(true);
    setError(null);
    setNotice(null);
    try {
      const r = await createOrderAlbaran(orderId);
      setNotice("Albarán encolado en FACTUSOL: el worker es serie, puede tardar unos segundos.");
      setJobId(r.job_id);
    } catch (e) {
      // Si la ficha escucha, el error va a su banner (la petición pudo venir
      // de arriba: email al SAT / «Siguiente paso»); si no, aquí mismo.
      const msg = extractErrorMessage(e, "No se pudo encolar el albarán en FACTUSOL.");
      if (onCreated.current) onCreated.current({ numero: null, error: msg });
      else setError(msg);
    } finally {
      setCreating(false);
    }
  }

  // Petición externa de crear el albarán: se ignora el montaje inicial, los
  // pedidos web y cuando el pedido ya tiene albarán (o un job en vuelo).
  useEffect(() => {
    if (createSignal > 0 && !factusolAlbaranNumber && !isWooOrder) void crearAlbaran();
    // `crearAlbaran` solo usa setters y la API; disparar por el contador.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [createSignal]);

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
    <section className="erp-flow-panel" aria-label="Documentos de envío">
      <h3>Documentos de envío</h3>
      {error ? <p className="form-error">{error}</p> : null}
      {notice ? <p className="form-info" role="status">{notice}</p> : null}
      <div className="erp-flow-docrow" role="group" aria-label="Albarán">
        <span className="k">Albarán</span>
        <span className="v">
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
          ) : jobId ? (
            <span className="badge warn">Creando el albarán en FACTUSOL…</span>
          ) : isWooOrder ? (
            <span className="muted small">Albarán FACTUSOL: lo crea WooCommerce.</span>
          ) : (
            <>
              <span className="badge muted">Sin albarán en FACTUSOL</span>
              {canCreateAlbaran ? (
                <button
                  type="button" className="button small" disabled={creating}
                  title={orderSource === "manual"
                    ? "Crea el albarán en FACTUSOL (F_ALB + líneas) desde las líneas de este pedido manual (empresa vinculada a F_CLI); idempotente"
                    : "Crea el albarán (F_ALB + líneas) a partir del documento de origen; idempotente"}
                  onClick={() => void crearAlbaran()}
                >
                  {creating ? "Encolando…" : "Crear albarán en FACTUSOL"}
                </button>
              ) : null}
            </>
          )}
          {albaran ? (
            <>
              <button type="button" className="button small secondary"
                      onClick={() => openShippingFile(albaran)}>
                Ver albarán
              </button>
              <FileUploadButton label="Reemplazar albarán"
                                onFile={(f) => upload("albaran", f)} />
            </>
          ) : isWooOrder ? (
            <>
              <button type="button" className="button small secondary"
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
        </span>
      </div>

      <div className="erp-flow-docrow" role="group" aria-label="Etiqueta">
        <span className="k">Etiqueta</span>
        <span className="v">
          {etiqueta ? (
            <>
              <button type="button" className="button small secondary"
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
        </span>
      </div>
    </section>
  );
}
