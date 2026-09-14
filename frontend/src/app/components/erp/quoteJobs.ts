import { getQuoteJobStatus } from "../../lib/erpApi";

const POLL_MS = 2000;
const POLL_MAX_TRIES = 30;  // ~60 s: el worker es serie, puede haber cola

export type QuoteJobOutcome =
  | { status: "finished"; result: Record<string, unknown> }
  | { status: "failed"; error: string; code?: string }
  | { status: "timeout" };

/** Espera a que termine un job de proformas (crear / editar / duplicar /
 *  convertir): las escrituras van por la cola serializada de FACTUSOL, así que
 *  se hace polling hasta que acaba. Compartido por la pestaña de proformas de
 *  la ficha de empresa y la pantalla Proformas (Fase 4). */
export async function pollQuoteJob(jobId: string): Promise<QuoteJobOutcome> {
  for (let i = 0; i < POLL_MAX_TRIES; i++) {
    const s = await getQuoteJobStatus(jobId);
    if (s.status === "finished") return { status: "finished", result: s.result };
    if (s.status === "failed") {
      return { status: "failed", error: s.error || "La operación falló en FACTUSOL.", code: s.code };
    }
    await new Promise((r) => setTimeout(r, POLL_MS));
  }
  return { status: "timeout" };
}

/** Texto del albarán tal como lo devuelve el job de conversión (Fase 2). */
export function albaranSummary(result: Record<string, unknown>): string {
  const alb = result.albaran as { numero?: string; status?: string } | null | undefined;
  if (alb?.numero) {
    return alb.status === "already" || alb.status === "linked"
      ? `Albarán FACTUSOL ${alb.numero} (ya existía).`
      : `Albarán FACTUSOL ${alb.numero} creado.`;
  }
  if (typeof result.albaran_error === "string" && result.albaran_error) {
    return `El albarán NO se creó: ${result.albaran_error} Reintenta desde la ficha del pedido.`;
  }
  if (typeof result.albaran_skipped === "string" && result.albaran_skipped) {
    return `Sin albarán: ${result.albaran_skipped}`;
  }
  return "";
}

/** Aviso al operador tras convertir: pedido creado, albarán y pago. */
export function conversionNotice(
  codpre: string, result: Record<string, unknown>, paid: boolean,
): string {
  const pago = paid
    ? " Pago apuntado (el cobro se registra a mano con «Registrar cobro» cuando exista la factura)."
    : " Sin pago: pendiente.";
  const existed = result.already_existed === true ? " (ya existía)" : "";
  return `Pedido ${result.order_number}${existed} creado desde la proforma ${codpre}. `
    + albaranSummary(result) + pago;
}
