"""Las máquinas, artículo a artículo, y las series que cuentan (31/10/2026).

**«Máquina» se decide por artículo.** Una familia de clase máquina ya no
mete a todos sus artículos en el parque: marca cuáles hay que mirar (en la
021 de Flux, de 65 vendidos solo una docena son máquinas; el resto, filtros,
lentes, tubos…). Un artículo es máquina SOLO si está marcado
(`historico_articulos_clase.clase = maquina`); lo que no esté marcado no
entra, y un artículo nuevo se queda fuera hasta que alguien lo marque (antes
falta una máquina que sobra un filtro). La clase de la familia sigue valiendo
para todo lo demás (consumible, recambio, accesorio, otro) y la gama sigue
viniendo de la familia.

**BoHub propone, Bart confirma.** Pistas en la descripción
(`historico_reglas_articulo`, editables) que delatan que algo NO es una
máquina; lo demás se propone como máquina. «Aceptar todas» solo toca lo que
nadie ha revisado (sin fila en `historico_articulos_clase`); lo que Bart
marcó o desmarcó no se pisa.

**Series que cuentan como venta** (`historico_series`): una serie marcada como
que no cuenta queda fuera del parque y de «Quién compró qué». Sin fila,
cuenta.

Solo lee lo volcado de FACTUSOL; lo que se escribe es de BoHub.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case, func, null, select
from sqlalchemy.orm import Session

from app.erp.models.historico import (
    CLASE_MAQUINA,
    CLASE_NO_MAQUINA,
    TIPOS_VENTA,
    HistoricoArticulo,
    HistoricoArticuloClase,
    HistoricoDocumento,
    HistoricoFamilia,
    HistoricoLinea,
    HistoricoReglaArticulo,
    HistoricoSerie,
)

#: Las pistas con las que arranca (las de Bart, 31/10/2026). La migración
#: 0158 las siembra; desde ahí son datos editables.
PISTAS_INICIALES = (
    "SPARE PARTS", "RECAMBIO", "FILTER", "FILTRO", "LENS", "LENTE", "TUBE", "TUBO",
    "ROTARY MODULE", "SOFTWARE", "CARTRIDGE", "CARTUCHO", "ADAPTER", "ADAPTADOR",
    "KIT", "GARANTÍA", "GARANTIE", "WARRANTY", "MANUAL", "PUMP", "BOMBA", "MIRROR",
    "ESPEJO", "LID", "TAPA", "HOSE", "PLATFORM", "DONGLE", "PROBE", "COVER",
)

#: Lo que el nombre dice que SÍ es una máquina (31/10/2026: la propuesta exige
#: evidencia positiva). La 0159 las siembra.
PISTAS_MAQUINA = (
    "IMPRESORA", "PRINTER", "DUPLICADORA", "ENCELOFANADORA", "BEAMO", "BEAMBOX", "HEXA",
    "ADOR", "SPEEDSTAR", "CELOPACK", "MBO", "ARTIS", "OMNIPRINTER", "EPSON PP",
)
#: Negativas que añade la 0159, de los artículos que la primera propuesta
#: daba por máquina sin serlo (placa, motor, ventilador…).
PISTAS_NO_0159 = (
    "MAINBOARD", "MOTOR", "STEPPER", "FAN", "POWER SUPPLY", "CLAMP", "DUCT", "SENSOR",
    "LUBRICANT", "CUCHILLA", "LAMINA", "PLATO", "MESA", "RASPBERRY",
)
#: Positivas que añade la 0160 (las que se repiten en el catálogo).
PISTAS_MAQUINA_0160 = ("ADR", "TORRE DUPLICADORA", "PLANCHA", "MULTIFUNCION", "FIBER LASER")
#: El umbral de precio (`historico_ajustes`): por encima, un artículo SIN
#: pista se propone como dudoso. Nunca cancela una propuesta del texto.
AJUSTE_UMBRAL = "umbral_precio_maquina"
UMBRAL_POR_DEFECTO = 2000.0
#: Lo que deja «Dar por revisadas las que quedan» en `por`, para deshacerlo.
MARCA_LOTE = "(dar por revisadas {familia})"
#: 0161: positivas que faltaban, y las pistas que van como palabra entera
#: (abren la palabra que ensucian: HEXAGONAL, LAMINADORA).
PISTAS_MAQUINA_0161 = ("LAMINADORA", "ENCELOFANADORA")
PALABRA_ENTERA_0161 = ("HEXA", "LAMINA")
#: 0162: negativas de módulos y rotatorios que empiezan por BEAMBOX o ADOR.
PISTAS_NO_0162 = ("MODULE", "MÓDULO", "ROTATORIO")
PISTA_SI, PISTA_NO = "maquina", "no_maquina"
PROPUESTA_SI, PROPUESTA_NO, PROPUESTA_DUDOSA = "maquina", "no_maquina", "dudoso"

#: Estado de un artículo de una familia de máquinas.
MARCADA = "maquina"          # marcada como máquina
NO_MAQUINA = "no_maquina"    # revisada: no es máquina (o tiene otra clase propia)
SIN_REVISAR = "sin_revisar"  # nadie la ha mirado: no entra en el parque


def _ahora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# --- la clase que vale -------------------------------------------------------------------


def clase_efectiva(propia_clase: Any, familia_clase: Any) -> Any:
    """La clase de un artículo en SQL: la propia si la tiene; si no (o si es
    `no_maquina`, «revisado: no es máquina»), la de su familia, salvo
    «máquina», que por familia ya no basta (es por artículo)."""
    return case(
        ((propia_clase.is_not(None)) & (propia_clase != CLASE_NO_MAQUINA), propia_clase),
        (familia_clase == CLASE_MAQUINA, null()),
        else_=familia_clase,
    )


def clase_efectiva_py(propia: str | None, familia: str | None) -> str | None:
    if propia and propia != CLASE_NO_MAQUINA:
        return propia
    return None if familia == CLASE_MAQUINA else familia


# --- series ------------------------------------------------------------------------------


def series_excluidas(session: Session) -> set[str]:
    return set(session.scalars(select(HistoricoSerie.serie).where(
        HistoricoSerie.cuenta.is_(False))))


def nombres_de_serie(session: Session) -> dict[str, str]:
    """El nombre de cada serie, como lo enseña Ajustes ERP (1/11/2026): el
    nombre puesto a la serie; si no, el de la empresa emisora de esa serie;
    si no, su abreviatura. Antes solo se miraba el primero, que casi nunca
    se rellena, y todas salían «sin nombre»."""
    from app.erp.factusol_pdf import merge_companies  # noqa: PLC0415
    from app.erp.seguimiento import series_abbreviations_config  # noqa: PLC0415
    from app.integrations.factusol.service import (  # noqa: PLC0415
        series_config,
        series_names,
    )

    cfg = series_config(session)
    out = {str(k): v for k, v in series_abbreviations_config(
        cfg.get("series_abbreviations")).items() if v}
    for k, comp in merge_companies(cfg.get("companies")).items():
        nombre = str((comp or {}).get("nombre") or "").strip()
        if nombre:
            out[str(k)] = nombre
    out.update({str(k): v for k, v in series_names(session).items()})
    return out


def series(session: Session) -> dict[str, Any]:
    """Las series de las ventas volcadas, con su nombre (Ajustes ERP) si lo
    tiene, cuántos documentos y líneas tienen, y si cuentan. Y lo que se
    queda fuera con lo marcado."""
    doc, lin = HistoricoDocumento, HistoricoLinea
    nombres = nombres_de_serie(session)
    fuera = series_excluidas(session)
    docs = dict(session.execute(
        select(doc.serie, func.count(doc.id)).where(doc.tipo.in_(TIPOS_VENTA))
        .group_by(doc.serie)).all())
    lineas = dict(session.execute(
        select(doc.serie, func.count(lin.id)).join(doc, doc.id == lin.documento_id)
        .where(doc.tipo.in_(TIPOS_VENTA)).group_by(doc.serie)).all())
    # Documentos sin serie: siempre cuentan (no hay serie que marcar).
    items = [{
        "serie": s, "nombre": nombres.get(s), "documentos": docs.get(s, 0),
        "lineas": lineas.get(s, 0), "cuenta": s not in fuera,
    } for s in sorted((x for x in docs if x),
                      key=lambda x: (not x.isdigit(), int(x) if x.isdigit() else 0, x))]
    return {
        "items": items,
        "fuera": {"documentos": sum(i["documentos"] for i in items if not i["cuenta"]),
                  "lineas": sum(i["lineas"] for i in items if not i["cuenta"])},
    }


def marcar_serie(session: Session, serie: str, cuenta: bool, *, por: str) -> None:
    serie = (serie or "").strip()[:8]
    if not serie:
        raise ValueError("serie")
    fila = session.get(HistoricoSerie, serie) or HistoricoSerie(serie=serie)
    fila.cuenta, fila.por, fila.at = bool(cuenta), por, _ahora()
    session.add(fila)
    session.commit()


# --- pistas (reglas) ---------------------------------------------------------------------


def _plano(texto: str | None) -> str:
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return " ".join(t.upper().split())


def _regla(r: HistoricoReglaArticulo) -> dict[str, Any]:
    return {"id": r.id, "patron": r.patron, "orden": r.orden, "tipo": r.tipo,
            "palabra_entera": bool(r.palabra_entera)}


def reglas(session: Session) -> list[dict[str, Any]]:
    return [_regla(r) for r in session.scalars(
        select(HistoricoReglaArticulo).order_by(HistoricoReglaArticulo.tipo,
                                                HistoricoReglaArticulo.orden,
                                                HistoricoReglaArticulo.patron))]


def guardar_regla(session: Session, patron: str, tipo: str = PISTA_NO, *,
                  palabra_entera: bool = False) -> dict[str, Any]:
    """Una pista: `maquina` (el nombre dice que es una máquina) o
    `no_maquina` (delata un recambio, accesorio…)."""
    patron = " ".join((patron or "").split())[:64]
    if not patron or tipo not in (PISTA_SI, PISTA_NO):
        raise ValueError("pista")
    if _plano(patron) in {_plano(r.patron) for r in session.scalars(
            select(HistoricoReglaArticulo))}:
        raise ValueError("pista_existe")
    r = HistoricoReglaArticulo(patron=patron, orden=100, tipo=tipo,
                               palabra_entera=bool(palabra_entera))
    session.add(r)
    session.commit()
    return _regla(r)


def cambiar_palabra_entera(session: Session, regla_id: int, valor: bool) -> None:
    r = session.get(HistoricoReglaArticulo, regla_id)
    if r is None:
        raise LookupError("regla")
    r.palabra_entera = bool(valor)
    session.commit()


def borrar_regla(session: Session, regla_id: int) -> None:
    r = session.get(HistoricoReglaArticulo, regla_id)
    if r is None:
        raise LookupError("regla")
    session.delete(r)
    session.commit()


def _compilar(patrones: list[str | tuple[str, bool]]) -> list[tuple[str, re.Pattern[str]]]:
    """Sin mayúsculas ni acentos y **al principio de una palabra** (1/11/2026):
    el modelo abre la palabra y lo que varía es la cola, así que «ARTIS» casa
    con «ARTISJET», «EPSON PP» con «EPSON PP100» y «ADR» con «ADR1-10DVD»,
    pero no la pista enterrada en otra palabra («ADOR» en ALIMENTADOR, «MBO»
    en TAMBOR, «DUCT» en PRODUCTO). Con `palabra_entera` (dato, por pista)
    exige también el final (sin otra letra detrás; número o plural en «S»
    valen): «HEXA» coge HEXA60W y suelta HEXAGONAL, «LAMINA» coge LÁMINAS y
    suelta la LAMINADORA.
    Acepta el patrón solo o `(patrón, palabra_entera)`."""
    out = []
    for item in patrones:
        patron, entera = item if isinstance(item, tuple) else (item, False)
        plano = _plano(patron)
        if not plano:
            continue
        # Palabra entera: que no siga OTRA LETRA (un número pegado vale:
        # HEXA60W, HEXA2; y una «S» de plural: LAMINAS). HEXAGONAL y
        # LAMINADORA, fuera.
        cola = r"S?(?![A-Z])" if entera else ""
        out.append((patron, re.compile(rf"(?<![A-Z0-9]){re.escape(plano)}{cola}")))
    return out


def pista(descripcion: str | None, compiladas: list[tuple[str, re.Pattern[str]]]) -> str | None:
    """La primera de esas pistas que aparece en la descripción, o None."""
    texto = _plano(descripcion)
    for patron, rx in compiladas:
        if rx.search(texto):
            return patron
    return None


def proponer(descripcion: str | None, si: list[tuple[str, re.Pattern[str]]],
             no: list[tuple[str, re.Pattern[str]]], *, precio_max: float | None = None,
             umbral: float | None = None) -> dict[str, Any]:
    """La propuesta EXIGE evidencia positiva (31/10/2026): «máquina» solo si
    el nombre lo dice (IMPRESORA, BEAMBOX, HEXA…). Las pistas negativas van
    después:

    - positiva y ninguna negativa → `maquina`;
    - positiva y negativa (MAINBOARD BEAMBOX, CUCHILLA SPEEDSTAR, pero
      también SPEEDSTAR 2 ENCELOFANADORA MANUAL) → `dudoso`: sin propuesta,
      lo decide Bart;
    - solo negativa → `no_maquina`;
    - ninguna y precio máximo ≥ umbral → `dudoso` por precio (1/11/2026);
    - ninguna y por debajo → sin propuesta (`None`).

    El precio solo AÑADE propuestas: nunca cancela ni cambia una que venga
    del texto (una FLUX ADOR barata sigue siendo máquina)."""
    a_favor, en_contra = pista(descripcion, si), pista(descripcion, no)
    por_precio = False
    if a_favor and not en_contra:
        propuesta = PROPUESTA_SI
    elif a_favor:
        propuesta = PROPUESTA_DUDOSA
    elif en_contra:
        propuesta = PROPUESTA_NO
    elif umbral is not None and precio_max is not None and precio_max >= umbral:
        propuesta, por_precio = PROPUESTA_DUDOSA, True
    else:
        propuesta = None
    return {"propuesta": propuesta, "pista_si": a_favor, "pista_no": en_contra,
            "por_precio": por_precio}


def umbral(session: Session) -> float:
    from app.erp.models.historico import HistoricoAjuste  # noqa: PLC0415

    fila = session.get(HistoricoAjuste, AJUSTE_UMBRAL)
    try:
        return float(fila.valor) if fila else UMBRAL_POR_DEFECTO
    except (TypeError, ValueError):
        return UMBRAL_POR_DEFECTO


def guardar_umbral(session: Session, valor: float) -> float:
    from app.erp.models.historico import HistoricoAjuste  # noqa: PLC0415

    if valor is None or valor < 0:
        raise ValueError("umbral")
    fila = session.get(HistoricoAjuste, AJUSTE_UMBRAL) or HistoricoAjuste(clave=AJUSTE_UMBRAL)
    fila.valor = str(float(valor))
    session.add(fila)
    session.commit()
    return float(valor)


# --- los artículos de las familias de máquinas -------------------------------------------


def _k(codart: str | None) -> str:
    """La clave de un código de artículo: sin mayúsculas ni espacios."""
    # Sin tildes, como la colación de MySQL (que tampoco las distingue).
    t = unicodedata.normalize("NFKD", codart or "").encode("ascii", "ignore").decode()
    return t.strip().upper()


def _vendidas(session: Session, fuera: set[str], solo: set[str] | None = None,
              ) -> dict[str, tuple[int, float | None]]:
    """Por código de artículo (tal cual viene en las líneas): líneas de venta
    de producto de las series que cuentan, y el precio unitario máximo EN
    VALOR ABSOLUTO (1/11/2026): un artículo del que solo hay devoluciones
    tiene todas las líneas en negativo y es la misma máquina. Nunca la media:
    hay líneas a 0 € de garantías y reposiciones."""
    lin, doc = HistoricoLinea, HistoricoDocumento
    stmt = (
        select(lin.codart, func.count(lin.id), func.max(func.abs(lin.precio)))
        .join(doc, doc.id == lin.documento_id)
        .where(lin.es_producto.is_(True), doc.tipo.in_(TIPOS_VENTA),
               lin.codart.is_not(None))
        .group_by(lin.codart)
    )
    if fuera:
        stmt = stmt.where(doc.serie.notin_(sorted(fuera)))
    if solo is not None and session.get_bind().dialect.name == "mysql":
        # MySQL ya compara sin mayúsculas ni tildes: se filtra por el índice
        # de `codart` en vez de agregar todo el histórico. (En sqlite, que
        # distingue mayúsculas, se agrega todo y se filtra en Python.)
        if not solo:
            return {}
        stmt = stmt.where(lin.codart.in_(sorted(solo)))
    return {c: (int(n), float(p) if p is not None else None)
            for c, n, p in session.execute(stmt) if c and c.strip()}


def sin_catalogo(session: Session,
                 vendidas: dict[str, tuple[int, float | None]] | None = None) -> dict[str, Any]:
    """Lo vendido cuyo código no está en NINGÚN catálogo volcado (1/11/2026):
    no tiene familia, así que no sale en «Máquinas, artículo a artículo» ni
    en las búsquedas por familia, y no puede entrar en el parque. Se cuenta
    para que se vea el agujero; las más vendidas, con el nombre de la línea."""
    lin, doc = HistoricoLinea, HistoricoDocumento
    if vendidas is None:
        vendidas = _vendidas(session, series_excluidas(session))
    conocidos = {_k(c) for c in session.scalars(select(HistoricoArticulo.codart).distinct())}
    faltan: dict[str, list[Any]] = {}
    for c, (n, _p) in vendidas.items():
        if _k(c) not in conocidos:
            fila = faltan.setdefault(_k(c), [c, 0])
            fila[1] += n
    top = sorted(faltan.values(), key=lambda x: (-x[1], x[0]))[:50]
    nombres = dict(session.execute(
        select(lin.codart, func.max(lin.descripcion)).join(doc, doc.id == lin.documento_id)
        .where(lin.codart.in_([c for c, _ in top]), doc.tipo.in_(TIPOS_VENTA))
        .group_by(lin.codart)).all()) if top else {}
    return {
        "articulos": len(faltan),
        "lineas": sum(n for _, n in faltan.values()),
        "items": [{"codart": c, "descripcion": nombres.get(c), "lineas": n} for c, n in top],
    }


def _vacio(session: Session, con_sin_catalogo: bool) -> dict[str, Any]:
    return {"items": [], "marcadas": 0, "total": 0, "propuestas": 0, "sin_revisar": 0,
            "umbral": umbral(session), "colisiones": 0,
            **({"sin_catalogo": sin_catalogo(session)} if con_sin_catalogo else {})}


def articulos(session: Session, *, con_sin_catalogo: bool = False) -> dict[str, Any]:
    """Los artículos vendidos que hay que mirar: los que HOY (en el catálogo
    más reciente que los tiene) están en una familia de clase máquina, y los
    ya marcados como máquina en cualquier familia (para poder desmarcarlos).
    Una familia que fue de máquinas en un catálogo antiguo no cuenta: manda
    la clasificación actual. Por familia y por líneas vendidas (de las series
    que cuentan), con la propuesta y el estado. Y el recuento."""
    art, fam = HistoricoArticulo, HistoricoFamilia
    propia = HistoricoArticuloClase
    fuera = series_excluidas(session)
    familias = {f.codigo: f for f in session.scalars(select(fam))}
    # La familia de cada artículo es la de su catálogo más reciente.
    ultimo = select(art.codart, func.max(art.ejercicio).label("ej")).group_by(
        art.codart).subquery()
    candidatos = {
        codart for codart, familia in session.execute(
            select(art.codart, art.familia).join(
                ultimo, (ultimo.c.codart == art.codart) & (ultimo.c.ej == art.ejercicio)))
        if familia in familias and familias[familia].clase == CLASE_MAQUINA
    }
    candidatos |= set(session.scalars(select(propia.codart).where(
        propia.clase == CLASE_MAQUINA)))
    # Los códigos se comparan SIN mayúsculas (1/11/2026): FACTUSOL guarda el
    # artículo como «ADR1-3DVD» y la línea puede llevar «ADR1-3dvd». MySQL ya
    # los casa, pero aquí se buscaban en diccionarios y salían sin familia.
    crudos = candidatos
    candidatos = {_k(c) for c in candidatos}
    if not candidatos:
        return _vacio(session, con_sin_catalogo)
    vendidas = _vendidas(session, fuera, solo=crudos)
    vendidos: dict[str, int] = {}
    precios: dict[str, float | None] = {}
    nombre_de: dict[str, str] = {}
    for c, (n, p) in vendidas.items():
        if _k(c) not in candidatos:
            continue
        # Una misma referencia con dos grafías se junta en la primera.
        c0 = nombre_de.setdefault(_k(c), c)
        vendidos[c0] = vendidos.get(c0, 0) + n
        if p is not None:
            precios[c0] = max(precios.get(c0) or 0.0, p)
        else:
            precios.setdefault(c0, None)
    if not vendidos:
        return _vacio(session, con_sin_catalogo)
    # Nombre y familia: los del catálogo más reciente que lo tenga.
    catalogo: dict[str, HistoricoArticulo] = {}
    for a in session.scalars(select(art).where(
            func.upper(art.codart).in_(sorted(_k(c) for c in vendidos)))
            .order_by(art.ejercicio)):
        catalogo[_k(a.codart)] = a
    filas_propias = list(session.scalars(select(propia).where(
        func.upper(propia.codart).in_(sorted(_k(c) for c in vendidos)))))
    propias = {_k(p.codart): p.clase for p in filas_propias}
    lotes = {_k(p.codart): p.por for p in filas_propias}
    todas = reglas(session)
    si = _compilar([(r["patron"], r["palabra_entera"]) for r in todas if r["tipo"] == PISTA_SI])
    no = _compilar([(r["patron"], r["palabra_entera"]) for r in todas if r["tipo"] == PISTA_NO])
    corte = umbral(session)
    items = []
    for codart, n in vendidos.items():
        a = catalogo.get(_k(codart))
        f = familias.get(a.familia) if a and a.familia else None
        clase = propias.get(_k(codart))
        estado = (MARCADA if clase == CLASE_MAQUINA else
                  SIN_REVISAR if clase is None else NO_MAQUINA)
        hoy = proponer(a.descripcion if a else None, si, no,
                       precio_max=precios.get(codart), umbral=corte)
        revisada = estado != SIN_REVISAR
        items.append({
            # El código del catálogo (no la grafía de la línea): es con el que
            # se guarda la marca y con el que la buscan las demás pantallas.
            "codart": a.codart if a else codart, "descripcion": a.descripcion if a else None,
            "familia": a.familia if a else None,
            "familia_nombre": f.descripcion if f else None, "gama": f.gama if f else None,
            "familia_clase": f.clase if f else None,
            "lineas": int(n), "precio_max": precios.get(codart),
            "estado": estado, "clase_propia": clase,
            # Lo revisado (marcado o no) no lleva propuesta: es decisión de
            # Bart, no un hueco. Las reglas nuevas solo proponen en lo que
            # sigue sin revisar.
            **(hoy if not revisada else {"propuesta": None, "pista_si": None,
                                         "pista_no": None, "por_precio": False}),
            # Solo para el filtro «revisadas que hoy se propondrían»: lo que
            # una regla propondría hoy en una revisada como no máquina.
            "propondria": (hoy["propuesta"] if estado == NO_MAQUINA
                           and hoy["propuesta"] in (PROPUESTA_SI, PROPUESTA_DUDOSA)
                           else None),
            # Dada por revisada con el botón de SU familia de hoy (lo que
            # deshace ese mismo botón).
            "en_lote": bool(a and a.familia and (lotes.get(_k(codart)) or "").endswith(
                MARCA_LOTE.format(familia=a.familia))),
        })
    items.sort(key=lambda i: (i["familia"] or "~", -i["lineas"], i["codart"]))
    return {
        "items": items,
        "marcadas": sum(1 for i in items if i["estado"] == MARCADA),
        "total": len(items),
        "sin_revisar": sum(1 for i in items if i["estado"] == SIN_REVISAR),
        "umbral": corte,
        "colisiones": sum(1 for i in items if i["propondria"]),
        **({"sin_catalogo": sin_catalogo(session)} if con_sin_catalogo else {}),
        # Lo que «Aceptar todas» aplicaría: sin revisar Y con propuesta clara.
        "propuestas": sum(1 for i in items if i["estado"] == SIN_REVISAR
                          and i["propuesta"] in (PROPUESTA_SI, PROPUESTA_NO)),
    }


def marcar(session: Session, codart: str, es_maquina: bool, *, por: str,
           solo_sin_revisar: bool = False) -> None:
    """La casilla «es máquina». Marcarla: clase propia máquina. Quitarla:
    `no_maquina` (revisado), salvo que ya tuviera otra clase propia (la del
    «No es una máquina» del parque), que se respeta."""
    codart = (codart or "").strip()
    if session.scalar(select(HistoricoArticulo.codart).where(
            func.upper(HistoricoArticulo.codart) == _k(codart)).limit(1)) is None:
        raise LookupError("articulo")
    fila = session.get(HistoricoArticuloClase, codart)
    if solo_sin_revisar and fila is not None:
        # «No es máquina» desde una pantalla vieja: alguien ya lo revisó (o
        # marcó) mientras tanto; no se pisa.
        return
    if es_maquina:
        fila = fila or HistoricoArticuloClase(codart=codart)
        fila.clase = CLASE_MAQUINA
    elif fila is None or fila.clase == CLASE_MAQUINA:
        fila = fila or HistoricoArticuloClase(codart=codart)
        fila.clase = CLASE_NO_MAQUINA
    else:
        return   # ya tiene otra clase propia (accesorio, recambio…): no es máquina
    fila.por, fila.at = por, _ahora()
    session.add(fila)
    session.commit()


def aceptar_todas(session: Session, *, por: str) -> dict[str, int]:
    """Aplica las propuestas CLARAS (máquina o no máquina) a lo que NADIE ha
    revisado. Lo dudoso y lo que no tiene propuesta se queda sin revisar
    (fuera del parque) para que lo decida Bart; lo marcado o desmarcado a mano
    no se toca. Queda apuntado que fue «aceptar todas»."""
    hechas = {"maquina": 0, "no_maquina": 0}
    for i in articulos(session)["items"]:
        if i["estado"] != SIN_REVISAR or i["propuesta"] not in (PROPUESTA_SI, PROPUESTA_NO):
            continue
        # Releído: alguien pudo marcarlo mientras tanto (no se pisa ni choca).
        if session.get(HistoricoArticuloClase, i["codart"]):
            continue
        session.add(HistoricoArticuloClase(
            codart=i["codart"],
            clase=CLASE_MAQUINA if i["propuesta"] == PROPUESTA_SI else CLASE_NO_MAQUINA,
            por=f"{por} (aceptar todas)"[:255], at=_ahora()))
        hechas[i["propuesta"]] += 1
    session.commit()
    return hechas


# --- «Dar por revisadas las que quedan» de una familia -----------------------------------


def dar_por_revisadas(session: Session, familia: str, *, por: str) -> int:
    """Pasa a «no es máquina» TODO lo que siga sin revisar en esa familia (la
    de hoy de cada artículo). Lo apunta para poder deshacerlo por familia.
    Devuelve cuántas."""
    marca = MARCA_LOTE.format(familia=familia)
    hechas = 0
    for i in articulos(session)["items"]:
        if i["familia"] != familia or i["estado"] != SIN_REVISAR:
            continue
        if session.get(HistoricoArticuloClase, i["codart"]):
            continue   # alguien lo marcó mientras tanto
        session.add(HistoricoArticuloClase(codart=i["codart"], clase=CLASE_NO_MAQUINA,
                                           por=f"{por} {marca}"[:255], at=_ahora()))
        hechas += 1
    session.commit()
    return hechas


def deshacer_revisadas(session: Session, familia: str) -> int:
    """Vuelve a «sin revisar» lo que dio por revisado el botón de esa familia
    (solo lo que sigue tal cual: si luego se marcó a mano, no se toca)."""
    # Lo mismo que cuenta la pantalla: los de esa familia (la de hoy) que dio
    # por revisados su botón y siguen como «no es máquina».
    codarts = [i["codart"] for i in articulos(session)["items"]
               if i["familia"] == familia and i["en_lote"] and i["estado"] == NO_MAQUINA]
    filas = [p for c in codarts if (p := session.get(HistoricoArticuloClase, c)) is not None
             and p.clase == CLASE_NO_MAQUINA]
    for p in filas:
        session.delete(p)
    session.commit()
    return len(filas)
