import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CompanyPickerModal } from "./CompanyPickerModal";

// Buscador y formulario tienen sus propios tests: aquí importa que el modal
// esté bien montado (overlay estándar) y que enlace elegir / crear.
jest.mock("./CompanySearch", () => ({
  CompanySearch: ({ onPick, onCreate }: {
    onPick: (c: { id: string; name: string }) => void; onCreate: (n: string) => void;
  }) => (
    <div>
      <button type="button" onClick={() => onPick({ id: "maison", name: "La Maison" })}>elegir maison</button>
      <button type="button" onClick={() => onCreate("Nueva SL")}>crear nueva</button>
    </div>
  ),
}));
jest.mock("./CompanyCreateForm", () => ({
  CompanyCreateForm: ({ initialName, onCreated, onCancel }: {
    initialName: string;
    onCreated: (r: { company: { id: string; name: string }; factusol: null; factusolError: string | null }) => void;
    onCancel: () => void;
  }) => (
    <div>
      <span>FORM {initialName}</span>
      <button type="button" onClick={() => onCreated({
        company: { id: "c-new", name: initialName }, factusol: null, factusolError: null,
      })}>guardar</button>
      <button type="button" onClick={onCancel}>volver</button>
    </div>
  ),
}));

describe("CompanyPickerModal — «Asignar empresa» arreglado", () => {
  it("usa el modal estándar (un solo overlay) y elige con el buscador unificado", async () => {
    const onPick = jest.fn();
    const onClose = jest.fn();
    const user = userEvent.setup();
    render(<CompanyPickerModal open onClose={onClose} onPick={onPick} />);
    const dialog = screen.getByRole("dialog", { name: "Asignar empresa" });
    expect(dialog.className).toContain("modal-dialog");
    expect(dialog.parentElement?.className).toContain("modal-overlay");
    expect(document.querySelector(".email-compose-overlay")).toBeNull();
    expect(document.querySelector(".modal-backdrop")).toBeNull();
    await user.click(screen.getByRole("button", { name: "elegir maison" }));
    expect(onPick).toHaveBeenCalledWith("maison", "La Maison");
    expect(onClose).toHaveBeenCalled();
  });

  it("«Crear empresa nueva» abre el formulario completo prerrellenado y al guardar la elige", async () => {
    const onPick = jest.fn();
    const onClose = jest.fn();
    const user = userEvent.setup();
    render(<CompanyPickerModal open onClose={onClose} onPick={onPick} />);
    await user.click(screen.getByRole("button", { name: "crear nueva" }));
    expect(screen.getByRole("dialog", { name: "Crear empresa" })).toBeInTheDocument();
    expect(screen.getByText("FORM Nueva SL")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "volver" }));
    expect(screen.getByRole("dialog", { name: "Asignar empresa" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "crear nueva" }));
    await user.click(screen.getByRole("button", { name: "guardar" }));
    expect(onPick).toHaveBeenCalledWith("c-new", "Nueva SL");
    expect(onClose).toHaveBeenCalled();
  });

  it("cerrado no pinta nada; Escape cierra", async () => {
    const onClose = jest.fn();
    const { rerender } = render(<CompanyPickerModal open={false} onClose={onClose} onPick={() => {}} />);
    expect(screen.queryByRole("dialog")).toBeNull();
    rerender(<CompanyPickerModal open onClose={onClose} onPick={() => {}} />);
    await userEvent.setup().keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });
});
