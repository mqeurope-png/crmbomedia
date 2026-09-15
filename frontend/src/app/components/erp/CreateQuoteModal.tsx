"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { listCompanies, type Company } from "../../lib/companiesApi";
import { extractErrorMessage } from "../../lib/errors";
import {
  createFactusolQuote,
  downloadFactusolDocumentPdf,
  getFactusolCustomerAddresses,
  getFactusolQuote,
  saveBlob,
  searchFactusolQuotes,
  updateFactusolQuote,
  waitForQuoteJob,
  type FactusolAddress,
  type FactusolArticle,
  type FactusolQuote,
  type QuoteShippingInput,
} from "../../lib/erpApi";
import { ArticleAutocompleteInput } from "./ArticleAutocompleteInput";

type Mode = "articles" | "duplicate";

type LineRow = {
  codart: string;
  description: string;
  quantity: string;
  unit_price: string;
  /** DTO % → `DT1LPS`, el primero de los 3 niveles de descuento de F_LPS. */
  discount_pct: string;
  iva_pct: string;
};

const EMPTY_LINE: LineRow = {
  codart: "", description: "", quantity: "1", unit_price: "",
  discount_pct: "0", iva_pct: "21",
};

/** Destinatario libre (dropshipping, Lote B3b). Todo opcional. */
type ShippingForm = Required<QuoteShippingInput>;

const EMPTY_SHIPPING: ShippingForm = {
  name: "", address_line: "", city: "", postal_code: "", state: "", country: "",
};

/** Campo de línea con autocomplete que tiene el foco. Solo ESE campo busca en
 *  el catálogo (ver `ArticleAutocompleteInput enabled`). */
type AcField = { line: number; field: "codart" | "description" };

const DEBOUNCE_MS = 300;
const TEMPLATE_DAYS_BACK = 365;
const REF_PREVIEW_CHARS = 60;
/** Serie por defecto del presupuesto si la cabecera no trae TIPPRE. */
const PRESUPUESTO_SERIE = 1;
/** El PDF de la plantilla es para el operador, no para el cliente. */
const TEMPLATE_PDF_LANG = "es";

function num(v: string): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Total de la línea con el descuento aplicado — el mismo cálculo que hace el
 *  backend para `TOTLPS`, para que lo que se ve cuadre con lo que se escribe. */
function lineTotal(l: LineRow): number {
  return num(l.quantity) * num(l.unit_price) * (1 - num(l.discount_pct) / 100);
}

function serieOf(q: FactusolQuote): number {
  return q.serie ?? (Number(q.tippre) || PRESUPUESTO_SERIE);
}

/** Líneas de una proforma → filas editables. El SKU se precarga con el
 *  comercial (`sku`, EQUART) que es lo que enseña el autocomplete: así el
 *  artículo queda mapeado sin volver a elegirlo (Lote B4). Las de texto libre
 *  quedan con el SKU vacío y su descripción. */
function rowsFromQuote(quote: FactusolQuote): LineRow[] {
  return (quote.lines ?? []).map((l) => ({
    codart: l.sku ?? l.codart ?? "",
    description: l.description,
    quantity: String(l.quantity),
    unit_price: String(l.unit_price),
    discount_pct: String(l.discount_pct ?? 0),
    iva_pct: String(l.iva_pct),
  }));
}

function shippingFilled(s: ShippingForm): boolean {
  return Object.values(s).some((v) => v.trim());
}

/** Abre el PDF en una pestaña nueva (como `openShippingFile`). Si el navegador
 *  bloquea la ventana, cae a la descarga normal. */
function openPdfBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const win = window.open(url, "_blank");
  if (!win) saveBlob(blob, filename);
  // El navegador retiene el blob mientras la pestaña lo usa; se libera tras
  // un margen para no cortar la apertura.
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** Alta de proforma FACTUSOL. Dos modos:
 *
 *  - **Con artículos**: buscador F_ART + tabla de líneas editable. Una proforma
 *    «simple» se hace aquí con una sola línea escrita a mano, sin tocar el
 *    catálogo — por eso C-4-fix2 retiró la pestaña «Rápida», que duplicaba esto.
 *  - **Duplicar**: parte de una proforma anterior **de cualquier cliente**.
 *    Lote B4: al elegirla se enseña una vista previa de sus líneas (con el
 *    SKU comercial) y un «Ver PDF»; «Usar como plantilla» las vuelca a la
 *    tabla editable ya mapeadas.
 *
 *  Sobre duplicar: se carga la plantilla en la tabla y se crea una proforma
 *  NUEVA con el cliente destino. No se usa `POST /quotes/{codpre}/duplicate`
 *  porque ese endpoint copia la fila F_PRE entera, incluido `CLIPRE`: duplicar
 *  la de otro cliente dejaría la nueva a nombre del cliente equivocado.
 *
 *  Lote B3b: «Portes (€)» va a la banda de portes de la cabecera (IPOR1PRE),
 *  como en el pedido manual — no como línea. Y «Enviar a otro nombre /
 *  dirección» permite un destinatario libre (dropshipping) sin tocar el
 *  cliente fiscal.
 */
export function CreateQuoteModal({
  companyId,
  companyName,
  factusolCodcli,
  editCodpre,
  onCreated,
  onCancel,
}: {
  companyId: string;
  companyName: string;
  /** CODCLI del cliente, para cargar sus direcciones. */
  factusolCodcli?: string | null;
  /** Si viene, el modal edita esa proforma en vez de crear una nueva. */
  editCodpre?: string | null;
  onCreated: (jobId: string) => void;
  onCancel: () => void;
}) {
  const editing = Boolean(editCodpre);
  const [mode, setMode] = useState<Mode>("articles");
  const [lines, setLines] = useState<LineRow[]>([{ ...EMPTY_LINE }]);
  const [portes, setPortes] = useState("");
  const [fecha, setFecha] = useState(today());
  const [referencia, setReferencia] = useState("");
  // Direcciones del cliente: la sede + las adicionales de FACTUSOL.
  const [addresses, setAddresses] = useState<FactusolAddress[]>([]);
  const [addressCode, setAddressCode] = useState(0);
  // Destinatario libre (dropshipping). Solo se envía con el bloque abierto.
  const [shippingOpen, setShippingOpen] = useState(false);
  const [shipping, setShipping] = useState<ShippingForm>({ ...EMPTY_SHIPPING });
  // Cliente DESTINO: arranca en la empresa desde la que se abrió el modal.
  const [targetId, setTargetId] = useState(companyId);
  const [targetName, setTargetName] = useState(companyName);
  const [targetCodcli, setTargetCodcli] = useState(factusolCodcli ?? null);
  const [changingTarget, setChangingTarget] = useState(false);
  const [targetQuery, setTargetQuery] = useState("");
  const [targetOptions, setTargetOptions] = useState<Company[]>([]);
  // Reintento tras «esta proforma está aceptada».
  const [needsForce, setNeedsForce] = useState(false);
  // Modo duplicar: búsqueda libre entre TODAS las proformas.
  const [templateQuery, setTemplateQuery] = useState("");
  const [templates, setTemplates] = useState<FactusolQuote[]>([]);
  const [searchingTemplates, setSearchingTemplates] = useState(false);
  // Plantilla elegida (con sus líneas), a la espera de «Usar como plantilla».
  const [template, setTemplate] = useState<FactusolQuote | null>(null);
  const [loadingTemplate, setLoadingTemplate] = useState<string | null>(null);
  const [openingPdf, setOpeningPdf] = useState(false);
  const [loadedFrom, setLoadedFrom] = useState<string | null>(null);
  // Campo con autocomplete activo: solo el que tiene el foco busca.
  const [acField, setAcField] = useState<AcField | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Buscador de plantillas: NO filtra por cliente (C-4-fix1).
  useEffect(() => {
    if (mode !== "duplicate") return;
    let alive = true;
    setSearchingTemplates(true);
    const handle = window.setTimeout(() => {
      searchFactusolQuotes(templateQuery.trim(), { days_back: TEMPLATE_DAYS_BACK })
        .then((items) => { if (alive) setTemplates(items); })
        .catch(() => { if (alive) setTemplates([]); })
        .finally(() => { if (alive) setSearchingTemplates(false); });
    }, DEBOUNCE_MS);
    return () => { alive = false; window.clearTimeout(handle); };
  }, [mode, templateQuery]);

  // Empresas candidatas a cliente destino: solo las YA vinculadas a FACTUSOL,
  // que son las únicas que el backend acepta (si no, 409 company_not_linked).
  useEffect(() => {
    if (!changingTarget) return;
    const handle = window.setTimeout(() => {
      listCompanies({ q: targetQuery || undefined, limit: 20 })
        .then((page) =>
          setTargetOptions(page.items.filter((c) => c.factusol_company_id)))
        .catch(() => setTargetOptions([]));
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [changingTarget, targetQuery]);

  // Direcciones del cliente destino. Si solo hay la principal no se enseña
  // selector: sería una lista de un elemento.
  useEffect(() => {
    if (!targetCodcli) {
      setAddresses([]);
      return;
    }
    let alive = true;
    getFactusolCustomerAddresses(targetCodcli)
      .then((items) => { if (alive) setAddresses(items); })
      .catch(() => { if (alive) setAddresses([]); });
    return () => { alive = false; };
  }, [targetCodcli]);

  // Modo edición: precarga la proforma que se va a modificar (líneas con el
  // SKU comercial y portes de la cabecera, para no perderlos al reescribir).
  useEffect(() => {
    if (!editCodpre) return;
    let alive = true;
    getFactusolQuote(editCodpre)
      .then((quote) => {
        if (!alive) return;
        setReferencia(quote.referencia ?? "");
        const rows = rowsFromQuote(quote);
        setLines(rows.length > 0 ? rows : [{ ...EMPTY_LINE }]);
        setPortes(quote.portes ? String(quote.portes) : "");
      })
      .catch((e) => {
        if (alive) setError(extractErrorMessage(e, "No se pudo cargar la proforma."));
      });
    return () => { alive = false; };
  }, [editCodpre]);

  const linesTotal = useMemo(
    () => lines.reduce((sum, l) => sum + lineTotal(l), 0),
    [lines],
  );
  // Base imponible = líneas + portes, como `BAS1PRE` en FACTUSOL.
  const total = linesTotal + num(portes);

  function updateLine(i: number, key: keyof LineRow, value: string) {
    setLines((rs) => rs.map((r, j) => (j === i ? { ...r, [key]: value } : r)));
  }

  function updateShipping(key: keyof ShippingForm, value: string) {
    setShipping((s) => ({ ...s, [key]: value }));
  }

  /** ¿Este campo es el que tiene el foco? Solo él busca en el catálogo: las
   *  precargas (plantilla, edición, elegir un artículo) cambian el valor de
   *  TODAS las líneas a la vez y, si cada input buscase por su cuenta, se
   *  lanzarían 2N consultas y se abrirían 2N desplegables sin que nadie
   *  hubiera tecleado — era el «se buguea» del modo Duplicar. */
  function isAc(i: number, field: AcField["field"]): boolean {
    return acField?.line === i && acField.field === field;
  }

  function acCellProps(i: number, field: AcField["field"]) {
    return {
      onFocusCapture: () => setAcField({ line: i, field }),
      onBlurCapture: () => setAcField((cur) =>
        (cur?.line === i && cur.field === field ? null : cur)),
    };
  }

  /** Rellena la línea con el artículo elegido del catálogo. El precio de venta
   *  se deja EN BLANCO si FACTUSOL no lo tiene: forzar «0.00» invita a emitir
   *  una proforma a cero sin que nadie lo note. */
  function applyArticle(i: number, a: FactusolArticle) {
    setLines((rs) => rs.map((r, j) => (j === i ? {
      ...r,
      codart: a.sku ?? a.codart ?? "",
      description: a.descripcion ?? a.sku ?? "",
      unit_price: a.precio_venta ? String(a.precio_venta) : "",
      iva_pct: String(a.iva_pct || 21),
    } : r)));
  }

  /** Paso 1 de duplicar: carga la proforma elegida (líneas reales de F_LPS
   *  con su SKU comercial) y la enseña en la vista previa. No toca la tabla
   *  editable hasta «Usar como plantilla». */
  const loadTemplate = useCallback(async (quote: FactusolQuote) => {
    if (!quote.codpre) return;
    setError(null);
    setNotice(null);
    setLoadingTemplate(quote.codpre);
    try {
      setTemplate(await getFactusolQuote(quote.codpre));
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo cargar la plantilla."));
    } finally {
      setLoadingTemplate(null);
    }
  }, []);

  /** Paso 2 de duplicar: vuelca la plantilla a la tabla y pasa a «Con
   *  artículos». Las líneas llegan ya mapeadas (SKU comercial), así que no
   *  hay que volver a elegir cada artículo.
   *
   *  C-4-fix3: las líneas son las **reales** de F_LPS, así que funciona igual
   *  con proformas creadas en el FACTUSOL de escritorio. Si F_LPS no devuelve
   *  nada es que la proforma está vacía, y se avisa dejando la tabla editable. */
  function applyTemplate() {
    if (!template?.codpre) return;
    const rows = rowsFromQuote(template);
    if (rows.length > 0) {
      setLines(rows);
    } else {
      setLines([{ ...EMPTY_LINE }]);
      setNotice(
        `La proforma ${template.codpre} no tiene líneas en FACTUSOL. `
        + "Añádelas aquí.",
      );
    }
    setPortes(template.portes ? String(template.portes) : "");
    setLoadedFrom(template.codpre);
    setAcField(null);
    setMode("articles");
  }

  /** PDF de la plantilla (variante proforma) en una pestaña nueva. */
  async function viewTemplatePdf() {
    if (!template?.codpre) return;
    setOpeningPdf(true);
    setError(null);
    try {
      const blob = await downloadFactusolDocumentPdf(
        "presupuestos", serieOf(template), template.codpre, TEMPLATE_PDF_LANG,
        { variant: "proforma" },
      );
      openPdfBlob(blob, `Proforma_${template.codpre}.pdf`);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo generar el PDF de la proforma."));
    } finally {
      setOpeningPdf(false);
    }
  }

  const valid = lines.some((l) => l.description.trim() && num(l.quantity) > 0);

  async function submit(force = false) {
    if (!valid) return;
    setSubmitting(true);
    setError(null);
    const chosen = addresses.find((a) => a.codigo === addressCode);
    const payload = {
      company_id: targetId,
      referencia: referencia.trim(),
      lines: lines
        .filter((l) => l.description.trim() && num(l.quantity) > 0)
        .map((l) => ({
          codart: l.codart.trim() || undefined,
          description: l.description.trim(),
          quantity: num(l.quantity),
          unit_price: num(l.unit_price),
          discount_pct: num(l.discount_pct),
          iva_pct: num(l.iva_pct),
        })),
      fecha: fecha || null,
      // Solo se manda si el operador eligió una alternativa: la principal ya
      // es lo que el backend toma de la empresa CRM.
      address: chosen && chosen.codigo !== 0 ? {
        direccion: chosen.direccion, ciudad: chosen.ciudad, cp: chosen.cp,
        provincia: chosen.provincia, pais: chosen.pais,
      } : null,
      // Siempre un número: al editar, un 0 le dice al backend que quite los
      // portes que la proforma tuviera.
      portes: Math.max(0, num(portes)),
      // Destinatario libre: solo con el bloque abierto y algo escrito.
      shipping: shippingOpen && shippingFilled(shipping) ? {
        name: shipping.name.trim(),
        address_line: shipping.address_line.trim(),
        city: shipping.city.trim(),
        postal_code: shipping.postal_code.trim(),
        state: shipping.state.trim(),
        country: shipping.country.trim(),
      } : null,
    };
    try {
      const r = editCodpre
        ? await updateFactusolQuote(editCodpre, { ...payload, force })
        : await createFactusolQuote(payload);
      // Se espera al job aquí para poder ofrecer «Guardar de todos modos» sin
      // que el operador pierda lo que acaba de escribir: si el modal se
      // cerrase al encolar, el rechazo por estado llegaría con el formulario
      // ya desmontado.
      const outcome = await waitForQuoteJob(r.job_id);
      if (outcome.status === "failed") {
        if (outcome.code === "quote_not_editable") {
          setNeedsForce(true);
          setError(outcome.error ?? "La proforma no está en estado editable.");
        } else {
          setError(outcome.error ?? "La operación falló en FACTUSOL.");
        }
        setSubmitting(false);
        return;
      }
      onCreated(r.job_id);
    } catch (e) {
      setError(extractErrorMessage(
        e, editing ? "No se pudo guardar la proforma."
                   : "No se pudo crear la proforma."));
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label={editing ? "Editar proforma FACTUSOL" : "Nueva proforma FACTUSOL"}>
      {/* Lote 2 · E6: molde plano del ERP; `modal-wide` es la única excepción
          de ancho (editor de líneas de 8 columnas), ver styles.css. */}
      <div className="modal-dialog erp-modal modal-wide">
        <h2>{editing ? `Editar proforma nº ${editCodpre}` : "Nueva proforma"}</h2>

        <div className="erp-quote-target">
          <span>
            Cliente destino: <strong>{targetName}</strong>
            {loadedFrom ? (
              <span className="muted small"> · plantilla nº {loadedFrom}</span>
            ) : null}
          </span>
          <button type="button" className="button small secondary"
                  onClick={() => setChangingTarget((v) => !v)}>
            Cambiar
          </button>
        </div>
        {changingTarget ? (
          <label className="field">
            <span>Empresa destino (vinculada a FACTUSOL)</span>
            <input type="text" list="erp-quote-target-companies"
                   value={targetQuery} placeholder="Buscar empresa…"
                   aria-label="Empresa destino"
                   onChange={(e) => {
                     setTargetQuery(e.target.value);
                     const hit = targetOptions.find((c) => c.name === e.target.value);
                     if (hit) {
                       setTargetId(hit.id);
                       setTargetName(hit.name);
                       setTargetCodcli(hit.factusol_company_id ?? null);
                       setAddressCode(0);
                       setChangingTarget(false);
                     }
                   }} />
            <datalist id="erp-quote-target-companies">
              {targetOptions.map((c) => <option key={c.id} value={c.name} />)}
            </datalist>
          </label>
        ) : null}

        {/* Solo se ofrece si el cliente tiene alguna dirección adicional. */}
        {addresses.length > 1 ? (
          <label className="field">
            <span>Dirección de envío</span>
            <select value={addressCode} aria-label="Dirección de envío"
                    onChange={(e) => setAddressCode(Number(e.target.value))}>
              {addresses.map((a) => (
                <option key={a.codigo} value={a.codigo}>
                  {a.nombre}
                  {a.direccion ? ` — ${a.direccion}` : ""}
                  {a.ciudad ? `, ${a.ciudad}` : ""}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        {/* Lote B3b: destinatario libre (dropshipping). La proforma sigue a
            nombre fiscal del cliente; cambia solo a quién y dónde se entrega.
            Si también se eligió una dirección de F_CLI, manda este bloque. */}
        <label className="field-toggle">
          <input type="checkbox" checked={shippingOpen}
                 aria-label="Enviar a otro nombre / dirección (dropshipping)"
                 onChange={(e) => setShippingOpen(e.target.checked)} />
          <span>Enviar a otro nombre / dirección (dropshipping)</span>
        </label>
        {shippingOpen ? (
          <div className="erp-quote-shipping" role="group"
               aria-label="Destinatario de envío">
            <label className="field">
              <span>Nombre de envío</span>
              <input type="text" value={shipping.name} maxLength={255}
                     aria-label="Nombre de envío"
                     placeholder="A quién se entrega (vacío = el cliente)"
                     onChange={(e) => updateShipping("name", e.target.value)} />
            </label>
            <label className="field">
              <span>Dirección</span>
              <input type="text" value={shipping.address_line} maxLength={255}
                     aria-label="Dirección de entrega"
                     onChange={(e) => updateShipping("address_line", e.target.value)} />
            </label>
            <div className="form-row">
              <label className="field">
                <span>Ciudad</span>
                <input type="text" value={shipping.city} maxLength={255}
                       aria-label="Ciudad de entrega"
                       onChange={(e) => updateShipping("city", e.target.value)} />
              </label>
              <label className="field">
                <span>Código postal</span>
                <input type="text" value={shipping.postal_code} maxLength={20}
                       aria-label="Código postal de entrega"
                       onChange={(e) => updateShipping("postal_code", e.target.value)} />
              </label>
            </div>
            <div className="form-row">
              <label className="field">
                <span>Provincia</span>
                <input type="text" value={shipping.state} maxLength={255}
                       aria-label="Provincia de entrega"
                       onChange={(e) => updateShipping("state", e.target.value)} />
              </label>
              <label className="field">
                <span>País</span>
                <input type="text" value={shipping.country} maxLength={64}
                       aria-label="País de entrega"
                       placeholder="ES, BE… o el nombre (vacío = el del cliente)"
                       onChange={(e) => updateShipping("country", e.target.value)} />
              </label>
            </div>
            <span className="muted small">
              La proforma sigue a nombre fiscal de <strong>{targetName}</strong>;
              solo cambia el destinatario del documento. Si se deja la dirección
              en blanco, se usa la del cliente.
            </span>
          </div>
        ) : null}

        <label className="field">
          <span>Referencia (opcional)</span>
          <input type="text" value={referencia} maxLength={250}
                 aria-label="Referencia (opcional)"
                 placeholder="Ej: nº pedido cliente, código proyecto, obra…"
                 onChange={(e) => setReferencia(e.target.value)} />
        </label>
        <span className="muted small">
          Va al campo «Su ref.» del documento. Si lo dejas vacío, queda vacío.
        </span>

        <p className="form-error">
          {editing
            ? <>Se modificará un presupuesto <strong>real</strong> de FACTUSOL.</>
            : <>Se creará un presupuesto <strong>real</strong> en FACTUSOL.</>}
        </p>
        {error ? <p className="form-error">{error}</p> : null}
        {notice ? <p className="form-info" role="status">{notice}</p> : null}

        <div className="tab-bar">
          <button type="button" className={`tab${mode === "articles" ? " is-active" : ""}`}
                  onClick={() => setMode("articles")}>
            Con artículos
          </button>
          <button type="button" className={`tab${mode === "duplicate" ? " is-active" : ""}`}
                  onClick={() => setMode("duplicate")}>
            Duplicar
          </button>
        </div>

        {mode === "duplicate" ? (
          <>
            <p className="form-info">
              Puedes duplicar <strong>cualquier</strong> proforma — se creará
              como nueva con el cliente que elijas arriba.
            </p>
            <label className="field">
              <span>Buscar plantilla</span>
              <input type="text" value={templateQuery}
                     placeholder="Buscar por nº, cliente o descripción…"
                     onChange={(e) => setTemplateQuery(e.target.value)} />
            </label>
            {searchingTemplates ? (
              <p className="muted small">Buscando…</p>
            ) : templates.length === 0 ? (
              <p className="muted small">Sin proformas que coincidan.</p>
            ) : (
              <ul className="erp-quote-list">
                {templates.slice(0, 20).map((q) => (
                  <li key={q.codpre ?? ""}>
                    <span>
                      <strong>nº {q.codpre}</strong> · {q.fecha ?? "—"} ·{" "}
                      {q.cliente_nombre ?? "—"} · {q.total.toFixed(2)} € ·{" "}
                      {q.referencia.slice(0, REF_PREVIEW_CHARS)}
                    </span>
                    <button type="button" className="button small"
                            disabled={loadingTemplate !== null}
                            onClick={() => loadTemplate(q)}>
                      {loadingTemplate === q.codpre ? "Cargando…" : "Cargar esta plantilla"}
                    </button>
                  </li>
                ))}
              </ul>
            )}

            {/* Vista previa de la plantilla elegida (Lote B4): líneas reales
                con su SKU comercial, PDF y el paso a la tabla editable. */}
            {template ? (
              <div className="erp-quote-preview" role="region"
                   aria-label={`Plantilla nº ${template.codpre}`}>
                <div className="erp-quote-preview-head">
                  <span>
                    <strong>Proforma nº {template.codpre}</strong>
                    {" · "}{template.fecha ?? "—"}
                    {" · "}{template.cliente_nombre ?? "—"}
                    {" · "}{template.total.toFixed(2)} €
                    {template.referencia ? ` · ${template.referencia}` : ""}
                  </span>
                  <div className="erp-quote-preview-actions">
                    <button type="button" className="button small secondary"
                            onClick={() => void viewTemplatePdf()}
                            disabled={openingPdf}>
                      {openingPdf ? "Generando PDF…" : "Ver PDF"}
                    </button>
                    <button type="button" className="button small"
                            onClick={applyTemplate}>
                      Usar como plantilla
                    </button>
                  </div>
                </div>
                {(template.lines ?? []).length === 0 ? (
                  <p className="form-info" role="status">
                    La proforma {template.codpre} no tiene líneas en FACTUSOL.
                  </p>
                ) : (
                  <table className="data-table" aria-label="Líneas de la plantilla">
                    <thead>
                      <tr>
                        <th>SKU</th><th>Descripción</th>
                        <th className="num">Cant.</th>
                        <th className="num">Precio ud.</th>
                        <th className="num">Total</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(template.lines ?? []).map((l) => (
                        <tr key={l.position}>
                          <td>
                            <span className="erp-article-sku">
                              {l.sku ?? l.codart ?? "—"}
                            </span>
                          </td>
                          <td>{l.description}</td>
                          <td className="num">{l.quantity}</td>
                          <td className="num">{l.unit_price.toFixed(2)}</td>
                          <td className="num">{l.line_total.toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {template.portes ? (
                  <p className="muted small">
                    Portes: <strong>{template.portes.toFixed(2)} €</strong> — se
                    cargan en el campo «Portes» al usarla como plantilla.
                  </p>
                ) : null}
              </div>
            ) : null}
          </>
        ) : (
          <>
            <p className="muted small">
              Busca en el catálogo escribiendo en <strong>SKU</strong> o{" "}
              <strong>Descripción</strong>. Para conceptos que no son artículos
              (mano de obra, reparaciones) escribe la línea a mano y deja el SKU
              vacío. Los portes van en su propio campo, debajo de la tabla.
            </p>
            <table className="data-table erp-quote-lines">
              <thead>
                <tr>
                  <th>SKU (opcional)</th><th>Descripción</th><th>Cant.</th>
                  <th>Precio ud.</th><th>DTO %</th><th>IVA %</th>
                  <th>Total</th><th />
                </tr>
              </thead>
              <tbody>
                {lines.map((l, i) => (
                  <tr key={i}>
                    <td {...acCellProps(i, "codart")}>
                      <ArticleAutocompleteInput
                        value={l.codart}
                        enabled={isAc(i, "codart")}
                        ariaLabel={`SKU línea ${i + 1}`}
                        placeholder="CDR80WPT"
                        onChange={(v) => updateLine(i, "codart", v)}
                        onPick={(a) => applyArticle(i, a)}
                      />
                    </td>
                    <td {...acCellProps(i, "description")}>
                      <ArticleAutocompleteInput
                        value={l.description}
                        enabled={isAc(i, "description")}
                        ariaLabel={`Descripción línea ${i + 1}`}
                        placeholder="Descripción del artículo o concepto"
                        onChange={(v) => updateLine(i, "description", v)}
                        onPick={(a) => applyArticle(i, a)}
                      />
                    </td>
                    <td>
                      <input type="number" min="0" step="1" value={l.quantity}
                             aria-label={`Cantidad línea ${i + 1}`}
                             onChange={(e) => updateLine(i, "quantity", e.target.value)} />
                    </td>
                    <td>
                      <input type="number" min="0" step="0.01" value={l.unit_price}
                             aria-label={`Precio línea ${i + 1}`}
                             onChange={(e) => updateLine(i, "unit_price", e.target.value)} />
                    </td>
                    <td>
                      <input type="number" min="0" max="100" step="0.01"
                             value={l.discount_pct}
                             aria-label={`Descuento línea ${i + 1}`}
                             onChange={(e) => updateLine(i, "discount_pct", e.target.value)} />
                    </td>
                    <td>
                      <input type="number" min="0" step="1" value={l.iva_pct}
                             aria-label={`IVA línea ${i + 1}`}
                             onChange={(e) => updateLine(i, "iva_pct", e.target.value)} />
                    </td>
                    <td>{lineTotal(l).toFixed(2)}</td>
                    <td>
                      {lines.length > 1 ? (
                        <button type="button" className="button small secondary"
                                aria-label={`Eliminar línea ${i + 1}`}
                                onClick={() => setLines((rs) => rs.filter((_, j) => j !== i))}>
                          ✕
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="erp-quote-foot">
              <button type="button" className="button small secondary"
                      onClick={() => setLines((rs) => [...rs, { ...EMPTY_LINE }])}>
                + Añadir línea
              </button>
              <label className="field">
                <span>Fecha</span>
                <input type="date" value={fecha}
                       onChange={(e) => setFecha(e.target.value)} />
              </label>
              {/* Portes aparte de la mercancía, como en el pedido manual. En
                  FACTUSOL van a la banda de portes de la cabecera (IPOR1PRE),
                  que es donde los deja la app Woo→FACTUSOL y de donde el PDF
                  los pinta como línea de cargo. */}
              <label className="field erp-quote-portes">
                <span>Portes (€)</span>
                <input type="number" min="0" step="0.01" value={portes}
                       aria-label="Portes" placeholder="0.00"
                       title="Gastos de envío. Van en los portes del documento, aparte de la mercancía (como en los pedidos web)."
                       onChange={(e) => setPortes(e.target.value)} />
              </label>
              <p className="erp-manual-total">
                Base: <strong>{total.toFixed(2)} EUR</strong>
                {num(portes) > 0 ? (
                  <span className="muted small">
                    {" "}(líneas {linesTotal.toFixed(2)} + portes {num(portes).toFixed(2)})
                  </span>
                ) : null}
              </p>
            </div>
            <p className="muted small">
              Las líneas se guardan en FACTUSOL (F_LPS) y se ven igual en el
              escritorio. El descuento va al primer nivel (DTO 1).
            </p>
          </>
        )}

        <div className="modal-actions">
          <button type="button" className="button secondary"
                  onClick={onCancel} disabled={submitting}>
            Cancelar
          </button>
          {needsForce ? (
            <button type="button" className="button danger"
                    onClick={() => submit(true)} disabled={submitting}>
              {submitting ? "Guardando…" : "Guardar de todos modos"}
            </button>
          ) : (
            <button type="button" className="button"
                    onClick={() => submit()} disabled={!valid || submitting}>
              {submitting
                ? (editing ? "Guardando…" : "Creando…")
                : (editing ? "Guardar cambios" : "Crear proforma")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
