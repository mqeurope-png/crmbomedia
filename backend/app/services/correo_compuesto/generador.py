"""El HTML de correo de una estructura de bloques, y su texto plano.

Reglas de los clientes de correo, no de gusto: maquetación con `<table>`,
estilos en línea en cada elemento, ancho máximo de 600 px centrado, columnas
que se apilan en móvil con una consulta de medio (la única hoja de estilo, en
el `<head>`), imágenes con `alt`, ancho explícito y URL absoluta, y nada de
JavaScript, fuentes externas, SVG ni `<div>`.

El aspecto es el de los correos que Bomedia manda hoy: fondo blanco, textos en
la gama pizarra, fichas con la imagen arriba, insignia, el área en una tabla
de etiqueta y valor, las características con ✓, el precio centrado en el color
de acento del producto con «+ IVA» y el botón a lo ancho en mayúsculas con su
flecha; fichas compactas cuando van tres en fila; el vídeo en un panel oscuro;
la tira de marca con el logo y el enlace a su web; el hero con su imagen de
las webs encima, al lado o de fondo, y la galería de muestras en columnas. El
código es de BoHub.

`generar` es determinista y no toca la base de datos: recibe la estructura y
los datos ya resueltos (`Datos`: los productos del catálogo en el idioma del
correo, las marcas y los avisos de IVA). Con `preview=True` cada bloque lleva
`data-bh-bloque` y cada texto editable `data-bh-campo` (la vista previa los
usa para editar pulsando encima); el HTML que se envía o se copia va sin ellos.
"""

from __future__ import annotations

import html as html_mod
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from app.services.asistente.texto import html_a_texto
from app.services.correo_compuesto import bloques as bq
from app.services.correo_compuesto import videos as videos_mod

ANCHO = 600
#: Por debajo de este ancho las columnas se apilan (todo lo que no es un
#: escritorio: los móviles, también en horizontal).
ANCHO_MOVIL = 600
FUENTE = "'Helvetica Neue',Helvetica,Arial,sans-serif"

# La paleta: la gama pizarra de los correos de hoy.
TINTA = "#0f172a"
TEXTO = "#1e293b"
VALOR = "#334155"
SECUNDARIO = "#475569"
APAGADO = "#64748b"
ETIQUETA = "#94a3b8"
BORDE = "#e2e8f0"
BORDE_SUAVE = "#f1f5f9"
ENLACE = "#2563eb"
BOTON = "#1d4ed8"
ACENTO = "#2563eb"
PANEL_VIDEO = "#0f172a"
ENLACE_VIDEO = "#93c5fd"

ANCHO_FICHA_SOLA = 320
ANCHO_MINIATURA_VIDEO = 480
#: El ancho útil de una fila (600 menos el relleno de 8 px a cada lado).
ANCHO_UTIL = ANCHO - 16
#: El fondo del hero «fondo» cuando la imagen no carga (Outlook no pinta
#: imágenes de fondo): el texto, en blanco, se sigue leyendo.
FONDO_HERO = "#0f172a"
LOGO_ALTO = 28
LOGO_ANCHO_MAX = 180

#: Las marcas con ficha propia: PimPam lleva sus tonos naranja y «Detalles».
ESTILO_MARCA: dict[str, dict[str, str]] = {
    "pimpam": {"borde": "#fed7aa", "linea": "#ffedd5", "fondo": "#fff7ed", "boton": "detalles"},
}

ESTILO_MOVIL = (
    f"@media only screen and (max-width: {ANCHO_MOVIL}px) {{"
    " .bh-wrap { width: 100% !important; max-width: 100% !important; }"
    " .bh-col { display: block !important; width: 100% !important; max-width: 100% !important;"
    " box-sizing: border-box !important; }"
    " .bh-ficha { padding: 0 0 16px 0 !important; }"
    " .bh-img { width: 100% !important; max-width: 100% !important; height: auto !important; }"
    " .bh-sola { width: 100% !important; max-width: 100% !important; }"
    " .bh-izq { text-align: left !important; }"
    " }"
)

#: Marcas de la firma: las mismas que pone el compositor, para que la
#: comparación de lo enviado con el borrador la quite igual.
FIRMA_INICIO = "<!--crmbo:signature-->"
FIRMA_FIN = "<!--/crmbo:signature-->"

_IVA_EN_PRECIO_RE = re.compile(r"\b(IVA|VAT|MwSt|USt|TVA|TTC|HT|BTW)\b", re.IGNORECASE)


#: Un precio que nombra el impuesto: sin él, «instalación incluida» no dice
#: nada del IVA.
_IMPUESTO_RE = re.compile(r"\b(?:iva|vat|mwst|ust|tva|btw|ttc|netto|tax\w*)\b|\bHT\b",
                          re.IGNORECASE)


def iva_del_texto(precio: str) -> str | None:
    """`sin_iva`/`con_iva` según lo que dice el propio precio («14.900 € + IVA»,
    «IVA no incluido», «plus VAT»), o None si no dice nada. La misma regla con
    la que el índice guarda el IVA del precio de la web."""
    from app.services.webs import lector  # noqa: PLC0415

    if not _IMPUESTO_RE.search(precio or ""):
        return None
    iva = lector.iva_de(precio)
    return None if iva == lector.IVA_DESCONOCIDO else iva


@dataclass
class Datos:
    """Lo que el generador necesita del catálogo, ya resuelto."""

    #: `bloques.clave_de(producto)` → el producto como lo da
    #: `catalogo.producto_para_correo` (en el idioma del correo).
    productos: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: clave de la marca (minúsculas, solo letras y cifras) → nombre, logo,
    #: color, URLs por idioma y sus datos de diseño.
    marcas: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Los avisos del catálogo en el idioma del correo (IVA, VIES, «no es una
    #: factura», precio orientativo).
    avisos: dict[str, str] = field(default_factory=dict)
    #: `videos.clave(video)` → los datos del vídeo (URL, miniatura, título,
    #: duración), de su origen (`videos.resolver`).
    videos: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: id del índice de imágenes → título, medidas y tamaños (`{"url", "ancho",
    #: "alto"}`, de las mismas proporciones), de la fila viva.
    imagenes: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Si los precios de cada fuente llevan IVA (`con`, `sin` o nada).
    iva_por_fuente: dict[str, str | None] = field(default_factory=dict)


@dataclass
class Correo:
    html: str
    texto: str
    #: Lo que no se pudo poner (un producto que ya no está en el catálogo…).
    avisos: list[str] = field(default_factory=list)
    #: Los precios que salen en el correo, tal cual.
    precios: list[str] = field(default_factory=list)
    #: De ellos, los de las fichas del catálogo y los de la web (el que dice la
    #: web, no el que indicó quien envía): un texto que repite uno de la web lo
    #: presenta como si fuera de oferta.
    precios_catalogo: list[str] = field(default_factory=list)
    precios_web: list[str] = field(default_factory=list)
    #: El pie que lleva el correo: el de la persona o los avisos del catálogo.
    pie: str = ""


def clave_marca(nombre: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(nombre or "").lower())


def _e(valor: Any) -> str:
    return html_mod.escape(str(valor or ""), quote=True)


def _color(valor: Any) -> str | None:
    s = str(valor or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", s):
        return s.lower()
    if re.fullmatch(r"#[0-9a-fA-F]{3}", s):
        return "#" + "".join(c * 2 for c in s[1:]).lower()
    return None


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _texto_sobre(color: str) -> str:
    """Blanco o tinta, el que se lea sobre `color`."""
    r, g, b = _rgb(color)
    return "#ffffff" if (0.299 * r + 0.587 * g + 0.114 * b) < 160 else TINTA


def _tinte(color: str, claro: float) -> str:
    """`color` mezclado con blanco (fondos y bordes suaves)."""
    r, g, b = _rgb(color)
    return "#" + "".join(f"{round(c + (255 - c) * claro):02x}" for c in (r, g, b))


def _entero(valor: Any, defecto: int, minimo: int, maximo: int) -> int:
    try:
        n = int(float(str(valor).strip().removesuffix("px")))
    except (TypeError, ValueError):
        return defecto
    return max(minimo, min(maximo, n))


def _campo(preview: bool, campo: str) -> str:
    return f' data-bh-campo="{_e(campo)}"' if preview else ""


def _fila(contenido: str, *, bloque: dict[str, Any] | None, preview: bool, padding: str,
          estilo: str = "", alinear: str = "") -> str:
    datos = (f' data-bh-bloque="{_e(bloque["id"])}" data-bh-tipo="{_e(bloque["tipo"])}"'
             if preview and bloque else "")
    align = f' align="{alinear}"' if alinear else ""
    return (f"<tr{datos}><td{align} style=\"padding:{padding};font-family:{FUENTE};{estilo}\">"
            f"{contenido}</td></tr>")


def _tabla(contenido: str, *, estilo: str = "", ancho: str = "100%", atributos: str = "") -> str:
    width = f' width="{ancho}"' if ancho else ""
    return (f'<table role="presentation"{width} cellpadding="0" cellspacing="0" '
            f'border="0"{atributos} style="border-collapse:separate;mso-table-lspace:0pt;'
            f'mso-table-rspace:0pt;{estilo}">{contenido}</table>')


def _p(contenido: str, estilo: str, atributos: str = "") -> str:
    return f'<p{atributos} style="margin:0;font-family:{FUENTE};{estilo}">{contenido}</p>'


def _lineas(texto: str) -> str:
    return "<br>".join(_e(ln) for ln in texto.split("\n"))


def _tamano(img: dict[str, Any], ancho: int) -> str | None:
    """La URL del tamaño de la imagen que cubre `ancho` px en el correo al doble
    de densidad (pantallas retina), o el mayor si ninguno llega; nunca la
    original de varios megas si hay uno que basta."""
    objetivo = ancho * 2
    tamanos = sorted((t for t in img.get("tamanos") or [] if isinstance(t, dict)),
                     key=lambda t: int(t.get("ancho") or 0))
    elegido = next((t for t in tamanos if int(t.get("ancho") or 0) >= objetivo),
                   tamanos[-1] if tamanos else None)
    return bq.url_valida((elegido or {}).get("url")) or bq.url_valida(img.get("url"))


def _url_css(url: str) -> str:
    """Una URL dentro de `url('…')` de una hoja de estilo en línea."""
    return url.replace("'", "%27").replace("(", "%28").replace(")", "%29")


class _Render:
    """Un correo en construcción: el HTML, el texto plano y lo que se anota."""

    def __init__(self, estructura: dict[str, Any], datos: Datos, preview: bool) -> None:
        self.e = estructura
        self.datos = datos
        self.preview = preview
        self.idioma = estructura.get("idioma") or bq.IDIOMA_RAIZ
        self.avisos: list[str] = []
        self.precios: list[str] = []
        self.precios_catalogo: list[str] = []
        self.precios_web: list[str] = []
        self.fuentes_con_precio: list[str] = []
        primero = next(iter(datos.productos.values()), None)
        self.acento = (self._color_de_marca(estructura.get("marca"))
                       or (self._acento(primero) if primero else None) or ACENTO)

    def t(self, clave: str) -> str:
        return bq.texto_fijo(clave, self.idioma)

    # -- marcas y productos --------------------------------------------------------------

    def _marca(self, nombre: Any) -> dict[str, Any] | None:
        return self.datos.marcas.get(clave_marca(nombre)) if nombre else None

    def _color_de_marca(self, nombre: Any) -> str | None:
        m = self._marca(nombre)
        return _color(m.get("color")) if m else None

    def _acento(self, p: dict[str, Any]) -> str:
        return _color(p.get("acento")) or self._color_de_marca(p.get("marca")) or ACENTO

    def _producto(self, ref: dict[str, str]) -> dict[str, Any] | None:
        p = self.datos.productos.get(bq.clave_de(ref))
        if p is None:
            donde = ("el índice de las webs" if ref.get("fuente") == bq.FUENTE_WEB
                     else "el catálogo")
            self.avisos.append(
                f"El producto «{ref['origen_id']}» ya no está en {donde}: no sale en el correo.")
            return None
        if p.get("fuente") != bq.FUENTE_WEB:
            return p
        # Una ficha de la web: el precio que indicó quien envía y otra imagen
        # de su galería, si los lleva la estructura.
        if ref.get("precio"):
            p = {**p, "precio_texto": ref["precio"], "precio_publico": True,
                 "precio_consultar": False, "precio_origen": "usuario",
                 "precio_iva": iva_del_texto(ref["precio"])}
        imagen = ref.get("imagen")
        if imagen and imagen in [p.get("imagen"), *(p.get("galeria") or [])]:
            p = {**p, "imagen": imagen}
        return p

    def _sufijo_iva(self, precio: str, fuente: str) -> str:
        if _IVA_EN_PRECIO_RE.search(precio):
            return ""
        iva = self._iva(fuente)
        return self.t("iva_sin") if iva == "sin" else self.t("iva_con") if iva == "con" else ""

    def _iva(self, fuente: str) -> str | None:
        """`con`/`sin`/None: el del catálogo para su fuente; en una ficha de la
        web (`web:sin_iva`…), lo que dice la web de ese precio."""
        if fuente.startswith(f"{bq.FUENTE_WEB}:"):
            return {"sin_iva": "sin", "con_iva": "con"}.get(fuente.split(":", 1)[1])
        return self.datos.iva_por_fuente.get(fuente)

    # -- la ficha de producto --------------------------------------------------------------

    def _ficha(self, p: dict[str, Any], *, bloque: dict[str, Any], compacta: bool,
               ancho_img: int) -> tuple[str, str]:
        """La ficha (grande o compacta) y su texto plano."""
        acento = self._acento(p)
        propio = ESTILO_MARCA.get(clave_marca(p.get("marca")), {})
        borde = propio.get("borde", BORDE)
        linea = propio.get("linea", BORDE_SUAVE)
        fondo_cuerpo = f"background-color:{propio['fondo']};" if propio.get("fondo") else ""
        radio = 10 if compacta else 12
        enlace = bq.url_valida(p.get("enlace"))
        imagen = bq.url_valida(p.get("imagen"))
        nombre = str(p.get("nombre") or "")
        texto: list[str] = [nombre]
        filas: list[str] = []
        if imagen:
            img = (f'<img src="{_e(imagen)}" alt="{_e(nombre)}" width="{ancho_img}" class="bh-img" '
                   f'style="display:block;width:100%;max-width:{ancho_img}px;height:auto;border:0;'
                   f'outline:none;text-decoration:none;margin:0 auto;'
                   f'border-radius:{6 if compacta else 8}px;">')
            if enlace:
                img = (f'<a href="{_e(enlace)}" target="_blank" '
                       f'style="text-decoration:none;display:block;">{img}</a>')
            relleno = "6px 3px 3px" if compacta else "8px 4px 4px"
            filas.append(f'<tr><td align="center" style="padding:{relleno};'
                         f'background-color:#ffffff;border-bottom:1px solid {linea};'
                         f'text-align:center;border-radius:{radio}px {radio}px 0 0;">'
                         f"{img}</td></tr>")
        cuerpo: list[str] = []
        badge = str(p.get("badge") or "").strip()
        if badge:
            fondo_badge = _color(p.get("badge_fondo")) or _tinte(acento, 0.88)
            color_badge = _color(p.get("badge_color")) or acento
            cuerpo.append(_tabla(
                f'<tr><td style="background-color:{fondo_badge};border-radius:20px;'
                f'padding:{"2px 7px" if compacta else "3px 9px"};font-family:{FUENTE};'
                f'font-size:{8 if compacta else 9}px;font-weight:800;'
                f'letter-spacing:{1 if compacta else 1.5}px;text-transform:uppercase;'
                f'color:{color_badge};">{_e(badge)}</td></tr>',
                ancho="", estilo=f"margin:0 0 {4 if compacta else 8}px;"))
        cuerpo.append(_p(_e(nombre), f"font-size:{13 if compacta else 15}px;font-weight:900;"
                                     f"color:{TINTA};line-height:1.3;"))
        descripcion = str(p.get("descripcion") or "").strip()
        if descripcion:
            corta = descripcion if len(descripcion) <= 160 else (
                descripcion[:157].rsplit(" ", 1)[0] + "…")
            cuerpo.append(_p(_e(corta), f"font-size:{10 if compacta else 12}px;color:{APAGADO};"
                                        f"margin:{3 if compacta else 5}px 0 0;"
                                        f"line-height:{1.4 if compacta else 1.5};"))
            texto.append(corta)
        es_maquina = p.get("fuente") == "compositor" and p.get("tipo") == "maquina"
        area = str(p.get("categoria") or "").strip() if es_maquina else ""
        area = "" if area == "-" else area
        alto = str(p.get("alto_max") or "").strip() if es_maquina else ""
        alto = "" if alto == "-" else alto
        if area:
            if compacta:
                etiquetas = [f"{self.t('area')}: {area}"]
                if alto:
                    etiquetas.append(f"{self.t('alto')}: {alto}")
                cuerpo.append(_tabla("".join(
                    f'<tr><td style="font-family:{FUENTE};font-size:9px;font-weight:700;'
                    f'color:{ETIQUETA};text-transform:uppercase;">{_e(et)}</td></tr>'
                    for et in etiquetas), estilo="margin:6px 0;"))
            else:
                pares = [(self.t("area"), area)]
                if alto:
                    pares.append((self.t("alto_max"), alto))
                cuerpo.append(_tabla("".join(
                    f'<tr><td width="38%" style="font-family:{FUENTE};font-size:10px;'
                    f'font-weight:700;color:{ETIQUETA};text-transform:uppercase;padding:1px 0;">'
                    f'{_e(et)}</td>'
                    f'<td style="font-family:{FUENTE};font-size:11px;font-weight:700;color:{VALOR};'
                    f'padding:1px 0;">{_e(valor)}</td></tr>' for et, valor in pares),
                    estilo="margin:8px 0;"))
            texto.append(f"{self.t('area')}: {area}" + (
                f" · {self.t('alto_max')}: {alto}" if alto else ""))
        caracteristicas = [str(c).strip() for c in (p.get("caracteristicas") or [])
                           if str(c).strip()][:2]
        for i, c in enumerate(caracteristicas):
            margen = f"{6 if compacta else 8}px 0 0" if i == 0 and not area else "0"
            cuerpo.append(_p(f"✓ {_e(c)}", f"font-size:{10 if compacta else 11}px;"
                                          f"color:{SECUNDARIO};padding:{1 if compacta else 2}px 0;"
                                          f"margin:{margen};"))
            texto.append(f"✓ {c}")
        precio = str(p.get("precio_texto") or "").strip()
        estilo_precio = (f"font-size:{13 if compacta else 16}px;font-weight:900;color:{acento};"
                         f"margin:{8 if compacta else 10}px 0 0;text-align:center;")
        if precio:
            self.precios.append(precio)
            fuente = str(p.get("fuente") or "")
            if fuente == bq.FUENTE_WEB:
                if p.get("precio_origen") != "usuario":
                    self.precios_web.append(precio)
                # El IVA de una ficha de la web es el de su precio: el que dice
                # la web o, si lo indicó quien envía, el que dice ese precio.
                fuente = f"{bq.FUENTE_WEB}:{p.get('precio_iva') or 'desconocido'}"
            else:
                self.precios_catalogo.append(precio)
            if fuente not in self.fuentes_con_precio:
                self.fuentes_con_precio.append(fuente)
            sufijo = self._sufijo_iva(precio, fuente)
            sufijo_html = ""
            if sufijo:
                envoltorio = f"({sufijo})" if compacta else sufijo
                sufijo_html = (f' <span style="font-size:{9 if compacta else 11}px;font-weight:500;'
                               f'color:{SECUNDARIO};">{_e(envoltorio)}</span>')
            cuerpo.append(_p(_e(precio) + sufijo_html, estilo_precio))
            notas = []
            if p.get("orientativo") and self.datos.avisos.get("orientativo"):
                notas.append(self.datos.avisos["orientativo"])
            if p.get("idioma_respaldo"):
                notas.append(self.t("precio_otra_web"))
            if notas:
                cuerpo.append(_p(_e(" ".join(notas)), f"font-size:{9 if compacta else 10}px;"
                                                      f"color:{ETIQUETA};margin:3px 0 0;"
                                                      "text-align:center;line-height:1.4;"))
            texto.append(" ".join(t for t in (precio, sufijo) if t)
                         + (f" ({' '.join(notas)})" if notas else ""))
        elif p.get("precio_consultar"):
            # Una ficha de la web sin precio en la web: «precio a consultar».
            consultar = self.t("precio_consultar")
            cuerpo.append(_p(_e(consultar), estilo_precio))
            texto.append(consultar)
        elif p.get("precio_publico") is False:
            # Sin importe: «a medida» en el sitio del precio, sin el IVA.
            a_medida = self.t("precio_a_medida")
            cuerpo.append(_p(_e(a_medida), estilo_precio))
            texto.append(a_medida)
        if enlace:
            etiqueta = bq.en_idioma(bloque.get("boton"), self.idioma) or (
                self.t(propio["boton"]) if propio.get("boton") else
                self.t("info") if compacta else self.t("mas_info"))
            fg = _texto_sobre(acento)
            radio_boton = 6 if compacta else 8
            cuerpo.append(_tabla(
                f'<tr><td align="center" bgcolor="{acento}" style="background-color:{acento};'
                f'border-radius:{radio_boton}px;">'
                f'<a href="{_e(enlace)}" target="_blank"'
                f' style="display:block;text-align:center;font-family:{FUENTE};'
                f'font-size:{10 if compacta else 12}px;font-weight:700;text-decoration:none;'
                f'padding:{"7px 8px" if compacta else "8px 10px"};text-transform:uppercase;'
                f'letter-spacing:{0.3 if compacta else 0.4}px;color:{fg};'
                f'border-radius:{radio_boton}px;">'
                # Lo editable es la etiqueta; la flecha es del diseño.
                f'<span{_campo(self.preview, bloque["id"] + ":boton")}>{_e(etiqueta)}</span>'
                " →</a></td></tr>",
                estilo=f"margin:{6 if compacta else 8}px 0 0;"))
            texto.append(f"{etiqueta}: {enlace}")
        filas.append(f'<tr><td style="padding:{10 if compacta else 14}px;{fondo_cuerpo}'
                     f'font-family:{FUENTE};">{"".join(cuerpo)}</td></tr>')
        html = _tabla("".join(filas), estilo=(
            f"border:{1 if compacta else 1.5}px solid {borde};border-radius:{radio}px;"
            "overflow:hidden;background-color:#ffffff;"))
        return html, "\n".join(t for t in texto if t)

    # -- bloques -------------------------------------------------------------------------

    def texto(self, b: dict[str, Any]) -> tuple[str, str]:
        html = bq.limpiar_texto_rico(b.get("html"))
        if not html:
            return "", ""
        base = f"font-family:{FUENTE};font-size:14px;line-height:1.65;color:{TEXTO};"
        con_estilo = html.replace("<p>", f'<p style="margin:0 0 14px;{base}">')
        con_estilo = re.sub(r"<(ul|ol)>", lambda m: f'<{m.group(1)} style="margin:0 0 14px;'
                                                     f'padding:0 0 0 20px;{base}">', con_estilo)
        con_estilo = con_estilo.replace("<li>", '<li style="margin:0 0 4px;">')
        con_estilo = re.sub(r'<a href="([^"]*)">',
                            lambda m: f'<a href="{m.group(1)}" target="_blank" '
                                      f'style="color:{ENLACE};text-decoration:underline;">',
                            con_estilo)
        contenido = _tabla(f"<tr><td{_campo(self.preview, b['id'] + ':html')} style=\"padding:0;"
                           f'{base}">{con_estilo}</td></tr>')
        return (_fila(contenido, bloque=b, preview=self.preview, padding="16px 8px 2px"),
                html_a_texto(html))

    def producto(self, b: dict[str, Any]) -> tuple[str, str]:
        p = self._producto(b["producto"])
        if p is None:
            return "", ""
        ficha, texto = self._ficha(p, bloque=b, compacta=False, ancho_img=ANCHO_FICHA_SOLA - 16)
        # Centrada (12/10/2026: a 320 px pegada a la izquierda dejaba medio correo
        # vacío); en el móvil, a todo el ancho (`.bh-sola`).
        sola = _tabla(f'<tr><td style="padding:0;">{ficha}</td></tr>', ancho=str(ANCHO_FICHA_SOLA),
                      atributos=' class="bh-sola" align="center"',
                      estilo=f"width:{ANCHO_FICHA_SOLA}px;margin:0 auto;")
        return (_fila(sola, bloque=b, preview=self.preview, padding="8px 8px 16px",
                      alinear="center"), texto)

    def productos_fila(self, b: dict[str, Any]) -> tuple[str, str]:
        pares = [(r, p) for r, p in ((r, self._producto(r)) for r in b.get("productos") or [])
                 if p]
        productos = [p for _, p in pares]
        if not productos:
            return "", ""
        if len(productos) == 1:
            # La referencia tal cual: una ficha de la web conserva su precio y
            # su imagen.
            return self.producto({**b, "tipo": bq.TIPO_PRODUCTO, "producto": pares[0][0]})
        n = len(productos)
        compacta = n >= 3
        separacion = 4 if compacta else 5
        ancho_col = (ANCHO - 16) // n
        ancho_img = ancho_col - 2 * separacion - (8 if compacta else 12)
        celdas: list[str] = []
        textos: list[str] = []
        for i, p in enumerate(productos):
            ficha, texto = self._ficha(p, bloque=b, compacta=compacta, ancho_img=ancho_img)
            if i == 0:
                relleno = f"0 {separacion}px 0 0"
            elif i == n - 1:
                relleno = f"0 0 0 {separacion}px"
            else:
                relleno = f"0 {separacion}px"
            celdas.append(f'<td class="bh-col bh-ficha" width="{ancho_col}" valign="top" '
                          f'style="width:{round(100 / n, 2)}%;padding:{relleno};">{ficha}</td>')
            textos.append(texto)
        fila = _tabla(f'<tr>{"".join(celdas)}</tr>')
        return (_fila(fila, bloque=b, preview=self.preview, padding="8px 8px 16px"),
                "\n\n".join(textos))

    def marca(self, b: dict[str, Any]) -> tuple[str, str]:
        m = self._marca(b.get("marca")) or {}
        color = _color(m.get("color")) or self.acento
        nombre = str(m.get("nombre") or b.get("marca") or "")
        etiqueta = str(bq.en_idioma(m.get("etiqueta"), self.idioma) or "").strip() or (
            nombre.replace("_", " ").upper() if nombre.islower() else nombre)
        enlace = bq.url_valida(bq.en_idioma(b.get("enlace"), self.idioma)) or bq.url_valida(
            bq.en_idioma(m.get("urls") or {}, self.idioma))
        texto_enlace = str(bq.en_idioma(m.get("texto_enlace"), self.idioma) or "").strip()
        if enlace and not texto_enlace:
            texto_enlace = (urlparse(enlace).hostname or "").removeprefix("www.")
        logo = bq.url_valida(m.get("logo"))
        alto = _entero(m.get("logo_alto"), LOGO_ALTO, 12, 80)
        ancho = _entero(m.get("logo_ancho_max"), LOGO_ANCHO_MAX, 40, 300)
        if logo:
            marca_html = (f'<img src="{_e(logo)}" alt="{_e(etiqueta)}" width="{ancho}" '
                          f'style="display:block;width:auto;height:auto;max-height:{alto}px;'
                          f'max-width:{ancho}px;border:0;outline:none;">')
        else:
            marca_html = _p(_e(etiqueta), f"font-size:14px;font-weight:800;color:{color};")
        lema = bq.en_idioma(b.get("texto"), self.idioma)
        if lema:
            marca_html += _p(_lineas(lema), f"font-size:12px;color:{APAGADO};margin:6px 0 0;",
                             _campo(self.preview, b["id"] + ":texto"))
        enlace_html = (f'<a href="{_e(enlace)}" target="_blank" style="font-family:{FUENTE};'
                       f'font-size:12px;font-weight:700;color:{color};text-decoration:none;'
                       f'white-space:nowrap;">{_e(texto_enlace)}</a>' if enlace else "&nbsp;")
        fondo_logo = _color(m.get("logo_fondo"))
        relleno = "16px 24px" if fondo_logo else "4px 0"
        celdas = (f'<td class="bh-col" valign="middle" width="70%" style="padding:{relleno};">'
                  f"{marca_html}</td>"
                  f'<td class="bh-col bh-izq" valign="middle" width="30%" align="right" '
                  f'style="padding:{relleno};text-align:right;">{enlace_html}</td>')
        if fondo_logo:
            tira = _tabla(f"<tr>{celdas}</tr>", atributos=f' bgcolor="{fondo_logo}"',
                          estilo=f"background-color:{fondo_logo};border-radius:10px;")
        else:
            divisoria = _color(m.get("divisoria")) or _tinte(color, 0.75)
            tira = _tabla(f"<tr>{celdas}</tr>"
                          f'<tr><td colspan="2" style="padding:8px 0 0;font-size:0;line-height:0;'
                          f'border-bottom:1px solid {divisoria};">&nbsp;</td></tr>')
        plano = " — ".join(t for t in (etiqueta, enlace) if t) + (f"\n{lema}" if lema else "")
        return _fila(tira, bloque=b, preview=self.preview, padding="16px 8px 8px"), plano

    def video(self, b: dict[str, Any]) -> tuple[str, str]:
        # El bloque dice QUÉ vídeo es; sus datos (URL, miniatura, título,
        # duración) vienen de su origen (`videos.resolver`): hoy la URL pegada,
        # luego la tabla de vídeos. Lo que escriba el bloque manda.
        video = videos_mod.identidad(b.get("video"))
        if video is None:
            return "", ""
        datos = self.datos.videos.get(videos_mod.clave(video)) or videos_mod.desde_url(video)
        url = bq.url_valida(datos.get("url")) or videos_mod.url_de(video)
        miniatura = bq.url_valida(datos.get("miniatura"))
        titulo = bq.en_idioma(b.get("titulo"), self.idioma) or str(datos.get("titulo") or "")
        texto = bq.en_idioma(b.get("texto"), self.idioma)
        boton = bq.en_idioma(b.get("boton"), self.idioma) or self.t("ver_video")
        duracion = videos_mod.duracion_texto(datos.get("duracion"))
        partes: list[str] = []
        if titulo:
            partes.append(_p(_lineas(titulo), f"font-size:16px;font-weight:700;color:{TINTA};"
                                              "margin:0 0 10px;line-height:1.3;",
                             _campo(self.preview, b["id"] + ":titulo")))
        filas_panel: list[str] = []
        if miniatura:
            # Siempre al ancho de la ficha (480, o todo el ancho en el móvil):
            # una miniatura pequeña (la 320×180 de YouTube) se escala antes que
            # poner una 4:3 con bandas negras.
            img = (f'<img src="{_e(miniatura)}" '
                   f'alt="{_e(titulo or self.t("video"))}" width="{ANCHO_MINIATURA_VIDEO}" '
                   'class="bh-img" '
                   f'style="display:block;width:100%;max-width:{ANCHO_MINIATURA_VIDEO}px;'
                   'height:auto;margin:0 auto;border:0;opacity:0.85;">')
            filas_panel.append(
                f'<tr><td align="center" style="padding:0;text-align:center;">'
                f'<a href="{_e(url)}" target="_blank" style="text-decoration:none;display:block;">'
                f"{img}</a></td></tr>")
        # Sin miniatura (un vídeo de Vimeo pegado): el panel con su botón.
        filas_panel.append(
            f'<tr><td align="center" bgcolor="{PANEL_VIDEO}" style="padding:12px 16px;'
            f'background-color:{PANEL_VIDEO};text-align:center;">'
            f'<a href="{_e(url)}" target="_blank" '
            f'style="color:{ENLACE_VIDEO};font-family:{FUENTE};font-size:14px;font-weight:700;'
            f'text-decoration:none;">▶ <span{_campo(self.preview, b["id"] + ":boton")}>'
            f"{_e(boton)}</span>{_e(f' · {duracion}') if duracion else ''}</a></td></tr>")
        partes.append(_tabla(
            "".join(filas_panel), atributos=f' bgcolor="{PANEL_VIDEO}"',
            estilo=f"border-radius:12px;overflow:hidden;background-color:{PANEL_VIDEO};"))
        if texto:
            partes.append(_p(_lineas(texto), f"font-size:13px;color:{APAGADO};margin:10px 0 0;"
                                             "line-height:1.5;",
                             _campo(self.preview, b["id"] + ":texto")))
        boton_plano = f"▶ {boton} ({duracion})" if duracion else f"▶ {boton}"
        plano = "\n".join(t for t in (titulo, f"{boton_plano}: {url}", texto) if t)
        return _fila("".join(partes), bloque=b, preview=self.preview, padding="8px 8px 16px"), plano

    def _botones(self, b: dict[str, Any], *, alinear: str = "center") -> tuple[str, list[str]]:
        """Los botones de un bloque (cta, hero) y sus líneas de texto plano."""
        celdas: list[str] = []
        textos: list[str] = []
        color = self.acento if self.e.get("marca") else BOTON
        fg = _texto_sobre(color)
        for i, bt in enumerate(b.get("botones") or []):
            texto = bq.en_idioma(bt.get("texto"), self.idioma)
            url = bq.url_valida(bq.en_idioma(bt.get("url"), self.idioma), mailto=True)
            if not texto or not url:
                continue
            if celdas:
                celdas.append('<td class="bh-col" width="8" style="width:8px;font-size:0;'
                              'line-height:0;padding:0 0 8px;">&nbsp;</td>')
            campo = _campo(self.preview, f"{b['id']}:botones.{i}.texto")
            celdas.append(
                f'<td class="bh-col" align="center" bgcolor="{color}" '
                f'style="background-color:{color};border-radius:6px;padding:0;">'
                f'<a href="{_e(url)}" target="_blank"{campo} style="display:inline-block;'
                f'padding:10px 22px;font-family:{FUENTE};font-size:13px;font-weight:600;color:{fg};'
                f'text-decoration:none;white-space:nowrap;">{_e(texto)}</a></td>')
            textos.append(f"{texto}: {url}")
        if not celdas:
            return "", []
        margen = "margin:0 auto;" if alinear == "center" else "margin:0;"
        return (_tabla(f'<tr>{"".join(celdas)}</tr>', ancho="", atributos=f' align="{alinear}"',
                       estilo=margen), textos)

    def cta(self, b: dict[str, Any]) -> tuple[str, str]:
        botones, textos = self._botones(b)
        if not botones:
            return "", ""
        return (_fila(botones, bloque=b, preview=self.preview, padding="8px 20px 16px",
                      alinear="center"), "\n".join(textos))

    # -- imágenes de las webs: hero y muestras ----------------------------------------------

    def _imagen(self, ref: Any) -> dict[str, Any] | None:
        img = bq.clave_imagen(ref)
        datos = self.datos.imagenes.get(img["id"]) if img else None
        if datos is None:
            self.avisos.append("Una imagen del correo ya no está en el índice de imágenes de "
                               "las webs: no sale.")
        return datos

    def _img(self, img: dict[str, Any], *, ancho: int, alt: str, radio: int = 10) -> str:
        src = _tamano(img, ancho) or ""
        return (f'<img src="{_e(src)}" alt="{_e(alt)}" width="{ancho}" class="bh-img" '
                f'style="display:block;width:100%;max-width:{ancho}px;height:auto;border:0;'
                f'outline:none;text-decoration:none;margin:0 auto;border-radius:{radio}px;">')

    def _textos_hero(self, b: dict[str, Any], *, claro: bool,
                     alinear: str) -> tuple[str, list[str]]:
        titulo = bq.en_idioma(b.get("titulo"), self.idioma)
        subtitulo = bq.en_idioma(b.get("subtitulo"), self.idioma)
        tinta = "#ffffff" if claro else TINTA
        suave = "#e2e8f0" if claro else SECUNDARIO
        partes: list[str] = []
        plano: list[str] = []
        if titulo:
            partes.append(_p(_lineas(titulo), f"font-size:24px;font-weight:800;color:{tinta};"
                                              f"line-height:1.25;margin:0 0 8px;"
                                              f"text-align:{alinear};",
                             _campo(self.preview, b["id"] + ":titulo")))
            plano.append(titulo)
        if subtitulo:
            partes.append(_p(_lineas(subtitulo), f"font-size:15px;color:{suave};"
                                                 f"line-height:1.5;margin:0 0 10px;"
                                                 f"text-align:{alinear};",
                             _campo(self.preview, b["id"] + ":subtitulo")))
            plano.append(subtitulo)
        puntos = [(i, bq.en_idioma(p, self.idioma)) for i, p in enumerate(b.get("puntos") or [])]
        puntos = [(i, p) for i, p in puntos if p]
        if puntos:
            # La tabla de puntos se centra entera; dentro, cada punto va a la izquierda (si no,
            # hereda el centrado de la celda del hero y las marcas ✓ quedan en zigzag).
            filas = "".join(
                f'<tr><td valign="top" style="font-family:{FUENTE};font-size:14px;'
                f'color:{tinta};padding:2px 8px 2px 0;text-align:left;">✓</td>'
                f"<td{_campo(self.preview, b['id'] + f':puntos.{i}')} "
                f'style="font-family:{FUENTE};font-size:14px;color:{suave};line-height:1.45;'
                f'padding:2px 0;text-align:left;">{_e(p)}</td></tr>'
                for i, p in puntos)
            margen = "margin:4px auto 12px;" if alinear == "center" else "margin:4px 0 12px;"
            partes.append(_tabla(filas, ancho="", atributos=f' align="{alinear}"', estilo=margen))
            plano.extend(f"✓ {p}" for _, p in puntos)
        botones, textos_botones = self._botones(b, alinear=alinear)
        if botones:
            partes.append(botones)
            plano.extend(textos_botones)
        return "".join(partes), plano

    def hero(self, b: dict[str, Any]) -> tuple[str, str]:
        img = self._imagen(b.get("imagen"))
        if img is None:
            return "", ""
        disposicion = b.get("disposicion") or "arriba"
        alt = (bq.en_idioma(b.get("titulo"), self.idioma) or str(img.get("titulo") or "")).strip()
        if disposicion == "fondo":
            src = _tamano(img, ANCHO_UTIL) or ""
            textos, plano = self._textos_hero(b, claro=True, alinear="center")
            capa = _tabla(f'<tr><td style="background-color:rgba(15,23,42,0.55);'
                          f'padding:20px 22px;border-radius:10px;font-family:{FUENTE};">'
                          f"{textos}</td></tr>")
            contenido = _tabla(
                f'<tr><td background="{_e(src)}" bgcolor="{FONDO_HERO}" valign="middle" '
                f'style="background-color:{FONDO_HERO};'
                f"background-image:url('{_e(_url_css(src))}');background-size:cover;"
                f'background-position:center;background-repeat:no-repeat;padding:44px 28px;'
                f'border-radius:12px;font-family:{FUENTE};">{capa}</td></tr>')
        elif disposicion == "lateral":
            mitad = ANCHO_UTIL // 2
            textos, plano = self._textos_hero(b, claro=False, alinear="left")
            contenido = _tabla(
                f'<tr><td class="bh-col bh-ficha" width="{mitad}" valign="middle" '
                f'style="width:50%;padding:0 10px 0 0;">'
                f"{self._img(img, ancho=mitad - 10, alt=alt)}</td>"
                f'<td class="bh-col" width="{mitad}" valign="middle" '
                f'style="width:50%;padding:0 0 0 10px;font-family:{FUENTE};">{textos}</td></tr>')
        else:
            textos, plano = self._textos_hero(b, claro=False, alinear="center")
            contenido = _tabla(
                f'<tr><td style="padding:0;">{self._img(img, ancho=ANCHO_UTIL, alt=alt)}</td></tr>'
                + (f'<tr><td align="center" style="padding:16px 12px 0;text-align:center;'
                   f'font-family:{FUENTE};">{textos}</td></tr>' if textos else ""))
        return (_fila(contenido, bloque=b, preview=self.preview, padding="8px 8px 16px"),
                "\n".join(plano))

    def muestras(self, b: dict[str, Any]) -> tuple[str, str]:
        celdas: list[tuple[int, dict[str, Any]]] = []
        for i, ref in enumerate(b.get("imagenes") or []):
            img = self._imagen(ref)
            if img is not None:
                celdas.append((i, img))
        if not celdas:
            return "", ""
        pies = b.get("pies") or []
        titulo = bq.en_idioma(b.get("titulo"), self.idioma)
        n = len(celdas)
        # De 2 en 2 o de 3 en 3: 4 → 2 + 2, 5 → 3 + 2, 6 → 3 + 3.
        por_fila = 2 if n in (2, 4) else 3
        filas_html: list[str] = []
        plano: list[str] = [titulo] if titulo else []
        for inicio in range(0, n, por_fila):
            grupo = celdas[inicio:inicio + por_fila]
            ancho_col = ANCHO_UTIL // por_fila
            tds: list[str] = []
            for j, (i, img) in enumerate(grupo):
                pie = bq.en_idioma(pies[i], self.idioma) if i < len(pies) else ""
                relleno = ("0 6px 12px 0" if j == 0 else
                           "0 0 12px 6px" if j == len(grupo) - 1 else "0 3px 12px")
                alt = pie or str(img.get("titulo") or "")
                pie_html = _p(_e(pie), f"font-size:12px;color:{APAGADO};margin:6px 0 0;"
                                       "text-align:center;line-height:1.4;",
                              _campo(self.preview, f"{b['id']}:pies.{i}")) if pie else ""
                tds.append(f'<td class="bh-col bh-ficha" width="{ancho_col}" valign="top" '
                           f'style="width:{round(100 / por_fila, 2)}%;padding:{relleno};">'
                           f"{self._img(img, ancho=ancho_col - 12, alt=alt, radio=8)}{pie_html}"
                           "</td>")
                if pie:
                    plano.append(pie)
            if len(grupo) < por_fila:
                tds.append(f'<td class="bh-col" width="{ancho_col * (por_fila - len(grupo))}" '
                           'style="font-size:0;line-height:0;">&nbsp;</td>')
            filas_html.append(f"<tr>{''.join(tds)}</tr>")
        cabecera = (_p(_lineas(titulo), f"font-size:16px;font-weight:700;color:{TINTA};"
                                        "margin:0 0 10px;line-height:1.3;",
                       _campo(self.preview, b["id"] + ":titulo")) if titulo else "")
        return (_fila(cabecera + _tabla("".join(filas_html)), bloque=b, preview=self.preview,
                      padding="8px 8px 8px"), "\n".join(plano))

    def destacado(self, b: dict[str, Any]) -> tuple[str, str]:
        estilo = b.get("estilo") or "aviso"
        if estilo == "promo":
            fondo, borde = _tinte(self.acento, 0.93), _tinte(self.acento, 0.7)
        elif estilo == "condicion":
            fondo, borde = "#f8fafc", BORDE
        else:
            fondo, borde = "#fffbeb", "#fde68a"
        titulo = bq.en_idioma(b.get("titulo"), self.idioma)
        texto = bq.en_idioma(b.get("texto"), self.idioma)
        partes = ""
        if titulo:
            partes += _p(_lineas(titulo), f"font-size:16px;font-weight:700;color:{TINTA};"
                                          "margin:0 0 6px;line-height:1.3;",
                         _campo(self.preview, b["id"] + ":titulo"))
        if texto:
            partes += _p(_lineas(texto), f"font-size:13px;color:{SECUNDARIO};line-height:1.5;",
                         _campo(self.preview, b["id"] + ":texto"))
        panel = _tabla(f'<tr><td bgcolor="{fondo}" style="padding:16px 18px;'
                       f'background-color:{fondo};border-radius:8px;">{partes}</td></tr>',
                       estilo=f"border:1px solid {borde};border-radius:8px;")
        return (_fila(panel, bloque=b, preview=self.preview, padding="8px 20px"),
                "\n".join(t for t in (titulo, texto) if t))

    def separador(self, b: dict[str, Any]) -> tuple[str, str]:
        estilo = b.get("estilo") or "linea"
        if estilo == "aire":
            return _fila("&nbsp;", bloque=b, preview=self.preview, padding="12px 20px",
                         estilo="font-size:0;line-height:0;"), ""
        if estilo == "puntos":
            return (_fila("·&nbsp;·&nbsp;·", bloque=b, preview=self.preview, padding="24px 20px",
                          estilo=f"font-size:18px;letter-spacing:8px;color:{BORDE};line-height:1;",
                          alinear="center"), "· · ·")
        if estilo == "corta":
            corta = _tabla(f'<tr><td style="border-top:2px solid {BORDE};font-size:0;'
                           'line-height:0;">&nbsp;</td></tr>', ancho="80",
                           atributos=' align="center"', estilo="width:80px;margin:0 auto;")
            return (_fila(corta, bloque=b, preview=self.preview, padding="24px 20px",
                          alinear="center"), "----")
        linea = _tabla(f'<tr><td style="border-top:1px solid {BORDE};font-size:0;'
                       'line-height:0;">&nbsp;</td></tr>')
        return _fila(linea, bloque=b, preview=self.preview, padding="24px 20px"), "----"

    # -- pie ------------------------------------------------------------------------------

    def notas(self) -> str:
        """Las frases del catálogo cuando el correo enseña algún precio: el IVA
        de cada fuente, VIES y «no es una factura», en el idioma del correo."""
        if not self.precios:
            return ""
        avisos = self.datos.avisos
        frases: list[str] = []
        for fuente in self.fuentes_con_precio:
            iva = self._iva(fuente)
            frase = avisos.get(iva if iva in ("con", "sin") else "sin_declarar")
            if frase and frase not in frases:
                frases.append(frase)
        for clave in ("vies", "no_factura"):
            if avisos.get(clave):
                frases.append(avisos[clave])
        return " ".join(frases)


_RENDER = {
    bq.TIPO_TEXTO: _Render.texto, bq.TIPO_PRODUCTO: _Render.producto,
    bq.TIPO_PRODUCTOS_FILA: _Render.productos_fila, bq.TIPO_MARCA: _Render.marca,
    bq.TIPO_VIDEO: _Render.video, bq.TIPO_CTA: _Render.cta,
    bq.TIPO_DESTACADO: _Render.destacado, bq.TIPO_SEPARADOR: _Render.separador,
    bq.TIPO_HERO: _Render.hero, bq.TIPO_MUESTRAS: _Render.muestras,
}


def generar(estructura: dict[str, Any], datos: Datos, *, asunto: str | None = None,
            preview: bool = False, firma_html: str | None = None) -> Correo:
    """El correo entero (documento HTML autocontenido) y su texto plano."""
    e = bq.normalizar(estructura)
    r = _Render(e, datos, preview)
    filas: list[str] = []
    textos: list[str] = []
    for b in e["bloques"]:
        html, texto = _RENDER[b["tipo"]](r, b)
        if html:
            filas.append(html)
        if texto:
            textos.append(texto)
    pie = e.get("pie") or r.notas()
    if pie:
        filas.append(_fila(
            _p(_lineas(pie), f"font-size:11px;color:{ETIQUETA};line-height:1.5;",
               _campo(preview, ":pie")),
            bloque=None, preview=preview, padding="8px 8px 16px"))
        textos.append(pie)
    if firma_html and firma_html.strip():
        filas.append(_fila(f"{FIRMA_INICIO}{firma_html}{FIRMA_FIN}", bloque=None, preview=preview,
                           padding="8px 8px 16px",
                           estilo=f"font-size:14px;line-height:1.65;color:{TEXTO};"))
        firma_texto = html_a_texto(firma_html)
        if firma_texto:
            textos.append("-- \n" + firma_texto)
    cuerpo = _tabla(
        '<tr><td align="center" style="padding:0;">'
        + _tabla("".join(filas), ancho=str(ANCHO), atributos=' class="bh-wrap"',
                 estilo=f"width:{ANCHO}px;max-width:{ANCHO}px;margin:0 auto;"
                        "background-color:#ffffff;")
        + "</td></tr>",
        atributos=' bgcolor="#ffffff"', estilo="width:100%;background-color:#ffffff;")
    texto = "\n\n".join(t.strip() for t in textos if t.strip())
    return Correo(html=documento(cuerpo, idioma=e["idioma"], asunto=asunto), texto=texto,
                  avisos=r.avisos, precios=r.precios, precios_catalogo=r.precios_catalogo,
                  precios_web=r.precios_web, pie=pie or "")


#: La marca del documento: al enviar, el saneado reconoce por ella un correo
#: compuesto y le aplica su propia lista blanca (`html_sanitizer`), que
#: conserva la maquetación; la cabecera y la hoja de estilo las vuelve a poner
#: esta función, nunca las del HTML que llega.
MARCA_DOCUMENTO = '<meta name="generator" content="BoHub correo compuesto">'


def documento(cuerpo: str, *, idioma: str, asunto: str | None = None) -> str:
    """El documento de correo alrededor del cuerpo: la cabecera (con la marca
    y la única hoja de estilo, la de las columnas en el móvil) y el `<body>`."""
    idioma = idioma if re.fullmatch(r"[a-z]{2}", idioma or "") else bq.IDIOMA_RAIZ
    return (
        "<!DOCTYPE html>"
        f'<html lang="{idioma}" xmlns="http://www.w3.org/1999/xhtml">'
        "<head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="x-apple-disable-message-reformatting">'
        f"{MARCA_DOCUMENTO}"
        f"<title>{_e(asunto)}</title>"
        f'<style type="text/css">{ESTILO_MOVIL}</style>'
        "</head>"
        f'<body style="margin:0;padding:0;background-color:#ffffff;color:{TEXTO};'
        '-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">'
        f"{cuerpo}"
        "</body></html>"
    )
