import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { copyText, SatObservaciones, SatTechData } from "./SatTechData";

/** Lote 2 · PR-2 — bloques de lectura del taller: observaciones del comercial
 *  (solo si hay), datos técnicos en caja con «copiar» (portapapeles o
 *  fallback) y origen como pastilla. */

/** `userEvent.setup()` instala su propio stub de portapapeles: el mock se
 *  pone DESPUÉS para que mande el del test. */
function mockClipboard(writeText: jest.Mock | undefined) {
  Object.defineProperty(navigator, "clipboard", {
    value: writeText ? { writeText } : undefined,
    configurable: true,
  });
}

afterEach(() => {
  Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
  // jsdom no implementa execCommand; cada test deja lo que puso.
  delete (document as unknown as { execCommand?: unknown }).execCommand;
});

describe("SatObservaciones", () => {
  it("pinta el bloque ámbar solo si hay nota (espacios no cuentan)", () => {
    const { rerender } = render(<SatObservaciones notes="Avisar antes de enviar." />);
    const note = screen.getByRole("note", { name: "Observaciones del comercial" });
    expect(note).toHaveClass("sat-obs");
    expect(note).toHaveTextContent("Observaciones del comercial");
    expect(note).toHaveTextContent("Avisar antes de enviar.");
    rerender(<SatObservaciones notes="   " />);
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    rerender(<SatObservaciones notes={null} />);
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });
});

describe("SatTechData", () => {
  it("cada dato en su caja, en mono grande, con botón de copiar; sin dato «—» y sin botón", () => {
    render(<SatTechData serial="FLX-7741-2026" license={null} origin="" />);
    expect(screen.getByText("Nº de serie")).toHaveClass("sat-tech-label");
    expect(screen.getByText("FLX-7741-2026")).toHaveClass("sat-tech-value");
    expect(screen.getByRole("button", { name: "Copiar nº de serie" })).toHaveClass("sat-copy-btn");
    // La caja de licencia sigue ahí (el layout no baila) pero sin botón.
    expect(screen.getByText("Licencia WhiteRIP")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copiar licencia WhiteRIP" })).not.toBeInTheDocument();
    // Origen vacío → pastilla con «—».
    expect(screen.getByText("Origen")).toBeInTheDocument();
    expect(screen.getAllByText("—")).toHaveLength(2);
  });

  it("copia con navigator.clipboard y confirma «Copiado» a la vista", async () => {
    const user = userEvent.setup();
    const writeText = jest.fn().mockResolvedValue(undefined);
    mockClipboard(writeText);
    render(<SatTechData serial="FLX-7741-2026" license="WR-4C-88231" origin="SAT" />);
    await user.click(screen.getByRole("button", { name: "Copiar licencia WhiteRIP" }));
    expect(writeText).toHaveBeenCalledWith("WR-4C-88231");
    const status = await screen.findByText("Copiado");
    expect(status).toHaveAttribute("role", "status");
    expect(screen.getByRole("button", { name: "Copiar licencia WhiteRIP" })).toHaveClass("is-done");
    expect(screen.getByText("SAT")).toHaveClass("sat-origin-pill");
  });

  it("sin navigator.clipboard cae al execCommand(\"copy\") y también confirma", async () => {
    const user = userEvent.setup();
    mockClipboard(undefined);
    const exec = jest.fn().mockReturnValue(true);
    (document as unknown as { execCommand: unknown }).execCommand = exec;
    render(<SatTechData serial="FLX-7741-2026" license={null} origin={null} />);
    await user.click(screen.getByRole("button", { name: "Copiar nº de serie" }));
    expect(exec).toHaveBeenCalledWith("copy");
    expect(await screen.findByText("Copiado")).toBeInTheDocument();
    // El textarea temporal no se queda en el DOM.
    expect(document.querySelector("textarea")).toBeNull();
  });

  it("si no se puede copiar lo dice, sin romper la caja", async () => {
    const user = userEvent.setup();
    mockClipboard(jest.fn().mockRejectedValue(new Error("denied")));
    render(<SatTechData serial="FLX-7741-2026" license={null} origin={null} />);
    await user.click(screen.getByRole("button", { name: "Copiar nº de serie" }));
    expect(await screen.findByText("No se pudo copiar")).toHaveClass("is-fail");
    expect(screen.getByText("FLX-7741-2026")).toBeInTheDocument();
  });

  it("compacto (lista): solo lo que tenga valor, y nada si no hay nada", () => {
    const { container, rerender } = render(
      <SatTechData compact serial="FLX-1" license="" origin={null} />,
    );
    expect(container.querySelector(".sat-tech")).toHaveClass("is-compact");
    expect(screen.getByText("FLX-1")).toBeInTheDocument();
    expect(screen.queryByText("Licencia WhiteRIP")).not.toBeInTheDocument();
    expect(screen.queryByText("Origen")).not.toBeInTheDocument();
    rerender(<SatTechData compact serial="" license={null} origin="  " />);
    expect(container.querySelector(".sat-tech")).toBeNull();
  });
});

describe("copyText", () => {
  it("devuelve false si no hay portapapeles ni execCommand", async () => {
    mockClipboard(undefined);
    await expect(copyText("x")).resolves.toBe(false);
  });
});
