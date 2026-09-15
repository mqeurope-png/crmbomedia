import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import {
  DocumentLinesTable,
  documentLineTotal,
  emptyDocumentLine,
  type DocumentLine,
} from "./DocumentLinesTable";
import { searchFactusolArticles } from "../../lib/erpApi";

/** Lote 2 · PR-2 — tabla de líneas compartida por el pedido manual y la
 *  proforma: mismas columnas y etiquetas, totales en mono a la derecha,
 *  columnas DTO/IVA opcionales y autocompletado solo en el campo con foco. */

jest.mock("../../lib/erpApi", () => ({ searchFactusolArticles: jest.fn() }));
const mockArticles = searchFactusolArticles as jest.Mock;

const ARTICLE = {
  codart: "00001", equart: "CDR80WPT", sku: "CDR80WPT",
  descripcion: "CD TQ 700 MB white Thermal WPT",
  desart: "CD TQ 700 MB white Thermal WPT", deeart: null, detart: null,
  eanart: null, famart: null,
  precio_venta: 0.79, precio_venta_columna: "PVPART", precio_coste: 0.25,
  precio: 0.79, stock: 100, iva_pct: 10,
};

function Harness({
  initial, onLines, ...props
}: {
  initial?: DocumentLine[];
  onLines?: (next: DocumentLine[]) => void;
} & Partial<React.ComponentProps<typeof DocumentLinesTable>>) {
  const [lines, setLines] = useState<DocumentLine[]>(initial ?? [emptyDocumentLine()]);
  return (
    <DocumentLinesTable
      lines={lines}
      onChange={(next) => { setLines(next); onLines?.(next); }}
      {...props}
    />
  );
}

beforeEach(() => {
  mockArticles.mockReset();
  mockArticles.mockResolvedValue([]);
});

describe("DocumentLinesTable", () => {
  it("columnas base (SKU opcional · Descripción · Cant. · Precio ud. · Total) y DTO/IVA solo si se piden", () => {
    const { unmount } = render(<Harness />);
    const heads = () => screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(heads()).toEqual(["SKU (opcional)", "Descripción", "Cant.", "Precio ud.", "Total", ""]);
    expect(screen.queryByLabelText("Descuento línea 1")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("IVA línea 1")).not.toBeInTheDocument();
    unmount();

    render(<Harness showDiscount showIva />);
    expect(heads()).toEqual([
      "SKU (opcional)", "Descripción", "Cant.", "Precio ud.", "DTO %", "IVA %", "Total", "",
    ]);
    expect(screen.getByLabelText("Descuento línea 1")).toHaveValue(0);
    expect(screen.getByLabelText("IVA línea 1")).toHaveValue(21);
  });

  it("anchos fijos: la tabla lleva colgroup y las cifras van en celdas .num (mono, derecha)", () => {
    render(<Harness initial={[emptyDocumentLine({ description: "Vinilo", quantity: "2", unit_price: "100" })]} />);
    const table = screen.getByRole("table", { name: "Líneas" });
    expect(table).toHaveClass("erp-doc-lines", "data-table--responsive");
    expect(table.querySelector("colgroup col.erp-doc-col-total")).not.toBeNull();
    const total = screen.getByText("200.00");
    expect(total.tagName).toBe("TD");
    expect(total).toHaveClass("num", "erp-doc-total");
    // Cabeceras numéricas también alineadas a la derecha.
    expect(screen.getByRole("columnheader", { name: "Total" })).toHaveClass("num");
    expect(screen.getByRole("columnheader", { name: "Cant." })).toHaveClass("num");
  });

  it("añade y quita líneas; la única línea no se puede quitar", async () => {
    const user = userEvent.setup();
    const onLines = jest.fn();
    render(<Harness onLines={onLines} />);
    expect(screen.queryByRole("button", { name: "Eliminar línea 1" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "+ Añadir línea" }));
    expect(screen.getByLabelText("SKU línea 2")).toBeInTheDocument();
    expect(onLines).toHaveBeenLastCalledWith([emptyDocumentLine(), emptyDocumentLine()]);
    await user.click(screen.getByRole("button", { name: "Eliminar línea 2" }));
    expect(screen.queryByLabelText("SKU línea 2")).not.toBeInTheDocument();
  });

  it("editar recalcula el total de la línea con el descuento (mismo cálculo que el backend)", async () => {
    const user = userEvent.setup();
    render(<Harness showDiscount />);
    await user.type(screen.getByLabelText("Descripción línea 1"), "Vinilo");
    await user.clear(screen.getByLabelText("Cantidad línea 1"));
    await user.type(screen.getByLabelText("Cantidad línea 1"), "2");
    await user.type(screen.getByLabelText("Precio línea 1"), "100");
    expect(screen.getByText("200.00")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Descuento línea 1"));
    await user.type(screen.getByLabelText("Descuento línea 1"), "10");
    expect(screen.getByText("180.00")).toBeInTheDocument();
    expect(documentLineTotal(emptyDocumentLine({ quantity: "2", unit_price: "100", discount_pct: "10" })))
      .toBeCloseTo(180);
  });

  it("elegir un artículo rellena SKU, descripción y precio de venta (y el IVA solo con columna IVA)", async () => {
    mockArticles.mockResolvedValue([ARTICLE]);
    const user = userEvent.setup();
    const onLines = jest.fn();
    render(<Harness showIva onLines={onLines} />);
    await user.type(screen.getByLabelText("SKU línea 1"), "CDR80");
    await user.click(await screen.findByRole("button", { name: /CDR80WPT/ }));

    expect(screen.getByLabelText("SKU línea 1")).toHaveValue("CDR80WPT");
    expect(screen.getByLabelText("Descripción línea 1")).toHaveValue("CD TQ 700 MB white Thermal WPT");
    expect(screen.getByLabelText("Precio línea 1")).toHaveValue(0.79);
    expect(screen.getByLabelText("IVA línea 1")).toHaveValue(10);
  });

  it("sin columna IVA, elegir un artículo no toca `iva_pct`; sin precio de venta respeta el tecleado", async () => {
    mockArticles.mockResolvedValue([{ ...ARTICLE, precio_venta: null, precio: 0 }]);
    const user = userEvent.setup();
    const onLines = jest.fn();
    render(<Harness onLines={onLines} />);
    await user.type(screen.getByLabelText("Precio línea 1"), "5");
    await user.type(screen.getByLabelText("Descripción línea 1"), "CDR80");
    await user.click(await screen.findByRole("button", { name: /CDR80WPT/ }));
    const last = onLines.mock.calls.at(-1)?.[0][0] as DocumentLine;
    expect(last.sku).toBe("CDR80WPT");
    expect(last.unit_price).toBe("5");
    expect(last.iva_pct).toBe("21");
  });

  it("solo busca en el campo con foco: una precarga de N líneas no lanza N búsquedas", async () => {
    mockArticles.mockResolvedValue([ARTICLE]);
    const user = userEvent.setup();
    render(<Harness initial={[
      emptyDocumentLine({ sku: "CDR80WPT", description: "CD TQ 700 MB" }),
      emptyDocumentLine({ sku: "CAB-HDMI", description: "Cable HDMI" }),
    ]} />);
    await new Promise((r) => setTimeout(r, 400));
    expect(mockArticles).not.toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("SKU línea 2"), "X");
    await waitFor(() => expect(mockArticles).toHaveBeenCalledWith("CAB-HDMIX"));
    expect(await screen.findByRole("listbox", { name: /SKU línea 2/ })).toBeInTheDocument();
    expect(screen.queryByRole("listbox", { name: /SKU línea 1/ })).not.toBeInTheDocument();
  });

  it("articleSearch=false: sin catálogo, SKU y Descripción son inputs normales", async () => {
    mockArticles.mockResolvedValue([ARTICLE]);
    const user = userEvent.setup();
    render(<Harness articleSearch={false} />);
    await user.type(screen.getByLabelText("Descripción línea 1"), "tinta");
    await new Promise((r) => setTimeout(r, 400));
    expect(mockArticles).not.toHaveBeenCalled();
  });

  it("el pie lleva «+ Añadir línea» y lo que pase el consumidor (fecha, portes…)", () => {
    render(<Harness footer={<label className="field"><span>Portes</span><input aria-label="Portes" /></label>} />);
    const foot = screen.getByRole("button", { name: "+ Añadir línea" }).parentElement as HTMLElement;
    expect(foot).toHaveClass("erp-doc-lines-foot");
    expect(within(foot).getByLabelText("Portes")).toBeInTheDocument();
  });
});
