import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ActionsMenu } from "./ActionsMenu";

/** Menú «⋯». Normal: absoluto junto al botón (tarjetas, ficha). Flotante
 *  (filas de una tabla con scroll propio): fijo junto al botón, para que el
 *  contenedor no lo recorte, y se cierra al desplazar / redimensionar. */

function Menu({ floating }: { floating?: boolean }) {
  return (
    <ActionsMenu label="Más acciones X-1" floating={floating}>
      <button type="button">Abrir ficha</button>
      <div className="lista-larga" data-testid="lista">…</div>
    </ActionsMenu>
  );
}

describe("ActionsMenu", () => {
  it("flotante: se coloca fijo bajo el botón, alineado a su derecha", async () => {
    const user = userEvent.setup();
    render(<Menu floating />);
    const trigger = screen.getByRole("button", { name: "Más acciones X-1" });
    jest.spyOn(trigger, "getBoundingClientRect").mockReturnValue({
      top: 100, bottom: 130, left: 900, right: 940, width: 40, height: 30, x: 900, y: 100,
      toJSON: () => ({}),
    } as DOMRect);
    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const pop = screen.getByRole("button", { name: "Abrir ficha" }).parentElement as HTMLElement;
    expect(pop).toHaveClass("erp-flow-menu-pop", "is-floating");
    expect(pop.style.top).toBe("134px");
    expect(pop.style.right).toBe(`${window.innerWidth - 940}px`);
    expect(pop.parentElement).toHaveClass("erp-flow-menu", "is-open");
  });

  it("flotante: se cierra al desplazar la página o la tabla y al redimensionar; no al desplazar dentro del menú", async () => {
    const user = userEvent.setup();
    render(<Menu floating />);
    const trigger = screen.getByRole("button", { name: "Más acciones X-1" });

    await user.click(trigger);
    fireEvent.scroll(screen.getByTestId("lista"));
    expect(screen.getByRole("button", { name: "Abrir ficha" })).toBeInTheDocument();
    fireEvent.scroll(document);
    expect(screen.queryByRole("button", { name: "Abrir ficha" })).toBeNull();

    await user.click(trigger);
    fireEvent(window, new Event("resize"));
    expect(screen.queryByRole("button", { name: "Abrir ficha" })).toBeNull();
  });

  it("normal (tarjetas, ficha): sin posición fija y desplazar no lo cierra", async () => {
    const user = userEvent.setup();
    render(<Menu />);
    await user.click(screen.getByRole("button", { name: "Más acciones X-1" }));
    const pop = screen.getByRole("button", { name: "Abrir ficha" }).parentElement as HTMLElement;
    expect(pop).not.toHaveClass("is-floating");
    expect(pop.style.top).toBe("");
    fireEvent.scroll(document);
    expect(screen.getByRole("button", { name: "Abrir ficha" })).toBeInTheDocument();
    // Elegir una acción lo cierra (como siempre).
    await user.click(screen.getByRole("button", { name: "Abrir ficha" }));
    expect(screen.queryByRole("button", { name: "Abrir ficha" })).toBeNull();
  });
});
