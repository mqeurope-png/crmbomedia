import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { InteresesPicker } from "./InteresesPicker";

const OPCIONES = [
  { id: "uv_mediano", label: "UV LED mediano formato", comercial: true, activo: true },
  { id: "dtf", label: "DTF · impresión textil", comercial: true, activo: true },
  { id: "vending", label: "Vending", comercial: true, activo: true },
  { id: "laser_cnc", label: "Láser y CNC", comercial: true, activo: false },
  { id: "otro", label: "Otro", comercial: false, activo: true },
];

function pintar(value: string[], over: Partial<React.ComponentProps<typeof InteresesPicker>> = {}) {
  const onChange = jest.fn();
  render(
    <InteresesPicker
      value={value}
      onChange={onChange}
      opciones={OPCIONES}
      labelPrincipal="Interés de Ana"
      sujeto="Ana"
      {...over}
    />,
  );
  return onChange;
}

describe("InteresesPicker (varios intereses, el primero el principal)", () => {
  it("con un solo interés es el desplegable de siempre, y añadir otro lo deja detrás", async () => {
    const user = userEvent.setup();
    const onChange = pintar(["vending"]);
    const principal = screen.getByLabelText("Interés de Ana") as HTMLSelectElement;
    expect(principal).toHaveValue("vending");
    // Sin secundarios no hay chips ni marca de «principal».
    expect(screen.queryByText("principal")).not.toBeInTheDocument();
    // Un interés desactivado no se ofrece si no está ya en la lista.
    expect(Array.from(principal.options).map((o) => o.value)).not.toContain("laser_cnc");
    await user.selectOptions(screen.getByLabelText("Añadir interés a Ana"), "dtf");
    expect(onChange).toHaveBeenLastCalledWith(["vending", "dtf"]);
    await user.selectOptions(principal, "otro");
    expect(onChange).toHaveBeenLastCalledWith(["otro"]);
  });

  it("los secundarios son chips con subir y quitar, en su orden", async () => {
    const user = userEvent.setup();
    const onChange = pintar(["uv_mediano", "dtf", "vending"]);
    expect(screen.getByLabelText("Interés de Ana")).toHaveValue("uv_mediano");
    expect(screen.getByText("principal")).toBeInTheDocument();
    expect(screen.getByText("DTF · impresión textil")).toBeInTheDocument();
    expect(screen.getByText("Vending")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Subir DTF · impresión textil en los intereses de Ana" }));
    expect(onChange).toHaveBeenLastCalledWith(["dtf", "uv_mediano", "vending"]);
    await user.click(screen.getByRole("button", { name: "Quitar Vending de los intereses de Ana" }));
    expect(onChange).toHaveBeenLastCalledWith(["uv_mediano", "dtf"]);
    // Lo que ya está en la lista no se ofrece para añadir; lo demás, sí.
    const anadir = screen.getByLabelText("Añadir interés a Ana") as HTMLSelectElement;
    expect(Array.from(anadir.options).map((o) => o.value)).toEqual(["", "otro"]);
    // El desplegable del principal SUSTITUYE al principal: no ofrece los
    // secundarios (para subir uno está la flecha).
    const principal = screen.getByLabelText("Interés de Ana") as HTMLSelectElement;
    expect(Array.from(principal.options).map((o) => o.value)).toEqual(["uv_mediano", "otro"]);
    await user.selectOptions(principal, "otro");
    expect(onChange).toHaveBeenLastCalledWith(["otro", "dtf", "vending"]);
  });

  it("un interés desactivado que ya lleva el lead se sigue viendo, marcado", () => {
    pintar(["laser_cnc"]);
    const principal = screen.getByLabelText("Interés de Ana") as HTMLSelectElement;
    expect(principal).toHaveValue("laser_cnc");
    expect(principal.options[principal.selectedIndex].textContent).toBe("Láser y CNC (desactivado)");
  });

  it("vacío (una combinación nueva) arranca en «—» y no ofrece añadir hasta tener principal", async () => {
    const user = userEvent.setup();
    const onChange = pintar([], { labelPrincipal: "Intereses de la combinación nueva",
                                 sujeto: "la combinación nueva", permitirVacio: true });
    expect(screen.getByLabelText("Intereses de la combinación nueva")).toHaveValue("");
    expect(screen.queryByLabelText("Añadir interés a la combinación nueva")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Intereses de la combinación nueva"), "vending");
    expect(onChange).toHaveBeenLastCalledWith(["vending"]);
  });
});
