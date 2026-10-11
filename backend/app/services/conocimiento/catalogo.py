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
  Bart; los intereses sin máquina en el compositor (láser, packaging, CNC)
  quedan como huecos y se enlaza la página de producto del interés.
- Los **consumibles y repuestos de la máquina que tiene el cliente**: se
  casan por las palabras de sus pedidos contra el nombre y la categoría de
  los productos de las tiendas.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, ProgrammingError
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

#: La propuesta de interés → máquinas del 11/10/2026 (a revisar por Bart):
#: (interés, marca del compositor, patrón del nombre; vacío = toda la marca).
MAPA_DE_PARTIDA: tuple[tuple[str, str, str, str | None], ...] = (
    ("uv_pequeno", "artisJet", "Young", None),
    ("uv_pequeno", "artisJet", "Proud", None),
    ("uv_pequeno", "MBO", "1015", None),
    ("uv_mediano", "artisJet", "3000Pro", None),
    ("uv_mediano", "artisJet", "5000U", None),
    ("uv_mediano", "MBO", "3050", None),
    ("uv_mediano", "MBO", "4060", None),
    ("uv_grande", "artisJet", "6090 Trust", None),
    ("uv_grande", "MBO", "6090", None),
    ("uv_grande", "MBO", "UV1612G", None),
    ("uv_grande", "MBO", "UV1812", None),
    ("uv_grande", "MBO", "UV2513", None),
    ("dtf", "SmartJet FLEX", "", "Las cuatro SmartJet FLEX; confirmar cuáles van a DTF."),
    ("vending", "PimPam", "CaseBox", None),
    ("vending", "PimPam", "Custom", None),
)
#: Intereses comerciales sin máquina en el compositor (hallazgo del 11/10/2026:
#: las láser de FLUX y MBO Lasers, el packaging y el CNC no están en él). Se
#: enlaza la página de producto del interés (`kb_rutas`) y no se inventa nada.
HALLAZGO_SIN_PRODUCTO = (
    "Las máquinas láser (FLUX, MBO Lasers), el packaging y el CNC no están en el compositor de "
    "correos: para esos intereses el asistente enlaza la página de producto del interés y no cita "
    "ningún precio."
)

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


def numero_de(texto: str | None) -> float | None:
    """El primer número del texto, entendiendo los separadores europeos y
    anglosajones: «9.300 €» → 9300; «9 300 €» → 9300; «9.300,50» → 9300.5;
    «9,300.50» → 9300.5; «12,50» → 12.5; «ab 2.990 €» → 2990. Sin número, None.
    Es solo para ordenar: lo que se enseña es el texto."""
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
    "datos_json",
)


def _aplicar(fila: ProductoCatalogo, datos: dict[str, Any]) -> bool:
    """Copia lo indexado del producto. True si algo cambió (sin contar la
    fecha de origen, que en las tiendas es la propia pasada)."""
    antes = tuple(getattr(fila, c) for c in CAMPOS_FILA if c != "actualizado_origen")
    for campo in CAMPOS_FILA:
        if campo in datos:
            setattr(fila, campo, datos[campo])
    despues = tuple(getattr(fila, c) for c in CAMPOS_FILA if c != "actualizado_origen")
    return antes != despues


def escribir_filas(
    session: Session, filas: Iterable[dict[str, Any]], *, fuente: str, ahora: datetime,
    sitio: str | None = None, idioma: str | None = None,
) -> dict[str, Any]:
    """Vuelca lo descargado de UNA fuente (y, en las tiendas, de una web e
    idioma): altas, cambios y los que ya no están (`ido_en`). Cada fila es un
    dict con `origen_id`, `idioma` y los `CAMPOS_FILA`."""
    consulta = select(ProductoCatalogo).where(ProductoCatalogo.fuente == fuente)
    if sitio is not None:
        consulta = consulta.where(ProductoCatalogo.sitio == sitio)
    if idioma is not None:
        consulta = consulta.where(ProductoCatalogo.idioma == idioma)
    existentes = {(f.origen_id, f.idioma): f for f in session.scalars(consulta)}
    vistos: set[tuple[str, str]] = set()
    nuevos = actualizados = 0
    for datos in filas:
        clave = (str(datos["origen_id"]), str(datos["idioma"]))
        if clave in vistos:
            continue
        vistos.add(clave)
        fila = existentes.get(clave)
        if fila is None:
            fila = ProductoCatalogo(fuente=fuente, origen_id=clave[0], idioma=clave[1],
                                    nombre="", visto_en=ahora)
            _aplicar(fila, datos)
            session.add(fila)
            nuevos += 1
        else:
            if _aplicar(fila, datos) or fila.ido_en is not None:
                actualizados += 1
            fila.ido_en = None
        fila.visto_en = ahora
    idos = 0
    for clave, fila in existentes.items():
        if clave not in vistos and fila.ido_en is None:
            fila.ido_en = ahora
            idos += 1
    session.flush()
    return {"productos": len(vistos), "nuevos": nuevos, "actualizados": actualizados,
            "idos": idos, "error": None}


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
    pasada.resumen_json = json.dumps(resumen, ensure_ascii=False, default=str)
    if errores and (total == 0 or len(errores) >= total):
        pasada.estado = SYNC_ERROR
    elif errores:
        pasada.estado = SYNC_PARCIAL
    else:
        pasada.estado = SYNC_OK
    pasada.detalle = "\n".join(errores) or None
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
        "sitio": p.sitio or None,
        "actualizado_origen": p.actualizado_origen.isoformat() if p.actualizado_origen else None,
        "visible": bool(p.visible), "datos": _json_dict(p.datos_json),
        "visto_en": p.visto_en.isoformat() if p.visto_en else None,
        "ido_en": p.ido_en.isoformat() if p.ido_en else None,
    }


def listar(
    session: Session, *, fuente: str | None = None, tipo: str | None = None,
    idioma: str | None = None, marca: str | None = None, sitio: str | None = None,
    texto: str | None = None, con_idos: bool = False, limite: int = 500,
) -> list[ProductoCatalogo]:
    """Los productos de la pantalla, filtrados. Los idos no salen salvo que se pidan."""
    consulta = select(ProductoCatalogo)
    if not con_idos:
        consulta = consulta.where(ProductoCatalogo.ido_en.is_(None))
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
    try:
        por_fuente_tipo = session.execute(
            select(ProductoCatalogo.fuente, ProductoCatalogo.tipo, func.count())
            .where(ProductoCatalogo.ido_en.is_(None))
            .group_by(ProductoCatalogo.fuente, ProductoCatalogo.tipo)
        ).all()
        por_fuente_idioma = session.execute(
            select(ProductoCatalogo.fuente, ProductoCatalogo.idioma, func.count())
            .where(ProductoCatalogo.ido_en.is_(None))
            .group_by(ProductoCatalogo.fuente, ProductoCatalogo.idioma)
        ).all()
        por_sitio = session.execute(
            select(ProductoCatalogo.sitio, func.count())
            .where(ProductoCatalogo.ido_en.is_(None),
                   ProductoCatalogo.fuente == FUENTE_WOOCOMMERCE)
            .group_by(ProductoCatalogo.sitio)
        ).all()
        idos = session.scalar(select(func.count()).select_from(ProductoCatalogo)
                              .where(ProductoCatalogo.ido_en.is_not(None))) or 0
        con_precio = session.scalar(select(func.count()).select_from(ProductoCatalogo).where(
            ProductoCatalogo.ido_en.is_(None), ProductoCatalogo.precio_texto.is_not(None))) or 0
    except (OperationalError, ProgrammingError):
        por_fuente_tipo, por_fuente_idioma, por_sitio, idos, con_precio = [], [], [], 0, 0
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
    """Las máquinas vigentes del compositor, agrupadas por producto (todas sus
    filas de idioma)."""
    try:
        filas = session.scalars(select(ProductoCatalogo).where(
            ProductoCatalogo.fuente == FUENTE_COMPOSITOR, ProductoCatalogo.tipo == TIPO_MAQUINA,
            ProductoCatalogo.ido_en.is_(None), ProductoCatalogo.visible.is_(True),
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
    return {
        "origen_id": base.origen_id, "fuente": base.fuente, "tipo": base.tipo,
        "nombre": base.nombre, "marca": base.marca,
        "descripcion": base.descripcion or respaldo.descripcion,
        "caracteristicas": [str(c) for c in _json_lista(base.caracteristicas_json)]
        or [str(c) for c in _json_lista(respaldo.caracteristicas_json)],
        "categoria": base.categoria or respaldo.categoria,
        "imagen": base.imagen or respaldo.imagen,
        "precio_texto": con_precio.precio_texto if con_precio is not None else None,
        "moneda": con_precio.moneda if con_precio is not None else None,
        "precio_idioma": con_precio.idioma if con_precio is not None else None,
        "idioma_respaldo": bool(con_precio is not None and con_precio.idioma != idioma),
        "orientativo": bool(con_precio is not None
                            and es_orientativo(actualizado, ahora=ahora, meses=meses)),
        "actualizado_origen": actualizado.isoformat() if actualizado else None,
        "enlace": enlace, "idioma_enlace": idioma_enlace, "idioma": idioma,
        "sitio": base.sitio or None, "iva": iva, "interes": interes,
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


# --- lo que ve el asistente ----------------------------------------------------------------


def para_asistente(
    session: Session, *, intereses: Iterable[str] | None, idioma: str, maquina: str | None,
    modelo_flux: str | None, ahora: datetime | None = None, config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """El bloque CATÁLOGO de un borrador: máquinas de su interés, consumibles de
    su máquina, los avisos en su idioma y la declaración del IVA por fuente.
    Nunca el catálogo entero."""
    from app.services.conocimiento.config import configuracion  # noqa: PLC0415

    cfg = config or configuracion(session)
    ahora = ahora or datetime.now(UTC)
    filas, _ = cargar_mapa(session)
    maquinas = maquinas_para(session, intereses, idioma=idioma, ahora=ahora, config=cfg,
                             filas=filas)
    consumibles = consumibles_para(session, maquina=maquina, modelo_flux=modelo_flux,
                                   idioma=idioma, ahora=ahora, config=cfg)
    codigos = [str(c).strip().lower() for c in (intereses or []) if str(c or "").strip()]
    sin_maquina = [c for c in codigos if not any(m["interes"] == c for m in maquinas)]
    codigo_avisos = (idioma or "").strip().lower()[:2]
    return {
        "maquinas": maquinas,
        "consumibles": consumibles,
        "sin_maquina": sin_maquina,
        "con_filas": sorted({f.interes for f in filas if f.activo}),
        "avisos": avisos_en(idioma),
        "idioma_avisos": codigo_avisos if codigo_avisos in AVISOS else IDIOMA_AVISO_RESPALDO,
        "iva_por_fuente": dict(cfg.get("iva_por_fuente") or {}),
        "precio_meses_orientativo": int(cfg.get("precio_meses_orientativo") or 6),
    }
