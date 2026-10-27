"use client";

import { useState } from "react";
import type { FactusolArticle, OfertaLinea, OfertaLineaIn } from "../../lib/erpApi";
import { ArticleAutocompleteInput } from "./ArticleAutocompleteInput";

/** Una línea de oferta tal como se edita (todo en texto, que es lo que hay en
 *  los inputs). `codart` es el artículo de FACTUSOL elegido del catálogo; se
 *  pierde si el SKU se reescribe a mano (entonces la línea «no tiene artículo»
 *  y se resolverá al convertir). */
export type OfertaLineaRow = {
  /** Clave estable para React al reordenar. */
  key: number;
  codart: string | null;
  sku: string;
  descripcion: string;
  cantidad: string;
  precio: string;
  descuento_pct: string;
  iva_pct: string;
};

const DEFAULT_IVA_PCT = "21";
let nextKey = 1;

export function emptyOfertaLineaRow(over: Partial<OfertaLineaRow> = {}): OfertaLineaRow {
  return {
    key: nextKey++,
    codart: null, sku: "", descripcion: "", cantidad: "1", precio: "",
    descuento_pct: "0", iva_pct: DEFAULT_IVA_PCT,
    ...over,
  };
}

/** Filas editables a partir de las líneas que devuelve la API (oferta o
 *  plantilla). Sin líneas, una fila vacía para empezar. */
export function rowsFromOfertaLineas(
  lineas: (Pick<OfertaLinea, "codart" | "sku" | "descripcion" | "cantidad" | "precio"
    | "descuento_pct" | "iva_pct"> & Partial<OfertaLinea>)[] | null | undefined,
): OfertaLineaRow[] {
  const rows = (lineas ?? []).map((l) => emptyOfertaLineaRow({
    codart: l.codart,
    sku: l.sku ?? l.codart ?? "",
    descripcion: l.descripcion,
    cantidad: String(l.cantidad),
    precio: String(l.precio),
    descuento_pct: String(l.descuento_pct ?? 0),
    iva_pct: String(l.iva_pct ?? DEFAULT_IVA_PCT),
  }));
  return rows.length > 0 ? rows : [emptyOfertaLineaRow()];
}

/** Número de un campo de texto de la fila; lo que no es número cuenta 0. */
export function rowNum(v: string): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

/** Redondeo a céntimos como `_redondear` del backend (medio hacia arriba). */
export function round2(n: number): number {
  return Math.round((n + Number.EPSILON) * 100) / 100;
}

/** Total de la línea sin IVA, con el descuento aplicado: el mismo cálculo que
 *  `normalizar_lineas` en el backend. */
export function ofertaLineaTotal(row: OfertaLineaRow): number {
  return round2(rowNum(row.cantidad) * rowNum(row.precio) * (1 - rowNum(row.descuento_pct) / 100));
}

/** Fila sin nada: ni texto, ni artículo, ni SKU. No se envía. */
export function isRowBlank(row: OfertaLineaRow): boolean {
  return !row.descripcion.trim() && !row.codart && !row.sku.trim();
}

/** Línea solo de texto (sin artículo, cantidad 0 y precio 0): vale para la
 *  oferta y NO bloquea la conversión. Mismo criterio que el backend. */
export function isTextOnlyRow(row: OfertaLineaRow): boolean {
  return !row.codart && rowNum(row.cantidad) === 0 && rowNum(row.precio) === 0;
}

/** Línea con importe pero sin artículo de FACTUSOL: vale para la oferta, pero
 *  habrá que elegirle el artículo antes de convertirla (`lineas_sin_articulo`). */
export function isRowSinArticulo(row: OfertaLineaRow): boolean {
  return !isRowBlank(row) && !row.codart && !isTextOnlyRow(row);
}

/** Las filas → líneas para la API (sin las vacías). */
export function rowsToOfertaLineasIn(rows: OfertaLineaRow[]): OfertaLineaIn[] {
  return rows.filter((r) => !isRowBlank(r)).map((r) => ({
    codart: r.codart || null,
    sku: r.sku.trim() || null,
    descripcion: r.descripcion.trim(),
    cantidad: rowNum(r.cantidad),
    precio: rowNum(r.precio),
    descuento_pct: rowNum(r.descuento_pct),
    iva_pct: rowNum(r.iva_pct),
  }));
}

export type OfertaTotales = { base: number; iva_pct: number; iva: number; total: number };

/** Totales en vivo con el MISMO criterio que `totales` del backend: base =
 *  suma de las líneas sin IVA; una sola banda de IVA (como F_PRE), la de la
 *  primera línea con importe; sin IVA → tipo 0 e IVA 0. */
export function ofertaTotales(rows: OfertaLineaRow[], conIva: boolean): OfertaTotales {
  const vivas = rows.filter((r) => !isRowBlank(r));
  const base = round2(vivas.reduce((sum, r) => sum + ofertaLineaTotal(r), 0));
  const conImporte = vivas.filter((r) => !isTextOnlyRow(r));
  const ivaPct = conImporte.length > 0 ? rowNum(conImporte[0].iva_pct) : Number(DEFAULT_IVA_PCT);
  if (!conIva) return { base, iva_pct: 0, iva: 0, total: base };
  const iva = round2(base * ivaPct / 100);
  return { base, iva_pct: ivaPct, iva, total: round2(base + iva) };
}

/** Campo de línea con autocomplete que tiene el foco. Solo ESE campo busca
 *  (como en `DocumentLinesTable`: las precargas cambian todas las filas a la
 *  vez y, si cada input buscase por su cuenta, serían 2N consultas). */
type AcField = { line: number; field: "sku" | "descripcion" };

/** Editor de líneas de una oferta (y de una plantilla): artículo del catálogo
 *  F_ART (SKU + descripción + precio + IVA al elegirlo), descripción, cantidad,
 *  precio, DTO %, IVA %, total de línea; añadir, quitar y reordenar. Una línea
 *  sin artículo es válida: se avisa suave y se resolverá al convertir.
 *  `marcadas` (posiciones 1-based) resalta las que la conversión rechazó. */
export function OfertaLineasEditor({
  rows,
  onChange,
  disabled = false,
  ariaLabel = "Líneas de la oferta",
  marcadas = [],
}: {
  rows: OfertaLineaRow[];
  /** Devuelve la lista completa ya modificada. */
  onChange: (next: OfertaLineaRow[]) => void;
  disabled?: boolean;
  ariaLabel?: string;
  /** Posiciones (1-based) a resaltar: las líneas sin artículo que bloquearon
   *  «Convertir en presupuesto». */
  marcadas?: number[];
}) {
  const [acField, setAcField] = useState<AcField | null>(null);

  function update(i: number, patch: Partial<OfertaLineaRow>) {
    onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  }

  function remove(i: number) {
    const next = rows.filter((_, j) => j !== i);
    onChange(next.length > 0 ? next : [emptyOfertaLineaRow()]);
  }

  function add() {
    onChange([...rows, emptyOfertaLineaRow()]);
  }

  function move(i: number, delta: -1 | 1) {
    const j = i + delta;
    if (j < 0 || j >= rows.length) return;
    const next = [...rows];
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  }

  /** Rellena la línea con el artículo del catálogo: código interno, SKU
   *  comercial, descripción, precio de venta (solo si FACTUSOL lo tiene) e IVA. */
  function applyArticle(i: number, a: FactusolArticle) {
    update(i, {
      codart: a.codart ?? null,
      sku: a.sku ?? a.codart ?? "",
      descripcion: a.descripcion ?? a.sku ?? "",
      precio: a.precio_venta ? String(a.precio_venta) : rows[i].precio,
      iva_pct: String(a.iva_pct || Number(DEFAULT_IVA_PCT)),
    });
  }

  function isAc(i: number, field: AcField["field"]): boolean {
    return !disabled && acField?.line === i && acField.field === field;
  }

  function acCellProps(i: number, field: AcField["field"]) {
    return {
      onFocusCapture: () => setAcField({ line: i, field }),
      onBlurCapture: () => setAcField((cur) =>
        (cur?.line === i && cur.field === field ? null : cur)),
    };
  }

  const marked = new Set(marcadas);
  const sinArticulo = rows.filter(isRowSinArticulo).length;

  return (
    <div className="erp-doc-lines-wrap">
      <table className="data-table data-table--responsive erp-doc-lines" aria-label={ariaLabel}>
        <colgroup>
          <col className="erp-doc-col-sku" />
          <col className="erp-doc-col-desc" />
          <col className="erp-doc-col-qty" />
          <col className="erp-doc-col-price" />
          <col className="erp-doc-col-pct" />
          <col className="erp-doc-col-pct" />
          <col className="erp-doc-col-total" />
          <col className="erp-doc-col-del" />
        </colgroup>
        <thead>
          <tr>
            <th scope="col">Artículo (SKU)</th>
            <th scope="col">Descripción</th>
            <th scope="col" className="num">Cant.</th>
            <th scope="col" className="num">Precio ud.</th>
            <th scope="col" className="num">DTO %</th>
            <th scope="col" className="num">IVA %</th>
            <th scope="col" className="num">Total</th>
            <th scope="col" aria-label="Acciones de la línea" />
          </tr>
        </thead>
        <tbody>
          {rows.map((l, i) => {
            const pos = i + 1;
            const marcada = marked.has(pos);
            const sinArt = isRowSinArticulo(l);
            return (
              <tr key={l.key} className={marcada ? "is-marked" : undefined}
                  aria-label={`Línea ${pos}`}>
                <td data-label="Artículo" {...acCellProps(i, "sku")}>
                  <ArticleAutocompleteInput
                    value={l.sku}
                    enabled={isAc(i, "sku")}
                    disabled={disabled}
                    ariaLabel={`SKU línea ${pos}`}
                    placeholder="Buscar en el catálogo…"
                    // Reescribir el SKU a mano deja la línea sin artículo:
                    // solo elegir del catálogo fija `codart`.
                    onChange={(v) => update(i, { sku: v, codart: null })}
                    onPick={(a) => applyArticle(i, a)}
                  />
                  {sinArt ? (
                    <span className={`small ${marcada ? "form-error" : "muted"}`}
                          role={marcada ? "alert" : undefined}>
                      {marcada
                        ? "Sin artículo de FACTUSOL: elige uno para poder convertir."
                        : "Sin artículo: se resolverá al convertir."}
                    </span>
                  ) : null}
                </td>
                <td data-label="Descripción" {...acCellProps(i, "descripcion")}>
                  <ArticleAutocompleteInput
                    value={l.descripcion}
                    enabled={isAc(i, "descripcion")}
                    disabled={disabled}
                    ariaLabel={`Descripción línea ${pos}`}
                    placeholder="Descripción del artículo o concepto"
                    multiline
                    onChange={(v) => update(i, { descripcion: v })}
                    onPick={(a) => applyArticle(i, a)}
                  />
                </td>
                <td data-label="Cant." className="num">
                  <input type="number" min="0" step="1" value={l.cantidad} disabled={disabled}
                         aria-label={`Cantidad línea ${pos}`}
                         onChange={(e) => update(i, { cantidad: e.target.value })} />
                </td>
                <td data-label="Precio ud." className="num">
                  <input type="number" min="0" step="0.01" value={l.precio} disabled={disabled}
                         aria-label={`Precio línea ${pos}`}
                         onChange={(e) => update(i, { precio: e.target.value })} />
                </td>
                <td data-label="DTO %" className="num">
                  <input type="number" min="0" max="100" step="0.01" value={l.descuento_pct}
                         disabled={disabled} aria-label={`Descuento línea ${pos}`}
                         onChange={(e) => update(i, { descuento_pct: e.target.value })} />
                </td>
                <td data-label="IVA %" className="num">
                  <input type="number" min="0" max="100" step="1" value={l.iva_pct}
                         disabled={disabled} aria-label={`IVA línea ${pos}`}
                         onChange={(e) => update(i, { iva_pct: e.target.value })} />
                </td>
                <td data-label="Total" className="num erp-doc-total">
                  {ofertaLineaTotal(l).toFixed(2)}
                </td>
                <td className="erp-doc-del">
                  <span className="erp-contact-channel" role="group" aria-label={`Acciones línea ${pos}`}>
                    <button type="button" className="button small tertiary"
                            aria-label={`Subir línea ${pos}`} title="Subir"
                            disabled={disabled || i === 0}
                            onClick={() => move(i, -1)}>
                      ↑
                    </button>
                    <button type="button" className="button small tertiary"
                            aria-label={`Bajar línea ${pos}`} title="Bajar"
                            disabled={disabled || i === rows.length - 1}
                            onClick={() => move(i, 1)}>
                      ↓
                    </button>
                    <button type="button" className="button small tertiary"
                            aria-label={`Eliminar línea ${pos}`} title="Quitar esta línea"
                            disabled={disabled}
                            onClick={() => remove(i)}>
                      ✕
                    </button>
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="erp-doc-lines-foot">
        <button type="button" className="button small secondary" disabled={disabled} onClick={add}>
          + Añadir línea
        </button>
        {sinArticulo > 0 ? (
          <span className="muted small" role="note">
            {sinArticulo === 1 ? "1 línea sin artículo de FACTUSOL" : `${sinArticulo} líneas sin artículo de FACTUSOL`}
            : valen para la oferta; al convertirla en presupuesto habrá que elegir el artículo.
          </span>
        ) : null}
      </div>
    </div>
  );
}
