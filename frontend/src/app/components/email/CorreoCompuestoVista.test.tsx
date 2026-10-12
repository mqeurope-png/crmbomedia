import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { CorreoCompuestoVista } from "./CorreoCompuestoVista";
import type { EstructuraCompuesta } from "../../lib/correoCompuesto";
import { renderCorreoCompuesto } from "../../lib/emailsApi";

jest.mock("../../lib/emailsApi", () => ({ renderCorreoCompuesto: jest.fn() }));
const mockRender = renderCorreoCompuesto as jest.Mock;

const ESTRUCTURA: EstructuraCompuesta = {
  version: 1, idioma: "es", marca: "mbo_dtf", pie: null,
  bloques: [
    { id: "b1", tipo: "texto", html: "<p>Hola Marny,</p><p>te paso la gama DTF:</p>" },
    { id: "b2", tipo: "productos_fila", productos: [
      { fuente: "compositor", origen_id: "orion-20" }, { fuente: "compositor", origen_id: "lyra-35" },
    ] },
    { id: "b3", tipo: "cta", botones: [{ texto: "Ver la gama", url: "https://mboprinters.com/es/" }] },
  ],
};

/** Lo que devuelve el servidor: el documento entero; con `preview`, con los
 *  atributos de edición. Es un correo de verdad en miniatura: tablas, estilos
 *  en línea y la hoja de estilo del móvil. */
function documento(e: EstructuraCompuesta, preview: boolean): string {
  const at = (s: string) => (preview ? ` ${s}` : "");
  const filas = e.bloques.map((b) => {
    const fila = at(`data-bh-bloque="${b.id}" data-bh-tipo="${b.tipo}"`);
    if (b.tipo === "texto") {
      return `<tr${fila}><td${at(`data-bh-campo="${b.id}:html"`)} style="padding:16px 8px 2px;">${b.html}</td></tr>`;
    }
    if (b.tipo === "cta") {
      return `<tr${fila}><td style="padding:8px;"><a href="https://mboprinters.com/es/" target="_blank"${at(`data-bh-campo="${b.id}:botones.0.texto"`)} style="color:#ffffff;">${String(b.botones?.[0].texto)}</a></td></tr>`;
    }
    return `<tr${fila}><td class="bh-col bh-ficha" style="padding:0 5px;">Orion 20 · 4.990 €</td></tr>`;
  }).join("");
  return "<!DOCTYPE html><html lang=\"es\"><head><meta charset=\"utf-8\">"
    + "<meta name=\"generator\" content=\"BoHub correo compuesto\"><title>Re: Equipos DTF</title>"
    + "<style type=\"text/css\">@media only screen and (max-width: 600px) { .bh-col { display: block !important; } }</style>"
    + `</head><body style="margin:0;"><table role="presentation" width="600" class="bh-wrap" style="max-width:600px;">${filas}</table></body></html>`;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockRender.mockImplementation(({ estructura, preview }) => Promise.resolve({
    estructura, html: documento(estructura, Boolean(preview)),
    texto: "Hola Marny,\n\nte paso la gama DTF:", avisos: [], precios: ["4.990 €"],
  }));
});

function marco(): Document {
  return (screen.getByTitle("Vista previa del correo") as HTMLIFrameElement).contentDocument as Document;
}

/** Como el compositor: guarda la estructura y, al mover un bloque, regenera. */
function ConEstado({ alCambiar }: { alCambiar: (e: EstructuraCompuesta) => void }) {
  const [estructura, setEstructura] = useState(ESTRUCTURA);
  const [version, setVersion] = useState(0);
  return (
    <CorreoCompuestoVista
      estructura={estructura} version={version} asunto="Re: Equipos DTF" firmaHtml={null}
      onEditar={setEstructura}
      onCambiar={(e) => { alCambiar(e); setEstructura(e); setVersion((v) => v + 1); }}
    />
  );
}

async function montar() {
  const props = {
    estructura: ESTRUCTURA, version: 0, asunto: "Re: Equipos DTF", firmaHtml: "<p>Admin</p>",
    onEditar: jest.fn(), onCambiar: jest.fn(),
  };
  const utils = render(<CorreoCompuestoVista {...props} />);
  await waitFor(() => expect(marco().querySelector("[data-bh-campo]")).not.toBeNull());
  return { ...utils, props };
}

describe("CorreoCompuestoVista", () => {
  it("enseña el correo que genera BoHub, a 640 px o en un móvil, con los textos editables", async () => {
    await montar();
    expect(mockRender).toHaveBeenCalledWith(expect.objectContaining({
      estructura: ESTRUCTURA, preview: true, firma_html: "<p>Admin</p>",
    }));
    const doc = marco();
    expect(doc.querySelectorAll('[contenteditable="true"]')).toHaveLength(2);
    expect(doc.querySelector("style")?.textContent).toContain("@media only screen");
    const iframe = screen.getByTitle("Vista previa del correo") as HTMLIFrameElement;
    expect(iframe.style.width).toBe("640px");
    await userEvent.setup().click(screen.getByRole("button", { name: /Móvil/ }));
    expect(iframe.style.width).toBe("375px");
    expect(screen.getByRole("button", { name: /Móvil/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("editar un texto cambia ese campo de la estructura y nada más (ni bloques, ni orden, ni productos)", async () => {
    const { props } = await montar();
    const doc = marco();
    const texto = doc.querySelector('[data-bh-campo="b1:html"]') as HTMLElement;
    texto.innerHTML = '<p style="margin:0;">Hola Marny,</p><p style="margin:0;">lo que <b>hablamos</b>:</p>';
    act(() => { texto.dispatchEvent(new Event("input", { bubbles: true })); });
    expect(props.onEditar).toHaveBeenCalledTimes(1);
    const editada = props.onEditar.mock.calls[0][0] as EstructuraCompuesta;
    expect(editada.bloques[0]).toEqual({ ...ESTRUCTURA.bloques[0], html: "<p>Hola Marny,</p><p>lo que <strong>hablamos</strong>:</p>" });
    expect(editada.bloques.slice(1)).toEqual(ESTRUCTURA.bloques.slice(1));
    expect({ ...editada, bloques: [] }).toEqual({ ...ESTRUCTURA, bloques: [] });
    // El botón: su texto, nada más; y la vista no se ha vuelto a generar.
    const boton = doc.querySelector('[data-bh-campo="b3:botones.0.texto"]') as HTMLElement;
    boton.textContent = "Toda la gama";
    act(() => { boton.dispatchEvent(new Event("input", { bubbles: true })); });
    const otra = props.onEditar.mock.calls[1][0] as EstructuraCompuesta;
    expect(otra.bloques[2].botones).toEqual([{ texto: "Toda la gama", url: "https://mboprinters.com/es/" }]);
    expect(otra.bloques[0].html).toBe(editada.bloques[0].html);
    expect(props.onCambiar).not.toHaveBeenCalled();
    expect(mockRender).toHaveBeenCalledTimes(1);
  });

  it("un enlace de la vista no navega; pulsar un bloque lo elige y se puede subir, bajar, duplicar o quitar", async () => {
    const alCambiar = jest.fn();
    render(<ConEstado alCambiar={alCambiar} />);
    await waitFor(() => expect(marco().querySelector("[data-bh-campo]")).not.toBeNull());
    const props = { onCambiar: alCambiar };
    const doc = marco();
    const enlace = doc.querySelector("a") as HTMLAnchorElement;
    const clic = new MouseEvent("click", { bubbles: true, cancelable: true });
    act(() => { enlace.dispatchEvent(clic); });
    expect(clic.defaultPrevented).toBe(true);
    expect(screen.getByLabelText("Bloque")).toHaveValue("b3");
    expect(doc.querySelector('[data-bh-bloque="b3"]')?.classList.contains("bh-elegido")).toBe(true);
    expect(screen.getByRole("button", { name: "Bajar el bloque" })).toBeDisabled();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Subir el bloque" }));
    expect((props.onCambiar.mock.calls[0][0] as EstructuraCompuesta).bloques.map((b) => b.id))
      .toEqual(["b1", "b3", "b2"]);
    // Se regenera la vista, con el bloque aún elegido.
    await waitFor(() => expect(mockRender).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(marco().querySelector('[data-bh-bloque="b3"]')?.classList
      .contains("bh-elegido")).toBe(true));
    fireEvent.change(screen.getByLabelText("Bloque"), { target: { value: "b2" } });
    await user.click(screen.getByRole("button", { name: "Duplicar el bloque" }));
    const duplicada = props.onCambiar.mock.calls[1][0] as EstructuraCompuesta;
    // Tras subir el botón el orden es b1, b3, b2: la copia de b2 va justo debajo.
    expect(duplicada.bloques).toHaveLength(4);
    const copia = duplicada.bloques[3];
    expect(copia.id).not.toBe("b2");
    expect(copia).toEqual({ ...ESTRUCTURA.bloques[1], id: copia.id });
    expect(screen.getByLabelText("Bloque")).toHaveValue(copia.id);   // la copia, elegida
    await user.click(screen.getByRole("button", { name: "Quitar el bloque" }));
    expect((props.onCambiar.mock.calls[2][0] as EstructuraCompuesta).bloques.map((b) => b.id))
      .toEqual(["b1", "b3", "b2"]);
  });

  it("«Copiar HTML» copia el documento entero que se envía: el de la vista, con lo editado y sin los atributos de edición", async () => {
    const user = userEvent.setup();       // pone un portapapeles de prueba
    await montar();
    const doc = marco();
    const texto = doc.querySelector('[data-bh-campo="b1:html"]') as HTMLElement;
    texto.innerHTML = "<p>Hola Marny,</p><p>lo que hablamos:</p>";
    act(() => { texto.dispatchEvent(new Event("input", { bubbles: true })); });
    await user.click(screen.getByRole("button", { name: /Copiar HTML/ }));
    expect(await screen.findByRole("status")).toHaveTextContent("HTML copiado");
    const pedido = mockRender.mock.calls[1][0];
    expect(pedido.preview).toBeUndefined();
    expect(pedido.firma_html).toBe("<p>Admin</p>");
    expect(pedido.estructura.bloques[0].html).toBe("<p>Hola Marny,</p><p>lo que hablamos:</p>");
    const copiado = await navigator.clipboard.readText();
    expect(copiado.startsWith("<!DOCTYPE html>")).toBe(true);
    expect(copiado).toContain("@media only screen");
    expect(copiado).not.toContain("data-bh-");
    // Es la vista previa tal cual, sin sus atributos (y con lo editado).
    const vista = doc.documentElement.outerHTML;
    expect(vista).toContain("lo que hablamos");
    expect(copiado).toContain("<p>Hola Marny,</p><p>lo que hablamos:</p>");
  });

  it("«Descargar .html» descarga el correo con el nombre del asunto", async () => {
    const crear = jest.fn(() => "blob:correo");
    Object.assign(URL, { createObjectURL: crear, revokeObjectURL: jest.fn() });
    const clics: string[] = [];
    const original = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function clic(this: HTMLAnchorElement) { clics.push(this.download); };
    try {
      await montar();
      await userEvent.setup().click(screen.getByRole("button", { name: /Descargar .html/ }));
      await waitFor(() => expect(clics).toEqual(["equipos-dtf.html"]));
      const blob = (crear.mock.calls[0] as unknown as [Blob])[0];
      expect(blob.type).toBe("text/html;charset=utf-8");
    } finally {
      HTMLAnchorElement.prototype.click = original;
    }
  });
});
