import {
  type EstructuraCompuesta,
  aplicarEdicion,
  duplicarBloque,
  etiquetaBloque,
  htmlFinalDeVista,
  moverBloque,
  quitarBloque,
  textoAHtml,
  textoDeCampo,
  textoRicoDe,
} from "./correoCompuesto";

function estructura(): EstructuraCompuesta {
  return {
    version: 1, idioma: "es", marca: "mbo_dtf", pie: null,
    bloques: [
      { id: "b1", tipo: "texto", html: "<p>Hola Marny,</p><p>te paso la gama DTF:</p>" },
      { id: "b2", tipo: "productos_fila", productos: [
        { fuente: "compositor", origen_id: "orion-20" }, { fuente: "compositor", origen_id: "lyra-35" },
      ], boton: { es: "Más info →", en: "More info →" } },
      { id: "b3", tipo: "video", url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ", titulo: "La Orion 20" },
      { id: "b4", tipo: "cta", botones: [{ texto: "Ver la gama", url: "https://mboprinters.com/es/" }] },
    ],
  };
}

describe("correoCompuesto — editar un texto cambia ese campo y nada más", () => {
  it("el texto de un bloque, un título, un botón de fila por idioma, un botón de la fila de botones y el pie", () => {
    const e = estructura();
    const copia = JSON.parse(JSON.stringify(e));
    const a = aplicarEdicion(e, "b1:html", "<p>Hola Marny,</p><p>lo que hablamos:</p>");
    expect(a.bloques[0].html).toBe("<p>Hola Marny,</p><p>lo que hablamos:</p>");
    expect(a.bloques.slice(1)).toEqual(copia.bloques.slice(1));      // el resto, igual
    expect(a.bloques.map((b) => b.id)).toEqual(["b1", "b2", "b3", "b4"]);
    expect(e).toEqual(copia);                                         // sin mutar la de partida
    const b = aplicarEdicion(e, "b3:titulo", "La Orion 20 en marcha");
    expect(b.bloques[2]).toEqual({ ...copia.bloques[2], titulo: "La Orion 20 en marcha" });
    const c = aplicarEdicion(e, "b2:boton", "Ver ficha →");
    expect(c.bloques[1].boton).toEqual({ es: "Ver ficha →", en: "More info →" });
    expect(c.bloques[1].productos).toEqual(copia.bloques[1].productos);
    const d = aplicarEdicion(e, "b4:botones.0.texto", "Toda la gama");
    expect(d.bloques[3].botones).toEqual([{ texto: "Toda la gama", url: "https://mboprinters.com/es/" }]);
    expect(aplicarEdicion(e, ":pie", "Precios sin IVA.").pie).toBe("Precios sin IVA.");
    // Un campo que no existe no cambia nada.
    expect(aplicarEdicion(e, "b9:html", "x")).toBe(e);
    expect(aplicarEdicion(e, "b4:botones.3.texto", "x")).toBe(e);
    expect(aplicarEdicion(e, "b2:productos", "x")).toBe(e);
  });

  it("el hero y las muestras: subtítulo, cada punto y cada pie, sin tocar sus imágenes", () => {
    const e: EstructuraCompuesta = {
      version: 1, idioma: "es", marca: "mbo_dtf", pie: null,
      bloques: [
        { id: "h1", tipo: "hero", imagen: { id: "img-1" }, titulo: "La gama DTF",
          subtitulo: "En un día", puntos: ["Sin pretratamiento", { es: "Colores vivos", en: "Vivid colours" }],
          disposicion: "arriba" },
        { id: "m1", tipo: "muestras", imagenes: [{ id: "img-2" }, { id: "img-3" }], pies: ["Camisetas", ""] },
      ],
    };
    const copia = JSON.parse(JSON.stringify(e));
    const a = aplicarEdicion(e, "h1:subtitulo", "De la pantalla a la prenda");
    expect(a.bloques[0]).toEqual({ ...copia.bloques[0], subtitulo: "De la pantalla a la prenda" });
    const b = aplicarEdicion(e, "h1:puntos.1", "Colores que no se lavan");
    expect(b.bloques[0].puntos).toEqual(["Sin pretratamiento", { es: "Colores que no se lavan", en: "Vivid colours" }]);
    expect(b.bloques[0].imagen).toEqual({ id: "img-1" });
    const c = aplicarEdicion(e, "m1:pies.1", "Sudaderas");
    expect(c.bloques[1]).toEqual({ ...copia.bloques[1], pies: ["Camisetas", "Sudaderas"] });
    expect(e).toEqual(copia);
    // Un punto o un pie que no existe, o el mismo texto, no cambian nada.
    expect(aplicarEdicion(e, "h1:puntos.5", "x")).toBe(e);
    expect(aplicarEdicion(e, "m1:pies.0", "Camisetas")).toBe(e);
    expect(etiquetaBloque(e.bloques[0], "es")).toBe("Hero · «La gama DTF»");
    expect(etiquetaBloque(e.bloques[1], "es")).toBe("Muestras · «2 imágenes»");
  });

  it("subir, bajar, quitar y duplicar mueven bloques enteros, con sus id", () => {
    const e = estructura();
    expect(moverBloque(e, "b3", -1).bloques.map((b) => b.id)).toEqual(["b1", "b3", "b2", "b4"]);
    expect(moverBloque(e, "b4", 1)).toBe(e);
    expect(quitarBloque(e, "b3").bloques.map((b) => b.id)).toEqual(["b1", "b2", "b4"]);
    const { estructura: dup, id } = duplicarBloque(e, "b2");
    expect(dup.bloques).toHaveLength(5);
    expect(id).toMatch(/^b[0-9a-f]{8}$/);
    expect(dup.bloques[2]).toEqual({ ...e.bloques[1], id });
    expect(dup.bloques[2].productos).not.toBe(e.bloques[1].productos);   // copia, no la misma
  });

  it("lo editado en la vista se lee limpio: sin los estilos del correo ni etiquetas de fuera", () => {
    const td = document.createElement("td");
    td.innerHTML = '<p style="margin:0 0 14px;color:#1e293b">Hola <b>Marny</b>,</p>'
      + '<div>segunda <span style="color:red">línea</span></div>'
      + '<p>Mira <a href="https://mboprinters.com/es/" target="_blank" style="color:#2563eb">la web</a>'
      + ' y <a href="javascript:alert(1)">esto</a> &lt;3</p><p><br></p>';
    expect(textoRicoDe(td)).toBe(
      "<p>Hola <strong>Marny</strong>,</p><p>segunda línea</p>"
      + '<p>Mira <a href="https://mboprinters.com/es/">la web</a> y esto &lt;3</p>');
    const p = document.createElement("p");
    p.innerHTML = "Precios sin IVA.<br>Envío a la península&nbsp;incluido.";
    expect(textoDeCampo(p)).toBe("Precios sin IVA.\nEnvío a la península incluido.");
  });

  it("el HTML final es el de la vista sin los atributos de edición; el texto pasa a párrafos", () => {
    expect(htmlFinalDeVista('<tr data-bh-bloque="b1" data-bh-tipo="texto"><td data-bh-campo="b1:html" style="x">Hola</td></tr>'))
      .toBe('<tr><td style="x">Hola</td></tr>');
    expect(textoAHtml("Hola Marny,\n\nte paso <la gama>:\n- Orion 20")).toBe(
      "<p>Hola Marny,</p><p>te paso &lt;la gama&gt;:<br>- Orion 20</p>");
    expect(etiquetaBloque(estructura().bloques[0], "es")).toBe("Texto · «Hola Marny, te paso la gama DTF:»");
    expect(etiquetaBloque(estructura().bloques[1], "es")).toBe("Fichas en fila · «2 productos»");
  });
});
