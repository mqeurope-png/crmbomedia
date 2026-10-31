"""Las opciones de los campos que salen de los DATOS (1/11/2026), no de una
lista escrita en el código: gamas, modelos de máquina, familias, clases y los
orígenes de contacto que existan. Se rellenan al servir el esquema del filtro
(`extras["opciones"]` del FieldSpec)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

CLASE_LABEL = {"maquina": "Máquina", "consumible": "Consumible", "recambio": "Recambio",
               "accesorio": "Accesorio", "otro": "Otro"}
ORIGEN_LABEL = {"woocommerce": "WooCommerce", "brevo": "Brevo", "agilecrm": "AgileCRM",
                "factusol_import": "Importado de FACTUSOL"}


def _gamas(session: Session) -> list[tuple[str, str]]:
    from app.erp.models.historico import HistoricoGama  # noqa: PLC0415

    return [(g.codigo, g.nombre) for g in session.scalars(
        select(HistoricoGama).order_by(HistoricoGama.orden, HistoricoGama.nombre))]


def _modelos(session: Session) -> list[tuple[str, str]]:
    """Los artículos marcados como máquina, uno por modelo (los códigos
    equivalentes van juntos, con el canónico)."""
    from app.erp import historico_equivalencias as equivalencias  # noqa: PLC0415
    from app.erp.models.historico import (  # noqa: PLC0415
        CLASE_MAQUINA,
        HistoricoArticulo,
        HistoricoArticuloClase,
    )

    eq = equivalencias.cargar(session)
    marcados = list(session.scalars(select(HistoricoArticuloClase.codart).where(
        HistoricoArticuloClase.clase == CLASE_MAQUINA)))
    nombres: dict[str, str] = {}
    art = HistoricoArticulo
    for codart, desc in session.execute(select(art.codart, art.descripcion)
                                        .where(art.codart.in_(marcados))
                                        .order_by(art.ejercicio)):
        nombres[codart.strip().upper()] = desc or codart
    grupos: dict[str, list[str]] = {}
    for c in marcados:
        grupos.setdefault(eq.canonico(c), []).append(c)
    out = []
    for canon, codigos in grupos.items():
        nombre = nombres.get(canon) or next((nombres.get(c.strip().upper()) for c in codigos
                                             if nombres.get(c.strip().upper())), canon)
        otros = sorted({c for c in codigos if c.strip().upper() != canon})
        extra = f" (también {', '.join(otros)})" if otros else ""
        out.append((canon, f"{nombre} · {canon}{extra}"))
    return sorted(out, key=lambda x: x[1].lower())


def _familias(session: Session) -> list[tuple[str, str]]:
    from app.erp import historico_alias  # noqa: PLC0415
    from app.erp.models.historico import HistoricoFamilia  # noqa: PLC0415

    fundidas = historico_alias.familias(session, solo_tabla=True).fundidas
    return [(f.codigo, f"{f.codigo} · {f.descripcion}" if f.descripcion else f.codigo)
            for f in session.scalars(select(HistoricoFamilia).order_by(HistoricoFamilia.codigo))
            if f.codigo not in fundidas]


def _origenes(session: Session) -> list[tuple[str, str]]:
    from app.models.crm import Contact  # noqa: PLC0415

    filas = session.execute(select(Contact.origin, func.count(Contact.id))
                            .where(Contact.origin.is_not(None)).group_by(Contact.origin)
                            .order_by(func.count(Contact.id).desc())).all()
    return [(o, f"{ORIGEN_LABEL.get(o, o)} ({n})") for o, n in filas if o and o.strip()]


def _valores(session: Session, tipo: str) -> list[tuple[str, str]]:
    if tipo == "gamas":
        return _gamas(session)
    if tipo == "gamas_o_cualquiera":
        return [("cualquiera", "Cualquier gama"), *_gamas(session)]
    if tipo == "modelos":
        return _modelos(session)
    if tipo == "familias":
        return _familias(session)
    if tipo == "clases":
        return list(CLASE_LABEL.items())
    if tipo == "origenes":
        return _origenes(session)
    return []


def rellenar(session: Session, campos: list[dict[str, Any]],
             specs: dict[str, Any]) -> list[dict[str, Any]]:
    """Rellena `enum_values` y `enum_labels` de los campos con opciones de
    los datos. Si algo falla (p. ej. aún no hay histórico), el campo queda
    sin opciones: nunca rompe el esquema."""
    cache: dict[str, list[tuple[str, str]]] = {}
    for c in campos:
        spec = specs.get(c["key"])
        tipo = (getattr(spec, "extras", None) or {}).get("opciones") if spec else None
        if not tipo:
            continue
        if tipo not in cache:
            try:
                cache[tipo] = _valores(session, tipo)
            except Exception:  # noqa: BLE001
                cache[tipo] = []
        c["enum_values"] = [v for v, _ in cache[tipo]]
        c["enum_labels"] = dict(cache[tipo])
    return campos
