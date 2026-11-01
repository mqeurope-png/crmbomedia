"""Las columnas de la empresa en la lista de contactos (1/11/2026): nombre y
sector de la empresa, y su parque y compras del resumen ERP por empresa
(`crm_empresa_*`). Se rellenan para las filas de una página (o del CSV) con
tres consultas, no una por fila."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

#: Las claves que añade a cada fila (y que son columnas elegibles).
COLUMNAS = (
    "empresa_nombre", "empresa_sector", "parque_gamas", "parque_maquinas",
    "parque_meses_nueva", "parque_meses_vieja", "compra_gasto_total", "compra_ultima",
    "compra_meses_sin_comprar", "compra_dormido_gamas", "compra_hueco_gamas",
)


def rellenar(session: Session, filas: list[dict[str, Any]], *,
             con_erp: bool = True) -> list[dict[str, Any]]:
    from app.erp.models.historico import HistoricoGama  # noqa: PLC0415
    from app.erp.models.resumen_empresa import CrmEmpresaErp, CrmEmpresaGama  # noqa: PLC0415
    from app.models.crm import Company  # noqa: PLC0415

    ids = {f.get("company_id") for f in filas if f.get("company_id")}
    for f in filas:
        for c in COLUMNAS:
            f.setdefault(c, None)
    if not ids:
        return filas
    empresas = {cid: (nombre, sector) for cid, nombre, sector in session.execute(
        select(Company.id, Company.name, Company.sector).where(Company.id.in_(ids)))}
    if not con_erp:
        for f in filas:
            if f.get("company_id"):
                f["empresa_nombre"], f["empresa_sector"] = empresas.get(f["company_id"],
                                                                        (None, None))
        return filas
    resumen = {r.company_id: r for r in session.scalars(
        select(CrmEmpresaErp).where(CrmEmpresaErp.company_id.in_(ids)))}
    nombres = dict(session.execute(select(HistoricoGama.codigo, HistoricoGama.nombre)).all())
    gamas: dict[str, list[Any]] = {}
    for g in session.scalars(select(CrmEmpresaGama).where(CrmEmpresaGama.company_id.in_(ids))
                             .order_by(CrmEmpresaGama.gama)):
        gamas.setdefault(g.company_id, []).append(g)
    for f in filas:
        cid = f.get("company_id")
        if not cid:
            continue
        nombre, sector = empresas.get(cid, (None, None))
        f["empresa_nombre"], f["empresa_sector"] = nombre, sector
        r = resumen.get(cid)
        gs = gamas.get(cid, [])
        f["parque_gamas"] = [nombres.get(g.gama, g.gama) for g in gs] or None
        f["compra_dormido_gamas"] = [nombres.get(g.gama, g.gama) for g in gs if g.dormido] or None
        f["compra_hueco_gamas"] = [nombres.get(g.gama, g.gama) for g in gs if g.hueco] or None
        if r is not None and r.codcli:
            f["parque_maquinas"] = r.maquinas
            f["parque_meses_nueva"], f["parque_meses_vieja"] = r.meses_nueva, r.meses_vieja
            f["compra_gasto_total"] = float(r.gasto_total) if r.gasto_total is not None else None
            f["compra_ultima"] = r.ultima_compra.isoformat() if r.ultima_compra else None
            f["compra_meses_sin_comprar"] = r.meses_sin_comprar
    return filas
