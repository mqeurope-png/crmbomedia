import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErpSettingsPage from "./page";
import { getErpSettings, updateErpSettings, type ErpSettings } from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  getErpSettings: jest.fn(),
  updateErpSettings: jest.fn(),
}));
const mockGet = getErpSettings as jest.Mock;
const mockUpdate = updateErpSettings as jest.Mock;

function settings(over: Partial<ErpSettings> = {}): ErpSettings {
  return {
    default_invoice_mode: "manual",
    auto_invoice_max_amount_eur: null,
    default_carrier_id: null,
    factusol_default_ejercicio: "2026",
    factusol_live: false,
    factusol_series_default: "",
    factusol_series_by_source: {},
    ...over,
  };
}

beforeEach(() => {
  mockGet.mockReset();
  mockUpdate.mockReset();
  mockGet.mockResolvedValue(settings());
  mockUpdate.mockImplementation((patch) => Promise.resolve(settings(patch)));
});

describe("ErpSettingsPage — serie de facturación (C-2)", () => {
  it("renderiza la serie por defecto y una fila por origen", async () => {
    render(<ErpSettingsPage />);
    expect(await screen.findByLabelText("Serie por defecto")).toBeInTheDocument();
    expect(screen.getByLabelText("Serie WooCommerce (las 3 tiendas)")).toBeInTheDocument();
    expect(screen.getByLabelText("Serie Manual")).toBeInTheDocument();
    expect(screen.getByLabelText("Serie Proforma FACTUSOL")).toBeInTheDocument();
  });

  it("precarga los valores guardados", async () => {
    mockGet.mockResolvedValue(settings({
      factusol_series_default: "A",
      factusol_series_by_source: { manual: "M" },
    }));
    render(<ErpSettingsPage />);
    expect(await screen.findByLabelText("Serie por defecto")).toHaveValue("A");
    expect(screen.getByLabelText("Serie Manual")).toHaveValue("M");
    expect(screen.getByLabelText("Serie WooCommerce (las 3 tiendas)")).toHaveValue("");
  });

  it("guarda la serie por defecto y el override por origen", async () => {
    const user = userEvent.setup();
    render(<ErpSettingsPage />);
    await user.type(await screen.findByLabelText("Serie por defecto"), "A");
    await user.type(screen.getByLabelText("Serie Manual"), "M");
    await user.click(screen.getByRole("button", { name: "Guardar" }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalled());
    const patch = mockUpdate.mock.calls[0][0];
    expect(patch.factusol_series_default).toBe("A");
    expect(patch.factusol_series_by_source.manual).toBe("M");
  });
});
