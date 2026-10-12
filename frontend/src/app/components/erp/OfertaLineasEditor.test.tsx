import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { searchFactusolArticles } from "../../lib/erpApi";
import {
  emptyOfertaLineaRow,
  OfertaLineasEditor,
  ofertaTotales,
  rowsFromOfertaLineas,
  rowsToOfertaLineasIn,
  type OfertaLineaRow,
} from "./OfertaLineasEditor";

jest.mock("../../lib/erpApi", () => ({ searchFactusolArticles: jest.fn() }));
const mockArticles = searchFactusolArticles as jest.Mock;

function Harness({ initial, marcadas }: { initial: OfertaLineaRow[]; marcadas?: number[] }) {
  const [rows, setRows] = useState(initial);
  return (
    <>
      <OfertaLineasEditor rows={rows} onChange={setRows} marcadas={marcadas} />
      <pre data-testid="out">{JSON.stringify(rowsToOfertaLineasIn(rows))}</pre>
    </>
  );
}

beforeEach(() => {
  mockArticles.mockReset();
  mockArticles.mockResolvedValue([]);
});

describe("OfertaLineasEditor — cálculo", () => {
  it("totales como el backend: base con descuento, una banda de IVA (la primera línea con importe), sin IVA → 0", () => {
    const rows = [
      emptyOfertaLineaRow({ descripcion: "Nota", cantidad: "0", precio: "0" }),            // solo texto: no fija el tipo
      emptyOfertaLineaRow({ descripcion: "A", cantidad: "3", precio: "10", descuento_pct: "10", iva_pct: "10" }),
      emptyOfertaLineaRow({ descripcion: "B", cantidad: "1", precio: "0.335", iva_pct: "21" }),
      emptyOfertaLineaRow(),                                                                 // vacía: no cuenta
    ];
    // 3 × 10 × 0.9 = 27.00; 0.335 → 0.34 (redondeo por línea); base 27.34; IVA 10 % = 2.73.
    expect(ofertaTotales(rows, true)).toEqual({ base: 27.34, iva_pct: 10, iva: 2.73, total: 30.07 });
    expect(ofertaTotales(rows, false)).toEqual({ base: 27.34, iva_pct: 0, iva: 0, total: 27.34 });
    expect(ofertaTotales([], true)).toEqual({ base: 0, iva_pct: 21, iva: 0, total: 0 });
  });

  it("de la API a filas y vuelta: conserva codart y sku; las filas vacías no viajan", () => {
    const rows = rowsFromOfertaLineas([{
      id: "l1", posicion: 1, codart: "A1", sku: "SKU-1", descripcion: "Placa",
      cantidad: 2, precio: 50, descuento_pct: 5, iva_pct: 21, total: 95, text_only: false,
    }]);
    expect(rows[0]).toMatchObject({ codart: "A1", sku: "SKU-1", cantidad: "2", precio: "50", descuento_pct: "5" });
    expect(rowsToOfertaLineasIn([...rows, emptyOfertaLineaRow()])).toEqual([{
      codart: "A1", sku: "SKU-1", descripcion: "Placa", cantidad: 2, precio: 50, descuento_pct: 5, iva_pct: 21,
    }]);
    expect(rowsFromOfertaLineas(undefined)).toHaveLength(1);           // una vacía para empezar
  });
});

describe("OfertaLineasEditor — edición", () => {
  it("añade, reordena y quita líneas; elegir un artículo del catálogo rellena SKU, descripción, precio e IVA", async () => {
    mockArticles.mockResolvedValue([{
      codart: "00001", equart: "CDR80", sku: "CDR80", descripcion: "CD 700 MB", desart: "CD 700 MB",
      deeart: null, detart: null, eanart: null, famart: null, precio_venta: 0.79,
      precio_venta_columna: "PVPART", precio_coste: 0.25, precio: 0.79, stock: 10, iva_pct: 21,
    }]);
    const user = userEvent.setup();
    render(<Harness initial={[
      emptyOfertaLineaRow({ descripcion: "Primera", precio: "1" }),
      emptyOfertaLineaRow({ descripcion: "Segunda", precio: "2" }),
    ]} />);
    const table = within(screen.getByRole("table", { name: "Líneas de la oferta" }));
    expect(table.getByRole("button", { name: "Subir línea 1" })).toBeDisabled();
    await user.click(table.getByRole("button", { name: "Bajar línea 1" }));
    expect(table.getByLabelText("Descripción línea 1")).toHaveValue("Segunda");
    expect(table.getByLabelText("Descripción línea 2")).toHaveValue("Primera");

    await user.click(screen.getByRole("button", { name: "+ Añadir línea" }));
    await user.type(table.getByLabelText("SKU línea 3"), "cd");
    await user.click(await screen.findByRole("button", { name: /CDR80/ }));
    expect(table.getByLabelText("SKU línea 3")).toHaveValue("CDR80");
    expect(table.getByLabelText("Descripción línea 3")).toHaveValue("CD 700 MB");
    expect(table.getByLabelText("Precio línea 3")).toHaveValue(0.79);
    expect(JSON.parse(screen.getByTestId("out").textContent ?? "[]")[2]).toMatchObject({ codart: "00001", sku: "CDR80" });

    await user.click(table.getByRole("button", { name: "Eliminar línea 1" }));
    expect(table.getByLabelText("Descripción línea 1")).toHaveValue("Primera");
    expect(table.getAllByRole("row")).toHaveLength(3);                 // cabecera + 2
  });

  it("las líneas sin artículo avisan suave; las marcadas (422) se resaltan como error", () => {
    render(<Harness
      initial={[
        emptyOfertaLineaRow({ descripcion: "Con artículo", codart: "A1", sku: "A1", precio: "10" }),
        emptyOfertaLineaRow({ descripcion: "Sin artículo", precio: "10" }),
        emptyOfertaLineaRow({ descripcion: "Solo texto", cantidad: "0", precio: "0" }),
      ]}
      marcadas={[2]}
    />);
    expect(screen.getByRole("note")).toHaveTextContent("1 línea sin artículo de FACTUSOL");
    expect(screen.getByRole("alert")).toHaveTextContent("Sin artículo de FACTUSOL: elige uno para poder convertir.");
    expect(screen.getByRole("row", { name: "Línea 2" })).toHaveClass("is-marked");
    expect(screen.getByRole("row", { name: "Línea 3" })).not.toHaveClass("is-marked");
  });
});
