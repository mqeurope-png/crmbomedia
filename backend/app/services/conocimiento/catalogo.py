"""El catálogo y los precios verificados (Fase 2 de la base de conocimiento).

La regla del asistente no se toca: **el modelo redacta, no aporta hechos**, y
un precio que no esté en los DATOS no se escribe. Lo que faltaba eran los
datos. Aquí viven:

- Las **fuentes** (`kb_catalogo`): las máquinas salen del JSON del compositor
  de correos de Bomedia (`catalogo_compositor.py`); los consumibles y
  repuestos, de las tiendas WooCommerce de las webs (`catalogo_tiendas.py`).
  Todo lo demás no tiene precio: un precio que aparece en un correo antiguo
  no es un precio vigente.
- El **precio tal cual**: `precio_texto` se enseña como está escrito en el
  origen («9.300 €», «9 300 €»); `precio_num` es un número derivado para
  ordenar. **Nunca se convierte una divisa.**
- La **antigüedad**: un precio cuya fuente no se ha confirmado en más de
  `precio_meses_orientativo` meses (6 por defecto) es «orientativo» y el
  correo lo dice así.
- El **IVA**, declarado por fuente en la configuración (con / sin / sin
  declarar), nunca deducido del número. Los correos siempre dicen si el precio
  lleva IVA, avisan de la validación VIES para empresas de la UE y de que un
  correo no es una factura (`AVISOS`, en cinco idiomas).
- **Interés → máquinas** (`kb_interes_productos`): qué máquinas se le enseñan
  a un lead según su interés, por marca y patrón del nombre, editable en
  Configuración ERP. La propuesta de partida (`MAPA_DE_PARTIDA`) la revisa
  Bart; el único interés comercial sin máquina en el compositor es el CNC:
  queda como hueco y se enlaza la página de producto del interés.
- **De dónde se eligen las máquinas** (`para_asistente`), por este orden: lo
  que el cliente nombra en el hilo (por el nombre y, más flojo, por la
  descripción, las características y el texto alternativo), lo que pide la
  instrucción de quien envía («las máquinas de DTF» manda), los consumibles
  de la máquina que ya tiene y, por último, el interés clasificado.
- Los **consumibles y repuestos de la máquina que tiene el cliente**: se
  casan por las palabras de sus pedidos contra el nombre y la categoría de
  los productos de las tiendas.
- **Contra los datos reales** (11/10/2026): «Consultar» no es un precio sino la
  marca de que no hay precio público (`precio_publico`); una entrada sin
  precio y sin área de impresión no es una máquina (`descartado`, visible en
  la pantalla); ningún valor del origen desborda su columna (se recorta antes
  de escribir) y la escritura va por lotes con captura de error por fila: lo
  que no entra se salta, se cuenta y la pasada termina.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, ProgrammingError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.conocimiento import (
    FUENTE_COMPOSITOR,
    FUENTE_WOOCOMMERCE,
    FUENTES,
    TIPO_MAQUINA,
    TIPOS_PRODUCTO,
    CatalogoSincronizacion,
    InteresProducto,
    MarcaCatalogo,
    ProductoCatalogo,
)
from app.services.web_forms import sitios as registro_sitios

logger = logging.getLogger(__name__)

#: Cuántas máquinas y cuántos consumibles entran como mucho en un borrador:
#: el catálogo entero no va en cada llamada, se elige por interés y máquina.
MAQUINAS_MAX = 6
CONSUMIBLES_MAX = 8
#: Idioma de los campos raíz del compositor (`desc`, `price`, `link`); los
#: demás van en `i18n`.
IDIOMA_RAIZ = "es"
#: Idioma del aviso cuando el del correo no es uno de los cinco.
IDIOMA_AVISO_RESPALDO = "en"

#: Declaración del IVA por fuente (configuración `conocimiento.iva_por_fuente`).
IVA_CON = "con"
IVA_SIN = "sin"
IVA_VALORES: tuple[str, ...] = (IVA_CON, IVA_SIN)

#: Los textos que el correo lleva cuando cita un precio, en los cinco idiomas
#: del compositor. Son constantes: cambiarlos es un despliegue (anotado en el PR).
AVISOS: dict[str, dict[str, str]] = {
    "es": {
        "con": "Precios con IVA incluido.",
        "sin": "Precios sin IVA.",
        "sin_declarar": "Pendiente de confirmar si los precios incluyen IVA.",
        "vies": "Para empresas de la UE con número de IVA intracomunitario validado en VIES, la "
                "operación puede ir sin IVA.",
        "no_factura": "Este correo es información comercial, no una factura ni una oferta en "
                      "firme: lo confirmamos al preparar la oferta.",
        "orientativo": "Precio orientativo, lo confirmamos al preparar la oferta.",
    },
    "en": {
        "con": "Prices include VAT.",
        "sin": "Prices exclude VAT.",
        "sin_declarar": "Whether prices include VAT is still to be confirmed.",
        "vies": "For EU companies with a VAT number validated in VIES, the sale may be "
                "VAT-exempt.",
        "no_factura": "This email is commercial information, not an invoice or a binding offer: "
                      "we will confirm everything when preparing the quotation.",
        "orientativo": "Indicative price, to be confirmed when we prepare the quotation.",
    },
    "de": {
        "con": "Preise inkl. MwSt.",
        "sin": "Preise zzgl. MwSt.",
        "sin_declarar": "Ob die Preise die MwSt. enthalten, ist noch zu bestätigen.",
        "vies": "Für EU-Unternehmen mit einer in VIES validierten USt-IdNr. kann die Lieferung "
                "ohne MwSt. erfolgen.",
        "no_factura": "Diese E-Mail ist eine kommerzielle Information, keine Rechnung und kein "
                      "verbindliches Angebot: wir bestätigen alles mit dem Angebot.",
        "orientativo": "Richtpreis, wird mit dem Angebot bestätigt.",
    },
    "fr": {
        "con": "Prix TVA incluse.",
        "sin": "Prix hors TVA.",
        "sin_declarar": "Reste à confirmer si les prix incluent la TVA.",
        "vies": "Pour les entreprises de l'UE avec un numéro de TVA intracommunautaire validé "
                "dans VIES, la vente peut se faire hors TVA.",
        "no_factura": "Cet e-mail est une information commerciale, pas une facture ni une offre "
                      "ferme : nous confirmons tout en préparant le devis.",
        "orientativo": "Prix indicatif, à confirmer lors de la préparation du devis.",
    },
    "nl": {
        "con": "Prijzen inclusief btw.",
        "sin": "Prijzen exclusief btw.",
        "sin_declarar": "Of de prijzen inclusief btw zijn, moet nog worden bevestigd.",
        "vies": "Voor EU-bedrijven met een in VIES gevalideerd btw-nummer kan de levering "
                "zonder btw plaatsvinden.",
        "no_factura": "Deze e-mail is commerciële informatie, geen factuur en geen bindende "
                      "offerte: we bevestigen alles bij het opstellen van de offerte.",
        "orientativo": "Richtprijs, te bevestigen bij het opstellen van de offerte.",
    },
}

#: La propuesta de interés → máquinas (a revisar por Bart): (interés, marca del
#: compositor, patrón del nombre; vacío = toda la marca). Las marcas son los
#: ids del compositor (`artisjet`, `mbo`, `mbo_dtf`, `flux`, `smartjet`,
#: `pimpam`); se casan sin distinguir mayúsculas ni separadores. Reparto del
#: 11/10/2026 contra los datos reales: dtf → mbo_dtf; packaging → smartjet (los
#: overprinters single-pass sobre cajas y bolsas); corte_laser y grabado_laser
#: → flux; uv_* → artisjet y mbo; vending → pimpam. Es la propuesta que se
#: enseña mientras la tabla está vacía; la tabla ya sembrada no se toca desde
#: aquí (se edita en Configuración ERP).
MAPA_DE_PARTIDA: tuple[tuple[str, str, str, str | None], ...] = (
    ("uv_pequeno", "artisjet", "Young", None),
    ("uv_pequeno", "artisjet", "Proud", None),
    ("uv_pequeno", "mbo", "1015", None),
    ("uv_mediano", "artisjet", "3000Pro", None),
    ("uv_mediano", "artisjet", "5000U", None),
    ("uv_mediano", "mbo", "3050", None),
    ("uv_mediano", "mbo", "4060", None),
    ("uv_grande", "artisjet", "6090 Trust", None),
    ("uv_grande", "mbo", "6090", None),
    ("uv_grande", "mbo", "UV1612G", None),
    ("uv_grande", "mbo", "UV1812", None),
    ("uv_grande", "mbo", "UV2513", None),
    ("dtf", "mbo_dtf", "", "Las máquinas DTF de MBO (Orion, Lyra, Astra, Vega y sus hornos)."),
    ("corte_laser", "flux", "", "Las láser de FLUX."),
    ("grabado_laser", "flux", "", "Las láser de FLUX."),
    ("packaging", "smartjet", "", "SmartJet: overprinters single-pass sobre cajas y bolsas."),
    ("vending", "pimpam", "", None),
)
#: Intereses comerciales sin máquina en el compositor (hallazgo del 11/10/2026
#: contra los datos reales: solo el CNC). Se enlaza la página de producto del
#: interés (`kb_rutas`) y no se inventa nada.
HALLAZGO_SIN_PRODUCTO = (
    "El único interés comercial sin máquina en el compositor de correos es el CNC: para él el "
    "asistente enlaza la página de producto del interés y no cita ningún precio. Láser (FLUX), "
    "DTF (MBO DTF), packaging (SmartJet), UV (artisJet y MBO) y vending (PimPam) sí tienen "
    "máquinas."
)
#: Lo que el origen escribe cuando NO hay precio público («Consultar», «on request»).
_SIN_PRECIO_PUBLICO_RE = re.compile(
    r"^\W*(consultar|a consultar|precio a consultar|cons[uú]ltanos|cons[uú]ltenos|bajo consulta|"
    r"on request|price on request|upon request|poa|ask( us)?|auf anfrage|preis auf anfrage|"
    r"sur demande|prix sur demande|op aanvraag|prijs op aanvraag|n/?a|tba|tbd|-+)\W*$",
    re.IGNORECASE,
)
#: Cuántas filas se escriben por lote (un lote que falla se reintenta fila a fila).
LOTE_ESCRITURA = 200
#: Cuántos errores por fila se guardan en el resumen de la pasada.
MAX_ERRORES_RESUMEN = 25

_INTERES_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
_NUMERO_RE = re.compile(r"\d(?:[\d .,  ]*\d)?")
_MONEDAS: tuple[tuple[str, str], ...] = (
    ("€", "EUR"), ("eur", "EUR"), ("euro", "EUR"), ("$", "USD"), ("usd", "USD"), ("us$", "USD"),
    ("£", "GBP"), ("gbp", "GBP"), ("chf", "CHF"), ("fr.", "CHF"), ("zł", "PLN"), ("pln", "PLN"),
    ("kr", "SEK"), ("sek", "SEK"), ("dkk", "DKK"), ("nok", "NOK"), ("czk", "CZK"), ("kč", "CZK"),
)


# --- el precio, tal cual y como número ---------------------------------------------------


def moneda_de(texto: str | None) -> str | None:
    """La moneda que se lee en el texto del precio («9.300 €» → EUR), o None."""
    plano = (texto or "").strip().lower()
    if not plano:
        return None
    for marca, codigo in _MONEDAS:
        if marca in plano:
            return codigo
    return None


def sin_precio_publico(texto: str | None) -> bool:
    """True si lo que el origen escribe en el precio es la marca de que NO hay
    precio público («Consultar», «on request»). Un precio con importe («desde
    21.500 €») no lo es."""
    plano = (texto or "").strip()
    return bool(plano) and bool(_SIN_PRECIO_PUBLICO_RE.match(plano))


def numero_de(texto: str | None) -> float | None:
    """El primer número del texto, entendiendo los separadores europeos y
    anglosajones: «9.300 €» → 9300; «9 300 €» → 9300; «9.300,50» → 9300.5;
    «9,300.50» → 9300.5; «12,50» → 12.5; «ab 2.990 €» → 2990; «desde 21.500 €»
    → 21500. Sin importe («Consultar»), None, nunca cero. Es solo para ordenar:
    lo que se enseña es el texto."""
    m = _NUMERO_RE.search(texto or "")
    if not m:
        return None
    crudo = re.sub(r"[   ]", "", m.group(0))
    if "." in crudo and "," in crudo:
        decimal = "," if crudo.rfind(",") > crudo.rfind(".") else "."
        miles = "." if decimal == "," else ","
        crudo = crudo.replace(miles, "").replace(decimal, ".")
    elif "," in crudo or "." in crudo:
        separador = "," if "," in crudo else "."
        trozos = crudo.split(separador)
        if len(trozos) == 2 and len(trozos[1]) in (1, 2):
            crudo = f"{trozos[0]}.{trozos[1]}"            # decimal
        else:
            crudo = "".join(trozos)                        # miles
    try:
        return float(crudo)
    except ValueError:
        return None


def meses_a_dias(meses: int) -> int:
    return int(round(30.44 * max(int(meses), 0)))


def es_orientativo(actualizado: datetime | None, *, ahora: datetime, meses: int) -> bool:
    """Un precio sin confirmar en más de `meses` meses es orientativo (y uno
    sin fecha de origen, también)."""
    if actualizado is None:
        return True
    if actualizado.tzinfo is None:
        actualizado = actualizado.replace(tzinfo=UTC)
    return actualizado < ahora - timedelta(days=meses_a_dias(meses))


def avisos_en(idioma: str | None) -> dict[str, str]:
    codigo = (idioma or "").strip().lower()[:2]
    return AVISOS.get(codigo) or AVISOS[IDIOMA_AVISO_RESPALDO]


def _clave(texto: str | None) -> str:
    """Para casar marcas y patrones: minúsculas y solo letras y cifras
    («6090 Trust» → «6090trust»)."""
    return re.sub(r"[^a-z0-9]", "", (texto or "").lower())


def _json_lista(raw: str | None) -> list[Any]:
    try:
        datos = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return datos if isinstance(datos, list) else []


def _json_dict(raw: str | None) -> dict[str, Any]:
    try:
        datos = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


# --- escritura de filas (la usan las dos fuentes) -----------------------------------------

CAMPOS_FILA = (
    "tipo", "marca", "nombre", "descripcion", "caracteristicas_json", "categoria", "imagen",
    "precio_texto", "precio_num", "moneda", "enlace", "sitio", "actualizado_origen", "visible",
    "datos_json", "precio_publico", "descartado", "motivo_descarte",
)
_LARGOS: dict[str, int] = {
    c.name: int(c.type.length) for c in ProductoCatalogo.__table__.columns
    if getattr(c.type, "length", None)
}


def recortar_a_columna(campo: str, valor: Any) -> Any:
    """Ningún valor del origen desborda su columna: un texto más largo que la
    columna se recorta antes de escribir (el 11/10/2026 una categoría de 290
    caracteres, nueve categorías de una tienda, tiró la pasada entera)."""
    largo = _LARGOS.get(campo)
    if largo and isinstance(valor, str) and len(valor) > largo:
        return valor[:largo]
    return valor


def _aplicar(fila: ProductoCatalogo, datos: dict[str, Any]) -> bool:
    """Copia lo indexado del producto (recortado a su columna). True si algo
    cambió (sin contar la fecha de origen, que en las tiendas es la propia
    pasada)."""
    antes = tuple(getattr(fila, c) for c in CAMPOS_FILA if c != "actualizado_origen")
    for campo in CAMPOS_FILA:
        if campo in datos:
            setattr(fila, campo, recortar_a_columna(campo, datos[campo]))
    despues = tuple(getattr(fila, c) for c in CAMPOS_FILA if c != "actualizado_origen")
    return antes != despues


def _lotes(elementos: list[Any], tamano: int) -> Iterable[list[Any]]:
    for i in range(0, len(elementos), max(1, tamano)):
        yield elementos[i:i + tamano]


def escribir_filas(
    session: Session, filas: Iterable[dict[str, Any]], *, fuente: str, ahora: datetime,
    sitio: str | None = None, idioma: str | None = None,
) -> dict[str, Any]:
    """Vuelca lo descargado de UNA fuente (y, en las tiendas, de una web e
    idioma): altas, cambios y los que ya no están (`ido_en`). Cada fila es un
    dict con `origen_id`, `idioma` y los `CAMPOS_FILA`. Se escribe por lotes
    dentro de un SAVEPOINT; un lote que la base rechaza se reintenta fila a
    fila y la que no entra se salta (contada en `saltadas`, el motivo en
    `errores`): la pasada termina siempre."""
    consulta = select(ProductoCatalogo).where(ProductoCatalogo.fuente == fuente)
    if sitio is not None:
        consulta = consulta.where(ProductoCatalogo.sitio == sitio)
    if idioma is not None:
        consulta = consulta.where(ProductoCatalogo.idioma == idioma)
    existentes = {(f.origen_id, f.idioma): f for f in session.scalars(consulta)}
    vistos: set[tuple[str, str]] = set()
    pendientes: list[tuple[tuple[str, str], dict[str, Any]]] = []
    for datos in filas:
        clave = (str(datos.get("origen_id") or ""), str(datos.get("idioma") or ""))
        if clave in vistos:
            continue
        vistos.add(clave)
        pendientes.append((clave, datos))

    def _una(clave: tuple[str, str], datos: dict[str, Any]) -> tuple[int, int]:
        """`(nuevos, actualizados)` de escribir una fila (0 o 1 cada uno)."""
        fila = existentes.get(clave)
        if fila is None:
            fila = ProductoCatalogo(fuente=fuente, origen_id=clave[0], idioma=clave[1],
                                    nombre="", visto_en=ahora)
            _aplicar(fila, datos)
            session.add(fila)
            fila.visto_en = ahora
            return 1, 0
        cambio = _aplicar(fila, datos) or fila.ido_en is not None
        fila.ido_en = None
        fila.visto_en = ahora
        return 0, 1 if cambio else 0

    def _lote(lote: list[tuple[tuple[str, str], dict[str, Any]]]) -> tuple[int, int]:
        n = a = 0
        with session.begin_nested():
            for clave, datos in lote:
                dn, da = _una(clave, datos)
                n += dn
                a += da
        return n, a

    nuevos = actualizados = saltadas = 0
    errores: list[str] = []
    escritas: set[tuple[str, str]] = set()
    for lote in _lotes(pendientes, LOTE_ESCRITURA):
        try:
            n, a = _lote(lote)
            nuevos += n
            actualizados += a
            escritas.update(clave for clave, _ in lote)
            continue
        except SQLAlchemyError as exc:
            logger.warning("kb.catalogo: un lote de %d filas (%s) no entró (%s); fila a fila",
                           len(lote), fuente, type(exc).__name__)
        # El SAVEPOINT deshizo el lote entero: ahora una a una, y la que no
        # entra se salta con su motivo.
        for clave, datos in lote:
            try:
                n, a = _lote([(clave, datos)])
                nuevos += n
                actualizados += a
                escritas.add(clave)
            except SQLAlchemyError as exc:
                saltadas += 1
                motivo = str(getattr(exc, "orig", None) or exc).replace("\n", " ")[:160]
                if len(errores) < MAX_ERRORES_RESUMEN:
                    errores.append(f"{clave[0]} ({clave[1]}): {type(exc).__name__}: {motivo}")
                logger.warning("kb.catalogo: fila %s (%s) saltada: %s", clave[0], clave[1], motivo)
    idos = 0
    for clave, fila in existentes.items():
        if clave not in escritas and fila.ido_en is None and clave not in vistos:
            fila.ido_en = ahora
            idos += 1
    session.flush()
    return {"productos": len(escritas), "nuevos": nuevos, "actualizados": actualizados,
            "idos": idos, "saltadas": saltadas, "errores": errores, "error": None}


def marcar_idos_fuera_de(
    session: Session, *, fuente: str, sitios: set[str], ahora: datetime,
) -> int:
    """Marca idas las filas vigentes de la fuente cuya web no está entre las
    dadas (una web desactivada o quitada de la tabla). Devuelve cuántas."""
    consulta = select(ProductoCatalogo).where(
        ProductoCatalogo.fuente == fuente, ProductoCatalogo.ido_en.is_(None))
    if sitios:
        consulta = consulta.where(ProductoCatalogo.sitio.not_in(sorted(sitios)))
    idos = 0
    for fila in session.scalars(consulta):
        fila.ido_en = ahora
        idos += 1
    if idos:
        session.flush()
    return idos


def abrir_pasada(session: Session, *, fuente: str, origen: str, ahora: datetime,
                 ) -> CatalogoSincronizacion:
    pasada = CatalogoSincronizacion(fuente=fuente, origen=origen, inicio=ahora)
    session.add(pasada)
    return pasada


def cerrar_pasada(
    pasada: CatalogoSincronizacion, resumen: dict[str, dict[str, Any]], errores: list[str], *,
    total: int,
) -> None:
    """Totales, estado (ok / parcial / error) y detalle a partir del resumen
    por idioma o tienda."""
    from app.models.conocimiento import SYNC_ERROR, SYNC_OK, SYNC_PARCIAL  # noqa: PLC0415

    pasada.productos = sum(int(r.get("productos") or 0) for r in resumen.values())
    pasada.nuevos = sum(int(r.get("nuevos") or 0) for r in resumen.values())
    pasada.actualizados = sum(int(r.get("actualizados") or 0) for r in resumen.values())
    pasada.idos = sum(int(r.get("idos") or 0) for r in resumen.values())
    saltadas = sum(int(r.get("saltadas") or 0) for r in resumen.values())
    por_fila = [e for r in resumen.values() for e in (r.get("errores") or [])]
    pasada.resumen_json = json.dumps(resumen, ensure_ascii=False, default=str)
    if errores and (total == 0 or len(errores) >= total):
        pasada.estado = SYNC_ERROR
    elif errores or saltadas:
        pasada.estado = SYNC_PARCIAL
    else:
        pasada.estado = SYNC_OK
    lineas = list(errores)
    if saltadas:
        lineas.append(f"{saltadas} fila(s) no entraron y se saltaron:")
        lineas.extend(f"  - {e}" for e in por_fila[:MAX_ERRORES_RESUMEN])
    pasada.detalle = "\n".join(lineas) or None
    pasada.fin = datetime.now(UTC)


def sincronizacion_como_dict(fila: CatalogoSincronizacion | None) -> dict[str, Any] | None:
    if fila is None:
        return None
    return {
        "id": fila.id, "fuente": fila.fuente, "origen": fila.origen, "estado": fila.estado,
        "inicio": fila.inicio.isoformat() if fila.inicio else None,
        "fin": fila.fin.isoformat() if fila.fin else None,
        "productos": fila.productos, "nuevos": fila.nuevos, "actualizados": fila.actualizados,
        "idos": fila.idos,
        "origen_actualizado_en": (
            fila.origen_actualizado_en.isoformat() if fila.origen_actualizado_en else None),
        "resumen": _json_dict(fila.resumen_json), "detalle": fila.detalle,
    }


def ultima_sincronizacion(session: Session, fuente: str) -> CatalogoSincronizacion | None:
    try:
        return session.scalars(
            select(CatalogoSincronizacion).where(CatalogoSincronizacion.fuente == fuente)
            .order_by(CatalogoSincronizacion.inicio.desc()).limit(1)
        ).first()
    except (OperationalError, ProgrammingError):
        return None


# --- marcas --------------------------------------------------------------------------------


def marcas(session: Session) -> list[MarcaCatalogo]:
    try:
        return list(session.scalars(select(MarcaCatalogo).order_by(MarcaCatalogo.nombre)))
    except (OperationalError, ProgrammingError):
        return []


def marca_como_dict(m: MarcaCatalogo) -> dict[str, Any]:
    return {
        "nombre": m.nombre, "logo": m.logo, "color": m.color, "urls": _json_dict(m.urls_json),
        "idioma_defecto": m.idioma_defecto, "ida": m.ido_en is not None,
        "diseno": _json_dict(m.datos_json),
    }


def idioma_defecto_de_marca(session: Session, marca: str) -> str:
    clave = _clave(marca)
    for m in marcas(session):
        if _clave(m.nombre) == clave:
            return m.idioma_defecto or IDIOMA_RAIZ
    return IDIOMA_RAIZ


def actualizar_marca(session: Session, nombre: str, cambios: dict[str, Any]) -> MarcaCatalogo:
    fila = session.get(MarcaCatalogo, nombre)
    if fila is None:
        raise LookupError("Esa marca no está en el catálogo.")
    if "idioma_defecto" in cambios:
        from app.services.conocimiento.rutas import idioma_de  # noqa: PLC0415

        codigo = idioma_de(str(cambios["idioma_defecto"] or ""))
        if not codigo:
            raise ValueError("El idioma por defecto es un código de dos letras (es, fr, de…).")
        fila.idioma_defecto = codigo
    session.flush()
    return fila


# --- consultas ------------------------------------------------------------------------------


def producto_como_dict(p: ProductoCatalogo) -> dict[str, Any]:
    return {
        "id": p.id, "fuente": p.fuente, "tipo": p.tipo, "marca": p.marca, "origen_id": p.origen_id,
        "nombre": p.nombre, "descripcion": p.descripcion,
        "caracteristicas": [str(c) for c in _json_lista(p.caracteristicas_json)],
        "categoria": p.categoria, "imagen": p.imagen, "precio_texto": p.precio_texto,
        "precio_num": p.precio_num, "moneda": p.moneda, "enlace": p.enlace, "idioma": p.idioma,
        "precio_publico": p.precio_publico is not False,
        "descartado": bool(p.descartado), "motivo_descarte": p.motivo_descarte,
        "sitio": p.sitio or None,
        "actualizado_origen": p.actualizado_origen.isoformat() if p.actualizado_origen else None,
        "visible": bool(p.visible), "datos": _json_dict(p.datos_json),
        "visto_en": p.visto_en.isoformat() if p.visto_en else None,
        "ido_en": p.ido_en.isoformat() if p.ido_en else None,
    }


def listar(
    session: Session, *, fuente: str | None = None, tipo: str | None = None,
    idioma: str | None = None, marca: str | None = None, sitio: str | None = None,
    texto: str | None = None, con_idos: bool = False, con_descartados: bool = False,
    limite: int = 500,
) -> list[ProductoCatalogo]:
    """Los productos de la pantalla, filtrados. Los idos y los descartados no
    salen salvo que se pidan."""
    consulta = select(ProductoCatalogo)
    if not con_idos:
        consulta = consulta.where(ProductoCatalogo.ido_en.is_(None))
    if not con_descartados:
        consulta = consulta.where(ProductoCatalogo.descartado.is_(False))
    if fuente:
        consulta = consulta.where(ProductoCatalogo.fuente == fuente)
    if tipo:
        consulta = consulta.where(ProductoCatalogo.tipo == tipo)
    if idioma:
        consulta = consulta.where(ProductoCatalogo.idioma == idioma)
    if marca:
        consulta = consulta.where(func.lower(ProductoCatalogo.marca) == marca.strip().lower())
    if sitio:
        consulta = consulta.where(ProductoCatalogo.sitio == sitio)
    if texto and texto.strip():
        patron = f"%{texto.strip().lower()}%"
        consulta = consulta.where(
            func.lower(ProductoCatalogo.nombre).like(patron)
            | func.lower(func.coalesce(ProductoCatalogo.categoria, "")).like(patron)
            | func.lower(ProductoCatalogo.origen_id).like(patron)
        )
    consulta = consulta.order_by(
        ProductoCatalogo.fuente, ProductoCatalogo.marca, ProductoCatalogo.nombre,
        ProductoCatalogo.idioma,
    ).limit(max(1, min(int(limite), 5000)))
    try:
        return list(session.scalars(consulta))
    except (OperationalError, ProgrammingError):
        return []


def estado(session: Session, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Lo que pinta la pantalla: recuentos por fuente, tipo, idioma y tienda,
    la última pasada de cada fuente, las marcas y si la clave del compositor
    está configurada."""
    from app.core.config import get_settings  # noqa: PLC0415
    from app.services.conocimiento.config import configuracion  # noqa: PLC0415

    cfg = config or configuracion(session)
    vigente = (ProductoCatalogo.ido_en.is_(None), ProductoCatalogo.descartado.is_(False))
    try:
        por_fuente_tipo = session.execute(
            select(ProductoCatalogo.fuente, ProductoCatalogo.tipo, func.count())
            .where(*vigente).group_by(ProductoCatalogo.fuente, ProductoCatalogo.tipo)
        ).all()
        por_fuente_idioma = session.execute(
            select(ProductoCatalogo.fuente, ProductoCatalogo.idioma, func.count())
            .where(*vigente).group_by(ProductoCatalogo.fuente, ProductoCatalogo.idioma)
        ).all()
        por_sitio = session.execute(
            select(ProductoCatalogo.sitio, func.count())
            .where(*vigente, ProductoCatalogo.fuente == FUENTE_WOOCOMMERCE)
            .group_by(ProductoCatalogo.sitio)
        ).all()
        idos = session.scalar(select(func.count()).select_from(ProductoCatalogo)
                              .where(ProductoCatalogo.ido_en.is_not(None))) or 0
        con_precio = session.scalar(select(func.count()).select_from(ProductoCatalogo).where(
            *vigente, ProductoCatalogo.precio_texto.is_not(None))) or 0
        descartados = session.scalar(select(func.count()).select_from(ProductoCatalogo).where(
            ProductoCatalogo.ido_en.is_(None), ProductoCatalogo.descartado.is_(True))) or 0
        sin_precio_publico = session.scalar(
            select(func.count(func.distinct(ProductoCatalogo.origen_id)))
            .select_from(ProductoCatalogo)
            .where(*vigente, ProductoCatalogo.precio_publico.is_(False))) or 0
    except (OperationalError, ProgrammingError):
        por_fuente_tipo, por_fuente_idioma, por_sitio, idos, con_precio = [], [], [], 0, 0
        descartados = sin_precio_publico = 0
    fuentes: dict[str, dict[str, Any]] = {
        f: {"productos": 0, "por_tipo": {}, "por_idioma": {}, "ultima_sincronizacion": None}
        for f in FUENTES
    }
    for fuente, tipo, n in por_fuente_tipo:
        fuentes[fuente]["por_tipo"][tipo] = int(n)
        fuentes[fuente]["productos"] += int(n)
    for fuente, idioma, n in por_fuente_idioma:
        fuentes[fuente]["por_idioma"][idioma] = int(n)
    fuentes[FUENTE_WOOCOMMERCE]["por_tienda"] = {(s or "—"): int(n) for s, n in por_sitio}
    for fuente in FUENTES:
        fuentes[fuente]["ultima_sincronizacion"] = sincronizacion_como_dict(
            ultima_sincronizacion(session, fuente))
    settings = get_settings()
    fuentes[FUENTE_COMPOSITOR]["clave_configurada"] = bool(settings.supabase_composer_key)
    return {
        "fuentes": fuentes,
        "idos": int(idos),
        "con_precio": int(con_precio),
        "descartados": int(descartados),
        "sin_precio_publico": int(sin_precio_publico),
        "marcas": [marca_como_dict(m) for m in marcas(session)],
        "iva_por_fuente": dict(cfg.get("iva_por_fuente") or {}),
        "precio_meses_orientativo": int(cfg.get("precio_meses_orientativo") or 6),
        "tipos": list(TIPOS_PRODUCTO),
    }


# --- interés → máquinas ----------------------------------------------------------------------


def _mapa_de_partida() -> list[InteresProducto]:
    return [
        InteresProducto(id=f"partida-{i}", interes=interes, marca=marca, patron=patron, nota=nota,
                        posicion=i, activo=True)
        for i, (interes, marca, patron, nota) in enumerate(MAPA_DE_PARTIDA)
    ]


def cargar_mapa(session: Session) -> tuple[list[InteresProducto], bool]:
    """`(filas, de_la_base)`: las de la tabla o, vacía (o sin crear), la propuesta."""
    try:
        filas = list(session.scalars(
            select(InteresProducto).order_by(InteresProducto.posicion, InteresProducto.interes)))
    except (OperationalError, ProgrammingError) as exc:
        logger.warning("kb.catalogo: la tabla kb_interes_productos no responde (%s)", exc)
        return _mapa_de_partida(), False
    if not filas:
        return _mapa_de_partida(), False
    return filas, True


def sembrar_mapa_si_vacio(session: Session) -> int:
    if session.scalar(select(InteresProducto.id).limit(1)) is not None:
        return 0
    for i, (interes, marca, patron, nota) in enumerate(MAPA_DE_PARTIDA):
        session.add(InteresProducto(interes=interes, marca=marca, patron=patron, nota=nota,
                                    posicion=i, activo=True))
    session.flush()
    return len(MAPA_DE_PARTIDA)


def mapa_como_dict(fila: InteresProducto) -> dict[str, Any]:
    return {
        "id": fila.id, "interes": fila.interes, "marca": fila.marca, "patron": fila.patron,
        "nota": fila.nota, "posicion": fila.posicion, "activo": bool(fila.activo),
    }


def _validar_interes(interes: Any) -> str:
    codigo = str(interes or "").strip().lower()
    if not _INTERES_RE.match(codigo):
        raise ValueError("El interés es su código (p. ej. uv_mediano).")
    return codigo


def crear_mapa(
    session: Session, *, interes: str, marca: str, patron: str = "", nota: str | None = None,
    posicion: int | None = None,
) -> InteresProducto:
    sembrar_mapa_si_vacio(session)
    codigo = _validar_interes(interes)
    marca_ok = str(marca or "").strip()
    patron_ok = str(patron or "").strip()
    if not marca_ok and not patron_ok:
        raise ValueError("Hace falta la marca o un patrón del nombre: sin los dos sería todo el "
                         "catálogo.")
    repetida = session.scalar(select(InteresProducto).where(
        InteresProducto.interes == codigo, InteresProducto.marca == marca_ok,
        InteresProducto.patron == patron_ok))
    if repetida is not None:
        raise ValueError("Ya hay una fila igual para ese interés: edítala.")
    if posicion is None:
        posicion = (session.scalar(select(InteresProducto.posicion).order_by(
            InteresProducto.posicion.desc()).limit(1)) or 0) + 1
    fila = InteresProducto(interes=codigo, marca=marca_ok, patron=patron_ok,
                           nota=(nota or "").strip() or None, posicion=int(posicion), activo=True)
    session.add(fila)
    session.flush()
    return fila


def _fila_del_mapa(session: Session, fila_id: str) -> InteresProducto:
    """La fila por id. Con la tabla recién sembrada desde la propuesta, un id
    `partida-N` de la pantalla es la fila sembrada en esa posición."""
    sembrar_mapa_si_vacio(session)
    fila = session.get(InteresProducto, fila_id)
    if fila is None and fila_id.startswith("partida-") and fila_id[8:].isdigit():
        fila = session.scalar(select(InteresProducto).where(
            InteresProducto.posicion == int(fila_id[8:])).order_by(InteresProducto.created_at))
    if fila is None:
        raise LookupError("Esa fila no existe.")
    return fila


def actualizar_mapa(session: Session, fila_id: str, cambios: dict[str, Any]) -> InteresProducto:
    fila = _fila_del_mapa(session, fila_id)
    if "interes" in cambios:
        fila.interes = _validar_interes(cambios["interes"])
    if "marca" in cambios:
        fila.marca = str(cambios["marca"] or "").strip()
    if "patron" in cambios:
        fila.patron = str(cambios["patron"] or "").strip()
    if not fila.marca and not fila.patron:
        raise ValueError("Hace falta la marca o un patrón del nombre.")
    repetida = session.scalar(select(InteresProducto.id).where(
        InteresProducto.interes == fila.interes, InteresProducto.marca == fila.marca,
        InteresProducto.patron == fila.patron, InteresProducto.id != fila.id))
    if repetida is not None:
        raise ValueError("Ya hay otra fila igual para ese interés, marca y patrón.")
    if "nota" in cambios:
        fila.nota = str(cambios["nota"] or "").strip() or None
    if "activo" in cambios:
        fila.activo = bool(cambios["activo"])
    if "posicion" in cambios and cambios["posicion"] is not None:
        fila.posicion = int(cambios["posicion"])
    session.flush()
    return fila


def borrar_mapa(session: Session, fila_id: str) -> None:
    fila = _fila_del_mapa(session, fila_id)
    session.delete(fila)
    session.flush()


def _maquinas_por_producto(session: Session) -> dict[str, list[ProductoCatalogo]]:
    """Las máquinas vigentes del compositor (sin las descartadas: un premio no
    es una máquina), agrupadas por producto (todas sus filas de idioma)."""
    try:
        filas = session.scalars(select(ProductoCatalogo).where(
            ProductoCatalogo.fuente == FUENTE_COMPOSITOR, ProductoCatalogo.tipo == TIPO_MAQUINA,
            ProductoCatalogo.ido_en.is_(None), ProductoCatalogo.visible.is_(True),
            ProductoCatalogo.descartado.is_(False),
        ).order_by(ProductoCatalogo.marca, ProductoCatalogo.nombre, ProductoCatalogo.idioma))
    except (OperationalError, ProgrammingError):
        return {}
    grupos: dict[str, list[ProductoCatalogo]] = {}
    for f in filas:
        grupos.setdefault(f.origen_id, []).append(f)
    return grupos


def _casa(fila: InteresProducto, producto: ProductoCatalogo) -> bool:
    if fila.marca and _clave(fila.marca) != _clave(producto.marca):
        return False
    patron = _clave(fila.patron)
    if not patron:
        return bool(fila.marca)
    return patron in _clave(producto.nombre) or patron in _clave(producto.origen_id)


def _fila_en(variantes: list[ProductoCatalogo], idioma: str | None) -> ProductoCatalogo | None:
    for v in variantes:
        if v.idioma == idioma:
            return v
    return None


def producto_para_correo(
    variantes: list[ProductoCatalogo], *, idioma: str, idioma_marca: str, ahora: datetime,
    meses: int, iva: str | None, interes: str | None = None,
) -> dict[str, Any]:
    """Un producto como lo ve el asistente: en el idioma pedido; si no tiene
    precio en ese idioma, el del idioma por defecto de su marca, diciendo de
    qué idioma es y en qué moneda (nunca se convierte). Enlace en el idioma
    pedido si lo hay. Marca si es orientativo."""
    propia = _fila_en(variantes, idioma)
    respaldo = (_fila_en(variantes, idioma_marca) or _fila_en(variantes, IDIOMA_RAIZ)
                or variantes[0])
    base = propia or respaldo
    con_precio = propia if (propia is not None and propia.precio_texto) else (
        respaldo if respaldo.precio_texto else None)
    enlace = (propia.enlace if propia is not None and propia.enlace else None) or respaldo.enlace
    idioma_enlace = (propia.idioma if propia is not None and propia.enlace else
                     (respaldo.idioma if respaldo.enlace else None))
    actualizado = con_precio.actualizado_origen if con_precio is not None else None
    # Sin importe en ningún idioma y el origen dice «Consultar» en alguno: no
    # tiene precio público (el correo dice que se prepara a medida).
    sin_publico = con_precio is None and any(v.precio_publico is False for v in variantes)
    # Lo que el compositor guarda para pintar la ficha (insignia, alto máximo,
    # colores): de la fila del idioma, y si no, de la de respaldo.
    diseno = {**_json_dict(respaldo.datos_json), **{
        k: v for k, v in _json_dict(base.datos_json).items() if v}}
    return {
        "origen_id": base.origen_id, "fuente": base.fuente, "tipo": base.tipo,
        "nombre": base.nombre, "marca": base.marca,
        "descripcion": base.descripcion or respaldo.descripcion,
        "caracteristicas": [str(c) for c in _json_lista(base.caracteristicas_json)]
        or [str(c) for c in _json_lista(respaldo.caracteristicas_json)],
        "categoria": base.categoria or respaldo.categoria,
        "imagen": base.imagen or respaldo.imagen,
        "precio_texto": con_precio.precio_texto if con_precio is not None else None,
        "precio_publico": not sin_publico,
        "moneda": con_precio.moneda if con_precio is not None else None,
        "precio_idioma": con_precio.idioma if con_precio is not None else None,
        "idioma_respaldo": bool(con_precio is not None and con_precio.idioma != idioma),
        "orientativo": bool(con_precio is not None
                            and es_orientativo(actualizado, ahora=ahora, meses=meses)),
        "actualizado_origen": actualizado.isoformat() if actualizado else None,
        "enlace": enlace, "idioma_enlace": idioma_enlace, "idioma": idioma,
        "sitio": base.sitio or None, "iva": iva, "interes": interes,
        "badge": diseno.get("badge") or None, "alto_max": diseno.get("alt") or None,
        "acento": diseno.get("acento") or None, "badge_fondo": diseno.get("badge_fondo") or None,
        "badge_color": diseno.get("badge_color") or None,
    }


def maquinas_para(
    session: Session, intereses: Iterable[str] | None, *, idioma: str, ahora: datetime,
    config: dict[str, Any], maximo: int = MAQUINAS_MAX,
    filas: list[InteresProducto] | None = None,
) -> list[dict[str, Any]]:
    """Las máquinas que encajan con los intereses del lead (el principal
    primero), en su idioma, con precio tal cual y enlace. Como mucho `maximo`.
    `filas` es el mapa ya cargado, si quien llama lo tiene."""
    codigos = [str(c).strip().lower() for c in (intereses or []) if str(c or "").strip()]
    if not codigos:
        return []
    if filas is None:
        filas, _ = cargar_mapa(session)
    grupos = _maquinas_por_producto(session)
    if not grupos:
        return []
    meses = int(config.get("precio_meses_orientativo") or 6)
    iva = (config.get("iva_por_fuente") or {}).get(FUENTE_COMPOSITOR)
    idiomas_marca = {_clave(m.nombre): (m.idioma_defecto or IDIOMA_RAIZ) for m in marcas(session)}
    salida: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for codigo in codigos:
        for fila in filas:
            if not fila.activo or fila.interes != codigo:
                continue
            for origen_id, variantes in grupos.items():
                if origen_id in vistos or not _casa(fila, variantes[0]):
                    continue
                vistos.add(origen_id)
                salida.append(producto_para_correo(
                    variantes, idioma=idioma,
                    idioma_marca=idiomas_marca.get(_clave(variantes[0].marca), IDIOMA_RAIZ),
                    ahora=ahora, meses=meses, iva=iva, interes=codigo,
                ))
                if len(salida) >= maximo:
                    return salida
    return salida


def consumibles_para(
    session: Session, *, maquina: str | None, modelo_flux: str | None, idioma: str,
    ahora: datetime, config: dict[str, Any], maximo: int = CONSUMIBLES_MAX,
) -> list[dict[str, Any]]:
    """Consumibles y repuestos de las tiendas para la máquina que tiene el
    cliente (lo que dicen sus pedidos): se casan por las palabras de la
    máquina contra nombre, categoría y marca del producto; en su idioma si la
    tienda lo publica, y si no en el de la tienda (dicho)."""
    from app.services.asistente.historico import palabras_de_producto  # noqa: PLC0415

    palabras = palabras_de_producto([maquina or ""], extra=[modelo_flux] if modelo_flux else ())
    palabras |= {p.lower() for p in re.findall(r"[A-Za-z]{4,}", modelo_flux or "")}
    if not palabras:
        return []
    try:
        filas = list(session.scalars(select(ProductoCatalogo).where(
            ProductoCatalogo.fuente == FUENTE_WOOCOMMERCE, ProductoCatalogo.tipo != TIPO_MAQUINA,
            ProductoCatalogo.ido_en.is_(None), ProductoCatalogo.visible.is_(True),
        )))
    except (OperationalError, ProgrammingError):
        return []
    if not filas:
        return []
    meses = int(config.get("precio_meses_orientativo") or 6)
    iva = (config.get("iva_por_fuente") or {}).get(FUENTE_WOOCOMMERCE)
    reg = registro_sitios.registro()

    def _puntos(f: ProductoCatalogo) -> int:
        texto = f"{f.nombre} {f.categoria or ''} {f.marca}".lower()
        tokens = {t.strip("-_") for t in re.findall(r"[\w\-]+", texto)}
        return len(palabras & tokens)

    puntuados = [(p, f) for f in filas if (p := _puntos(f)) > 0]
    if not puntuados:
        return []
    # Las traducciones de una tienda son filas aparte (en WPML, hasta con otro
    # id): se eligen las del idioma pedido; si la tienda no lo publica, las del
    # idioma por defecto de cada tienda (y se dice de qué idioma es el precio).
    def _idioma_tienda(clave_sitio: str) -> str:
        sitio = reg.sitio(clave_sitio)
        return sitio.idioma_defecto if sitio is not None else IDIOMA_RAIZ

    candidatos = [(p, f) for p, f in puntuados if f.idioma == idioma]
    if not candidatos:
        candidatos = [(p, f) for p, f in puntuados if f.idioma == _idioma_tienda(f.sitio)]
    if not candidatos:
        candidatos = puntuados
    mejores = sorted(candidatos, key=lambda pf: (-pf[0], pf[1].nombre.lower(), pf[1].idioma))
    salida: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for _, f in mejores:
        if f.origen_id in vistos:
            continue
        vistos.add(f.origen_id)
        salida.append(producto_para_correo(
            [f], idioma=idioma, idioma_marca=f.idioma, ahora=ahora, meses=meses, iva=iva))
        if len(salida) >= maximo:
            break
    return salida


def mapa_resuelto(session: Session, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """La tabla interés → máquinas con lo que cada fila casa hoy en el catálogo,
    los huecos (intereses comerciales sin máquina, filas sin coincidencia) y si
    Bart la ha dado por revisada."""
    from app.services.conocimiento.config import configuracion  # noqa: PLC0415
    from app.services.leads import intereses as cat_intereses  # noqa: PLC0415

    cfg = config or configuracion(session)
    filas, de_la_base = cargar_mapa(session)
    grupos = _maquinas_por_producto(session)
    catalogo = cat_intereses.cargar(session)
    salida_filas: list[dict[str, Any]] = []
    con_maquina: set[str] = set()
    for fila in filas:
        coincide = [
            {"origen_id": oid, "nombre": vs[0].nombre, "marca": vs[0].marca}
            for oid, vs in grupos.items() if _casa(fila, vs[0])
        ]
        if fila.activo and coincide:
            con_maquina.add(fila.interes)
        salida_filas.append({
            **mapa_como_dict(fila), "etiqueta": catalogo.etiqueta(fila.interes),
            "coincide": coincide,
        })
    huecos: list[dict[str, Any]] = []
    for interes in catalogo.activos:
        if not interes.comercial or interes.codigo in ("distribucion", "tienda"):
            continue
        if interes.codigo in con_maquina:
            continue
        tiene_filas = any(f.interes == interes.codigo and f.activo for f in filas)
        motivo = (
            "las filas no casan con ninguna máquina del catálogo"
            if tiene_filas else "sin máquina en el compositor: se enlaza la página de producto"
            if grupos else "el catálogo está vacío: sincroniza el compositor"
        )
        huecos.append({"interes": interes.codigo, "etiqueta": interes.etiqueta, "motivo": motivo})
    return {
        "filas": salida_filas, "de_la_base": de_la_base, "huecos": huecos,
        "revisado": bool(cfg.get("interes_productos_revisado")),
        "hallazgo": HALLAZGO_SIN_PRODUCTO, "catalogo_vacio": not grupos,
    }


# --- de dónde se eligen las máquinas: el hilo, la instrucción, el interés --------------------

#: Lo que NO identifica un producto aunque esté en su nombre («impresora UV
#: LED», «pro», «max»): se ignora al buscar menciones.
_PALABRAS_SIN_PESO: frozenset[str] = frozenset({
    "pro", "max", "plus", "mini", "ii", "iii", "uv", "led", "dtf", "laser", "láser", "flex", "new",
    "nuevo", "nueva", "impresora", "impresoras", "printer", "printers", "drucker", "imprimante",
    "machine", "máquina", "maquina", "horno", "oven", "secadora", "shaker", "serie", "series",
    "model", "modelo", "kit", "set", "pack", "with", "con", "para", "the", "and", "for", "von",
    "und", "mit", "der", "die", "das", "les", "des", "pour", "avec", "van", "met", "voor",
})
#: Palabras que, en la instrucción o en el hilo, señalan un interés aunque no
#: se nombre ninguna máquina («las máquinas de DTF»).
_UV_GENERICO: tuple[str, ...] = ("uv", "uv-led", "ultravioleta", "ultraviolet")
#: Los tres tamaños de UV solo con «UV» delante («una UV grande»); «UV» a secas
#: son los tres, el mediano primero.
_UV_TAMANO: dict[str, tuple[str, ...]] = {
    "uv_pequeno": ("pequeño", "pequeña", "pequeña", "small", "klein", "petit", "a3", "a4"),
    "uv_mediano": ("mediano", "mediana", "medium", "mittel", "moyen", "a2"),
    "uv_grande": ("grande", "gran", "large", "groß", "gross", "flatbed", "industrial"),
}
_PALABRAS_DE_INTERES: dict[str, tuple[str, ...]] = {
    "dtf": ("dtf", "textil", "textile", "textiel", "camiseta", "camisetas", "t-shirt", "tshirt",
            "shirts", "sudadera", "prenda", "prendas", "transfer"),
    "corte_laser": ("láser", "laser", "corte", "cortar", "cortadora", "cutting", "cutter",
                    "schneiden", "découpe", "snijden"),
    "grabado_laser": ("láser", "laser", "grabado", "grabar", "grabadora", "engraving", "engraver",
                      "gravur", "gravieren", "gravure", "graveren"),
    "packaging": ("packaging", "cajas", "caja", "bolsas", "bolsa", "embalaje", "overprint",
                  "overprinter", "sobreimpresión", "boxes", "bags", "verpackung", "emballage",
                  "verpakking"),
    "vending": ("vending", "expendedora", "expendedoras", "máquina expendedora",
                "vendingautomat", "distributeur"),
    "cnc": ("cnc", "fresadora", "fresado", "router", "milling", "fräse"),
}


def _tokens(texto: str | None) -> set[str]:
    return {t.strip("-_.").lower() for t in re.findall(r"[\w][\w\-\.]*", texto or "")} - {""}


def _es_codigo(token: str) -> bool:
    """Un código de modelo: letras y cifras mezcladas (DM3H, PT5, 5000U) o un
    número de cuatro cifras o más (6090). Un «20» o un «35» sueltos, no."""
    tiene_cifra = any(ch.isdigit() for ch in token)
    tiene_letra = any(ch.isalpha() for ch in token)
    return tiene_cifra and (tiene_letra or len(token) >= 4)


def _tokens_con_peso(texto: str | None) -> set[str]:
    """Los tokens que pueden identificar una máquina: códigos de modelo (DM3H,
    PT5, 6090) o palabras de cuatro letras o más que no sean genéricas (Orion,
    Lyra, Beambox)."""
    return {
        t for t in _tokens(texto)
        if t not in _PALABRAS_SIN_PESO and (_es_codigo(t) or (len(t) >= 4 and t.isalpha()))
    }


FUERZA_NOMBRE = "nombre"
FUERZA_PALABRA = "palabra"
FUERZA_DESCRIPCION = "descripcion"


def _nombres_propios(texto: str | None) -> set[str]:
    """Las palabras que el texto escribe con mayúscula A MEDIA FRASE («la
    Beambox», «el horno Vega»): ahí una sola palabra del nombre de una máquina
    sí la señala. En minúscula («I trust you») o a principio de frase la
    mayúscula no dice nada."""
    texto = texto or ""
    salida: set[str] = set()
    for m in re.finditer(r"[\w][\w\-\.]*", texto):
        palabra = m.group(0)
        if not palabra[:1].isupper():
            continue
        previo = texto[:m.start()].rstrip(" \t")
        if not previo or previo[-1] in ".!?¡¿:;\n\"«(":
            continue
        salida.add(palabra.strip("-_.").lower())
    return salida


def menciones_en(texto: str | None, grupos: dict[str, list[ProductoCatalogo]],
                 ) -> list[tuple[str, str]]:
    """Los productos que un texto nombra, por orden de fuerza: `(origen_id,
    "nombre")` si el NOMBRE del producto está en el texto (entero, dos de sus
    palabras con peso, un código como «6090», o una palabra escrita como
    nombre propio a media frase: «la Beambox»); `(origen_id, "palabra")` si
    solo está UNA palabra de un nombre más largo y nada dice que sea el nombre
    («I trust you» y la «6090 Trust»: posible, no seguro);
    `(origen_id, "descripcion")` si un código del texto (un token con cifras:
    DM3H, PT5) aparece en la descripción, las características, el texto
    alternativo o el id del producto. Lo que no esté en ningún sitio no se
    encuentra: si el cliente usa otros nombres, haría falta una lista de
    nombres alternativos por producto (en el compositor)."""
    palabras = _tokens_con_peso(texto)
    if not palabras or not grupos:
        return []
    todas = _tokens(texto)
    propias = _nombres_propios(texto)
    codigos = {p for p in palabras if _es_codigo(p)}
    fuertes: list[tuple[float, str, str]] = []
    posibles: list[tuple[float, str, str]] = []
    flojas: list[str] = []
    for origen_id, variantes in grupos.items():
        base = variantes[0]
        marca = _tokens(base.marca)
        nombre = _tokens_con_peso(base.nombre) - marca
        acierto = nombre & palabras
        if acierto:
            # Cuanto más del nombre está en el texto, antes va («Orion 20» entero
            # por delante de una «Vega 60 Pro» de la que solo se dijo «Vega»).
            completo = _tokens(base.nombre) - marca
            parte = len(completo & todas) / len(completo) if completo else 0.0
            seguro = (parte >= 1.0 or len(acierto) >= 2 or bool(acierto & propias)
                      or any(_es_codigo(t) for t in acierto))
            (fuertes if seguro else posibles).append((-parte, base.nombre.lower(), origen_id))
            continue
        if not codigos:
            continue
        otros: set[str] = set()
        for v in variantes:
            otros |= _tokens(v.descripcion)
            otros |= _tokens(" ".join(str(c) for c in _json_lista(v.caracteristicas_json)))
            otros |= _tokens(str(_json_dict(v.datos_json).get("alt") or ""))
        otros |= _tokens(base.origen_id)
        if codigos & otros:
            flojas.append(origen_id)
    return ([(o, FUERZA_NOMBRE) for _, _, o in sorted(fuertes)]
            + [(o, FUERZA_PALABRA) for _, _, o in sorted(posibles)]
            + [(o, FUERZA_DESCRIPCION) for o in flojas])


def _intereses_con_maquina(codigos: Iterable[str], filas: list[InteresProducto],
                           grupos: dict[str, list[ProductoCatalogo]]) -> set[str]:
    """Los intereses (de `codigos`) que tienen alguna máquina en el catálogo
    por el mapa interés → máquinas, se haya llegado o no al tope de máquinas
    del borrador."""
    pedidos = set(codigos)
    return {
        fila.interes for fila in filas
        if fila.activo and fila.interes in pedidos
        and any(_casa(fila, variantes[0]) for variantes in grupos.values())
    }


def intereses_de_texto(texto: str | None, codigos_validos: Iterable[str] | None = None,
                       ) -> list[str]:
    """Los intereses que nombra un texto («las máquinas de DTF» → dtf; «una
    láser para cortar» → corte_laser, grabado_laser), en el orden en que
    aparecen sus palabras. Solo los códigos que existen en el catálogo de
    intereses, si se pasa."""
    tokens = _tokens(texto)
    if not tokens:
        return []
    validos = set(codigos_validos) if codigos_validos is not None else None

    def _vale(codigo: str) -> bool:
        return validos is None or codigo in validos

    salida: list[str] = []
    # UV: con tamaño («una UV grande»), ese; «UV» a secas, los tres (el mediano primero).
    if any(p in tokens for p in _UV_GENERICO) or any(c in tokens for c in _UV_TAMANO):
        con_tamano = [c for c, palabras in _UV_TAMANO.items()
                      if _vale(c) and (any(p in tokens for p in palabras) or c in tokens)]
        if con_tamano:
            salida.extend(con_tamano)
        elif any(p in tokens for p in _UV_GENERICO):
            salida.extend(c for c in ("uv_mediano", "uv_pequeno", "uv_grande") if _vale(c))
    for codigo, palabras in _PALABRAS_DE_INTERES.items():
        if _vale(codigo) and (any(p in tokens for p in palabras) or codigo in tokens):
            salida.append(codigo)
    return salida


def maquinas_por_origen(
    session: Session, origen_ids: Iterable[str], *, idioma: str, ahora: datetime,
    config: dict[str, Any], grupos: dict[str, list[ProductoCatalogo]] | None = None,
    interes: str | None = None,
) -> list[dict[str, Any]]:
    """Productos concretos (por `origen_id`) como los ve el asistente."""
    grupos = grupos if grupos is not None else _maquinas_por_producto(session)
    meses = int(config.get("precio_meses_orientativo") or 6)
    iva = (config.get("iva_por_fuente") or {}).get(FUENTE_COMPOSITOR)
    idiomas_marca = {_clave(m.nombre): (m.idioma_defecto or IDIOMA_RAIZ) for m in marcas(session)}
    salida: list[dict[str, Any]] = []
    for origen_id in origen_ids:
        variantes = grupos.get(origen_id)
        if not variantes:
            continue
        salida.append(producto_para_correo(
            variantes, idioma=idioma,
            idioma_marca=idiomas_marca.get(_clave(variantes[0].marca), IDIOMA_RAIZ),
            ahora=ahora, meses=meses, iva=iva, interes=interes,
        ))
    return salida


def productos_por_clave(
    session: Session, refs: Iterable[dict[str, str]], *, idioma: str, ahora: datetime | None,
    config: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Productos concretos de cualquier fuente por su clave (`{"fuente",
    "origen_id"}`), leídos de la fila viva del catálogo y en el idioma pedido,
    como los ve un correo compuesto: `"{fuente}|{origen_id}"` → el producto de
    `producto_para_correo`. Solo los vigentes, visibles y no descartados; lo que
    ya no esté no sale."""
    por_fuente: dict[str, list[str]] = {}
    for r in refs:
        fuente, origen_id = str(r.get("fuente") or ""), str(r.get("origen_id") or "")
        if fuente and origen_id and origen_id not in por_fuente.setdefault(fuente, []):
            por_fuente[fuente].append(origen_id)
    if not por_fuente:
        return {}
    ahora = ahora or datetime.now(UTC)
    meses = int(config.get("precio_meses_orientativo") or 6)
    iva_por_fuente = config.get("iva_por_fuente") or {}
    idiomas_marca = {_clave(m.nombre): (m.idioma_defecto or IDIOMA_RAIZ) for m in marcas(session)}
    salida: dict[str, dict[str, Any]] = {}
    for fuente, ids in por_fuente.items():
        try:
            filas = list(session.scalars(select(ProductoCatalogo).where(
                ProductoCatalogo.fuente == fuente, ProductoCatalogo.origen_id.in_(ids),
                ProductoCatalogo.ido_en.is_(None), ProductoCatalogo.visible.is_(True),
                ProductoCatalogo.descartado.is_(False),
            ).order_by(ProductoCatalogo.origen_id, ProductoCatalogo.idioma)))
        except (OperationalError, ProgrammingError):
            continue
        grupos: dict[str, list[ProductoCatalogo]] = {}
        for f in filas:
            grupos.setdefault(f.origen_id, []).append(f)
        for origen_id, variantes in grupos.items():
            salida[f"{fuente}|{origen_id}"] = producto_para_correo(
                variantes, idioma=idioma,
                idioma_marca=idiomas_marca.get(_clave(variantes[0].marca), IDIOMA_RAIZ),
                ahora=ahora, meses=meses, iva=iva_por_fuente.get(fuente))
    return salida


# --- lo que ve el asistente ----------------------------------------------------------------


def para_asistente(
    session: Session, *, intereses: Iterable[str] | None, idioma: str, maquina: str | None,
    modelo_flux: str | None, ahora: datetime | None = None, config: dict[str, Any] | None = None,
    texto_hilo: str | None = None, instruccion: str | None = None,
) -> dict[str, Any]:
    """El bloque CATÁLOGO de un borrador. Las máquinas se eligen por este
    orden: (1) las que el cliente nombra en el hilo, (2) las que pide la
    instrucción de quien envía (una máquina concreta o un interés: «las
    máquinas de DTF» manda), (4) las que PUEDE que nombren el hilo o la
    instrucción (una sola palabra del nombre, o un código de su descripción:
    `hilo_posible`, `instruccion_posible`), (5) las del interés clasificado;
    entre medias (3) van los consumibles de la máquina que ya tiene. Cada
    máquina lleva de dónde salió (`eleccion`). Los avisos en su idioma y la
    declaración del IVA por fuente. Nunca el catálogo entero."""
    from app.services.conocimiento.config import configuracion  # noqa: PLC0415

    cfg = config or configuracion(session)
    ahora = ahora or datetime.now(UTC)
    filas, _ = cargar_mapa(session)
    grupos = _maquinas_por_producto(session)
    codigos = [str(c).strip().lower() for c in (intereses or []) if str(c or "").strip()]
    codigos_validos = {f.interes for f in filas} | set(_PALABRAS_DE_INTERES) | set(codigos)
    intereses_instruccion = intereses_de_texto(instruccion, codigos_validos)
    maquinas: list[dict[str, Any]] = []
    vistos: set[str] = set()

    def _añadir(lista: list[dict[str, Any]], eleccion: str) -> None:
        for m in lista:
            if m["origen_id"] in vistos or len(maquinas) >= MAQUINAS_MAX:
                continue
            vistos.add(m["origen_id"])
            maquinas.append({**m, "eleccion": eleccion})

    def _por_origen(origenes: list[str]) -> list[dict[str, Any]]:
        return maquinas_por_origen(session, origenes, idioma=idioma, ahora=ahora, config=cfg,
                                   grupos=grupos)

    def _partir(menciones: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
        """Seguras (por el nombre) y posibles (una palabra, la descripción)."""
        return ([o for o, fuerza in menciones if fuerza == FUERZA_NOMBRE],
                [o for o, fuerza in menciones if fuerza != FUERZA_NOMBRE])

    # 1. Lo que nombra el cliente en el hilo.
    menciones_hilo = menciones_en(texto_hilo, grupos)
    hilo_seguras, hilo_posibles = _partir(menciones_hilo)
    _añadir(_por_origen(hilo_seguras), "hilo")
    # 2. Lo que pide quien envía: una máquina concreta, o un interés entero.
    menciones_instruccion = menciones_en(instruccion, grupos)
    instruccion_seguras, instruccion_posibles = _partir(menciones_instruccion)
    _añadir(_por_origen(instruccion_seguras), "instruccion")
    if intereses_instruccion:
        _añadir(maquinas_para(session, intereses_instruccion, idioma=idioma, ahora=ahora,
                              config=cfg, filas=filas), "instruccion")
    # 3. Los consumibles de la máquina que ya tiene (lista aparte).
    consumibles = consumibles_para(session, maquina=maquina, modelo_flux=modelo_flux,
                                   idioma=idioma, ahora=ahora, config=cfg)
    # 4. Lo que PUEDE que nombren (una palabra suelta del nombre, un código de
    # la descripción): detrás de lo seguro, delante del interés.
    _añadir(_por_origen(hilo_posibles), "hilo_posible")
    _añadir(_por_origen(instruccion_posibles), "instruccion_posible")
    # 5. El interés clasificado.
    _añadir(maquinas_para(session, codigos, idioma=idioma, ahora=ahora, config=cfg, filas=filas),
            "interes")
    pedidos = list(dict.fromkeys(intereses_instruccion + codigos))
    # Sin máquina de verdad (ninguna fila del mapa con producto), no porque el
    # tope del borrador ya estuviera lleno con lo que nombra el hilo.
    con_maquina = _intereses_con_maquina(pedidos, filas, grupos)
    sin_maquina = [c for c in pedidos if c not in con_maquina]
    codigo_avisos = (idioma or "").strip().lower()[:2]
    return {
        "maquinas": maquinas,
        "consumibles": consumibles,
        "sin_maquina": sin_maquina,
        "con_filas": sorted({f.interes for f in filas if f.activo}),
        "intereses_instruccion": intereses_instruccion,
        "menciones": {
            "hilo": [grupos[o][0].nombre for o, _ in menciones_hilo if o in grupos],
            "instruccion": [grupos[o][0].nombre for o, _ in menciones_instruccion if o in grupos],
            "flojas": [grupos[o][0].nombre for o, fuerza in menciones_hilo + menciones_instruccion
                       if fuerza != FUERZA_NOMBRE and o in grupos],
        },
        "avisos": avisos_en(idioma),
        "idioma_avisos": codigo_avisos if codigo_avisos in AVISOS else IDIOMA_AVISO_RESPALDO,
        "iva_por_fuente": dict(cfg.get("iva_por_fuente") or {}),
        "precio_meses_orientativo": int(cfg.get("precio_meses_orientativo") or 6),
    }
