import { render, screen } from "@testing-library/react";
import type { BorradorAsistente } from "../../lib/emailsApi";
import { LoQueHaUsado } from "./AsistenteFranja";

function borrador(contexto: Record<string, unknown>): BorradorAsistente {
  return {
    alcance: "contacto",
    turno: 0,
    tokens_cache_lectura: 0,
    tokens_cache_escritura: 0,
    enlaces: [],
    contexto,
  } as unknown as BorradorAsistente;
}

describe("Lo que ha usado", () => {
  it("cuenta lo que entró de la ficha: notas, campos, etiquetas y teléfonos", () => {
    render(
      <LoQueHaUsado
        borrador={borrador({
          mensajes_hilo: 2,
          contacto: { nombre: "Klaus Barquito", empresa: "El Barquito SL" },
          notas: 3,
          campos_personalizados: 1,
          etiquetas: 2,
          telefonos: 0,
          pedidos: 1,
        })}
      />,
    );
    expect(
      screen.getByText(
        /la ficha de Klaus Barquito \(El Barquito SL\) · 3 notas internas, 1 campo personalizado, 2 etiquetas · 1 pedido/,
      ),
    ).toBeInTheDocument();
  });

  it("sin nada de eso, no dice nada de más", () => {
    render(<LoQueHaUsado borrador={borrador({ mensajes_hilo: 1, contacto: { nombre: "Ana" } })} />);
    const texto = screen.getByText(/la ficha de Ana/).textContent ?? "";
    expect(texto).not.toMatch(/nota|campo|etiqueta|teléfono/);
  });
});
