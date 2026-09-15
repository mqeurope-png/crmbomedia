import { render, screen, within } from "@testing-library/react";
import { PrimaryActionBar } from "./PrimaryActionBar";

/** Lote 2 · E7 — barra de acción principal: región con nombre, la pista de
 *  consecuencia y los botones dentro de `.erp-primary-sticky` (en móvil el
 *  CSS la pega abajo con el primario a 48 px). */

describe("PrimaryActionBar", () => {
  it("es una región «Acción principal» con los botones dentro", () => {
    render(
      <PrimaryActionBar>
        <button type="button" className="button">Registrar cobro</button>
        <button type="button" className="button secondary">Reclamar por email</button>
      </PrimaryActionBar>,
    );
    const region = screen.getByRole("region", { name: "Acción principal" });
    expect(region).toHaveClass("erp-primary-sticky");
    const actions = within(region).getAllByRole("button");
    expect(actions.map((b) => b.textContent)).toEqual(["Registrar cobro", "Reclamar por email"]);
    expect(actions[0].closest(".erp-primary-sticky-actions")).not.toBeNull();
    expect(screen.queryByText(/FACTUSOL/)).not.toBeInTheDocument();
  });

  it("muestra la consecuencia escrita, y admite nombre y clase propios", () => {
    render(
      <PrimaryActionBar hint="Se creará en FACTUSOL al guardar." label="Crear pedido" className="extra">
        <button type="button" className="button">Crear pedido</button>
      </PrimaryActionBar>,
    );
    const region = screen.getByRole("region", { name: "Crear pedido" });
    expect(region).toHaveClass("erp-primary-sticky", "extra");
    expect(within(region).getByText("Se creará en FACTUSOL al guardar.")).toHaveClass("erp-primary-sticky-hint");
  });
});
