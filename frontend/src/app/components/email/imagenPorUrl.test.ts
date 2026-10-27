import type { Ui } from "tinymce";
import type { ComprobacionImagenUrl } from "../../lib/emailsApi";
import {
  type DatosDialogo,
  MOTIVO_NO_HTTPS,
  abrirDialogoImagenUrl,
  especDialogoImagenUrl,
  htmlImagenUrl,
  pesoLegible,
  validarImagenUrl,
} from "./imagenPorUrl";

// El diálogo es de TinyMCE (que no arranca en jsdom): se prueba con un
// windowManager falso que guarda la especificación y hace lo que haría TinyMCE
// con ella (`redial` la sustituye; los datos salen de `initialData`).
jest.mock("../../lib/emailsApi", () => ({ comprobarImagenUrl: jest.fn() }));

type Spec = Ui.Dialog.DialogSpec<DatosDialogo>;
type Componente = Ui.Dialog.BodyComponentSpec;

function abrir(comprobar: jest.Mock) {
  let spec = null as unknown as Spec;
  let datos: DatosDialogo = { url: "" };
  const api = {
    getData: () => datos,
    setData: (d: Partial<DatosDialogo>) => { datos = { ...datos, ...d }; },
    setEnabled: jest.fn(),
    focus: jest.fn(),
    showTab: jest.fn(),
    toggleFullscreen: jest.fn(),
    redial: jest.fn((nu: Spec) => { spec = nu; datos = { url: "", ...nu.initialData }; }),
    block: jest.fn(),
    unblock: jest.fn(),
    close: jest.fn(),
  };
  const editor = {
    windowManager: {
      open: jest.fn((s: Spec) => { spec = s; datos = { url: "", ...s.initialData }; return api; }),
    },
    insertContent: jest.fn(),
  };
  abrirDialogoImagenUrl(editor, comprobar);
  const items = (): Componente[] => (spec.body.type === "panel" ? spec.body.items : []);
  return {
    editor,
    api,
    get spec() { return spec; },
    escribir(url: string) {
      datos = { url };
      spec.onChange?.(api, { name: "url" });
    },
    async comprobar() {
      spec.onAction?.(api, { name: "comprobar" });
      await new Promise((r) => setTimeout(r, 0));
    },
    async intro() {
      spec.onSubmit?.(api);
      await new Promise((r) => setTimeout(r, 0));
    },
    insertarHabilitado(): boolean {
      const b = spec.buttons?.find((x) => "name" in x && x.name === "insertar");
      return Boolean(b && b.enabled);
    },
    avisos(nivel: "error" | "warn"): string[] {
      return items().flatMap((i) => (i.type === "alertbanner" && i.level === nivel ? [i.text] : []));
    },
    /** La vista previa tal como la pintaría el diálogo (el htmlpanel). */
    previa(): HTMLElement | null {
      const panel = items().find((i) => i.type === "htmlpanel");
      if (!panel || panel.type !== "htmlpanel") return null;
      const div = document.createElement("div");
      div.innerHTML = panel.html;
      return div;
    },
  };
}

const URL_OK = "https://www.fluxlasers.eu/wp-content/uploads/2024/05/beamo.jpg";

function ok(over: Partial<Extract<ComprobacionImagenUrl, { ok: true }>> = {}): ComprobacionImagenUrl {
  return {
    ok: true, url: URL_OK, content_type: "image/jpeg", bytes: 358_400, pesada: false, aviso: null, ...over,
  };
}

describe("Imagen por URL — validar en el cliente y lo que se inserta", () => {
  it("http:// se rechaza con el mismo texto que el backend; vacío y otros esquemas también", () => {
    expect(validarImagenUrl("http://x.com/a.jpg")).toEqual({
      ok: false, url: "http://x.com/a.jpg", codigo: "no_https", motivo: MOTIVO_NO_HTTPS,
    });
    expect(MOTIVO_NO_HTTPS).toBe(
      "Solo se admiten direcciones https://: una imagen en http:// la bloquean los clientes de correo.");
    expect(validarImagenUrl("   ")).toMatchObject({ ok: false, codigo: "url_invalida" });
    expect(validarImagenUrl("javascript:alert(1)")).toMatchObject({ ok: false, codigo: "url_invalida" });
    expect(validarImagenUrl("  https://x.com/a.jpg \n")).toEqual({ ok: true, url: "https://x.com/a.jpg" });
  });

  it("el <img> lleva la URL tal cual (enlazada en caliente), alt vacío y sin salirse del ancho", () => {
    expect(htmlImagenUrl(URL_OK)).toBe(
      `<img src="${URL_OK}" alt="" style="max-width:100%;height:auto">`);
    // Con & y comillas el atributo se escapa, y el navegador lee la URL igual.
    const rara = "https://cdn.x.com/img.jpg?w=600&h=400&n=\"a\"";
    const div = document.createElement("div");
    div.innerHTML = htmlImagenUrl(rara);
    expect(div.querySelector("img")?.getAttribute("src")).toBe(rara);
    expect(div.querySelector("img")?.getAttribute("alt")).toBe("");
  });

  it("el peso se dice en KB o MB", () => {
    expect(pesoLegible(358_400)).toBe("350 KB");
    expect(pesoLegible(1_468_006)).toBe("1,4 MB");
    expect(pesoLegible(null)).toBeNull();
  });
});

describe("Imagen por URL — el diálogo", () => {
  it("abre con «Insertar» deshabilitado y una nota de que la imagen no se sube", () => {
    const d = abrir(jest.fn());
    expect(d.editor.windowManager.open).toHaveBeenCalledTimes(1);
    expect(d.spec.title).toBe("Insertar imagen por URL");
    expect(d.insertarHabilitado()).toBe(false);
    expect(d.previa()?.textContent).toContain("no se sube a ningún sitio");
  });

  it("http:// se rechaza ya en el cliente: el motivo a la vista, sin llamar al backend y sin insertar", async () => {
    const comprobar = jest.fn();
    const d = abrir(comprobar);
    d.escribir("http://www.boprint.net/foto.jpg");
    await d.comprobar();
    expect(comprobar).not.toHaveBeenCalled();
    expect(d.avisos("error")).toEqual([MOTIVO_NO_HTTPS]);
    expect(d.insertarHabilitado()).toBe(false);
    // Intro (o «Insertar» a la fuerza) tampoco inserta.
    await d.intro();
    expect(d.editor.insertContent).not.toHaveBeenCalled();
    expect(d.api.close).not.toHaveBeenCalled();
  });

  it("comprobar bien → vista previa con la imagen → «Insertar» mete la URL tal cual", async () => {
    const comprobar = jest.fn(() => Promise.resolve(ok()));
    const d = abrir(comprobar);
    d.escribir(`  ${URL_OK}  `);
    await d.comprobar();
    expect(comprobar).toHaveBeenCalledWith(URL_OK);
    expect(d.api.block).toHaveBeenCalledWith("Comprobando la imagen…");
    expect(d.api.unblock).toHaveBeenCalled();
    const previa = d.previa();
    expect(previa?.querySelector("img")?.getAttribute("src")).toBe(URL_OK);
    expect(previa?.textContent).toContain("image/jpeg · 350 KB");
    expect(d.avisos("error")).toEqual([]);
    expect(d.avisos("warn")).toEqual([]);
    expect(d.insertarHabilitado()).toBe(true);
    // El campo conserva lo escrito tras redibujar.
    expect(d.api.getData().url).toBe(URL_OK);
    await d.intro();
    expect(d.editor.insertContent).toHaveBeenCalledWith(
      `<img src="${URL_OK}" alt="" style="max-width:100%;height:auto">`);
    expect(d.api.close).toHaveBeenCalled();
  });

  it("si el backend dice que no (no es imagen, red interna…), el motivo a la vista y no se inserta", async () => {
    const comprobar = jest.fn(() => Promise.resolve({
      ok: false, url: "https://intranet.local/x.png", codigo: "red_interna",
      motivo: "La dirección apunta a la red interna: no se puede usar.",
    } as ComprobacionImagenUrl));
    const d = abrir(comprobar);
    d.escribir("https://intranet.local/x.png");
    await d.comprobar();
    expect(d.avisos("error")).toEqual(["La dirección apunta a la red interna: no se puede usar."]);
    expect(d.previa()).toBeNull();
    expect(d.insertarHabilitado()).toBe(false);
    // Intro vuelve a comprobar (no inserta).
    await d.intro();
    expect(comprobar).toHaveBeenCalledTimes(2);
    expect(d.editor.insertContent).not.toHaveBeenCalled();
  });

  it("pesada: se ve el aviso de peso y se puede insertar igual", async () => {
    const aviso = "La imagen pesa 2,4 MB: se insertará igual, pero el correo tardará en cargar.";
    const d = abrir(jest.fn(() => Promise.resolve(ok({ bytes: 2_516_582, pesada: true, aviso }))));
    d.escribir(URL_OK);
    await d.comprobar();
    expect(d.avisos("warn")).toEqual([aviso]);
    expect(d.previa()?.querySelector("img")).not.toBeNull();
    expect(d.insertarHabilitado()).toBe(true);
    await d.intro();
    expect(d.editor.insertContent).toHaveBeenCalledTimes(1);
  });

  it("cambiar el enlace después de comprobarlo deshabilita «Insertar» hasta comprobar otra vez", async () => {
    const comprobar = jest.fn(() => Promise.resolve(ok()));
    const d = abrir(comprobar);
    d.escribir(URL_OK);
    await d.comprobar();
    d.escribir(`${URL_OK}?otra=1`);
    expect(d.api.setEnabled).toHaveBeenLastCalledWith("insertar", false);
    await d.intro();
    // Intro con un enlace sin comprobar: comprueba ESE, no inserta el viejo.
    expect(comprobar).toHaveBeenLastCalledWith(`${URL_OK}?otra=1`);
    expect(d.editor.insertContent).not.toHaveBeenCalled();
  });

  it("si la comprobación falla por la red, lo dice y no inserta", async () => {
    const d = abrir(jest.fn(() => Promise.reject(new Error("No se pudo conectar con el servidor."))));
    d.escribir(URL_OK);
    await d.comprobar();
    expect(d.avisos("error")).toEqual(["No se pudo conectar con el servidor."]);
    expect(d.insertarHabilitado()).toBe(false);
  });
});

describe("Imagen por URL — lo que viene de fuera entra escapado (TinyMCE lo pinta como HTML)", () => {
  // El alertbanner y el htmlpanel de TinyMCE meten su texto con innerHTML:
  // así lo vería Bart.
  const pintado = (html: string) => {
    const div = document.createElement("div");
    div.innerHTML = html;
    return div;
  };
  const ATAQUE = "<img src=x onerror=alert(1)>";

  it("el motivo del backend (con el Content-Type de la web ajena) no se pinta como HTML", async () => {
    const motivo = `El enlace no es una imagen (es text/x${ATAQUE}): usa el enlace de la imagen.`;
    const d = abrir(jest.fn(() => Promise.resolve({
      ok: false, url: URL_OK, codigo: "no_es_imagen", motivo,
    } as ComprobacionImagenUrl)));
    d.escribir(URL_OK);
    await d.comprobar();
    const [texto] = d.avisos("error");
    expect(texto).toContain("&lt;img src=x onerror=alert(1)&gt;");
    expect(texto).not.toContain("<img");
    expect(pintado(texto).querySelector("img")).toBeNull();
    expect(pintado(texto).textContent).toBe(motivo);
  });

  it("el error de la llamada y el aviso de peso tampoco", async () => {
    const d = abrir(jest.fn(() => Promise.reject(new Error(ATAQUE))));
    d.escribir(URL_OK);
    await d.comprobar();
    expect(d.avisos("error")).toEqual(["&lt;img src=x onerror=alert(1)&gt;"]);

    const conAviso = abrir(jest.fn(() => Promise.resolve(ok({
      content_type: `image/x${ATAQUE}`, aviso: "<b>x</b>",
    }))));
    conAviso.escribir(URL_OK);
    await conAviso.comprobar();
    expect(conAviso.avisos("warn")).toEqual(["&lt;b&gt;x&lt;/b&gt;"]);
    expect(pintado(conAviso.avisos("warn")[0]).querySelector("b")).toBeNull();
    // En la vista previa solo está la imagen comprobada; el Content-Type, como texto.
    const previa = conAviso.previa();
    expect(previa?.querySelectorAll("img")).toHaveLength(1);
    expect(previa?.querySelector("img")?.getAttribute("src")).toBe(URL_OK);
    expect(previa?.textContent).toContain(`image/x${ATAQUE}`);
  });

  it("la vista previa solo pinta una URL https:// y con el atributo escapado", () => {
    const sinAcciones = {};
    const items = (s: Spec): Componente[] => (s.body.type === "panel" ? s.body.items : []);
    const html = (s: Spec) => items(s).flatMap((i) => (i.type === "htmlpanel" ? [i.html] : [])).join("");
    const conUrl = (url: string) => especDialogoImagenUrl(url, ok({ url }), sinAcciones);

    const rara = `${URL_OK}?a="><script>alert(1)</script>`;
    const buena = pintado(html(conUrl(rara)));
    expect(buena.querySelector("script")).toBeNull();
    expect(buena.querySelector("img")?.getAttribute("src")).toBe(rara);

    for (const mala of ["javascript:alert(1)", "data:image/svg+xml,<svg onload=alert(1)>", "http://x.com/a.jpg"]) {
      const div = pintado(html(conUrl(mala)));
      expect(div.querySelector("img")).toBeNull();
      expect(div.querySelector("svg")).toBeNull();
    }
  });
});
