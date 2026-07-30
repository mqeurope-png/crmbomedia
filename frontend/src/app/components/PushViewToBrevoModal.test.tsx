import { render, screen } from "@testing-library/react";
import { PushViewToBrevoModal } from "./PushViewToBrevoModal";

jest.mock("../lib/brevoApi", () => ({
  resolvePrimaryBrevoAccount: jest.fn().mockResolvedValue("main"),
  listBrevoLists: jest
    .fn()
    .mockResolvedValue([
      { id: 1, name: "Fespa 2026", total_subscribers: 10 },
      { id: 2, name: "Leads MBO", total_subscribers: 20 },
    ]),
}));

jest.mock("../lib/errors", () => ({
  extractErrorMessage: (_err: unknown, fallback: string) => fallback,
}));

function renderModal(overrides: Record<string, unknown> = {}) {
  const props = {
    viewName: "Mi vista",
    contactsCount: 42,
    onSubmit: jest.fn().mockResolvedValue(undefined),
    onClose: jest.fn(),
    ...overrides,
  };
  return { props, ...render(<PushViewToBrevoModal {...props} />) };
}

describe("PushViewToBrevoModal", () => {
  it("renderiza dentro de la caja .modal-dialog (regresión hotfix #266)", () => {
    const { container } = renderModal();
    expect(container.querySelector(".modal-dialog")).toBeInTheDocument();
    // La clase `.modal` (inexistente) fue el bug — no debe usarse como caja.
    expect(container.querySelector(".modal-backdrop > .modal")).toBeNull();
  });

  it("muestra el nombre de la vista y el conteo de contactos", () => {
    renderModal({ viewName: "Abiertos campaña 48", contactsCount: 1 });
    expect(screen.getByText("Abiertos campaña 48")).toBeInTheDocument();
    expect(screen.getByText(/1 contacto\b/)).toBeInTheDocument();
  });

  it("carga las listas Brevo y ofrece el formulario de destino", async () => {
    renderModal();
    // Las listas llegan async: aparece la opción en el dropdown.
    expect(await screen.findByRole("option", { name: /Fespa 2026/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Leads MBO/ })).toBeInTheDocument();
    // Radios de destino + input de lista nueva + botón enviar.
    expect(screen.getByLabelText(/Lista existente/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Crear lista nueva/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Enviar/i })).toBeInTheDocument();
  });
});
