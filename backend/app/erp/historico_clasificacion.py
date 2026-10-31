"""La clasificación de las familias del histórico: clase, gama y las reglas
que las PROPONEN (30/10/2026).

- **Clase** (`CLASES_FAMILIA`): máquina, consumible, recambio, accesorio, otro.
  Un accesorio no es una máquina (el parque instalado no lo cuenta); un
  recambio se compra cuando algo se rompe (avisa de servicio), un consumible
  se repone con un ritmo (da la previsión).
- **Gama** (`historico_gamas`, ampliable): en una máquina, qué es; en lo
  demás, para qué máquina sirve. «Clientes con impresora UV LED» es
  máquina + UV LED.
- **Reglas** (`historico_reglas_familia`, datos editables): proponen clase y
  gama por el código o por el nombre de la familia. Gana la de menor orden.

La propuesta nunca pisa lo que Bart ya decidió:

- sin clase ni gama → se propone y «Aceptar todas» la aplica;
- con algo puesto que no choca (p. ej. clase puesta y sin gama) → «Aceptar
  todas» rellena solo lo que falta;
- con algo puesto que CHOCA → queda «para revisar»; solo cambia si Bart
  acepta esa propuesta, o la descarta («Mantener la mía»).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.erp.models.historico import (
    CAMPOS_REGLA,
    CLASES_FAMILIA,
    REGLA_CODIGO,
    HistoricoFamilia,
    HistoricoGama,
    HistoricoReglaFamilia,
)

#: Estado de la propuesta de una familia.
SIN_PROPUESTA = "sin_propuesta"     # ninguna regla casa
PROPUESTA = "propuesta"             # hay algo que rellenar y no choca
COINCIDE = "coincide"               # ya tiene lo que se propone
REVISAR = "revisar"                 # lo que tiene choca con la propuesta
DESCARTADA = "descartada"           # chocaba y Bart se quedó con lo suyo


def _ahora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# --- gamas -------------------------------------------------------------------------------


def gamas(session: Session) -> list[dict[str, Any]]:
    return [{"codigo": g.codigo, "nombre": g.nombre} for g in session.scalars(
        select(HistoricoGama).order_by(HistoricoGama.orden, HistoricoGama.nombre))]


def _slug(nombre: str) -> str:
    import unicodedata  # noqa: PLC0415

    t = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode().lower()
    return "-".join("".join(c if c.isalnum() else " " for c in t).split())[:32]


def crear_gama(session: Session, nombre: str) -> dict[str, Any]:
    """Una gama nueva al final de la lista. ValueError si está vacía o ya existe."""
    nombre = (nombre or "").strip()[:64]
    codigo = _slug(nombre)
    if not codigo:
        raise ValueError("vacia")
    existentes = {g.codigo for g in session.scalars(select(HistoricoGama))}
    nombres = {g.nombre.lower() for g in session.scalars(select(HistoricoGama))}
    if codigo in existentes or nombre.lower() in nombres:
        raise ValueError("existe")
    orden = (session.scalar(select(func.max(HistoricoGama.orden))) or 0) + 10
    session.add(HistoricoGama(codigo=codigo, nombre=nombre, orden=orden))
    session.commit()
    return {"codigo": codigo, "nombre": nombre}


def _validar(session: Session, clase: str | None, gama: str | None) -> None:
    if clase is not None and clase not in CLASES_FAMILIA:
        raise ValueError("clase")
    if gama is not None and session.get(HistoricoGama, gama) is None:
        raise ValueError("gama")


# --- reglas ------------------------------------------------------------------------------


def _regla_dict(r: HistoricoReglaFamilia) -> dict[str, Any]:
    return {"id": r.id, "campo": r.campo, "patron": r.patron, "clase": r.clase,
            "gama": r.gama, "orden": r.orden}


def reglas(session: Session) -> list[dict[str, Any]]:
    return [_regla_dict(r) for r in session.scalars(
        select(HistoricoReglaFamilia).order_by(
            HistoricoReglaFamilia.orden, HistoricoReglaFamilia.campo,
            HistoricoReglaFamilia.patron))]


def guardar_regla(session: Session, datos: dict[str, Any],
                  regla_id: int | None = None) -> dict[str, Any]:
    """Crea (sin id) o cambia una regla. ValueError si no es válida;
    LookupError si el id no existe."""
    campo = datos.get("campo") or REGLA_CODIGO
    patron = (datos.get("patron") or "").strip()[:64]
    clase, gama = datos.get("clase") or None, datos.get("gama") or None
    if campo not in CAMPOS_REGLA or not patron or not (clase or gama):
        raise ValueError("regla")
    _validar(session, clase, gama)
    if regla_id is None:
        regla = HistoricoReglaFamilia()
        session.add(regla)
    else:
        regla = session.get(HistoricoReglaFamilia, regla_id)
        if regla is None:
            raise LookupError("regla")
    regla.campo, regla.patron, regla.clase, regla.gama = campo, patron, clase, gama
    orden = datos.get("orden")
    regla.orden = 100 if orden is None else int(orden)
    session.commit()
    return _regla_dict(regla)


def borrar_regla(session: Session, regla_id: int) -> None:
    regla = session.get(HistoricoReglaFamilia, regla_id)
    if regla is None:
        raise LookupError("regla")
    session.delete(regla)
    session.commit()


# --- la propuesta ------------------------------------------------------------------------


def _plano(texto: str) -> str:
    """Sin mayúsculas ni acentos: «Láser» casa con «laser»."""
    import unicodedata  # noqa: PLC0415

    t = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower().strip()


def _casa(regla: HistoricoReglaFamilia, fam: HistoricoFamilia) -> bool:
    if regla.campo == REGLA_CODIGO:
        return fam.codigo.strip() == regla.patron.strip()
    return _plano(regla.patron) in _plano(fam.descripcion or "")


def _clave(clase: str | None, gama: str | None) -> str:
    return f"{clase or ''}|{gama or ''}"


def propuesta(fam: HistoricoFamilia,
              todas: list[HistoricoReglaFamilia]) -> dict[str, Any]:
    """Lo que proponen las reglas para esta familia y cómo está respecto a
    lo que ya tiene. Clase y gama pueden venir de reglas distintas (la
    primera que propone cada una)."""
    clase = gama = None
    for r in todas:   # ya ordenadas
        if not _casa(r, fam):
            continue
        clase = clase or r.clase
        gama = gama or r.gama
        if clase and gama:
            break
    if not (clase or gama):
        return {"clase": None, "gama": None, "estado": SIN_PROPUESTA}
    choca = ((clase and fam.clase and fam.clase != clase)
             or (gama and fam.gama and fam.gama != gama))
    if choca:
        estado = (DESCARTADA if fam.propuesta_descartada == _clave(clase, gama)
                  else REVISAR)
    elif (clase and not fam.clase) or (gama and not fam.gama):
        estado = PROPUESTA
    else:
        estado = COINCIDE
    return {"clase": clase, "gama": gama, "estado": estado}


def _choques(fam: HistoricoFamilia, p: dict[str, Any]) -> set[str]:
    """Los campos en que lo puesto choca con lo propuesto."""
    return {c for c in ("clase", "gama")
            if p.get(c) and getattr(fam, c) and getattr(fam, c) != p[c]}


def _rellenar(fam: HistoricoFamilia, p: dict[str, Any]) -> None:
    """Pone lo propuesto solo donde no hay nada."""
    if p.get("clase") and not fam.clase:
        fam.clase = p["clase"]
    if p.get("gama") and not fam.gama:
        fam.gama = p["gama"]


def _reglas_ordenadas(session: Session) -> list[HistoricoReglaFamilia]:
    return list(session.scalars(select(HistoricoReglaFamilia).order_by(
        HistoricoReglaFamilia.orden, HistoricoReglaFamilia.id)))


def propuestas(session: Session) -> dict[str, dict[str, Any]]:
    todas = _reglas_ordenadas(session)
    return {f.codigo: propuesta(f, todas) for f in session.scalars(select(HistoricoFamilia))}


def _marcar(fam: HistoricoFamilia, por: str) -> None:
    fam.clase_at, fam.clase_por = _ahora(), por


def clasificar(session: Session, codigo: str, cambios: dict[str, str | None], *,
               por: str) -> dict[str, Any]:
    """Pone a mano la clase y/o la gama que vengan en `cambios` (lo que no
    viene, no se toca; None la quita). ValueError si no es válida."""
    fam = session.get(HistoricoFamilia, codigo)
    if fam is None:
        raise LookupError("familia")
    _validar(session, cambios.get("clase"), cambios.get("gama"))
    # «Mantener la mía» sigue valiendo si no se toca lo que chocaba.
    choques = _choques(fam, propuesta(fam, _reglas_ordenadas(session)))
    if "clase" in cambios:
        fam.clase = cambios["clase"]
    if "gama" in cambios:
        fam.gama = cambios["gama"]
    if choques & set(cambios):
        fam.propuesta_descartada = None
    _marcar(fam, por)
    session.commit()
    return {"codigo": fam.codigo, "clase": fam.clase, "gama": fam.gama}


def aceptar(session: Session, codigo: str, *, por: str) -> dict[str, Any]:
    """Acepta la propuesta de UNA familia, también si choca (es la
    confirmación de Bart para las marcadas para revisar)."""
    fam = session.get(HistoricoFamilia, codigo)
    if fam is None:
        raise LookupError("familia")
    p = propuesta(fam, _reglas_ordenadas(session))
    if p["estado"] == SIN_PROPUESTA:
        raise ValueError("sin_propuesta")
    if p["clase"]:
        fam.clase = p["clase"]
    if p["gama"]:
        fam.gama = p["gama"]
    fam.propuesta_descartada = None
    _marcar(fam, por)
    session.commit()
    return {"codigo": fam.codigo, "clase": fam.clase, "gama": fam.gama}


def mantener(session: Session, codigo: str, *, por: str) -> dict[str, Any]:
    """«Mantener la mía»: deja de marcar esta familia para revisar mientras
    la propuesta no cambie. Lo que la propuesta trae y no choca (p. ej. la
    gama, si no tenía) sí se rellena."""
    fam = session.get(HistoricoFamilia, codigo)
    if fam is None:
        raise LookupError("familia")
    p = propuesta(fam, _reglas_ordenadas(session))
    if p["estado"] not in (REVISAR, DESCARTADA):
        raise ValueError("no_choca")
    # Lo que no choca y falta (p. ej. la gama) sí se toma de la propuesta.
    _rellenar(fam, p)
    fam.propuesta_descartada = _clave(p["clase"], p["gama"])
    _marcar(fam, por)
    session.commit()
    return {"codigo": fam.codigo, "clase": fam.clase, "gama": fam.gama}


def aceptar_todas(session: Session, *, por: str) -> dict[str, int]:
    """Aplica las propuestas que no chocan: rellena clase y gama donde
    faltan. Las marcadas para revisar NO se tocan."""
    todas = _reglas_ordenadas(session)
    aplicadas = revisar = 0
    for fam in session.scalars(select(HistoricoFamilia)):
        p = propuesta(fam, todas)
        if p["estado"] in (REVISAR, DESCARTADA):
            revisar += p["estado"] == REVISAR
            continue
        if p["estado"] != PROPUESTA:
            continue
        _rellenar(fam, p)
        _marcar(fam, por)
        aplicadas += 1
    session.commit()
    return {"aplicadas": aplicadas, "revisar": revisar}


# --- clase propia de un artículo ---------------------------------------------------------


def clases_de_articulo(session: Session) -> list[dict[str, Any]]:
    """Los artículos con clase propia (por encima de la de su familia), con su
    nombre y su familia en el catálogo más reciente que lo tenga."""
    from app.erp.models.historico import (  # noqa: PLC0415
        HistoricoArticulo,
        HistoricoArticuloClase,
    )

    propias = list(session.scalars(
        select(HistoricoArticuloClase).order_by(HistoricoArticuloClase.at.desc())))
    if not propias:
        return []
    catalogo: dict[str, HistoricoArticulo] = {}
    for a in session.scalars(select(HistoricoArticulo).where(
            HistoricoArticulo.codart.in_([p.codart for p in propias]))
            .order_by(HistoricoArticulo.ejercicio)):
        catalogo[a.codart] = a   # se queda el del año más reciente
    familias = {f.codigo: f for f in session.scalars(select(HistoricoFamilia))}
    out = []
    for p in propias:
        a = catalogo.get(p.codart)
        fam = familias.get(a.familia) if a and a.familia else None
        # «Es máquina» / «no es máquina» en una familia de máquinas se lleva
        # en el panel de máquinas (31/10/2026); aquí, lo que va contra su
        # familia.
        if p.clase == "no_maquina" or (p.clase == "maquina" and fam is not None
                                        and fam.clase == "maquina"):
            continue
        out.append({
            "codart": p.codart, "clase": p.clase, "por": p.por,
            "at": f"{p.at.isoformat()}Z" if p.at else None,
            "nombre": a.descripcion if a else None,
            "familia": a.familia if a else None,
            "familia_nombre": fam.descripcion if fam else None,
            "clase_familia": fam.clase if fam else None,
        })
    return out


def clasificar_articulo(session: Session, codart: str, clase: str | None, *,
                        por: str) -> None:
    """Pone la clase propia de un artículo (vale para todos los años y todos
    los clientes) o la quita (`None`: vuelve a la de su familia). ValueError
    si la clase no existe; LookupError si el artículo no está en ningún
    catálogo volcado."""
    from app.erp.models.historico import (  # noqa: PLC0415
        HistoricoArticulo,
        HistoricoArticuloClase,
    )

    codart = (codart or "").strip()
    actual = session.get(HistoricoArticuloClase, codart)
    if clase is None:
        if actual is not None:
            session.delete(actual)
            session.commit()
        return
    _validar(session, clase, None)
    if actual is None and session.scalar(
            select(HistoricoArticulo.codart).where(HistoricoArticulo.codart == codart)
            .limit(1)) is None:
        raise LookupError("articulo")
    p = actual or HistoricoArticuloClase(codart=codart)
    p.clase, p.por, p.at = clase, por, _ahora()
    session.add(p)
    session.commit()
