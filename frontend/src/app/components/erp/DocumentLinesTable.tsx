"use client";

import { useState } from "react";
import type { FactusolArticle } from "../../lib/erpApi";
import { ArticleAutocompleteInput } from "./ArticleAutocompleteInput";

/** Lote 2 · PR-2 — tabla de líneas COMPARTIDA por el pedido manual y la
 *  proforma (revisión de diseño §6 y §10: «hoy son dos tablas distintas y se
 *  comportan distinto»). Una sola forma de editar líneas en todo el ERP:
 *
 *  - Anchos de columna FIJOS (`<colgroup>` + `table-layout: fixed`): las
 *    columnas ya no se dimensionan según el contenido, que era la causa del
 *    «descuadre» al meter descripciones largas.
 *  - Cifras a la derecha y en monoespaciada (`td.num`), incluido el total de
 *    la línea.
 *  - Autocompletado de artículo (F_ART) en SKU y Descripción, pero SOLO en el
 *    campo que tiene el foco: las precargas (plantilla, proforma, documento
 *    FACTUSOL) cambian todas las líneas a la vez y, si cada input buscase por
 *    su cuenta, se lanzarían 2N consultas y se abrirían 2N desplegables sin
 *    que nadie tecleara.
 *  - Columnas DTO % / IVA % opcionales (`showDiscount` / `showIva`): la
 *    proforma las usa (F_LPS), el pedido manual no.
 *
 *  Cada consumidor conserva la forma de SU payload: aquí solo viven las filas
 *  editables (`DocumentLine`, todo en texto porque es lo que hay en los
 *  inputs) y el cálculo del total de línea. */
export type DocumentLine = {
  /** SKU comercial (EQUART) o CODART; vacío en líneas de texto libre. */
  sku: string;
  description: string;
  quantity: string;
  unit_price: string;
  /** DTO % (primer nivel de descuento, `DT1LPS`). "0" si la tabla no lo enseña. */
  discount_pct: string;
  /** IVA % de la línea. Solo significativo con `showIva`. */
  iva_pct: string;
};

export type DocumentLineField = keyof DocumentLine;

const DEFAULT_IVA_PCT = "21";

export function emptyDocumentLine(over: Partial<DocumentLine> = {}): DocumentLine {
  return {
    sku: "", description: "", quantity: "1", unit_price: "",
    discount_pct: "0", iva_pct: DEFAULT_IVA_PCT,
    ...over,
  };
}

/** Número de un campo de texto de la fila; lo que no es número cuenta 0. */
export function lineNum(v: string): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

/** Total de la línea con el descuento aplicado — el mismo cálculo que hace el
 *  backend (`TOTLPS` en la proforma; `line_total` en el pedido, donde el
 *  descuento es 0), para que lo que se ve cuadre con lo que se escribe. */
export function documentLineTotal(l: DocumentLine): number {
  return lineNum(l.quantity) * lineNum(l.unit_price) * (1 - lineNum(l.discount_pct) / 100);
}

/** Campo de línea con autocomplete que tiene el foco. Solo ESE campo busca. */
type AcField = { line: number; field: "sku" | "description" };

export function DocumentLinesTable({
  lines,
  onChange,
  articleSearch = true,
  showDiscount = false,
  showIva = false,
  ariaLabel = "Líneas",
  addLabel = "+ Añadir línea",
  skuPlaceholder,
  descriptionPlaceholder,
  footer,
}: {
  lines: DocumentLine[];
  /** Devuelve la lista completa ya modificada (añadir, quitar, editar). */
  onChange: (next: DocumentLine[]) => void;
  /** false = sin catálogo contra el que buscar (empresa sin vínculo FACTUSOL):
   *  SKU y Descripción quedan como inputs normales. */
  articleSearch?: boolean;
  showDiscount?: boolean;
  showIva?: boolean;
  ariaLabel?: string;
  addLabel?: string;
  skuPlaceholder?: string;
  descriptionPlaceholder?: string;
  /** Lo que va en la fila de pie junto a «+ Añadir línea» (fecha, portes…). */
  footer?: React.ReactNode;
}) {
  const [acField, setAcField] = useState<AcField | null>(null);

  function update(i: number, key: DocumentLineField, value: string) {
    onChange(lines.map((r, j) => (j === i ? { ...r, [key]: value } : r)));
  }

  function remove(i: number) {
    onChange(lines.filter((_, j) => j !== i));
  }

  function add() {
    onChange([...lines, emptyDocumentLine()]);
  }

  /** Rellena la línea con el artículo elegido del catálogo. El precio de
   *  venta solo se escribe si FACTUSOL lo tiene — forzar «0.00» invita a
   *  emitir un documento a cero sin que nadie lo note. */
  function applyArticle(i: number, a: FactusolArticle) {
    onChange(lines.map((r, j) => (j === i ? {
      ...r,
      sku: a.sku ?? a.codart ?? "",
      description: a.descripcion ?? a.sku ?? "",
      unit_price: a.precio_venta ? String(a.precio_venta) : r.unit_price,
      iva_pct: showIva ? String(a.iva_pct || Number(DEFAULT_IVA_PCT)) : r.iva_pct,
    } : r)));
  }

  function isAc(i: number, field: AcField["field"]): boolean {
    return articleSearch && acField?.line === i && acField.field === field;
  }

  function acCellProps(i: number, field: AcField["field"]) {
    return {
      onFocusCapture: () => setAcField({ line: i, field }),
      onBlurCapture: () => setAcField((cur) =>
        (cur?.line === i && cur.field === field ? null : cur)),
    };
  }

  return (
    <div className="erp-doc-lines-wrap">
      {/* `data-table--responsive` + `data-label`: por debajo de 768 px cada
          línea pasa a ser una tarjeta de pares etiqueta/campo (E5). */}
      <table className="data-table data-table--responsive erp-doc-lines" aria-label={ariaLabel}>
        <colgroup>
          <col className="erp-doc-col-sku" />
          <col className="erp-doc-col-desc" />
          <col className="erp-doc-col-qty" />
          <col className="erp-doc-col-price" />
          {showDiscount ? <col className="erp-doc-col-pct" /> : null}
          {showIva ? <col className="erp-doc-col-pct" /> : null}
          <col className="erp-doc-col-total" />
          <col className="erp-doc-col-del" />
        </colgroup>
        <thead>
          <tr>
            <th scope="col">SKU (opcional)</th>
            <th scope="col">Descripción</th>
            <th scope="col" className="num">Cant.</th>
            <th scope="col" className="num">Precio ud.</th>
            {showDiscount ? <th scope="col" className="num">DTO %</th> : null}
            {showIva ? <th scope="col" className="num">IVA %</th> : null}
            <th scope="col" className="num">Total</th>
            <th scope="col" aria-label="Quitar línea" />
          </tr>
        </thead>
        <tbody>
          {lines.map((l, i) => (
            <tr key={i}>
              <td data-label="SKU" {...acCellProps(i, "sku")}>
                <ArticleAutocompleteInput
                  value={l.sku}
                  enabled={isAc(i, "sku")}
                  ariaLabel={`SKU línea ${i + 1}`}
                  placeholder={skuPlaceholder}
                  onChange={(v) => update(i, "sku", v)}
                  onPick={(a) => applyArticle(i, a)}
                />
              </td>
              <td data-label="Descripción" {...acCellProps(i, "description")}>
                <ArticleAutocompleteInput
                  value={l.description}
                  enabled={isAc(i, "description")}
                  ariaLabel={`Descripción línea ${i + 1}`}
                  placeholder={descriptionPlaceholder}
                  onChange={(v) => update(i, "description", v)}
                  onPick={(a) => applyArticle(i, a)}
                />
              </td>
              <td data-label="Cant." className="num">
                <input type="number" min="0" step="1" value={l.quantity}
                       aria-label={`Cantidad línea ${i + 1}`}
                       onChange={(e) => update(i, "quantity", e.target.value)} />
              </td>
              <td data-label="Precio ud." className="num">
                <input type="number" min="0" step="0.01" value={l.unit_price}
                       aria-label={`Precio línea ${i + 1}`}
                       onChange={(e) => update(i, "unit_price", e.target.value)} />
              </td>
              {showDiscount ? (
                <td data-label="DTO %" className="num">
                  <input type="number" min="0" max="100" step="0.01" value={l.discount_pct}
                         aria-label={`Descuento línea ${i + 1}`}
                         onChange={(e) => update(i, "discount_pct", e.target.value)} />
                </td>
              ) : null}
              {showIva ? (
                <td data-label="IVA %" className="num">
                  <input type="number" min="0" step="1" value={l.iva_pct}
                         aria-label={`IVA línea ${i + 1}`}
                         onChange={(e) => update(i, "iva_pct", e.target.value)} />
                </td>
              ) : null}
              <td data-label="Total" className="num erp-doc-total">
                {documentLineTotal(l).toFixed(2)}
              </td>
              <td className="erp-doc-del">
                {lines.length > 1 ? (
                  <button type="button" className="button small tertiary"
                          aria-label={`Eliminar línea ${i + 1}`}
                          title="Quitar esta línea"
                          onClick={() => remove(i)}>
                    ✕
                  </button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="erp-doc-lines-foot">
        <button type="button" className="button small secondary" onClick={add}>
          {addLabel}
        </button>
        {footer}
      </div>
    </div>
  );
}
