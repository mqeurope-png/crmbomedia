import type { Ui } from "tinymce";
import { type ComprobacionImagenUrl, comprobarImagenUrl } from "../../lib/emailsApi";
import { extractErrorMessage } from "../../lib/errors";

/** «Imagen por URL» del editor de correo: Bart pega el enlace de una imagen
 *  (de sus webs, de un fabricante) y va en el `<img>` TAL CUAL, enlazada en
 *  caliente: no se descarga, no se rehospeda, no va a ninguna biblioteca.
 *
 *  Antes de insertarla, el diálogo la COMPRUEBA en el backend (HTTPS, que
 *  responda 200 con `image/*`, que no apunte a la red interna, cuánto pesa) y
 *  la enseña: solo se inserta lo que se ha visto. Lo que no pasa la
 *  comprobación no se inserta, y el motivo queda a la vista.
 *
 *  Vive aparte de `RichEditor.tsx` (que carga TinyMCE entero al importarse)
 *  para poder probar el diálogo sin TinyMCE: recibe cualquier cosa con
 *  `windowManager.open` e `insertContent`. */

type Dialogo = Ui.Dialog.DialogSpec<DatosDialogo>;
type ApiDialogo = Ui.Dialog.DialogInstanceApi<DatosDialogo>;
type Componente = Ui.Dialog.BodyComponentSpec;

export type DatosDialogo = { url: string };

/** Lo justo del editor de TinyMCE que necesita el diálogo. */
export type EditorParaImagenUrl = {
  windowManager: { open: (config: Dialogo) => ApiDialogo };
  insertContent: (html: string) => void;
};

/** Los mismos textos que el backend (`externo/red.py`, `api/imagen_url.py`):
 *  lo que se ve no cambia según quién lo haya rechazado. */
export const MOTIVO_NO_HTTPS =
  "Solo se admiten direcciones https://: una imagen en http:// la bloquean los clientes de correo.";
export const MOTIVO_VACIA = "Pega el enlace de la imagen (https://…).";
export const MOTIVO_NO_URL = "La dirección tiene que empezar por https://.";

export type ValidacionCliente =
  | { ok: true; url: string }
  | { ok: false; url: string; codigo: string; motivo: string };

/** Lo que se puede decir sin preguntar al backend: vacía, http:// u otra cosa
 *  que no es https://. El resto (que exista, que sea imagen, que no sea de la
 *  red interna) lo decide el backend. Solo se recortan los espacios de los
 *  lados (los que arrastra el pegado): la URL no se toca. */
export function validarImagenUrl(texto: string): ValidacionCliente {
  const url = texto.trim();
  if (!url) return { ok: false, url, codigo: "url_invalida", motivo: MOTIVO_VACIA };
  const esquema = /^([a-z][a-z0-9+.-]*):/i.exec(url)?.[1]?.toLowerCase() ?? "";
  if (esquema === "http") return { ok: false, url, codigo: "no_https", motivo: MOTIVO_NO_HTTPS };
  if (esquema !== "https") return { ok: false, url, codigo: "url_invalida", motivo: MOTIVO_NO_URL };
  return { ok: true, url };
}

/** Solo para valores de atributo que escribimos nosotros: un `"` en la URL no
 *  rompe la etiqueta y el navegador lee la URL de vuelta exactamente igual. */
function escaparAtributo(valor: string): string {
  return valor
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function escaparTexto(valor: string): string {
  return valor.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** Lo que se inserta en el correo: la URL tal cual (enlazada en caliente),
 *  `alt` vacío para que Bart lo rellene si quiere, y que nunca se salga del
 *  ancho del correo. */
export function htmlImagenUrl(url: string): string {
  return `<img src="${escaparAtributo(url)}" alt="" style="max-width:100%;height:auto">`;
}

/** «350 KB», «1,4 MB» o null si el servidor no lo dice. */
export function pesoLegible(bytes: number | null | undefined): string | null {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes < 0) return null;
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1).replace(".", ",")} MB`;
}

const NOTA_INICIAL =
  "La imagen no se sube a ningún sitio: el correo la enlaza tal cual desde su web. "
  + "Pulsa «Comprobar» para verla antes de insertarla.";
const NO_SE_VE =
  "La comprobación ha ido bien pero aquí no se ve: puede que su web no deje enseñarla desde "
  + "otra página. Si tampoco la ves en el editor, usa otro enlace.";

/** El diálogo según lo comprobado: sin comprobar, la nota; con error, el
 *  motivo; bien, la vista previa (y el aviso de peso si lo hay). «Insertar»
 *  solo se habilita con una comprobación buena. */
export function especDialogoImagenUrl(
  url: string,
  comprobada: ComprobacionImagenUrl | null,
  acciones: Pick<Dialogo, "onAction" | "onChange" | "onSubmit" | "onClose">,
): Dialogo {
  const items: Componente[] = [
    {
      type: "input",
      name: "url",
      label: "Enlace de la imagen (https://…)",
      placeholder: "https://www.tuweb.com/imagenes/foto.jpg",
    },
    { type: "button", name: "comprobar", text: "Comprobar", buttonType: "secondary" },
  ];
  if (comprobada === null) {
    items.push({ type: "htmlpanel", html: `<p class="imagen-url-nota">${escaparTexto(NOTA_INICIAL)}</p>` });
  } else if (!comprobada.ok) {
    items.push({ type: "alertbanner", level: "error", icon: "warning", text: comprobada.motivo });
  } else {
    const peso = pesoLegible(comprobada.bytes);
    const datos = [comprobada.content_type, peso ?? "peso desconocido", "enlazada tal cual, sin subirla"]
      .filter(Boolean).join(" · ");
    items.push({
      type: "htmlpanel",
      html:
        `<div class="imagen-url-previa">`
        + `<img src="${escaparAtributo(comprobada.url)}" alt="Vista previa de la imagen" referrerpolicy="no-referrer">`
        + `</div>`
        + `<p class="imagen-url-dato">${escaparTexto(datos)}</p>`,
      // Si la web no deja enseñarla fuera (protección contra el enlazado),
      // aquí se ve rota: se dice, en vez de dejar un icono roto sin más.
      onInit: (el: HTMLElement) => {
        const img = el.querySelector("img");
        img?.addEventListener("error", () => {
          const nota = el.ownerDocument.createElement("p");
          nota.className = "imagen-url-nota imagen-url-nota-error";
          nota.textContent = NO_SE_VE;
          img.replaceWith(nota);
        });
      },
    });
    if (comprobada.aviso) {
      items.push({ type: "alertbanner", level: "warn", icon: "warning", text: comprobada.aviso });
    }
  }
  return {
    title: "Insertar imagen por URL",
    size: "medium",
    body: { type: "panel", items },
    initialData: { url },
    buttons: [
      { type: "cancel", text: "Cancelar" },
      {
        type: "submit", name: "insertar", text: "Insertar", primary: true,
        enabled: Boolean(comprobada?.ok),
      },
    ],
    ...acciones,
  };
}

/** Abre el diálogo. `comprobar` se puede cambiar en los tests. */
export function abrirDialogoImagenUrl(
  editor: EditorParaImagenUrl,
  comprobar: (url: string) => Promise<ComprobacionImagenUrl> = comprobarImagenUrl,
): void {
  // La última comprobación y la URL a la que corresponde: «Insertar» mete esa
  // y solo esa (si se cambia el enlace después de comprobarlo, hay que volver
  // a comprobar).
  let comprobada: ComprobacionImagenUrl | null = null;
  let comprobando = false;
  let cerrado = false;

  const urlDe = (api: ApiDialogo) => String(api.getData().url ?? "").trim();
  const vale = (url: string) => comprobada !== null && comprobada.ok && comprobada.url === url;

  async function comprobarAhora(api: ApiDialogo) {
    if (comprobando) return;
    const local = validarImagenUrl(urlDe(api));
    if (!local.ok) {
      comprobada = { ok: false, url: local.url, codigo: local.codigo, motivo: local.motivo };
      api.redial(especDialogoImagenUrl(local.url, comprobada, acciones));
      return;
    }
    const url = local.url;
    comprobando = true;
    api.block("Comprobando la imagen…");
    let resultado: ComprobacionImagenUrl;
    try {
      // Lo que se inserta es el enlace comprobado, exactamente el que se pegó.
      resultado = { ...(await comprobar(url)), url };
    } catch (err) {
      resultado = {
        ok: false, url, codigo: "error",
        motivo: extractErrorMessage(err, "No se ha podido comprobar la imagen: prueba otra vez."),
      };
    } finally {
      comprobando = false;
      if (!cerrado) api.unblock();
    }
    comprobada = resultado;
    if (!cerrado) api.redial(especDialogoImagenUrl(url, comprobada, acciones));
  }

  const acciones: Pick<Dialogo, "onAction" | "onChange" | "onSubmit" | "onClose"> = {
    onClose: () => { cerrado = true; },
    onAction: (api, details) => {
      if (details.name === "comprobar") void comprobarAhora(api);
    },
    // Cambiar el enlace después de comprobarlo deshabilita «Insertar» (sin
    // redibujar el diálogo, que se llevaría el cursor del campo).
    onChange: (api, details) => {
      if (details.name === "url") api.setEnabled("insertar", vale(urlDe(api)));
    },
    // Intro en el campo también llega aquí: sin una comprobación buena de ESTE
    // enlace, comprueba en vez de insertar.
    onSubmit: (api) => {
      const url = urlDe(api);
      if (!vale(url)) {
        void comprobarAhora(api);
        return;
      }
      editor.insertContent(htmlImagenUrl(url));
      api.close();
    },
  };

  editor.windowManager.open(especDialogoImagenUrl("", null, acciones));
}
