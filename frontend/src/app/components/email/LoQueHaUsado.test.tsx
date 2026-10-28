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

describe("Lo que ha usado — las fichas del correo, del catálogo o del índice de las webs", () => {
  /** Las fichas tal como las apunta el backend en un turno compuesto. */
  const CATALOGO = {
    procedencia: "catalogo", nombre: "artisJet 3000Pro", enlace: "https://www.boprint.net/es/producto/3000pro/",
    marca: "artisJet", origen_id: "3000pro", precio_texto: "4.990,00 €", precio_origen: "catalogo",
  };
  const DESKTOP = {
    procedencia: "indice", nombre: "beamo desktop", enlace: "https://www.fluxlasers.eu/es/producto/beamo-desktop/",
    dominio: "fluxlasers.eu", sitio: "fluxlasers", origen_id: "fluxlasers:812", precio_texto: "489,30 € (+ IVA)",
    precio_origen: "web", precio_iva: "sin_iva", idioma: "es", sin_idioma_pedido: false,
    imagen: "https://www.fluxlasers.eu/img/beamo.jpg",
    galeria: ["https://www.fluxlasers.eu/img/beamo.jpg", "https://www.fluxlasers.eu/img/beamo-2.jpg",
      "https://www.fluxlasers.eu/img/beamo.jpg"],
  };

  function catalogo() {
    return screen.getByRole("list", { name: "Fichas del catálogo en el correo" });
  }

  function indice() {
    return screen.getByRole("list", { name: "Fichas del índice de las webs en el correo" });
  }

  it("separa las del catálogo y las del índice, con su enlace, y lo dice plegado", () => {
    render(<LoQueHaUsado borrador={borrador({
      mensajes_hilo: 1, contacto: { nombre: "Ana" }, fichas: [CATALOGO, DESKTOP],
    })} />);
    expect(screen.getByText(/Lo que ha usado/)).toHaveTextContent(
      "Lo que ha usado (alcance: todo lo de este contacto) · 1 ficha del índice");
    expect(screen.getByText(/2 fichas: 1 del catálogo y 1 del índice/)).toBeInTheDocument();
    const delCatalogo = within(catalogo()).getAllByRole("listitem");
    expect(delCatalogo).toHaveLength(1);
    expect(delCatalogo[0]).toHaveTextContent("Ficha del catálogo: artisJet 3000Pro — artisJet · 4.990,00 €");
    expect(within(delCatalogo[0]).getByRole("link", { name: "artisJet 3000Pro" })).toHaveAttribute(
      "href", "https://www.boprint.net/es/producto/3000pro/");
    expect(catalogo()).not.toHaveTextContent("no lleva a una página de producto");
    const delIndice = within(indice()).getAllByRole("listitem");
    expect(delIndice).toHaveLength(1);
    // El precio de la web tal como está guardado: ya dice su IVA, no se repite.
    // Nunca como el de una oferta.
    expect(delIndice[0]).toHaveTextContent(
      "Ficha del índice de fluxlasers.eu: beamo desktop — precio de la web: 489,30 € (+ IVA) · 2 imágenes en la galería");
    expect(delIndice[0]).not.toHaveTextContent("sin IVA");
    expect(indice()).not.toHaveTextContent(/oferta/i);
    const enlace = within(delIndice[0]).getByRole("link", { name: "beamo desktop" });
    expect(enlace).toHaveAttribute("href", "https://www.fluxlasers.eu/es/producto/beamo-desktop/");
    expect(enlace).toHaveAttribute("target", "_blank");
    expect(enlace).toHaveAttribute("rel", "noreferrer");
  });

  it("el precio dice siempre de dónde sale: la web (con su IVA), quien envía o «a consultar»", () => {
    render(<LoQueHaUsado borrador={borrador({
      contacto: { nombre: "Ana" },
      fichas: [
        { ...DESKTOP, nombre: "beamo", precio_texto: "1.990,00 €", precio_iva: "sin_iva", galeria: [] },
        // El IVA de la web no se le pega al precio que dijo quien envía.
        { ...DESKTOP, nombre: "HEXA", precio_texto: "1.200 €", precio_origen: "usuario" },
        { ...DESKTOP, nombre: "Ador", precio_texto: null, precio_origen: "consultar", precio_iva: null },
        // Un precio de la web vacío no se enseña como precio: a consultar.
        { ...DESKTOP, nombre: "Lazervida", precio_texto: "", precio_origen: "web" },
      ],
    })} />);
    expect(screen.getByText(/Lo que ha usado/)).toHaveTextContent("· 4 fichas del índice");
    expect(screen.getByText(/4 fichas del índice\./)).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Fichas del catálogo en el correo" })).not.toBeInTheDocument();
    const filas = within(indice()).getAllByRole("listitem");
    expect(filas[0]).toHaveTextContent("beamo — precio de la web: 1.990,00 € (sin IVA)");
    expect(filas[0]).not.toHaveTextContent("galería");
    expect(filas[1]).toHaveTextContent(
      "HEXA — precio que has indicado tú: 1.200 € (no sale de la web ni del catálogo)");
    expect(filas[1]).not.toHaveTextContent(/precio de la web|IVA/);
    expect(filas[2]).toHaveTextContent("Ador — precio a consultar (la web no da precio)");
    expect(filas[3]).toHaveTextContent("Lazervida — precio a consultar (la web no da precio)");
  });

  it("una ficha que la web no tiene en el idioma del correo lo dice, con el idioma en que va", () => {
    render(<LoQueHaUsado borrador={borrador({
      contacto: { nombre: "Ana" },
      fichas: [{ ...DESKTOP, idioma: "en", sin_idioma_pedido: true, galeria: [] }],
    })} />);
    expect(within(indice()).getByRole("listitem")).toHaveTextContent(
      "beamo desktop — precio de la web: 489,30 € (+ IVA) · en inglés: no está en el índice en el idioma del correo");
  });

  it("una ficha del catálogo cuyo enlace no es un producto de nuestras webs se avisa, resaltada; sin precio, lo dice", () => {
    render(<LoQueHaUsado borrador={borrador({
      contacto: { nombre: "Ana" },
      fichas: [{ ...CATALOGO, nombre: "artisJet 3000Pro Freebird", enlace: "https://www.boprint.net/contacto/",
        precio_texto: null, enlace_no_producto: true, enlace_motivo: "servicio" }],
    })} />);
    expect(screen.getByText(/1 ficha del catálogo\./)).toBeInTheDocument();
    // Sin fichas del índice, plegado no dice nada de fichas.
    expect(screen.getByText(/Lo que ha usado/)).not.toHaveTextContent("ficha");
    const fila = within(catalogo()).getByRole("listitem");
    expect(fila).toHaveTextContent(
      "Ficha del catálogo: artisJet 3000Pro Freebird — artisJet · sin precio publicado — el enlace va a una "
      + "página de servicio (contacto, categoría, carrito…), no a la de la máquina: revísalo en el compositor");
    expect(within(fila).getByText(/el enlace va a una página de servicio/))
      .toHaveClass("asistente-panel-aviso");
    // Se avisa; el enlace se enseña como está (no se corrige).
    expect(within(fila).getByRole("link")).toHaveAttribute("href", "https://www.boprint.net/contacto/");
  });

  it("dice por qué avisa del enlace: una página que el índice ya no tiene, o una que no tiene", () => {
    render(<LoQueHaUsado borrador={borrador({
      contacto: { nombre: "Ana" },
      fichas: [
        { ...CATALOGO, nombre: "Retirada", enlace: "https://www.boprint.net/producto/retirada/",
          enlace_no_producto: true, enlace_motivo: "ido" },
        { ...CATALOGO, nombre: "Nueva", enlace: "https://www.boprint.net/producto/nueva/",
          enlace_no_producto: true, enlace_motivo: "fuera" },
      ],
    })} />);
    const [retirada, nueva] = within(catalogo()).getAllByRole("listitem");
    expect(retirada).toHaveTextContent(
      "el enlace es el de una página que el índice de las webs ya no tiene (se quitó de la web o una regla del índice la deja fuera): revísalo");
    expect(nueva).toHaveTextContent(
      "el enlace no está en el índice de las webs (ni como producto ni como página): si la página es nueva, aún no se ha leído; si no, revísalo en el compositor");
    expect(nueva).not.toHaveTextContent("página de servicio");
  });

  it("un borrador de antes (motivo «pagina» o sin motivo) no llama de servicio a una página de la web", () => {
    render(<LoQueHaUsado borrador={borrador({
      contacto: { nombre: "Ana" },
      fichas: [{ ...CATALOGO, nombre: "Beambox II", enlace: "https://fluxlasers.eu/beambox-ii/",
        enlace_no_producto: true, enlace_motivo: "pagina" }],
    })} />);
    const fila = within(catalogo()).getByRole("listitem");
    expect(fila).toHaveTextContent("el enlace no es una página de producto de nuestras webs (comprobación de un borrador anterior: una página de la web vale)");
    expect(fila).not.toHaveTextContent("página de servicio");
  });

  it("el precio de una ficha del catálogo dice lo mismo que el correo: «a medida» u orientativo", () => {
    render(<LoQueHaUsado borrador={borrador({
      contacto: { nombre: "Ana" },
      fichas: [
        { ...CATALOGO, nombre: "A medida", precio_texto: null, precio_publico: false },
        { ...CATALOGO, nombre: "Orientativa", precio_texto: "9.990 €", orientativo: true },
      ],
    })} />);
    const [medida, orientativa] = within(catalogo()).getAllByRole("listitem");
    expect(medida).toHaveTextContent("A medida — artisJet · sin precio público (a medida)");
    expect(orientativa).toHaveTextContent("Orientativa — artisJet · 9.990 € · orientativo");
  });

  it("solo enlaces web: una ficha con un enlace raro se enseña sin enlace; una sin nombre se ignora", () => {
    render(<LoQueHaUsado borrador={borrador({
      contacto: { nombre: "Ana" },
      fichas: [
        { ...DESKTOP, nombre: "Rara", enlace: "javascript:alert(1)",
          galeria: ["javascript:alert(1)", "https://www.fluxlasers.eu/img/beamo.jpg"] },
        { ...CATALOGO, enlace: "javascript:alert(2)" },
        { ...DESKTOP, nombre: "", origen_id: "" },
        { ...CATALOGO, procedencia: "otra" },
        "nada",
      ],
    })} />);
    expect(screen.getByText(/2 fichas: 1 del catálogo y 1 del índice/)).toBeInTheDocument();
    expect(within(indice()).getByRole("listitem")).toHaveTextContent("Ficha del índice de fluxlasers.eu: Rara");
    expect(within(indice()).queryByRole("link")).not.toBeInTheDocument();
    // La galería cuenta solo los enlaces web: queda una imagen, no se dice.
    expect(indice()).not.toHaveTextContent("galería");
    expect(within(catalogo()).getByRole("listitem")).toHaveTextContent("Ficha del catálogo: artisJet 3000Pro");
    expect(within(catalogo()).queryByRole("link")).not.toBeInTheDocument();
  });

  it("sin fichas (un correo de texto, un borrador de antes) no pinta listas ni las cuenta", () => {
    render(<LoQueHaUsado borrador={borrador({ mensajes_hilo: 1, contacto: { nombre: "Ana" } })} />);
    expect(screen.queryByRole("list", { name: "Fichas del catálogo en el correo" })).not.toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Fichas del índice de las webs en el correo" })).not.toBeInTheDocument();
    expect(screen.getByText(/la ficha de Ana/)).not.toHaveTextContent(/\d+ fichas?\b/);
    expect(screen.getByText(/Lo que ha usado/).textContent?.trim())
      .toBe("Lo que ha usado (alcance: todo lo de este contacto)");
  });

  it("los productos del catálogo que entraron van enlazados, y se marcan los de un enlace que no es un producto", () => {
    render(<LoQueHaUsado borrador={{
      ...borrador({
        mensajes_hilo: 1, contacto: { nombre: "Ana" },
        catalogo: {
          maquinas: [
            { nombre: "artisJet 3000Pro Freebird", marca: "artisJet", precio_texto: "5.490,00 €",
              enlace: "https://www.boprint.net/contacto/", eleccion: "instruccion" },
            { nombre: "Orion 20", marca: "MBO", precio_texto: "4.990,00 €",
              enlace: "https://mboprinters.com/es/producto/orion-20/", eleccion: "hilo" },
          ],
          consumibles: [{ nombre: "Tinta DTF blanca", precio_texto: null, enlace: "javascript:alert(1)" }],
          sin_maquina: [],
        },
        enlaces_no_producto: [
          { nombre: "artisJet 3000Pro Freebird", enlace: "https://www.boprint.net/contacto/", motivo: "servicio" },
          { nombre: "Orion 20", enlace: "https://mboprinters.com/es/producto/orion-20/", motivo: "fuera" },
        ],
      }),
      enlaces: [{ url: "https://mboprinters.com/es/producto/orion-20/", etiqueta: "Orion 20", origen: "catalogo_compositor" }],
    }} />);
    const filas = within(screen.getByRole("list", { name: "Productos del catálogo que entraron" }))
      .getAllByRole("listitem");
    expect(filas[0]).toHaveTextContent(
      "artisJet 3000Pro Freebird (artisJet) — 5.490,00 € — el enlace va a una página de servicio, no a la de la máquina");
    expect(within(filas[0]).getByRole("link", { name: "artisJet 3000Pro Freebird" })).toHaveAttribute(
      "href", "https://www.boprint.net/contacto/");
    // Uno que el índice no tiene (puede ser nuevo): se dice así, no que no es un producto.
    expect(filas[1]).toHaveTextContent("Orion 20 (MBO) — 4.990,00 € — el enlace no está en el índice de las webs");
    expect(filas[1]).not.toHaveTextContent("página de servicio");
    expect(within(filas[1]).getByRole("link", { name: "Orion 20" })).toHaveAttribute(
      "href", "https://mboprinters.com/es/producto/orion-20/");
    expect(filas[2]).toHaveTextContent("Tinta DTF blanca — sin precio publicado");
    expect(within(filas[2]).queryByRole("link")).not.toBeInTheDocument();
    const enlaces = screen.getByRole("list", { name: "Enlaces que podía usar" });
    expect(within(enlaces).getByRole("link", { name: "https://mboprinters.com/es/producto/orion-20/" }))
      .toBeInTheDocument();
    expect(enlaces).toHaveTextContent("— Orion 20");
  });

  it("una página de nuestras webs que es un producto dice que se puede poner como ficha, y si la pidió quien envía", () => {
    render(<LoQueHaUsado borrador={borrador({
      mensajes_hilo: 1, contacto: { nombre: "Ana" },
      material: {
        automatico: true, fuentes: { webs: true, soporte: false },
        consulta: { tecnica: false, webs: "beamo", origen: "ia" }, buscado_en_webs: "beamo", ms: 800,
        webs: [
          { ref: "W1", id: "w-1", titulo: "beamo desktop", url: "https://www.fluxlasers.eu/es/producto/beamo-desktop/",
            dominio: "fluxlasers.eu", tipo: "producto", precio_web_texto: "489,30 € (+ IVA)", precio_iva: "sin_iva",
            usado: true, ficha: true, wp_id: 812, pedida: true },
          { ref: "W2", id: "w-2", titulo: "beamo", url: "https://www.fluxlasers.eu/es/producto/beamo/",
            dominio: "fluxlasers.eu", tipo: "producto", precio_web_texto: null, usado: false, ficha: true,
            wp_id: 640, pedida: false },
          { ref: "W3", id: "w-3", titulo: "Guía del beamo", url: "https://www.fluxlasers.eu/es/guia-beamo/",
            dominio: "fluxlasers.eu", tipo: "entrada", usado: false, pedida: true },
        ],
      },
    })} />);
    const filas = within(screen.getByRole("list", { name: "Lo que trajo solo de webs y soporte" }))
      .getAllByRole("listitem");
    expect(filas[0]).toHaveTextContent(
      "Web: beamo desktop — fluxlasers.eu · producto · precio de la web: 489,30 € (+ IVA) · se puede poner como "
      + "ficha · pedida por quien envía · usado en el borrador");
    expect(filas[1]).toHaveTextContent(
      "Web: beamo — fluxlasers.eu · producto · se puede poner como ficha · no lo usó");
    expect(filas[1]).not.toHaveTextContent("pedida");
    // Una página que no es un producto no es una ficha (ni «pedida»).
    expect(filas[2]).toHaveTextContent("Web: Guía del beamo — fluxlasers.eu · entrada · no lo usó");
    expect(filas[2]).not.toHaveTextContent(/ficha|pedida/);
  });
});
