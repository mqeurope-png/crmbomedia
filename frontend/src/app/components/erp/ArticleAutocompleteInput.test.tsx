import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ArticleAutocompleteInput } from "./ArticleAutocompleteInput";
import { searchFactusolArticles } from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({ searchFactusolArticles: jest.fn() }));
const mockSearch = searchFactusolArticles as jest.Mock;

function article(over = {}) {
  return {
    codart: "00001", equart: "CDR80WPT", sku: "CDR80WPT",
    descripcion: "CD TQ 700 MB white Thermal WPT",
    desart: "CD TQ 700 MB white Thermal WPT", deeart: null, detart: null,
    eanart: null, famart: null,
    precio_venta: 0.79, precio_venta_columna: "PVPART", precio_coste: 0.25,
    precio: 0.79, stock: 100, iva_pct: 21, ...over,
  };
}

beforeEach(() => {
  mockSearch.mockReset();
  mockSearch.mockResolvedValue([]);
});

function base(over = {}) {
  return {
    value: "", onChange: jest.fn(), onPick: jest.fn(),
    ariaLabel: "SKU línea 1", ...over,
  };
}

describe("ArticleAutocompleteInput", () => {
  it("se comporta como un input normal y propaga lo que se escribe", async () => {
    const onChange = jest.fn();
    const user = userEvent.setup();
    render(<ArticleAutocompleteInput {...base({ onChange })} />);
    await user.type(screen.getByLabelText("SKU línea 1"), "x");
    expect(onChange).toHaveBeenCalledWith("x");
  });

  it("no busca con menos de 2 caracteres", async () => {
    render(<ArticleAutocompleteInput {...base({ value: "c" })} />);
    await new Promise((r) => setTimeout(r, 400));
    expect(mockSearch).not.toHaveBeenCalled();
  });

  it("busca en F_ART y muestra SKU, descripción y precio de venta", async () => {
    mockSearch.mockResolvedValue([article()]);
    render(<ArticleAutocompleteInput {...base({ value: "CDR80" })} />);

    await waitFor(() => expect(mockSearch).toHaveBeenCalledWith("CDR80"));
    expect(await screen.findByText("CDR80WPT")).toBeInTheDocument();
    expect(screen.getByText("CD TQ 700 MB white Thermal WPT")).toBeInTheDocument();
    expect(screen.getByText("0.79 €")).toBeInTheDocument();
  });

  it("elegir una sugerencia llama a onPick con el artículo", async () => {
    mockSearch.mockResolvedValue([article()]);
    const onPick = jest.fn();
    const user = userEvent.setup();
    render(<ArticleAutocompleteInput {...base({ value: "CDR80", onPick })} />);

    await user.click(await screen.findByRole("button", { name: /CDR80WPT/ }));
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ sku: "CDR80WPT" }));
  });

  it("sin precio de venta muestra «—» en vez de 0.00", async () => {
    mockSearch.mockResolvedValue([article({ precio_venta: null, precio: 0 })]);
    render(<ArticleAutocompleteInput {...base({ value: "CDR80" })} />);
    expect(await screen.findByText("—")).toBeInTheDocument();
  });

  it("deshabilitado no busca nada (sin empresa vinculada no hay catálogo)", async () => {
    render(<ArticleAutocompleteInput {...base({ value: "CDR80", enabled: false })} />);
    await new Promise((r) => setTimeout(r, 400));
    expect(mockSearch).not.toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });
});
