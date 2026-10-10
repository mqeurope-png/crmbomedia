"""El mapa intereses × idioma → plantilla de `email_templates`.

Las 30 plantillas están en la carpeta «plantillas autoresponse», con nombres
`Lead · <contenido> (<IDIOMA>)`: cinco contenidos comerciales en seis idiomas.
El mapa se guarda en la configuración (Configuración ERP → Respuesta a leads)
y es como Bart engancha contenidos nuevos (SmartJet, textil/DTF) sin un PR.

Cada fila del mapa es un **conjunto de uno o más intereses** más un idioma:

    "vending:es"              → una plantilla (un solo interés, como siempre)
    "dtf+uv_mediano:de"       → la plantilla para quien pide UV y textil a la vez
    "cnc:fr"                  → ""  (sin plantilla, a propósito)

La clave de una combinación lleva los códigos ordenados y unidos con `+`
(`clave_mapa`): el conjunto, no el orden. Para elegir la plantilla de un lead
(`plantilla_para`):

1. la fila cuyo conjunto es exactamente el del lead;
2. si no, la fila del interés principal solo (y, sin fila, la plantilla que
   se resuelve por nombre, `Lead · <etiqueta> (<IDIOMA>)`);
3. si no, ninguna: borrador vacío y la tarea lo dice.

Un interés que no es comercial (soporte postventa, «otro») no lleva plantilla
de venta. Los huecos (interés comercial × idioma sin plantilla) son normales:
no se inventan plantillas para DTF, packaging, CNC o la tienda; esos leads se
clasifican, van al pipeline y crean su tarea con el aviso. `huecos()` es lo
que la pantalla enseña para que se vean.
"""
from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.leads.intereses import Catalogo, normalizar_lista

CARPETA = "plantillas autoresponse"
#: Idiomas con plantilla.
IDIOMAS_CON_PLANTILLA: tuple[str, ...] = ("es", "en", "fr", "de", "nl", "pt")
#: Los contenidos con que se nombraron las 30 plantillas antes del catálogo
#: (08/10/2026): siguen resolviendo por nombre para los códigos que salieron
#: de cada uno («Láser y CNC» cubría corte, grabado y CNC; «UV
#: pequeño-mediano», las dos tallas). La migración 0134 además los deja
#: escritos en el mapa. Separar contenidos es cosa de Bart, cuando los haya.
NOMBRES_ANTIGUOS: dict[str, tuple[str, ...]] = {
    "uv_pequeno": ("UV pequeño-mediano",),
    "uv_mediano": ("UV pequeño-mediano",),
    "uv_grande": ("UV gran formato",),
    "corte_laser": ("Láser y CNC",),
    "grabado_laser": ("Láser y CNC",),
    "cnc": ("Láser y CNC",),
}


def nombre_plantilla(interes: str, idioma: str, catalogo: Catalogo | None = None) -> str:
    """`Lead · Vending (ES)`: el nombre con el que se busca la plantilla de
    un interés en un idioma."""
    catalogo = catalogo or Catalogo.de_partida()
    return f"Lead · {catalogo.etiqueta(interes)} ({(idioma or '').upper()})"


def nombres_plantilla(
    interes: str, idioma: str, catalogo: Catalogo | None = None,
) -> list[str]:
    """Los nombres que valen para ese interés e idioma: el de su etiqueta
    actual y, si lo tiene, el antiguo."""
    nombres = [nombre_plantilla(interes, idioma, catalogo)]
    for contenido in NOMBRES_ANTIGUOS.get(interes, ()):
        nombre = f"Lead · {contenido} ({(idioma or '').upper()})"
        if nombre not in nombres:
            nombres.append(nombre)
    return nombres


def clave_mapa(intereses: Iterable[str] | str, idioma: str) -> str:
    """`uv_mediano:de` o `dtf+uv_mediano:de` (códigos ordenados: la clave
    es del conjunto, no del orden)."""
    lista = [intereses] if isinstance(intereses, str) else list(intereses)
    codigos = sorted(set(normalizar_lista(lista)))
    return f"{'+'.join(codigos)}:{(idioma or '').strip().lower()}"


def partes_clave(clave: str) -> tuple[list[str], str]:
    """`(códigos, idioma)` de una clave del mapa; códigos vacíos si no vale."""
    intereses, _, idioma = str(clave or "").partition(":")
    codigos = normalizar_lista(intereses.split("+"))
    return codigos, idioma.strip().lower()


def _plano(texto: str) -> str:
    """Sin acentos, sin espacios dobles, en minúsculas: «Lead · Láser y CNC
    (FR)» y «lead · laser y cnc (fr)» son la misma plantilla."""
    sin = "".join(
        c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c)
    )
    return " ".join(sin.lower().split())


def _candidatas(session: Session) -> list[Any]:
    from app.email_templates.models import EmailTemplate  # noqa: PLC0415

    return list(session.scalars(
        select(EmailTemplate)
        .where(EmailTemplate.name.like("%Lead%"))
        .order_by(EmailTemplate.created_at.desc())
    ))


def buscar_por_nombre(session: Session, nombre: str) -> Any | None:
    """La plantilla con ese nombre (tolerando acentos y mayúsculas). Si hay
    varias, la más reciente."""
    buscado = _plano(nombre)
    for tpl in _candidatas(session):
        if _plano(tpl.name) == buscado:
            return tpl
    return None


def ids_por_nombre(session: Session) -> dict[str, str]:
    """Nombre plano → id de la plantilla (la más reciente manda). Se carga
    UNA vez y se pasa a `mapa_resuelto_por_nombre` y `huecos`."""
    por_plano: dict[str, str] = {}
    for tpl in _candidatas(session):
        por_plano.setdefault(_plano(tpl.name), tpl.id)
    return por_plano


def _resolver_por_nombre(
    por_plano: dict[str, str], interes: str, idioma: str, catalogo: Catalogo,
) -> str | None:
    for nombre in nombres_plantilla(interes, idioma, catalogo):
        template_id = por_plano.get(_plano(nombre))
        if template_id:
            return template_id
    return None


def mapa_resuelto_por_nombre(
    session: Session, catalogo: Catalogo | None = None,
    por_plano: dict[str, str] | None = None,
) -> dict[str, str | None]:
    """Para la pantalla: `interes:idioma` → id de la plantilla que se resuelve
    por nombre (o `None` si no hay), para cada interés comercial del catálogo,
    cargando las candidatas UNA sola vez."""
    catalogo = catalogo or Catalogo.de_partida()
    if por_plano is None:
        por_plano = ids_por_nombre(session)
    salida: dict[str, str | None] = {}
    for interes in catalogo.todos:
        if not interes.comercial:
            continue
        for idioma in IDIOMAS_CON_PLANTILLA:
            salida[clave_mapa(interes.codigo, idioma)] = _resolver_por_nombre(
                por_plano, interes.codigo, idioma, catalogo,
            )
    return salida


def huecos(
    session: Session, mapa: dict[str, Any] | None, catalogo: Catalogo | None = None,
    por_plano: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Los intereses comerciales ACTIVOS sin plantilla en alguno de los
    idiomas con plantilla: ni fila en el mapa con plantilla ni plantilla por
    nombre. `a_proposito` cuando la fila del mapa dice «sin plantilla». La
    pantalla de Configuración ERP calcula lo mismo en local para que se vea
    al editar el mapa sin guardar; esta es la lista de referencia."""
    catalogo = catalogo or Catalogo.de_partida()
    mapa = mapa or {}
    if por_plano is None:
        por_plano = ids_por_nombre(session)
    salida: list[dict[str, Any]] = []
    for interes in catalogo.activos:
        if not interes.comercial:
            continue
        for idioma in IDIOMAS_CON_PLANTILLA:
            clave = clave_mapa(interes.codigo, idioma)
            if clave in mapa:
                if str(mapa.get(clave) or "").strip():
                    continue
                salida.append({"interes": interes.codigo, "etiqueta": interes.etiqueta,
                               "idioma": idioma, "a_proposito": True})
                continue
            if _resolver_por_nombre(por_plano, interes.codigo, idioma, catalogo):
                continue
            salida.append({"interes": interes.codigo, "etiqueta": interes.etiqueta,
                           "idioma": idioma, "a_proposito": False})
    return salida


def _del_mapa(session: Session, valor: Any) -> Any | None:
    """La plantilla de una fila del mapa; `None` si la fila dice «sin
    plantilla» (vacío) o la plantilla ya no existe."""
    from app.email_templates.models import EmailTemplate  # noqa: PLC0415

    template_id = str(valor or "").strip()
    if not template_id:
        return None
    return session.get(EmailTemplate, template_id)


def plantilla_para(
    session: Session, intereses: Iterable[str] | str | None, idioma: str | None,
    mapa: dict[str, Any] | None = None, catalogo: Catalogo | None = None,
) -> Any | None:
    """La plantilla para esos intereses (el primero, el principal) en ese
    idioma, o `None`: la fila del conjunto exacto; si no, la del principal
    solo (fila del mapa o plantilla por nombre). Una fila con valor vacío es
    «sin plantilla a propósito» y corta ahí. Un principal que no es comercial
    no lleva plantilla de venta."""
    catalogo = catalogo or Catalogo.de_partida()
    lista = normalizar_lista([intereses] if isinstance(intereses, str) else (intereses or []))
    idioma = (idioma or "").strip().lower()
    if not lista or not idioma:
        return None
    principal = lista[0]
    if not catalogo.comercial(principal):
        return None
    mapa = mapa or {}
    if len(lista) > 1:
        clave = clave_mapa(lista, idioma)
        if clave in mapa:
            return _del_mapa(session, mapa[clave])
    clave = clave_mapa(principal, idioma)
    if clave in mapa:
        return _del_mapa(session, mapa[clave])
    for nombre in nombres_plantilla(principal, idioma, catalogo):
        tpl = buscar_por_nombre(session, nombre)
        if tpl is not None:
            return tpl
    return None
