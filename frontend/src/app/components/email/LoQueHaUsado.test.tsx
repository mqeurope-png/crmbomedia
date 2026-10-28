import { render, screen, within } from "@testing-library/react";
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
    expect(texto).not.toMatch(/nota|campo|etiqueta|teléfono|Oferta/);
  });

  it("con una oferta adjunta la nombra, con sus líneas y su total", () => {
    render(<LoQueHaUsado borrador={borrador({
      sin_hilo: true, contacto: { nombre: "Ana", empresa: "Rótulos Ana SL" },
      oferta: { id: "o-1", numero: "OF-2026-0001", fecha: "12/10/2026", valida_hasta: "11/11/2026",
        total: 1318.9, con_iva: true, estado: "borrador", emisora: "Bomedia", lineas: 3 },
    })} />);
    expect(screen.getByText(
      /sin hilo \(correo nuevo\) · la ficha de Ana \(Rótulos Ana SL\) · Oferta OF-2026-0001 adjunta \(3 líneas, total 1\.?318,90 €\)/,
    )).toBeInTheDocument();
  });

  it("una oferta de una sola línea va en singular", () => {
    render(<LoQueHaUsado borrador={borrador({
      contacto: { nombre: "Ana" },
      oferta: { id: "o-2", numero: "OF-2026-0002", total: 250, lineas: 1 },
    })} />);
    expect(screen.getByText(/Oferta OF-2026-0002 adjunta \(1 línea, total 250,00 €\)/)).toBeInTheDocument();
  });
});

describe("Lo que ha usado — lo que consultó solo en webs y soporte", () => {
  const FLUX = "FLUX · centro de ayuda";

  /** El `material` tal como lo apunta el backend con las casillas marcadas. */
  function automatico(over: Record<string, unknown> = {}): Record<string, unknown> {
    return {
      mensajes_hilo: 1, contacto: { nombre: "Ana" },
      material: {
        automatico: true, fuentes: { webs: true, soporte: true },
        consulta: { tecnica: true, marca: "flux", soporte_en: "beamo laser tube replacement",
          soporte: "beamo cambiar tubo láser", webs: "beamo", origen: "ia", modelo: "claude-haiku-5-5",
          error: null, plan_ms: 900, coste_usd: 0.0004 },
        ms: 1400, material_usado: null, ...over,
      },
    };
  }

  function lista() {
    return screen.getByRole("list", { name: "Lo que trajo solo de webs y soporte" });
  }

  it("el artículo, los otros artículos y las páginas, con su enlace, su fuente y si los usó", () => {
    render(<LoQueHaUsado borrador={borrador(automatico({
      soporte: { ref: "S1", fuente: "flux", fuente_nombre: FLUX, articulo_id: "4410",
        titulo: "Cambiar el tubo láser del beamo", url: "https://support.flux3dp.com/hc/es/articles/4410",
        idioma: "es", recortado: false, parece_orden: false, usado: true },
      otros_soporte: [{ ref: "S2", fuente: "flux", fuente_nombre: FLUX, articulo_id: "4411",
        titulo: "Alinear el láser", url: "https://support.flux3dp.com/hc/es/articles/4411", idioma: "es",
        extracto: "Antes de alinear…", usado: false }],
      webs: [
        { ref: "W1", id: "w-1", titulo: "beamo", url: "https://www.fluxlasers.eu/es/producto/beamo/",
          sitio: "fluxlasers", dominio: "fluxlasers.eu", tipo: "producto", idioma: "es",
          precio_web_texto: "1.990,00 €", precio_iva: "sin_iva", usado: true },
        { ref: "W2", id: "w-9", titulo: "Rara", url: "javascript:alert(1)", sitio: "fluxlasers",
          dominio: "fluxlasers.eu", tipo: "pagina", idioma: "es", precio_web_texto: null, precio_iva: null,
          usado: false },
      ],
      buscado_en_soporte: ["beamo laser tube replacement"], buscado_en_webs: "beamo", material_usado: ["S1", "W1"],
      enlace_anadido: true,
    }))} />);
    expect(screen.getByText(/Lo que ha usado/)).toHaveTextContent(
      "Lo que ha usado (alcance: todo lo de este contacto) · consultó soporte y webs");
    expect(screen.getByText(
      /consultó el soporte \(FLUX · centro de ayuda\): el artículo «Cambiar el tubo láser del beamo» · 2 páginas de nuestras webs/,
    )).toBeInTheDocument();
    const filas = within(lista()).getAllByRole("listitem");
    expect(filas[0]).toHaveTextContent(
      "Soporte: Cambiar el tubo láser del beamo — FLUX · centro de ayuda · usado en el borrador · su enlace se ha añadido al final del correo");
    expect(within(filas[0]).getByRole("link", { name: "Cambiar el tubo láser del beamo" })).toHaveAttribute(
      "href", "https://support.flux3dp.com/hc/es/articles/4410");
    expect(within(filas[0]).getByRole("link")).toHaveAttribute("target", "_blank");
    expect(within(filas[0]).getByRole("link")).toHaveAttribute("rel", "noreferrer");
    expect(filas[1]).toHaveTextContent(
      "Otro artículo del soporte: Alinear el láser — FLUX · centro de ayuda · solo enlazable · no lo usó");
    expect(within(filas[1]).getByRole("link", { name: "Alinear el láser" })).toBeInTheDocument();
    // El precio es el de la web, dicho así: nunca el de una oferta.
    expect(filas[2]).toHaveTextContent(
      "Web: beamo — fluxlasers.eu · producto · precio de la web: 1.990,00 € (sin IVA) · usado en el borrador");
    expect(within(filas[2]).getByRole("link", { name: "beamo" })).toHaveAttribute(
      "href", "https://www.fluxlasers.eu/es/producto/beamo/");
    // Solo enlaces web: lo demás se enseña sin enlace.
    expect(filas[3]).toHaveTextContent("Web: Rara — fluxlasers.eu · página · no lo usó");
    expect(within(filas[3]).queryByRole("link")).not.toBeInTheDocument();
    expect(filas[4]).toHaveTextContent(
      "Buscó «beamo laser tube replacement» en el soporte · «beamo» en las webs — la preparó la IA, en 1,4 s");
    expect(lista()).not.toHaveTextContent(/oferta/i);
  });

  it("sin procedimiento en el soporte, y unas webs que no respondieron; la búsqueda la armaron las palabras", () => {
    render(<LoQueHaUsado borrador={borrador(automatico({
      consulta: { tecnica: true, marca: "flux", soporte_en: "hexa error 12", soporte: "hexa error 12",
        webs: "HEXA", origen: "palabras", error: "APITimeoutError: Request timed out.", plan_ms: 5000 },
      sin_resultados: { consulta: "hexa error 12" }, buscado_en_soporte: ["hexa error 12"], buscado_en_webs: "HEXA",
      no_consultado: [{ fuente: "webs", motivo: "OperationalError" }], ms: 5200, material_usado: [],
    }))} />);
    expect(screen.getByText(/Lo que ha usado/)).toHaveTextContent("· consultó el soporte");
    expect(screen.getByText(/sin procedimiento en el soporte para «hexa error 12» · no se pudieron consultar las webs/))
      .toBeInTheDocument();
    expect(lista()).toHaveTextContent(
      "Sin procedimiento en el soporte para «hexa error 12» — el borrador lo dice, sin pasos");
    expect(lista()).toHaveTextContent("No se han podido consultar nuestras webs — OperationalError");
    expect(lista()).toHaveTextContent(
      "Buscó «hexa error 12» en el soporte · «HEXA» en las webs — con las palabras del correo (la IA no pudo prepararla), en 5,2 s");
  });

  it("el soporte que no respondió lo dice, y si el borrador trae pasos pide revisarlo", () => {
    render(<LoQueHaUsado borrador={borrador(automatico({
      fuentes: { webs: false, soporte: true },
      no_consultado: [{ fuente: "soporte", motivo: "FLUX · centro de ayuda: no responde a tiempo" }],
      pasos_sin_procedimiento: true,
    }))} />);
    // No consultó nada: el título plegado no presume de nada.
    expect(screen.getByText(/Lo que ha usado/).textContent?.trim())
      .toBe("Lo que ha usado (alcance: todo lo de este contacto)");
    expect(screen.getByText(/no se pudo consultar el soporte/)).toBeInTheDocument();
    expect(lista()).toHaveTextContent(
      "No se ha podido consultar el soporte — FLUX · centro de ayuda: no responde a tiempo");
    expect(lista()).toHaveTextContent(
      "El borrador trae pasos que no salen de ningún artículo del soporte — revísalo");
    // Con «Webs» desmarcada, de las webs no dice nada.
    expect(lista()).not.toHaveTextContent(/webs/i);
  });

  it("una marca sin centro de ayuda en la lista, y un correo que no pregunta por ningún producto", () => {
    render(<LoQueHaUsado borrador={borrador(automatico({
      consulta: { tecnica: true, marca: "artisjet", soporte_en: "artisjet nozzle check", soporte: "",
        webs: null, origen: "ia" },
      sin_centro: "artisjet",
    }))} />);
    expect(screen.getByText(/la ficha de Ana · artisjet no tiene centro de ayuda en la lista\./)).toBeInTheDocument();
    expect(lista()).toHaveTextContent("Soporte: artisjet no tiene centro de ayuda en la lista — no se ha consultado");
    expect(lista()).toHaveTextContent("Webs: no se han consultado — el correo no pregunta por ningún producto");
    // Ni en el soporte ni en las webs se buscó nada.
    expect(lista()).not.toHaveTextContent("Buscó");
  });

  it("un correo que no es técnico no va al soporte; las webs, sin nada para lo que pregunta", () => {
    render(<LoQueHaUsado borrador={borrador(automatico({
      consulta: { tecnica: false, marca: "", soporte_en: null, soporte: null, webs: "plancha vapor",
        origen: "ia" },
      buscado_en_webs: "plancha vapor", webs_sin_resultados: "plancha vapor",
    }))} />);
    expect(screen.getByText(/Lo que ha usado/)).toHaveTextContent("· consultó las webs");
    expect(screen.getByText(/webs: nada para «plancha vapor»/)).toBeInTheDocument();
    expect(lista()).toHaveTextContent("Soporte: no se ha consultado — el correo no es técnico");
    expect(lista()).toHaveTextContent("Webs: nada en el índice para «plancha vapor»");
    expect(lista()).toHaveTextContent("Buscó «plancha vapor» en las webs — la preparó la IA, en 1,4 s");
  });

  it("una corrección dice que reutiliza lo del primer borrador, sin volver a buscar", () => {
    render(<LoQueHaUsado borrador={borrador(automatico({
      consulta: { tecnica: false, webs: "beamo", origen: "ia", reutilizado: true },
      fuentes: { webs: true, soporte: false }, buscado_en_webs: "beamo",
      webs: [{ ref: "W1", id: "w-1", titulo: "beamo", url: "https://www.fluxlasers.eu/es/producto/beamo/",
        dominio: "fluxlasers.eu", tipo: "producto", usado: null }],
    }))} />);
    expect(lista()).toHaveTextContent("Web: beamo — fluxlasers.eu · producto");
    expect(lista()).not.toHaveTextContent("usado");
    expect(lista()).toHaveTextContent(
      "Buscó «beamo» en las webs — la del primer borrador: esta corrección no ha vuelto a buscar");
  });

  it("una corrección que no buscó en el soporte no lo da por buscado aunque el plan y la casilla lo pidan", () => {
    // El primer borrador fue con «Soporte» desmarcada: el plan salió técnico,
    // pero no se buscó. Ahora la casilla va marcada y no consta búsqueda.
    render(<LoQueHaUsado borrador={borrador(automatico({
      consulta: { tecnica: true, marca: "flux", soporte_en: "beamo laser tube replacement",
        soporte: "beamo cambiar tubo láser", webs: "beamo", origen: "ia", reutilizado: true },
      buscado_en_webs: "beamo",
      webs: [{ ref: "W1", id: "w-1", titulo: "beamo", url: "https://www.fluxlasers.eu/es/producto/beamo/",
        dominio: "fluxlasers.eu", tipo: "producto", usado: null }],
      ms: undefined,
    }))} />);
    expect(lista()).not.toHaveTextContent("en el soporte");
    expect(lista()).not.toHaveTextContent("beamo laser tube replacement");
    expect(lista()).toHaveTextContent(
      "Buscó «beamo» en las webs — la del primer borrador: esta corrección no ha vuelto a buscar");
    // Un correo técnico con «Soporte» marcada y nada buscado lo dice.
    expect(lista()).toHaveTextContent(
      "Soporte: no se ha consultado — no hay ningún procedimiento del fabricante que citar");
    expect(screen.getByText(/no se consultó el soporte/)).toBeInTheDocument();
    expect(screen.getByText(/Lo que ha usado/)).toHaveTextContent("· consultó las webs");
  });

  it("una corrección que busca ahora en el soporte lo dice, y lo del primer borrador como tal", () => {
    render(<LoQueHaUsado borrador={borrador(automatico({
      consulta: { tecnica: true, marca: "flux", soporte_en: "beamo laser tube replacement",
        soporte: "beamo cambiar tubo láser", webs: "beamo", origen: "ia", reutilizado: true },
      buscado_en_soporte: ["beamo laser tube replacement", "beamo cambiar tubo láser"], soporte_ms: 900,
      sin_resultados: { consulta: "beamo cambiar tubo láser" },
      buscado_en_webs: "beamo", ms: undefined,
    }))} />);
    expect(lista()).toHaveTextContent(
      "Buscó «beamo laser tube replacement / beamo cambiar tubo láser» en el soporte — en esta corrección, "
      + "con la búsqueda del primer borrador; «beamo» en las webs — la del primer borrador: esta corrección no ha "
      + "vuelto a buscar");
    expect(lista()).not.toHaveTextContent("Soporte: no se ha consultado");
  });

  it("las webs, igual: sin búsqueda apuntada no dice que buscó; buscadas ahora, lo dice", () => {
    const reutilizada = { tecnica: false, marca: "", soporte_en: null, soporte: null, webs: "beamo",
      origen: "ia", reutilizado: true };
    const view = render(<LoQueHaUsado borrador={borrador(automatico({
      consulta: reutilizada, fuentes: { webs: true, soporte: false }, ms: undefined,
    }))} />);
    expect(screen.queryByText(/Buscó/)).not.toBeInTheDocument();
    view.unmount();
    // El primer borrador, igual: el plan no es la búsqueda.
    const { unmount } = render(<LoQueHaUsado borrador={borrador(automatico({
      consulta: { ...reutilizada, reutilizado: false }, fuentes: { webs: true, soporte: false },
    }))} />);
    expect(screen.queryByText(/Buscó/)).not.toBeInTheDocument();
    unmount();
    render(<LoQueHaUsado borrador={borrador(automatico({
      consulta: reutilizada, fuentes: { webs: true, soporte: false }, ms: undefined,
      buscado_en_webs: "beamo", webs_ms: 300, webs_sin_resultados: "beamo",
    }))} />);
    expect(lista()).toHaveTextContent(
      "Buscó «beamo» en las webs — en esta corrección, con la búsqueda del primer borrador");
    expect(lista()).not.toHaveTextContent("no ha vuelto a buscar");
  });

  it("sin el plan no hay nada que contar: no pinta una lista vacía", () => {
    render(<LoQueHaUsado borrador={borrador(automatico({ consulta: undefined, ms: undefined }))} />);
    expect(screen.queryByRole("list", { name: "Lo que trajo solo de webs y soporte" })).not.toBeInTheDocument();
  });

  it("un borrador de antes (lo elegido con los botones) se sigue viendo como entonces", () => {
    render(<LoQueHaUsado borrador={borrador({
      mensajes_hilo: 2, contacto: { nombre: "Klaus" },
      material: {
        soporte: { fuente: "flux", fuente_nombre: "FLUX", articulo_id: "4410",
          titulo: "Cambiar el tubo láser del beamo", url: "https://support.flux3dp.com/hc/es/articles/4410" },
        webs: [
          { id: "w-1", titulo: "beamo", url: "https://www.fluxlasers.eu/de/produkt/beamo/" },
          { id: "w-9", titulo: "Rara", url: "javascript:alert(1)" },
        ],
      },
    })} />);
    const usado = screen.getByRole("list", { name: "Material de fuera que entró" });
    expect(within(usado).getByRole("link", { name: "Cambiar el tubo láser del beamo" })).toHaveAttribute(
      "href", "https://support.flux3dp.com/hc/es/articles/4410");
    expect(within(usado).getByRole("link", { name: "beamo" })).toBeInTheDocument();
    expect(within(usado).queryByRole("link", { name: "Rara" })).not.toBeInTheDocument();
    expect(usado).toHaveTextContent("Web: Rara");
    expect(screen.getByText(/el artículo de soporte «Cambiar el tubo láser del beamo» \(FLUX\)/)).toBeInTheDocument();
    expect(screen.getByText(/2 páginas de nuestras webs/)).toBeInTheDocument();
    expect(screen.getByText(/Lo que ha usado/)).not.toHaveTextContent("consultó");
    expect(screen.queryByRole("list", { name: "Lo que trajo solo de webs y soporte" })).not.toBeInTheDocument();
  });

  it("un borrador de antes con «sin procedimiento» lo sigue diciendo", () => {
    render(<LoQueHaUsado borrador={borrador({
      mensajes_hilo: 2, material: { sin_resultados: { consulta: "cambiar tubo hexa" } },
    })} />);
    expect(screen.getByRole("list", { name: "Material de fuera que entró" }))
      .toHaveTextContent("Sin procedimiento en el soporte para «cambiar tubo hexa»");
  });
});
