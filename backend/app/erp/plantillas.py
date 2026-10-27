"""Plantillas de documento: un catálogo, tres destinos.

Una plantilla es un conjunto de LÍNEAS (artículos de FACTUSOL, cantidades,
precios, descuentos y textos). Que acabe siendo una oferta, una proforma o un
pedido se decide al usarla, y cada destino usa el flujo que ya existe para
crearlo: aquí solo se dan las líneas en la forma de cada uno
(`lineas_para`). Se guarda desde una proforma (`F_PRE`/`F_LPS`), un albarán
(`F_ALB`/`F_LAL`) o un pedido de BoHub — siempre LEYENDO; nada de este módulo
escribe en FACTUSOL.

Carpetas: jerárquicas, máx. 3 niveles, como las de las plantillas de correo
(`app.email_templates`). Borrar una carpeta no borra nada: lo que tenía sube
a la raíz.

Visibilidad (`carpetas_permitidas`): las plantillas son de la empresa y se ven
todas. Si a un usuario se le da acceso a alguna carpeta, desde ese momento
SOLO ve esas carpetas y sus subcarpetas — en el árbol, en la lista, en el
buscador y por la API. Sin ninguna fila de acceso lo ve todo, que es el caso
normal. El administrador lo ve siempre todo.

Buscador: cada palabra tiene que aparecer en el nombre, la descripción, una
etiqueta o alguna LÍNEA (referencia comercial, código interno o descripción):
«DM3H» u «horno» encuentran la plantilla que lleva ese artículo aunque su
nombre no lo diga.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.erp.language import SUPPORTED_LANGS, normalize_language
from app.erp.models.plantillas import (
    ORIGEN_ALBARAN,
    ORIGEN_MANUAL,
    ORIGEN_PEDIDO,
    ORIGEN_PRESUPUESTO,
    ORIGENES,
    PlantillaCarpeta,
    PlantillaCarpetaAcceso,
    PlantillaCarpetaPreferencia,
    PlantillaDocumento,
    PlantillaDocumentoEtiqueta,
    PlantillaDocumentoLinea,
)
from app.erp.ofertas import OfertaError, idioma_para, normalizar_lineas
from app.models.crm import Company, Contact, User

logger = logging.getLogger(__name__)

#: Niveles de carpetas, como en las plantillas de correo.
MAX_PROFUNDIDAD = 3
NOMBRE_MAX = 160
CARPETA_NOMBRE_MAX = 200
ETIQUETA_MAX = 60
ETIQUETAS_MAX = 20
#: Palabras del buscador que se tienen en cuenta (las demás se ignoran).
PALABRAS_MAX = 6
#: Líneas que coinciden con la búsqueda que se enseñan por plantilla.
COINCIDENCIAS_MAX = 3

DESTINO_OFERTA = "oferta"
DESTINO_PROFORMA = "proforma"
DESTINO_PEDIDO = "pedido"
DESTINOS: tuple[str, ...] = (DESTINO_OFERTA, DESTINO_PROFORMA, DESTINO_PEDIDO)

#: Límites de la línea del pedido de BoHub (`order_lines`).
PEDIDO_DESCRIPCION_MAX = 255
PEDIDO_CODART_MAX = 13

_IVA_ESPANOL = frozenset({Decimal("0"), Decimal("4"), Decimal("10"), Decimal("21")})


class PlantillaError(OfertaError):
    """Un rechazo con código; la API lo devuelve como 4xx (es un `OfertaError`
    para que los endpoints de ofertas lo traten igual)."""


class _Sin:
    """Marca de «no viene» para distinguirlo de un `None` explícito."""


SIN = _Sin()


# --- utilidades -----------------------------------------------------------------


def normalizar_texto(texto: Any) -> str:
    """Minúsculas, sin acentos y con los espacios colapsados: para comparar
    etiquetas y resaltar coincidencias igual en MySQL que en SQLite."""
    s = unicodedata.normalize("NFKD", str(texto or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def _es_admin(user: User | None) -> bool:
    from app.erp.capabilities import effective_roles  # noqa: PLC0415

    return user is not None and "admin" in effective_roles(user)


def _f(value: Any) -> float:
    return float(Decimal(str(value if value is not None else 0)))


def _comprobar_idioma(idioma: Any) -> str:
    valor = normalize_language(idioma)
    if not valor:
        raise PlantillaError("idioma_invalido", f"Idioma no soportado: {idioma!r}.")
    return valor


# --- carpetas y visibilidad -----------------------------------------------------


def _carpetas(session: Session) -> dict[str, PlantillaCarpeta]:
    return {c.id: c for c in session.scalars(select(PlantillaCarpeta))}


def _hijos(carpetas: dict[str, PlantillaCarpeta]) -> dict[str | None, list[PlantillaCarpeta]]:
    hijos: dict[str | None, list[PlantillaCarpeta]] = {}
    for c in carpetas.values():
        padre = c.parent_id if c.parent_id in carpetas else None
        hijos.setdefault(padre, []).append(c)
    for lista in hijos.values():
        lista.sort(key=lambda c: (c.sort_order or 0, normalizar_texto(c.nombre)))
    return hijos


def _con_descendientes(hijos: dict[str | None, list[PlantillaCarpeta]],
                       ids: set[str]) -> set[str]:
    salida: set[str] = set()
    pendientes = list(ids)
    while pendientes:
        actual = pendientes.pop()
        if actual in salida:
            continue
        salida.add(actual)
        pendientes.extend(h.id for h in hijos.get(actual, []))
    return salida


def carpetas_permitidas(session: Session, user: User | None) -> set[str] | None:
    """Las carpetas que el usuario puede ver, o `None` si las ve todas (sin
    ninguna fila de acceso, o administrador). Con acceso a una carpeta se ven
    también sus subcarpetas."""
    if user is None or _es_admin(user):
        return None
    accesos = set(session.scalars(
        select(PlantillaCarpetaAcceso.carpeta_id).where(PlantillaCarpetaAcceso.user_id == user.id)
    ))
    if not accesos:
        return None
    return _con_descendientes(_hijos(_carpetas(session)), accesos)


def puede_ver_plantilla(session: Session, user: User | None,
                        plantilla: PlantillaDocumento,
                        permitidas: set[str] | None | _Sin = SIN) -> bool:
    if isinstance(permitidas, _Sin):
        permitidas = carpetas_permitidas(session, user)
    return permitidas is None or plantilla.carpeta_id in permitidas


def plantilla_visible(session: Session, user: User | None, plantilla_id: str) -> PlantillaDocumento:
    """La plantilla si el usuario la ve; si no existe O no la ve, 404 (sin
    distinguir: quien no la ve no tiene por qué saber que existe)."""
    plantilla = session.get(PlantillaDocumento, plantilla_id) if plantilla_id else None
    if plantilla is None or not puede_ver_plantilla(session, user, plantilla):
        raise PlantillaError("plantilla_not_found", "La plantilla no existe.", status=404)
    return plantilla


def carpeta_visible(session: Session, user: User | None, carpeta_id: str) -> PlantillaCarpeta:
    carpeta = session.get(PlantillaCarpeta, carpeta_id) if carpeta_id else None
    permitidas = carpetas_permitidas(session, user)
    if carpeta is None or (permitidas is not None and carpeta.id not in permitidas):
        raise PlantillaError("carpeta_not_found", "La carpeta no existe.", status=404)
    return carpeta


def _comprobar_destino_carpeta(session: Session, user: User | None,
                               carpeta_id: str | None) -> str | None:
    """Dónde puede dejar el usuario una plantilla o una carpeta. Quien tiene
    el acceso acotado solo puede usar sus carpetas (en la raíz dejaría de
    verla)."""
    if carpeta_id:
        return carpeta_visible(session, user, carpeta_id).id
    if carpetas_permitidas(session, user) is not None:
        raise PlantillaError(
            "carpeta_requerida",
            "Solo ves algunas carpetas: elige una de ellas (en la raíz no la verías).")
    return None


def _profundidad(carpetas: dict[str, PlantillaCarpeta], carpeta_id: str | None) -> int:
    """Nivel de una carpeta: 1 la de la raíz, 0 la raíz misma."""
    nivel = 0
    actual = carpeta_id
    vistos: set[str] = set()
    while actual and actual in carpetas and actual not in vistos:
        vistos.add(actual)
        nivel += 1
        actual = carpetas[actual].parent_id
    return nivel


def _altura(hijos: dict[str | None, list[PlantillaCarpeta]], carpeta_id: str) -> int:
    """Niveles del subárbol que cuelga de la carpeta (1 si no tiene hijas)."""
    return 1 + max((_altura(hijos, h.id) for h in hijos.get(carpeta_id, [])), default=0)


def crear_carpeta(session: Session, user: User | None, *, nombre: str,
                  parent_id: str | None = None) -> PlantillaCarpeta:
    nombre = str(nombre or "").strip()
    if not nombre:
        raise PlantillaError("nombre_requerido", "La carpeta necesita un nombre.")
    padre = _comprobar_destino_carpeta(session, user, parent_id)
    if _profundidad(_carpetas(session), padre) + 1 > MAX_PROFUNDIDAD:
        raise PlantillaError("profundidad_maxima",
                             f"Profundidad máxima alcanzada ({MAX_PROFUNDIDAD} niveles).")
    carpeta = PlantillaCarpeta(
        nombre=nombre[:CARPETA_NOMBRE_MAX], parent_id=padre,
        owner_user_id=user.id if user is not None else None, sort_order=0,
    )
    session.add(carpeta)
    session.flush()
    return carpeta


def actualizar_carpeta(session: Session, user: User | None, carpeta: PlantillaCarpeta, *,
                       nombre: str | None = None,
                       parent_id: str | None | _Sin = SIN) -> PlantillaCarpeta:
    if nombre is not None:
        if not nombre.strip():
            raise PlantillaError("nombre_requerido", "La carpeta necesita un nombre.")
        carpeta.nombre = nombre.strip()[:CARPETA_NOMBRE_MAX]
    if not isinstance(parent_id, _Sin):
        nuevo = _comprobar_destino_carpeta(session, user, parent_id)
        if nuevo == carpeta.id:
            raise PlantillaError("ciclo", "Una carpeta no puede ser su propia madre.")
        carpetas = _carpetas(session)
        actual = nuevo
        while actual:
            if actual == carpeta.id:
                raise PlantillaError("ciclo", "No puedes mover una carpeta dentro de sí misma.")
            actual = carpetas[actual].parent_id if actual in carpetas else None
        # A diferencia de las de correo, mover también respeta los 3 niveles
        # (contando todo lo que cuelga de la carpeta).
        if _profundidad(carpetas, nuevo) + _altura(_hijos(carpetas), carpeta.id) > MAX_PROFUNDIDAD:
            raise PlantillaError("profundidad_maxima",
                                 f"Profundidad máxima alcanzada ({MAX_PROFUNDIDAD} niveles).")
        carpeta.parent_id = nuevo
    session.flush()
    return carpeta


def borrar_carpeta(session: Session, carpeta: PlantillaCarpeta) -> None:
    """Como en las de correo: sus plantillas y subcarpetas suben a la raíz;
    su acceso y las preferencias que la apuntan desaparecen. No borra
    ninguna plantilla."""
    for p in session.scalars(select(PlantillaDocumento).where(
            PlantillaDocumento.carpeta_id == carpeta.id)):
        p.carpeta_id = None
    for hija in session.scalars(select(PlantillaCarpeta).where(
            PlantillaCarpeta.parent_id == carpeta.id)):
        hija.parent_id = None
    for fila in session.scalars(select(PlantillaCarpetaAcceso).where(
            PlantillaCarpetaAcceso.carpeta_id == carpeta.id)):
        session.delete(fila)
    for fila in session.scalars(select(PlantillaCarpetaPreferencia).where(
            PlantillaCarpetaPreferencia.carpeta_id == carpeta.id)):
        session.delete(fila)
    session.flush()
    session.delete(carpeta)
    session.flush()


def acceso_de(session: Session, carpeta_id: str) -> list[str]:
    return sorted(session.scalars(select(PlantillaCarpetaAcceso.user_id).where(
        PlantillaCarpetaAcceso.carpeta_id == carpeta_id)))


def fijar_acceso(session: Session, carpeta: PlantillaCarpeta, user_ids: list[str]) -> list[str]:
    """Reconcilia quién tiene acceso a la carpeta con la lista dada (lo que
    hace `_sync_folder_shares` en las de correo): quita los que sobran y
    añade los que faltan. Un usuario que no existe es un 422, no un 500."""
    deseados = {str(u).strip() for u in user_ids if str(u or "").strip()}
    existentes = set(session.scalars(select(User.id).where(User.id.in_(deseados)))) if deseados \
        else set()
    faltan = deseados - existentes
    if faltan:
        raise PlantillaError("usuario_no_existe",
                             f"No existe el usuario {sorted(faltan)[0]}.")
    actuales = {f.user_id: f for f in session.scalars(select(PlantillaCarpetaAcceso).where(
        PlantillaCarpetaAcceso.carpeta_id == carpeta.id))}
    for uid, fila in actuales.items():
        if uid not in deseados:
            session.delete(fila)
    ahora = datetime.now(UTC)
    for uid in deseados - set(actuales):
        session.add(PlantillaCarpetaAcceso(carpeta_id=carpeta.id, user_id=uid, created_at=ahora))
    session.flush()
    return sorted(deseados)


def carpeta_defecto(session: Session, user: User | None) -> str | None:
    if user is None:
        return None
    pref = session.scalar(select(PlantillaCarpetaPreferencia).where(
        PlantillaCarpetaPreferencia.user_id == user.id))
    if pref is None:
        return None
    permitidas = carpetas_permitidas(session, user)
    if permitidas is not None and pref.carpeta_id not in permitidas:
        return None
    return pref.carpeta_id


def fijar_carpeta_defecto(session: Session, user: User, carpeta_id: str | None) -> str | None:
    pref = session.scalar(select(PlantillaCarpetaPreferencia).where(
        PlantillaCarpetaPreferencia.user_id == user.id))
    if carpeta_id is None:
        if pref is not None:
            session.delete(pref)
        session.flush()
        return None
    carpeta = carpeta_visible(session, user, carpeta_id)
    if pref is None:
        session.add(PlantillaCarpetaPreferencia(user_id=user.id, carpeta_id=carpeta.id))
    else:
        pref.carpeta_id = carpeta.id
    session.flush()
    return carpeta.id


def rutas_carpetas(session: Session) -> dict[str, str]:
    """`{id: "Ferias / FESPA"}`: el camino de cada carpeta para enseñarlo."""
    carpetas = _carpetas(session)
    rutas: dict[str, str] = {}
    for cid in carpetas:
        partes: list[str] = []
        actual: str | None = cid
        vistos: set[str] = set()
        while actual and actual in carpetas and actual not in vistos:
            vistos.add(actual)
            partes.append(carpetas[actual].nombre)
            actual = carpetas[actual].parent_id
        rutas[cid] = " / ".join(reversed(partes))
    return rutas


def arbol(session: Session, user: User | None, *, con_acceso: bool = False) -> list[dict[str, Any]]:
    """El árbol de carpetas que el usuario ve, con cuántas plantillas tiene
    cada una (las que él ve). Una carpeta permitida cuya madre no lo está se
    enseña en la raíz (como hace el árbol de las de correo)."""
    carpetas = _carpetas(session)
    hijos = _hijos(carpetas)
    permitidas = carpetas_permitidas(session, user)
    defecto = carpeta_defecto(session, user)
    stmt = select(PlantillaDocumento.carpeta_id, func.count()).group_by(
        PlantillaDocumento.carpeta_id)
    cuentas = {cid: n for cid, n in session.execute(stmt)}
    accesos: dict[str, list[str]] = {}
    if con_acceso:
        for fila in session.scalars(select(PlantillaCarpetaAcceso)):
            accesos.setdefault(fila.carpeta_id, []).append(fila.user_id)

    def nodo(c: PlantillaCarpeta) -> dict[str, Any]:
        out = {
            "id": c.id, "nombre": c.nombre, "parent_id": c.parent_id,
            "num_plantillas": int(cuentas.get(c.id, 0)), "es_defecto": c.id == defecto,
            "children": construir(c.id),
        }
        if con_acceso:
            out["acceso_user_ids"] = sorted(accesos.get(c.id, []))
        return out

    def construir(padre: str | None) -> list[dict[str, Any]]:
        salida: list[dict[str, Any]] = []
        for c in hijos.get(padre, []):
            if permitidas is None or c.id in permitidas:
                salida.append(nodo(c))
            else:
                salida.extend(construir(c.id))
        return salida

    return construir(None)


# --- etiquetas --------------------------------------------------------------------


def limpiar_etiquetas(raw: Any) -> list[str]:
    """Las etiquetas tal cual se escribieron, sin repetidas (sin distinguir
    mayúsculas ni acentos), sin vacías y como mucho `ETIQUETAS_MAX`."""
    vistas: set[str] = set()
    salida: list[str] = []
    for item in raw or []:
        texto = re.sub(r"\s+", " ", str(item or "")).strip()[:ETIQUETA_MAX]
        norm = normalizar_texto(texto)
        if not norm or norm in vistas:
            continue
        vistas.add(norm)
        salida.append(texto)
    if len(salida) > ETIQUETAS_MAX:
        raise PlantillaError("demasiadas_etiquetas",
                             f"Una plantilla lleva como mucho {ETIQUETAS_MAX} etiquetas.")
    return salida


def _fijar_etiquetas(plantilla: PlantillaDocumento, etiquetas: list[str]) -> None:
    deseadas = {normalizar_texto(e)[:ETIQUETA_MAX]: e for e in etiquetas}
    for etq in list(plantilla.etiquetas):
        if etq.etiqueta_norm not in deseadas:
            plantilla.etiquetas.remove(etq)
        else:
            etq.etiqueta = deseadas.pop(etq.etiqueta_norm)
    for norm, texto in deseadas.items():
        plantilla.etiquetas.append(PlantillaDocumentoEtiqueta(etiqueta=texto, etiqueta_norm=norm))


def etiquetas_en_uso(session: Session, user: User | None) -> list[dict[str, Any]]:
    """Las etiquetas de las plantillas que el usuario ve, con cuántas llevan
    cada una (para el filtro y el autocompletado)."""
    permitidas = carpetas_permitidas(session, user)
    stmt = select(PlantillaDocumentoEtiqueta.etiqueta_norm,
                  func.min(PlantillaDocumentoEtiqueta.etiqueta), func.count()).join(
        PlantillaDocumento, PlantillaDocumento.id == PlantillaDocumentoEtiqueta.plantilla_id,
    ).group_by(PlantillaDocumentoEtiqueta.etiqueta_norm)
    if permitidas is not None:
        stmt = stmt.where(PlantillaDocumento.carpeta_id.in_(permitidas or {""}))
    filas = [{"etiqueta": texto, "norm": norm, "num": int(n)}
             for norm, texto, n in session.execute(stmt)]
    return sorted(filas, key=lambda f: f["norm"])


# --- líneas de los orígenes (solo LECTURA de FACTUSOL) -----------------------------


def _iva_de_codigo(valor: Any) -> Decimal:
    """`IVAL**` puede ser un código de tipo (0 = general): solo se acepta como
    porcentaje si es uno de los españoles y no es 0 (mismo criterio que
    `quotes._iva_pct_from_line`); si no, 21 %."""
    try:
        dec = Decimal(str(valor))
    except Exception:  # noqa: BLE001 — basura en la columna
        return Decimal("21")
    return dec if dec in _IVA_ESPANOL and dec != 0 else Decimal("21")


def lineas_de_presupuesto(client: Any, session: Session, *, serie: int, codigo: int,
                          ejercicio: str) -> list[dict[str, Any]]:
    """Las líneas REALES de un presupuesto de FACTUSOL (`F_LPS`) con su
    `codart` (interno) y su `sku` (comercial). 404 si no existe."""
    from app.integrations.factusol.quotes import get_quote  # noqa: PLC0415

    quote = get_quote(client, session, str(codigo), ejercicio=ejercicio, serie=serie)
    if quote is None:
        raise PlantillaError("documento_not_found",
                             f"No existe el presupuesto {serie}-{codigo}.", status=404)
    return normalizar_lineas([{
        "codart": ln.get("codart"), "sku": ln.get("sku"),
        "descripcion": ln.get("description") or "",
        "cantidad": ln.get("quantity"), "precio": ln.get("unit_price"),
        "descuento_pct": ln.get("discount_pct"), "iva_pct": ln.get("iva_pct"),
    } for ln in quote.get("lines") or []])


def lineas_de_albaran(client: Any, session: Session, *, serie: int, codigo: int,
                      ejercicio: str) -> list[dict[str, Any]]:
    """Las líneas de un albarán de FACTUSOL (`F_LAL`), con el `sku` comercial
    de cada artículo (una consulta a F_ART). 404 si no existe."""
    from app.integrations.factusol.documents import get_document  # noqa: PLC0415
    from app.integrations.factusol.quotes import skus_for_codarts  # noqa: PLC0415

    _ = session
    doc = get_document(client, "albaranes", serie=int(serie), codigo=int(codigo),
                       ejercicio=ejercicio)
    if doc is None:
        raise PlantillaError("documento_not_found",
                             f"No existe el albarán {serie}-{codigo}.", status=404)
    filas = list(doc.get("lines") or [])
    try:
        skus = skus_for_codarts(client, [ln.get("codart") for ln in filas], ejercicio=ejercicio)
    except Exception:  # noqa: BLE001 — sin el SKU comercial se enseña el código
        logger.warning("plantillas: no se pudo leer el SKU de los artículos del albarán "
                       "%s-%s", serie, codigo, exc_info=True)
        skus = {}
    return normalizar_lineas([{
        "codart": ln.get("codart"), "sku": skus.get(str(ln.get("codart") or "").strip())
        or ln.get("codart"),
        "descripcion": ln.get("description") or "",
        "cantidad": ln.get("quantity"), "precio": ln.get("unit_price"),
        "descuento_pct": ln.get("discount_pct") or 0,
        "iva_pct": _iva_de_codigo(ln.get("iva_pct")),
    } for ln in filas])


def lineas_de_pedido(order: Any) -> list[dict[str, Any]]:
    """Las líneas de MERCANCÍA de un pedido de BoHub (sin los portes), con el
    descuento recuperado de su nota «dto. X%» (como hace el albarán)."""
    from app.integrations.factusol.albaran_manual import order_lines_for_document  # noqa: PLC0415

    mercancia = [ln for ln in sorted(order.lines, key=lambda ln: ln.position)
                 if not getattr(ln, "is_shipping", False)]
    salida = []
    for linea, doc in zip(mercancia, order_lines_for_document(order), strict=False):
        salida.append({
            "codart": (linea.product_codart or "").strip() or None,
            "sku": (linea.product_sku or "").strip() or None,
            "descripcion": doc["description"], "cantidad": doc["quantity"],
            "precio": doc["unit_price"], "descuento_pct": doc["discount_pct"],
            "iva_pct": doc["iva_pct"],
        })
    return normalizar_lineas(salida)


# --- plantillas -----------------------------------------------------------------


def _volcar(plantilla: PlantillaDocumento, lineas: list[dict[str, Any]]) -> None:
    plantilla.lineas.clear()
    for pos, ln in enumerate(lineas, start=1):
        plantilla.lineas.append(PlantillaDocumentoLinea(
            posicion=pos, codart=ln["codart"], sku=ln["sku"], descripcion=ln["descripcion"],
            cantidad=ln["cantidad"], precio=ln["precio"], descuento_pct=ln["descuento_pct"],
            iva_pct=ln["iva_pct"],
        ))


def crear_plantilla(
    session: Session, *, nombre: str, lineas: list[dict[str, Any]], usuario: User | None,
    descripcion: str | None = None, idioma: str = "es", carpeta_id: str | None = None,
    etiquetas: list[str] | None = None, activa: bool = True,
    origen_tipo: str = ORIGEN_MANUAL, origen_serie: int | None = None,
    origen_codigo: int | None = None, origen_order_id: str | None = None,
    origen_numero: str | None = None,
) -> PlantillaDocumento:
    nombre = str(nombre or "").strip()
    if not nombre:
        raise PlantillaError("nombre_requerido", "La plantilla necesita un nombre.")
    if not lineas:
        raise PlantillaError("sin_lineas", "La plantilla necesita al menos una línea.")
    if origen_tipo not in ORIGENES:
        raise PlantillaError("origen_invalido", f"Origen no válido: {origen_tipo!r}.")
    plantilla = PlantillaDocumento(
        nombre=nombre[:NOMBRE_MAX], descripcion=(descripcion or "").strip() or None,
        activa=bool(activa), idioma=_comprobar_idioma(idioma or "es"),
        carpeta_id=_comprobar_destino_carpeta(session, usuario, carpeta_id),
        origen_tipo=origen_tipo, origen_serie=origen_serie, origen_codigo=origen_codigo,
        origen_order_id=origen_order_id, origen_numero=(origen_numero or None),
        created_by_user_id=usuario.id if usuario is not None else None,
    )
    _volcar(plantilla, lineas)
    _fijar_etiquetas(plantilla, limpiar_etiquetas(etiquetas))
    session.add(plantilla)
    session.flush()
    return plantilla


def numero_documento(serie: Any, codigo: Any) -> str:
    return f"{serie}-{int(codigo):06d}" if serie else str(codigo)


def plantilla_desde_documento(
    session: Session, client: Any, *, tipo: str, serie: int, codigo: int, ejercicio: str,
    nombre: str, usuario: User | None, **extra: Any,
) -> PlantillaDocumento:
    """«Guardar como plantilla» sobre una proforma o un albarán de FACTUSOL:
    copia sus líneas con sus artículos. Solo LEE de FACTUSOL."""
    if tipo == ORIGEN_PRESUPUESTO:
        lineas = lineas_de_presupuesto(client, session, serie=serie, codigo=codigo,
                                       ejercicio=ejercicio)
    elif tipo == ORIGEN_ALBARAN:
        lineas = lineas_de_albaran(client, session, serie=serie, codigo=codigo,
                                   ejercicio=ejercicio)
    else:
        raise PlantillaError("origen_invalido", f"Origen no válido: {tipo!r}.")
    return crear_plantilla(
        session, nombre=nombre, lineas=lineas, usuario=usuario, origen_tipo=tipo,
        origen_serie=int(serie), origen_codigo=int(codigo),
        origen_numero=numero_documento(serie, codigo), **extra,
    )


def plantilla_desde_pedido(session: Session, order: Any, *, nombre: str, usuario: User | None,
                           **extra: Any) -> PlantillaDocumento:
    """«Guardar como plantilla» sobre un pedido de BoHub (sus líneas de
    mercancía, sin los portes)."""
    return crear_plantilla(
        session, nombre=nombre, lineas=lineas_de_pedido(order), usuario=usuario,
        origen_tipo=ORIGEN_PEDIDO, origen_order_id=order.id,
        origen_numero=order.order_number, **extra,
    )


def actualizar_plantilla(
    session: Session, plantilla: PlantillaDocumento, *, usuario: User | None,
    nombre: str | None = None, descripcion: str | None = None, activa: bool | None = None,
    idioma: str | None = None, lineas: list[dict[str, Any]] | None = None,
    etiquetas: list[str] | None = None, carpeta_id: str | None | _Sin = SIN,
) -> PlantillaDocumento:
    if nombre is not None:
        if not nombre.strip():
            raise PlantillaError("nombre_requerido", "La plantilla necesita un nombre.")
        plantilla.nombre = nombre.strip()[:NOMBRE_MAX]
    if descripcion is not None:
        plantilla.descripcion = descripcion.strip() or None
    if activa is not None:
        plantilla.activa = bool(activa)
    if idioma is not None:
        plantilla.idioma = _comprobar_idioma(idioma)
    if not isinstance(carpeta_id, _Sin):
        plantilla.carpeta_id = _comprobar_destino_carpeta(session, usuario, carpeta_id)
    if etiquetas is not None:
        _fijar_etiquetas(plantilla, limpiar_etiquetas(etiquetas))
    if lineas is not None:
        if not lineas:
            raise PlantillaError("sin_lineas", "La plantilla necesita al menos una línea.")
        _volcar(plantilla, lineas)
    session.flush()
    return plantilla


def _linea_dict(ln: PlantillaDocumentoLinea) -> dict[str, Any]:
    return {
        "id": ln.id, "posicion": ln.posicion, "codart": ln.codart, "sku": ln.sku,
        "descripcion": ln.descripcion, "cantidad": _f(ln.cantidad), "precio": _f(ln.precio),
        "descuento_pct": _f(ln.descuento_pct), "iva_pct": _f(ln.iva_pct),
        "text_only": ln.codart is None and _f(ln.cantidad) == 0 and _f(ln.precio) == 0,
    }


def plantilla_como_dict(p: PlantillaDocumento, *, con_lineas: bool = True,
                        rutas: dict[str, str] | None = None,
                        coincidencias: list[dict[str, Any]] | None = None,
                        idioma_cliente: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": p.id, "nombre": p.nombre, "descripcion": p.descripcion, "activa": bool(p.activa),
        "idioma": p.idioma, "carpeta_id": p.carpeta_id,
        "carpeta": (rutas or {}).get(p.carpeta_id or "") if p.carpeta_id else None,
        "etiquetas": [e.etiqueta for e in p.etiquetas],
        "origen_tipo": p.origen_tipo, "origen_serie": p.origen_serie,
        "origen_codigo": p.origen_codigo, "origen_order_id": p.origen_order_id,
        "origen_numero": p.origen_numero,
        "num_lineas": len(p.lineas),
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }
    if idioma_cliente is not None:
        out["idioma_coincide"] = p.idioma == idioma_cliente
    if coincidencias is not None:
        out["coincidencias"] = coincidencias
    if con_lineas:
        out["lineas"] = [_linea_dict(ln) for ln in p.lineas]
    return out


# --- buscador y lista -------------------------------------------------------------


def palabras_busqueda(q: str | None) -> list[str]:
    vistas: set[str] = set()
    salida: list[str] = []
    for palabra in str(q or "").split():
        norm = normalizar_texto(palabra)
        if not norm or norm in vistas:
            continue
        vistas.add(norm)
        salida.append(palabra.strip())
        if len(salida) >= PALABRAS_MAX:
            break
    return salida


def _like(palabra: str) -> str:
    escapada = palabra.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escapada}%"


def _filtro_palabra(palabra: str) -> Any:
    patron = _like(palabra)
    en_lineas = select(PlantillaDocumentoLinea.plantilla_id).where(or_(
        PlantillaDocumentoLinea.codart.ilike(patron, escape="\\"),
        PlantillaDocumentoLinea.sku.ilike(patron, escape="\\"),
        PlantillaDocumentoLinea.descripcion.ilike(patron, escape="\\"),
    ))
    en_etiquetas = select(PlantillaDocumentoEtiqueta.plantilla_id).where(
        PlantillaDocumentoEtiqueta.etiqueta_norm.like(_like(normalizar_texto(palabra)),
                                                      escape="\\"))
    return or_(
        PlantillaDocumento.nombre.ilike(patron, escape="\\"),
        PlantillaDocumento.descripcion.ilike(patron, escape="\\"),
        PlantillaDocumento.id.in_(en_lineas),
        PlantillaDocumento.id.in_(en_etiquetas),
    )


def listar(
    session: Session, user: User | None, *, q: str | None = None,
    carpeta_id: str | None = None, sin_carpeta: bool = False,
    etiquetas: list[str] | None = None, idioma: str | None = None,
    solo_activas: bool = False, limit: int = 300,
) -> list[PlantillaDocumento]:
    """Las plantillas que el usuario ve, filtradas. Cada palabra de `q` tiene
    que estar en el nombre, la descripción, una etiqueta o alguna línea
    (referencia, código o descripción). `carpeta_id` incluye sus subcarpetas."""
    stmt = select(PlantillaDocumento).options(
        selectinload(PlantillaDocumento.lineas), selectinload(PlantillaDocumento.etiquetas),
    )
    permitidas = carpetas_permitidas(session, user)
    if permitidas is not None:
        stmt = stmt.where(PlantillaDocumento.carpeta_id.in_(permitidas or {""}))
    if carpeta_id:
        dentro = _con_descendientes(_hijos(_carpetas(session)), {carpeta_id})
        stmt = stmt.where(PlantillaDocumento.carpeta_id.in_(dentro))
    elif sin_carpeta:
        stmt = stmt.where(PlantillaDocumento.carpeta_id.is_(None))
    for etiqueta in etiquetas or []:
        norm = normalizar_texto(etiqueta)
        if norm:
            stmt = stmt.where(PlantillaDocumento.id.in_(
                select(PlantillaDocumentoEtiqueta.plantilla_id).where(
                    PlantillaDocumentoEtiqueta.etiqueta_norm == norm)))
    if idioma:
        stmt = stmt.where(PlantillaDocumento.idioma == normalize_language(idioma))
    if solo_activas:
        stmt = stmt.where(PlantillaDocumento.activa.is_(True))
    for palabra in palabras_busqueda(q):
        stmt = stmt.where(_filtro_palabra(palabra))
    stmt = stmt.order_by(PlantillaDocumento.activa.desc(), PlantillaDocumento.nombre.asc())
    return list(session.scalars(stmt.limit(max(1, min(int(limit), 1000)))))


def coincidencias(plantilla: PlantillaDocumento, palabras: list[str]) -> list[dict[str, Any]]:
    """Las líneas donde está alguna de las palabras buscadas (para enseñar por
    qué ha salido una plantilla cuyo nombre no lo dice)."""
    normas = [normalizar_texto(p) for p in palabras if normalizar_texto(p)]
    if not normas:
        return []
    salida = []
    for ln in plantilla.lineas:
        campos = " ".join(normalizar_texto(x) for x in (ln.sku, ln.codart, ln.descripcion) if x)
        if any(n in campos for n in normas):
            salida.append({"posicion": ln.posicion, "sku": ln.sku or ln.codart,
                           "descripcion": (ln.descripcion or "")[:140]})
            if len(salida) >= COINCIDENCIAS_MAX:
                break
    return salida


def ordenar_por_idioma(plantillas: list[PlantillaDocumento],
                       idioma: str | None) -> list[PlantillaDocumento]:
    """Las del idioma del cliente primero; las demás detrás (no se ocultan)."""
    if not idioma:
        return plantillas
    return sorted(plantillas, key=lambda p: 0 if p.idioma == idioma else 1)


def idioma_cliente(session: Session, *, contact_id: str | None = None,
                   company_id: str | None = None, order_id: str | None = None,
                   serie: int | None = None) -> tuple[str | None, str | None]:
    """`(idioma, fuente)` del cliente para el que se va a crear el documento:
    el del pedido si lo tiene; si no, la cascada de las ofertas (contacto,
    empresa, país, emisora). `(None, None)` si no hay cliente."""
    from app.erp.models import Order  # noqa: PLC0415

    company: Company | None = None
    contacto: Contact | None = None
    if order_id:
        order = session.get(Order, order_id)
        if order is not None:
            idioma = normalize_language(order.language)
            if idioma:
                return idioma, "pedido"
            company = session.get(Company, order.company_id) if order.company_id else None
            contacto = session.get(Contact, order.contact_id) if order.contact_id else None
    if contact_id:
        contacto = session.get(Contact, contact_id)
        if contacto is not None and contacto.company_id and company is None:
            company = contacto.company
    if company_id and company is None:
        company = session.get(Company, company_id)
    if contacto is None and company is None:
        return None, None
    try:
        serie_ok = int(serie) if serie else 1
    except (TypeError, ValueError):
        serie_ok = 1
    return idioma_para(session, contacto, company, serie_ok)


# --- las líneas para cada destino --------------------------------------------------


def _cortar(texto: str, limite: int) -> str:
    if len(texto) <= limite:
        return texto
    corte = texto[:limite].rsplit(" ", 1)[0] if " " in texto[:limite] else texto[:limite]
    return corte.rstrip()


def lineas_para(plantilla: PlantillaDocumento,
                destino: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Las líneas de la plantilla en la forma del flujo de cada destino, y los
    avisos de lo que no cabe tal cual. La plantilla solo aporta las líneas:
    cliente, serie, fechas y demás los pone el formulario de siempre.

    - oferta: las mismas líneas (`codart`, `sku`, `descripcion`, …).
    - proforma: la forma de `POST /api/erp/factusol/quotes` (`codart` con el
      código interno, que no necesita traducción; `sku` para enseñarlo).
    - pedido: la de `POST /api/erp/orders`. El pedido no tiene descuento por
      línea: el precio va NETO (cuadra el importe). Sus líneas no admiten
      texto suelto ni más de 255 caracteres: una línea de texto se pega a la
      anterior y lo que no cabe se recorta, y se avisa."""
    if destino not in DESTINOS:
        raise PlantillaError("destino_invalido", f"Destino no válido: {destino!r}.")
    lineas = [_linea_dict(ln) for ln in plantilla.lineas]
    avisos: list[str] = []
    if destino == DESTINO_OFERTA:
        return [{k: ln[k] for k in ("codart", "sku", "descripcion", "cantidad", "precio",
                                    "descuento_pct", "iva_pct")} for ln in lineas], avisos
    if destino == DESTINO_PROFORMA:
        return [{
            # Una línea de texto va sin artículo: cadena vacía, como en el alta.
            "codart": ln["codart"] or ln["sku"] or "", "sku": ln["sku"] or ln["codart"],
            "description": ln["descripcion"], "quantity": ln["cantidad"],
            "unit_price": ln["precio"], "discount_pct": ln["descuento_pct"],
            "iva_pct": ln["iva_pct"], "text_only": ln["text_only"],
        } for ln in lineas], avisos

    salida: list[dict[str, Any]] = []
    for ln in lineas:
        if ln["text_only"] and salida:
            salida[-1]["description"] = f"{salida[-1]['description']} {ln['descripcion']}".strip()
            continue
        precio = Decimal(str(ln["precio"]))
        if ln["descuento_pct"]:
            neto = precio * (1 - Decimal(str(ln["descuento_pct"])) / 100)
            precio = neto.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            avisos.append(
                f"Línea {ln['posicion']}: el pedido no lleva descuento por línea; el precio "
                f"va neto (−{ln['descuento_pct']:g} %).")
        cantidad = ln["cantidad"]
        if cantidad <= 0:
            cantidad = 1.0
        codart = ln["codart"] if ln["codart"] and len(ln["codart"]) <= PEDIDO_CODART_MAX else None
        salida.append({
            "product_sku": ln["sku"] or ln["codart"] or "", "product_codart": codart,
            "description": ln["descripcion"], "quantity": cantidad, "unit_price": float(precio),
            "tax_rate": ln["iva_pct"], "_posicion": ln["posicion"],
        })
    for item in salida:
        texto = item["description"] or item["product_sku"]
        if len(texto) > PEDIDO_DESCRIPCION_MAX:
            avisos.append(
                f"Línea {item['_posicion']}: la descripción tiene {len(texto)} caracteres y en "
                f"el pedido caben {PEDIDO_DESCRIPCION_MAX}; se ha recortado.")
            texto = _cortar(texto, PEDIDO_DESCRIPCION_MAX)
        item["description"] = texto
        item.pop("_posicion")
    return salida, avisos


def idiomas() -> list[str]:
    return list(SUPPORTED_LANGS)
