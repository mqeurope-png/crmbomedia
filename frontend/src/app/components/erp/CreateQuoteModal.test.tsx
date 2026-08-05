import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CreateQuoteModal } from "./CreateQuoteModal";
import {
  createFactusolQuote,
  duplicateFactusolQuote,
  listFactusolQuotes,
  searchFactusolArticles,
} from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  createFactusolQuote: jest.fn(),
  duplicateFactusolQuote: jest.fn(),
  listFactusolQuotes: jest.fn(),
  searchFactusolArticles: jest.fn(),
}));
const mockCreate = createFactusolQuote as jest.Mock;
const mockDuplicate = duplicateFactusolQuote as jest.Mock;
const mockList = listFactusolQuotes as jest.Mock;
const mockArticles = searchFactusolArticles as jest.Mock;

beforeEach(() => {
  mockCreate.mockReset();
  mockDuplicate.mockReset();
  mockList.mockReset();
  mockArticles.mockReset();
  mockCreate.mockResolvedValue({ job_id: "job-1", status: "queued" });
  mockDuplicate.mockResolvedValue({ job_id: "job-2", status: "queued" });
  mockList.mockResolvedValue({ items: [], unlinked: false });
  mockArticles.mockResolvedValue([]);
});

function base(over = {}) {
  return {
    companyId: "c1", companyName: "Acme SL",
    onCreated: jest.fn(), onCancel: jest.fn(), ...over,
  };
}

describe("CreateQuoteModal", () => {
  it("modo rápido: envía una línea única con el concepto y el importe", async () => {
    const onCreated = jest.fn();
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base({ onCreated })} />);

    await user.type(screen.getByLabelText("Concepto"), "Instalación sala 3");
    await user.type(screen.getByLabelText("Importe (base)"), "500");
    await user.click(screen.getByRole("button", { name: "Crear proforma" }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    const payload = mockCreate.mock.calls[0][0];
    expect(payload.company_id).toBe("c1");
    expect(payload.referencia).toBe("Instalación sala 3");
    expect(payload.lines).toEqual([
      expect.objectContaining({
        description: "Instalación sala 3", quantity: 1, unit_price: 500,
      }),
    ]);
    expect(onCreated).toHaveBeenCalledWith("job-1");
  });

  it("modo con artículos: busca en F_ART y añade el resultado como línea", async () => {
    mockArticles.mockResolvedValue([
      { codart: "ART-1", descripcion: "Cable HDMI 3m", precio: 8.5, iva_pct: 21 },
    ]);
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base()} />);

    await user.click(screen.getByRole("button", { name: "Con artículos" }));
    await user.type(screen.getByLabelText("Buscar artículo"), "hdmi");
    const hit = await screen.findByRole("button", { name: /ART-1 · Cable HDMI 3m/ });
    await user.click(hit);

    // La fila vacía inicial se reutiliza: queda UNA línea con el artículo.
    expect(screen.getByLabelText("Descripción línea 1")).toHaveValue("Cable HDMI 3m");
    expect(screen.getByLabelText("Precio línea 1")).toHaveValue(8.5);
  });

  it("modo duplicar: lista las proformas previas y duplica la elegida", async () => {
    mockList.mockResolvedValue({
      items: [{
        codpre: "77", referencia: "Proforma anterior", fecha: "2026-07-01",
        clipre: "55555", cliente_nombre: "Acme SL", base: 100, iva: 21, total: 121,
      }],
      unlinked: false,
    });
    const onCreated = jest.fn();
    const user = userEvent.setup();
    render(<CreateQuoteModal {...base({ onCreated })} />);

    await user.click(screen.getByRole("button", { name: "Duplicar" }));
    const select = await screen.findByLabelText("Proforma a duplicar");
    await user.selectOptions(select, "77");
    await user.click(screen.getByRole("button", { name: "Duplicar proforma" }));

    await waitFor(() => expect(mockDuplicate).toHaveBeenCalledWith("77"));
    expect(onCreated).toHaveBeenCalledWith("job-2");
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("no deja crear una proforma sin concepto ni líneas", async () => {
    render(<CreateQuoteModal {...base()} />);
    expect(screen.getByRole("button", { name: "Crear proforma" })).toBeDisabled();
  });
});
