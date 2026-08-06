import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RegisterCallModal } from "./RegisterCallModal";
import { createCallLog } from "../../lib/callsApi";

jest.mock("../../lib/api", () => ({
  getUsers: jest.fn().mockResolvedValue([]),
  listPipelines: jest.fn().mockResolvedValue([]),
}));

jest.mock("../../lib/callsApi", () => ({
  CALL_RESULTS: [
    { code: "contacted", label: "Contactado", icon: "📞" },
    { code: "other", label: "Otro…", icon: "✏️" },
  ],
  DURATION_BUCKETS: [{ code: "lt_1min", label: "<1 min" }],
  createCallLog: jest.fn().mockResolvedValue({ id: "call-1" }),
  listManualWorkflows: jest.fn().mockResolvedValue([]),
  runManualWorkflow: jest.fn(),
}));

jest.mock("../../lib/errors", () => ({
  extractErrorMessage: (_err: unknown, fallback: string) => fallback,
}));

function renderModal(overrides: Record<string, unknown> = {}) {
  const props = {
    contactId: "c-1",
    open: true,
    onClose: jest.fn(),
    onSaved: jest.fn(),
    onRequestCompose: jest.fn(),
    ...overrides,
  };
  return { props, ...render(<RegisterCallModal {...props} />) };
}

describe("RegisterCallModal", () => {
  it("renderiza dentro de la caja .modal-dialog (regresión hotfix #265)", () => {
    const { container } = renderModal();
    const box = container.querySelector(".modal-dialog");
    expect(box).toBeInTheDocument();
    expect(box).toHaveClass("register-call-modal");
  });

  it("no renderiza nada cuando open=false", () => {
    const { container } = renderModal({ open: false });
    expect(container).toBeEmptyDOMElement();
  });

  it("al elegir «Otro» aparece el campo libre con contador", async () => {
    const user = userEvent.setup();
    renderModal();
    // Sin «Otro» no hay campo libre.
    expect(screen.queryByText(/\/150/)).not.toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox", { name: /Resultado/i }), "other");

    expect(screen.getByText("Especifica (0/150)")).toBeInTheDocument();
    const inputs = screen.getAllByRole("textbox");
    // El input del contador tiene maxLength=150 (cap client-side).
    const custom = inputs.find((el) => el.getAttribute("maxLength") === "150");
    expect(custom).toBeDefined();
    await user.type(custom!, "Fax");
    expect(screen.getByText("Especifica (3/150)")).toBeInTheDocument();
  });

  it("«Otro» sin texto libre bloquea el guardado con error", async () => {
    const user = userEvent.setup();
    renderModal();
    await user.selectOptions(screen.getByRole("combobox", { name: /Resultado/i }), "other");
    await user.click(screen.getByRole("button", { name: /Guardar/i }));
    expect(
      screen.getByText(/obligatorio con «Otro»/i),
    ).toBeInTheDocument();
    expect(createCallLog).not.toHaveBeenCalled();
  });

  it("el toggle «Acciones tras la llamada» expande el sub-formulario", async () => {
    const user = userEvent.setup();
    renderModal();
    expect(screen.queryByText(/Cambiar pipeline/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Acciones tras la llamada/i }));
    expect(screen.getByText(/Cambiar pipeline/i)).toBeInTheDocument();
  });

  it("guarda una llamada válida llamando a createCallLog", async () => {
    const user = userEvent.setup();
    const { props } = renderModal();
    await user.click(screen.getByRole("button", { name: /Guardar/i }));
    await waitFor(() => expect(createCallLog).toHaveBeenCalledTimes(1));
    const [contactId, payload] = (createCallLog as jest.Mock).mock.calls[0];
    expect(contactId).toBe("c-1");
    expect(payload.result_code).toBe("contacted");
    expect(props.onSaved).toHaveBeenCalled();
  });

  it("«Ajustar star score» manda la valoración elegida (CRM-1)", async () => {
    const user = userEvent.setup();
    // Sin valoración previa → solo aparece el selector nuevo, sin ambigüedad.
    renderModal({ currentStarRating: 0 });
    await user.click(screen.getByRole("button", { name: /Acciones tras la llamada/i }));

    // No hay selector de estrellas hasta marcar la acción.
    expect(screen.queryByTestId("star-rating")).not.toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: /Ajustar star score/i }));

    await user.click(screen.getByRole("button", { name: "4 estrellas" }));
    await user.click(screen.getByRole("button", { name: /Guardar/i }));

    await waitFor(() => expect(createCallLog).toHaveBeenCalled());
    expect((createCallLog as jest.Mock).mock.calls[0][1].actions.adjust_star_score)
      .toBe(4);
  });

  it("sin marcar «Ajustar star score» no manda valoración", async () => {
    const user = userEvent.setup();
    renderModal({ currentStarRating: 3 });
    await user.click(screen.getByRole("button", { name: /Guardar/i }));
    await waitFor(() => expect(createCallLog).toHaveBeenCalled());
    expect((createCallLog as jest.Mock).mock.calls[0][1].actions.adjust_star_score)
      .toBeNull();
  });
});
