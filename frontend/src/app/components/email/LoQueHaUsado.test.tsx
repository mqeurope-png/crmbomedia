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

  it("cuenta las máquinas que entran por las palabras de la consulta", () => {
    render(<LoQueHaUsado borrador={borrador({ mensajes_hilo: 1, contacto: { nombre: "Ana" },
      catalogo: { maquinas: [{ nombre: "Orion 20", eleccion: "consulta" },
        { nombre: "artisJet 5000U", eleccion: "interes" }], consumibles: [], sin_maquina: [] } })} />);
    expect(screen.getByText(/2 máquinas del catálogo \(1 por las palabras de la consulta\)/))
      .toBeInTheDocument();
  });

  it("dice que leyó la consulta del formulario del lead, con el día de Madrid", () => {
    // Las 00:30 del 13/10 en Madrid son las 22:30 del 12/10 en UTC.
    render(<LoQueHaUsado borrador={borrador({ mensajes_hilo: 1, contacto: { nombre: "Ana" },
      consulta_lead: { fuente: "web_form", fecha: "2026-10-12T22:30:00+00:00", caracteres: 120 } })} />);
    expect(screen.getByText(/1 mensaje del hilo · la consulta de su formulario \(13\/10\/2026\) · la ficha de Ana/))
      .toBeInTheDocument();
  });

  it("en una corrección explica la caché: leída, o guardada en la de otro modelo", () => {
    const leida = { ...borrador({ contacto: { nombre: "Ana" } }), turno: 2,
      tokens_cache_lectura: 6446, modelo: "claude-haiku-5-5" };
    const { unmount } = render(<LoQueHaUsado borrador={leida} />);
    expect(screen.getByText(/turno 2: el contexto no se volvió a montar \(6\.?446 tokens leídos de la caché\)/))
      .toBeInTheDocument();
    unmount();
    const escrita = { ...borrador({ contacto: { nombre: "Ana" } }), turno: 1,
      tokens_cache_escritura: 6446, modelo: "claude-haiku-5-5" };
    render(<LoQueHaUsado borrador={escrita} />);
    expect(screen.getByText(/6\.?446 tokens guardados en la caché de claude-haiku-5-5: la caché es de cada modelo/))
      .toBeInTheDocument();
  });

  it("sin nada de eso, no dice nada de más", () => {
    render(<LoQueHaUsado borrador={borrador({ mensajes_hilo: 1, contacto: { nombre: "Ana" } })} />);
    const texto = screen.getByText(/la ficha de Ana/).textContent ?? "";
    expect(texto).not.toMatch(/nota|campo|etiqueta|teléfono/);
  });
});
