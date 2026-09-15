import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CompanyPickerModal } from "./CompanyPickerModal";

// Buscador y formulario tienen sus propios tests: aquí importa que el modal
// esté bien montado (molde del ERP, ancho fijo) y que enlace elegir / crear.
jest.mock("./CompanySearch", () => ({
  CompanySearch: ({ onPick, onCreate }: {
    onPick: (c: { id: string; name: string }) => void; onCreate: (n: string) => void;
  }) => (
    <div>
      <button type="button" onClick={() => onPick({ id: "maison", name: "La Maison" })}>elegir maison</button>
      <button type="button" onClick={() => onCreate("rotulacion")}>crear nueva</button>
    </div>
  ),
}));
jest.mock("./CompanyCreateForm", () => ({
  CompanyCreateForm: ({ initialName, onCreated, onCancel, onUseExisting }: {
    initialName: string;
    onCreated: (r: { company: { id: string; name: string }; factusol: null; factusolError: string | null }) => void;
    onCancel: () => void;
    onUseExisting: (c: { id: string; name: string }) => void;
  }) => (
    <div>
      <span>FORM {initialName}</span>
      <button type="button" onClick={() => onCreated({
        company: { id: "c-new", name: initialName }, factusol: null, factusolError: null,
      })}>guardar</button>
      <button type="button" onClick={() => onUseExisting({ id: "dupli", name: "Duplicoder SL" })}>usar existente</button>
      <button type="button" onClick={onCancel}>volver</button>
    </div>
  ),
}));

describe("CompanyPickerModal — «Asignar empresa» (molde ERP, 720 px fijos)", () => {
  it("usa el modal estándar (un solo overlay) y elige con el buscador unificado", async () => {
    const onPick = jest.fn();
    const onClose = jest.fn();
    const user = userEvent.setup();
    render(<CompanyPickerModal open onClose={onClose} onPick={onPick} />);
    const dialog = screen.getByRole("dialog", { name: /Asignar empresa/ });
    expect(dialog.className).toContain("modal-dialog");
    expect(dialog.parentElement?.className).toContain("modal-overlay");
    expect(document.querySelector(".email-compose-overlay")).toBeNull();
    expect(document.querySelector(".modal-backdrop")).toBeNull();
    await user.click(screen.getByRole("button", { name: "elegir maison" }));
    expect(onPick).toHaveBeenCalledWith("maison", "La Maison");
    expect(onClose).toHaveBeenCalled();
  });

  it("ancho fijo del molde (`erp-modal wide`) buscando y creando; la lista y el formulario van en `.modal-body`", async () => {
    const user = userEvent.setup();
    render(<CompanyPickerModal open onClose={() => {}} onPick={() => {}} />);
    let dialog = screen.getByRole("dialog", { name: /Asignar empresa/ });
    expect(dialog).toHaveClass("erp-modal", "wide", "company-picker-dialog", "is-searching");
    expect(dialog.querySelector(":scope > .modal-body")).not.toBeNull();
    expect(dialog.querySelector(":scope > .modal-body")).toContainElement(
      screen.getByRole("button", { name: "elegir maison" }),
    );
    await user.click(screen.getByRole("button", { name: "crear nueva" }));
    dialog = screen.getByRole("dialog", { name: /Crear empresa/ });
    // Mismo ancho: no cambia de tamaño al pasar a crear (ni al llegar resultados).
    expect(dialog).toHaveClass("erp-modal", "wide", "company-picker-dialog", "is-creating");
    expect(dialog).not.toHaveClass("is-searching");
    expect(dialog.querySelector(":scope > .modal-body")).toContainElement(
      screen.getByText("FORM rotulacion"),
    );
  });

  it("«Crear empresa «…»» abre el formulario completo con lo escrito y al guardar la elige", async () => {
    const onPick = jest.fn();
    const onClose = jest.fn();
    const user = userEvent.setup();
    render(<CompanyPickerModal open onClose={onClose} onPick={onPick} />);
    await user.click(screen.getByRole("button", { name: "crear nueva" }));
    expect(screen.getByRole("dialog", { name: /Crear empresa/ })).toBeInTheDocument();
    expect(screen.getByText("FORM rotulacion")).toBeInTheDocument();      // lo escrito se arrastra
    await user.click(screen.getByRole("button", { name: "volver" }));
    expect(screen.getByRole("dialog", { name: /Asignar empresa/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "crear nueva" }));
    await user.click(screen.getByRole("button", { name: "guardar" }));
    expect(onPick).toHaveBeenCalledWith("c-new", "rotulacion");
    expect(onClose).toHaveBeenCalled();
  });

  it("«Usar esta» sobre una candidata duplicada la elige sin crear nada", async () => {
    const onPick = jest.fn();
    const onClose = jest.fn();
    const user = userEvent.setup();
    render(<CompanyPickerModal open onClose={onClose} onPick={onPick} />);
    await user.click(screen.getByRole("button", { name: "crear nueva" }));
    await user.click(screen.getByRole("button", { name: "usar existente" }));
    expect(onPick).toHaveBeenCalledWith("dupli", "Duplicoder SL");
    expect(onClose).toHaveBeenCalled();
  });

  it("cerrado no pinta nada; Escape y ✕ cierran", async () => {
    const onClose = jest.fn();
    const { rerender } = render(<CompanyPickerModal open={false} onClose={onClose} onPick={() => {}} />);
    expect(screen.queryByRole("dialog")).toBeNull();
    rerender(<CompanyPickerModal open onClose={onClose} onPick={() => {}} />);
    const user = userEvent.setup();
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Cerrar" }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
