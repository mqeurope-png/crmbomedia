import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReportExceptionModal } from "./ReportExceptionModal";

describe("ReportExceptionModal", () => {
  it("muestra subtipos solo cuando el tipo los tiene (stock_shortage)", async () => {
    const user = userEvent.setup();
    render(<ReportExceptionModal onSubmit={() => {}} onClose={() => {}} />);
    // stock_shortage es el primero → subtipo visible.
    expect(screen.getByLabelText("Subtipo")).toBeInTheDocument();
    // Cambiar a sat_issue (sin subtipos) → desaparece.
    await user.selectOptions(screen.getByLabelText("Tipo de incidencia"), "sat_issue");
    expect(screen.queryByLabelText("Subtipo")).not.toBeInTheDocument();
  });

  it("envía tipo + subtipo + descripción", async () => {
    const onSubmit = jest.fn();
    const user = userEvent.setup();
    render(<ReportExceptionModal onSubmit={onSubmit} onClose={() => {}} />);
    await user.selectOptions(screen.getByLabelText("Subtipo"), "eta_unknown");
    await user.type(screen.getByLabelText("Descripción del problema"), "sin fecha");
    await user.click(screen.getByRole("button", { name: /Reportar y bloquear/ }));
    expect(onSubmit).toHaveBeenCalledWith({
      type: "stock_shortage",
      subtype: "eta_unknown",
      description: "sin fecha",
    });
  });

  it("para un tipo sin subtipos envía subtype undefined", async () => {
    const onSubmit = jest.fn();
    const user = userEvent.setup();
    render(<ReportExceptionModal onSubmit={onSubmit} onClose={() => {}} />);
    await user.selectOptions(screen.getByLabelText("Tipo de incidencia"), "material_defective");
    await user.click(screen.getByRole("button", { name: /Reportar y bloquear/ }));
    expect(onSubmit).toHaveBeenCalledWith({
      type: "material_defective",
      subtype: undefined,
      description: "",
    });
  });

  it("cancelar llama onClose", async () => {
    const onClose = jest.fn();
    const user = userEvent.setup();
    render(<ReportExceptionModal onSubmit={() => {}} onClose={onClose} />);
    await user.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(onClose).toHaveBeenCalled();
  });
});
