import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QuotePicker } from "./QuotePicker";
import { listFactusolQuotes, searchFactusolQuotes } from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  listFactusolQuotes: jest.fn(),
  searchFactusolQuotes: jest.fn(),
}));

function quote(over = {}) {
  return {
    codpre: "574", referencia: "Cabezal + SAT", fecha: "2026-09-01",
    clipre: "55555", cliente_nombre: "Roca Joiers", base: 355, iva: 74.55, total: 429.55,
    ...over,
  };
}

beforeEach(() => {
  (listFactusolQuotes as jest.Mock).mockReset();
  (listFactusolQuotes as jest.Mock).mockResolvedValue({ items: [quote()], unlinked: false });
  (searchFactusolQuotes as jest.Mock).mockReset();
  (searchFactusolQuotes as jest.Mock).mockResolvedValue([
    quote({ codpre: "575", referencia: "Tinta", cliente_nombre: "Otra SL", total: 121 }),
  ]);
});

describe("QuotePicker — buscador de proformas (el listado de la ficha, en el alta)", () => {
  it("con empresa vinculada lista sus proformas y elegir una la carga", async () => {
    const onPick = jest.fn();
    const user = userEvent.setup();
    render(<QuotePicker companyId="c1" onPick={onPick} />);
    await waitFor(() =>
      expect(listFactusolQuotes).toHaveBeenCalledWith({ company_id: "c1", days_back: 365 }),
    );
    expect(await screen.findByText("Cabezal + SAT")).toBeInTheDocument();
    expect(screen.getByText("Roca Joiers")).toBeInTheDocument();
    expect(screen.getByText("429.55 €")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cargar en el pedido" }));
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ codpre: "574" }));
  });

  it("con texto busca en cualquier cliente por nº, referencia o cliente", async () => {
    const user = userEvent.setup();
    render(<QuotePicker onPick={() => {}} />);
    expect(screen.getByText(/Elige una empresa, o busca/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Buscar proforma"), "tinta");
    await waitFor(() =>
      expect(searchFactusolQuotes).toHaveBeenCalledWith("tinta", { days_back: 365 }),
    );
    expect(await screen.findByText("Tinta")).toBeInTheDocument();
    expect(screen.getByText("Otra SL")).toBeInTheDocument();
    expect(listFactusolQuotes).not.toHaveBeenCalled();
  });

  it("empresa sin vínculo: lo dice y deja buscar por texto", async () => {
    (listFactusolQuotes as jest.Mock).mockResolvedValue({ items: [], unlinked: true });
    render(<QuotePicker companyId="c9" onPick={() => {}} />);
    expect(await screen.findByText(/no está vinculada a FACTUSOL/)).toBeInTheDocument();
  });
});
