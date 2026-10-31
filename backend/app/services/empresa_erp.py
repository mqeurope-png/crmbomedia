"""El recálculo del resumen ERP por empresa (1/11/2026).

Rellena las tablas derivadas `crm_empresa_*` para que los filtros de
contactos («tiene máquina de la gama…», «es hueco de…», «ha comprado de la
familia…») sean SQL y valgan igual en la pantalla, en los segmentos, en Brevo
y en los workflows.

- **Las mismas reglas que «Dormidos y huecos».** Dormido y hueco salen de
  `parque_consultas.informe()` tal cual (umbrales incluidos); el parque, de
  la misma lectura en lote (bajas, máquinas a mano, equivalencias, alias,
  familia de respaldo, series que no cuentan). No hay una segunda copia de
  la lógica.
- **Se borra y se rehace entera**, en una transacción. Si falla, se quedan las
  del recálculo anterior y `crm_resumen_estado` lo dice (la pantalla enseña
  «el último recálculo falló a las HH:MM»). No escribe en ninguna tabla de
  Bart: solo en las suyas y en las etiquetas automáticas.
- **Etiquetas automáticas** (`YA CLIENTE`, `ORIGEN WOO`): ver
  `_mantener_etiqueta`.

Solo lee lo volcado de FACTUSOL.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import and_, delete, func, insert, select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

YA_CLIENTE = "YA CLIENTE"
ORIGEN_WOO = "ORIGEN WOO"
#: Clave interna de cada etiqueta automática y su `ContactTag.source`.
ETIQUETAS = {YA_CLIENTE: "ya_cliente", ORIGEN_WOO: "origen_woo"}
PUESTA, QUITADA = "puesta", "quitada"


def _ahora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _compras(session: Session) -> tuple[dict[str, set[tuple[str, str | None, str | None]]],
                                        dict[str, date]]:
    """Por cliente (`clave_cliente`): los artículos comprados (código del
    catálogo, familia y clase que valen hoy) y la fecha de la última compra.
    Facturas y albaranes de las series que cuentan, unidades positivas."""
    from app.erp import (
        historico_alias,  # noqa: PLC0415
        parque,  # noqa: PLC0415
    )
    from app.erp.historico_maquinas import clase_efectiva_py, series_excluidas  # noqa: PLC0415
    from app.erp.models.historico import (  # noqa: PLC0415
        TIPOS_VENTA,
        HistoricoArticulo,
        HistoricoArticuloClase,
        HistoricoDocumento,
        HistoricoFamilia,
        HistoricoLinea,
    )

    lin, doc, art, fam = HistoricoLinea, HistoricoDocumento, HistoricoArticulo, HistoricoFamilia
    propia = HistoricoArticuloClase
    al_art, al_fam = historico_alias.articulos(session), historico_alias.familias(session)
    fam_expr = al_fam.expr_articulo(art)
    cod_lin = al_art.expr(lin.codart)
    stmt = (
        select(doc.codcli, func.coalesce(art.codart, cod_lin), fam_expr, fam.clase, propia.clase,
               func.max(doc.fecha))
        .select_from(lin)
        .join(doc, doc.id == lin.documento_id)
        .outerjoin(art, and_(art.ejercicio == lin.ejercicio, art.codart == cod_lin))
        .outerjoin(fam, fam.codigo == fam_expr)
        .outerjoin(propia, propia.codart == func.coalesce(art.codart, cod_lin))
        .where(lin.es_producto.is_(True), doc.tipo.in_(TIPOS_VENTA), lin.cantidad > 0,
               lin.codart.is_not(None))
        .group_by(doc.codcli, func.coalesce(art.codart, cod_lin), fam_expr, fam.clase,
                  propia.clase)
    )
    if fuera := series_excluidas(session):
        stmt = stmt.where(doc.serie.notin_(sorted(fuera)))
    compras: dict[str, set[tuple[str, str | None, str | None]]] = {}
    ultima: dict[str, date] = {}
    for codcli, codart, familia, clase_fam, clase_propia, fecha in session.execute(stmt):
        cli = parque.clave_cliente(codcli)
        if not cli or not codart:
            continue
        clase = clase_efectiva_py(clase_propia, clase_fam)
        compras.setdefault(cli, set()).add((str(codart).strip(), familia, clase))
        if fecha and (cli not in ultima or fecha > ultima[cli]):
            ultima[cli] = fecha
    return compras, ultima


def _empresas_por_cliente(session: Session) -> dict[str, list[str]]:
    from app.erp import parque  # noqa: PLC0415
    from app.models.crm import Company  # noqa: PLC0415

    out: dict[str, list[str]] = {}
    for cid, codcli in session.execute(
            select(Company.id, Company.factusol_company_id)
            .where(Company.factusol_company_id.is_not(None))):
        cli = parque.clave_cliente(codcli)
        if cli:
            out.setdefault(cli, []).append(cid)
    return out


def calcular(session: Session, *, hoy: date | None = None) -> dict[str, Any]:
    """Las filas del resumen (sin escribir nada)."""
    from app.erp import historico_equivalencias as equivalencias  # noqa: PLC0415
    from app.erp import parque_consultas as pc  # noqa: PLC0415
    from app.erp.models.historico import ParqueBaja, ParqueMaquinaManual  # noqa: PLC0415
    from app.erp.models.orders import Order  # noqa: PLC0415

    hoy = hoy or pc._hoy()
    informe = pc.informe(session, hoy=hoy)
    activo = pc._parque_activo(session)
    gasto = pc._gasto_por_cliente(session)
    compras, ultima = _compras(session)
    eq = equivalencias.cargar(session)
    empresas = _empresas_por_cliente(session)
    con_pedidos = {c for c in session.scalars(
        select(Order.company_id).where(Order.company_id.is_not(None)).distinct()) if c}
    con_baja = {c for c in session.scalars(select(ParqueBaja.company_id).distinct()) if c}
    con_baja |= {c for c in session.scalars(
        select(ParqueMaquinaManual.company_id).where(ParqueMaquinaManual.baja_at.is_not(None))
        .distinct()) if c}
    por_gama_estado: dict[tuple[str, str], dict[str, bool]] = {}
    for d in informe["dormidos"]:
        por_gama_estado.setdefault((d["codcli"], d["gama"]), {})["dormido"] = True
    for h in informe["huecos"]:
        por_gama_estado.setdefault((h["codcli"], h["gama"]), {})["hueco"] = True

    filas: list[dict[str, Any]] = []
    gamas: list[dict[str, Any]] = []
    modelos: list[dict[str, Any]] = []
    compra_filas: list[dict[str, Any]] = []
    clientes = set(empresas)
    for cli in sorted(clientes):
        maquinas = activo.get(cli, [])
        fechas = [m["fecha"] for m in maquinas if m["fecha"]]
        meses_nueva = pc._meses(max(fechas), hoy) if fechas else None
        meses_vieja = pc._meses(min(fechas), hoy) if fechas else None
        por_gama: dict[str, list[dict[str, Any]]] = {}
        for m in maquinas:
            if m["gama"]:
                por_gama.setdefault(m["gama"], []).append(m)
        mods = {eq.canonico(m["codart"]) for m in maquinas if m.get("codart")}
        total = round(float(gasto.get(cli, 0.0)), 2)
        u = ultima.get(cli)
        for cid in empresas[cli]:
            estados = [por_gama_estado.get((cli, g), {}) for g in por_gama]
            filas.append({
                "company_id": cid, "codcli": cli, "maquinas": len(maquinas),
                "meses_nueva": meses_nueva, "meses_vieja": meses_vieja,
                "tiene_baja": cid in con_baja, "gasto_total": total, "ultima_compra": u,
                "meses_sin_comprar": pc._meses(u, hoy) if u else None,
                "dormido": any(e.get("dormido") for e in estados),
                "hueco": any(e.get("hueco") for e in estados),
                "ya_cliente": total > 0 or cid in con_pedidos,
            })
            for g, ms in por_gama.items():
                fs = [m["fecha"] for m in ms if m["fecha"]]
                e = por_gama_estado.get((cli, g), {})
                gamas.append({
                    "company_id": cid, "gama": g, "maquinas": len(ms),
                    "meses_nueva": pc._meses(max(fs), hoy) if fs else None,
                    "meses_vieja": pc._meses(min(fs), hoy) if fs else None,
                    "dormido": bool(e.get("dormido")), "hueco": bool(e.get("hueco")),
                })
            modelos.extend({"company_id": cid, "modelo": m} for m in sorted(mods) if m)
            vistos: set[str] = set()
            for codart, familia, clase in sorted(compras.get(cli, ()), key=lambda x: x[0]):
                k = codart.upper()
                if k in vistos:
                    continue        # el mismo artículo con dos grafías
                vistos.add(k)
                compra_filas.append({"company_id": cid, "codart": codart[:64],
                                     "familia": familia, "clase": clase})
    # Empresas sin cliente de FACTUSOL pero con pedidos en el ERP: también
    # han comprado (YA CLIENTE), aunque no tengan histórico.
    con_fila = {f["company_id"] for f in filas}
    for cid in sorted(con_pedidos - con_fila):
        filas.append({"company_id": cid, "codcli": None, "maquinas": 0, "meses_nueva": None,
                      "meses_vieja": None, "tiene_baja": cid in con_baja, "gasto_total": 0,
                      "ultima_compra": None, "meses_sin_comprar": None, "dormido": False,
                      "hueco": False, "ya_cliente": True})
    return {"empresas": filas, "gamas": gamas, "modelos": modelos, "compras": compra_filas,
            "clientes_sin_empresa": len(set(gasto) - clientes)}


def _tag(session: Session, nombre: str) -> Any:
    from app.models.crm import Tag  # noqa: PLC0415

    norm = nombre.strip().lower()
    tag = session.scalar(select(Tag).where(Tag.name_normalized == norm))
    if tag is None:
        tag = Tag(name=nombre, name_normalized=norm,
                  description="Automática: la mantiene el recálculo del resumen ERP.")
        session.add(tag)
        session.flush()
    return tag


def _mantener_etiqueta(session: Session, nombre: str, deseados: Iterable[str]) -> dict[str, int]:
    """Pone y quita una etiqueta automática sin pisar lo hecho a mano:

    - El recálculo la pone a quien cumple y lo apunta (`puesta`), con
      `ContactTag.source = auto:<clave>`.
    - Si la puso él y ya no está, alguien la quitó a mano: se apunta
      `quitada` y **no la vuelve a poner**.
    - Si alguien la vuelve a añadir a mano a un contacto con `quitada`, el
      apunte se borra y el contacto vuelve al régimen normal (puesta).
    - Una etiqueta puesta a mano no se quita nunca, aunque no cumpla. Solo se
      quita la que puso él, cuando deja de cumplir. Si la de a mano cumple,
      también se apunta: quitarla a mano vale igual que quitar la suya.
    """
    from app.erp.models.resumen_empresa import CrmEtiquetaAuto  # noqa: PLC0415
    from app.models.crm import ContactTag  # noqa: PLC0415

    clave = ETIQUETAS[nombre]
    fuente = f"auto:{clave}"
    tag = _tag(session, nombre)
    deseados = set(deseados)
    presentes = {cid: src for cid, src in session.execute(
        select(ContactTag.contact_id, ContactTag.source).where(ContactTag.tag_id == tag.id))}
    apuntes = {a.contact_id: a for a in session.scalars(
        select(CrmEtiquetaAuto).where(CrmEtiquetaAuto.etiqueta == clave))}
    ahora = _ahora()
    puestas = quitadas_ahora = reanudadas = retiradas = 0
    for cid, ap in list(apuntes.items()):
        if ap.estado == QUITADA and cid in presentes:
            # La volvieron a añadir a mano: vuelve al régimen normal.
            ap.estado, ap.at = PUESTA, ahora
            reanudadas += 1
        elif ap.estado == PUESTA and cid not in presentes:
            # La puso el recálculo y alguien la quitó a mano.
            ap.estado, ap.at = QUITADA, ahora
            quitadas_ahora += 1
    nuevas = []
    for cid in deseados:
        ap = apuntes.get(cid)
        if cid in presentes:
            # Ya la tiene (puesta por él o a mano) y cumple: se apunta, para
            # que si alguien se la quita a mano no se vuelva a poner.
            if ap is None:
                session.add(CrmEtiquetaAuto(contact_id=cid, etiqueta=clave, estado=PUESTA,
                                            at=ahora))
            continue
        if ap is not None and ap.estado == QUITADA:
            continue        # la quitaron a mano: no se vuelve a poner
        nuevas.append({"contact_id": cid, "tag_id": tag.id, "assigned_at": ahora,
                       "source": fuente})
        if ap is None:
            session.add(CrmEtiquetaAuto(contact_id=cid, etiqueta=clave, estado=PUESTA, at=ahora))
        else:
            ap.estado, ap.at = PUESTA, ahora
        puestas += 1
    if nuevas:
        session.execute(insert(ContactTag), nuevas)
    # Las que puso él y ya no cumplen: se quitan (solo las suyas).
    for cid, ap in apuntes.items():
        if cid in deseados or ap.estado != PUESTA:
            continue
        if (presentes.get(cid) or "").startswith("auto:"):
            session.execute(delete(ContactTag).where(ContactTag.contact_id == cid,
                                                     ContactTag.tag_id == tag.id))
            retiradas += 1
        session.delete(ap)
    session.flush()
    total = session.scalar(select(func.count()).select_from(ContactTag)
                           .where(ContactTag.tag_id == tag.id)) or 0
    quitadas = session.scalar(select(func.count()).select_from(CrmEtiquetaAuto).where(
        CrmEtiquetaAuto.etiqueta == clave, CrmEtiquetaAuto.estado == QUITADA)) or 0
    return {"con_etiqueta": int(total), "puestas": puestas, "retiradas": retiradas,
            "quitadas_a_mano": int(quitadas), "reanudadas": reanudadas,
            "quitadas_ahora": quitadas_ahora}


def _estado(session: Session) -> Any:
    from app.erp.models.resumen_empresa import CrmResumenEstado  # noqa: PLC0415

    est = session.get(CrmResumenEstado, 1)
    if est is None:
        est = CrmResumenEstado(id=1)
        session.add(est)
    return est


def recalcular(session: Session, *, hoy: date | None = None) -> dict[str, Any]:
    """Borra y rehace el resumen y mantiene las etiquetas automáticas, en
    una transacción. Si falla, deshace, apunta el fallo (solo el tipo de
    error) y relanza."""
    from app.erp.models.resumen_empresa import (  # noqa: PLC0415
        CrmEmpresaCompra,
        CrmEmpresaErp,
        CrmEmpresaGama,
        CrmEmpresaModelo,
    )
    from app.models.crm import Contact  # noqa: PLC0415

    t0 = time.perf_counter()
    try:
        datos = calcular(session, hoy=hoy)
        for modelo in (CrmEmpresaCompra, CrmEmpresaModelo, CrmEmpresaGama, CrmEmpresaErp):
            session.execute(delete(modelo))
        for modelo, filas in ((CrmEmpresaErp, datos["empresas"]), (CrmEmpresaGama, datos["gamas"]),
                              (CrmEmpresaModelo, datos["modelos"]),
                              (CrmEmpresaCompra, datos["compras"])):
            for i in range(0, len(filas), 2000):
                session.execute(insert(modelo), filas[i:i + 2000])
        ya = {f["company_id"] for f in datos["empresas"] if f["ya_cliente"]}
        ya_contactos = set(session.scalars(select(Contact.id).where(Contact.company_id.in_(ya)))) \
            if ya else set()
        woo = set(session.scalars(select(Contact.id).where(
            func.lower(func.trim(Contact.origin)) == "woocommerce")))
        e_ya = _mantener_etiqueta(session, YA_CLIENTE, ya_contactos)
        e_woo = _mantener_etiqueta(session, ORIGEN_WOO, woo)
        est = _estado(session)
        est.ok_at, est.fallo_at, est.fallo = _ahora(), None, None
        est.duracion_ms = int((time.perf_counter() - t0) * 1000)
        est.empresas = len(datos["empresas"])
        est.ya_cliente_empresas = len(ya)
        est.ya_cliente_contactos = e_ya["con_etiqueta"]
        est.origen_woo_contactos = e_woo["con_etiqueta"]
        est.quitadas_a_mano = e_ya["quitadas_a_mano"] + e_woo["quitadas_a_mano"]
        session.commit()
        logger.info("resumen ERP: %d empresas, %d con YA CLIENTE, %d ms", est.empresas,
                    est.ya_cliente_contactos, est.duracion_ms)
        return {"empresas": len(datos["empresas"]), "ya_cliente": e_ya, "origen_woo": e_woo,
                "duracion_ms": est.duracion_ms}
    except Exception as exc:
        session.rollback()
        try:
            est = _estado(session)
            est.fallo_at, est.fallo = _ahora(), type(exc).__name__
            session.commit()
        except Exception:  # noqa: BLE001
            session.rollback()
        logger.error("resumen ERP: el recálculo falló (%s)", type(exc).__name__)
        raise


def estado(session: Session) -> dict[str, Any]:
    from app.erp.models.resumen_empresa import CrmResumenEstado  # noqa: PLC0415

    est = session.get(CrmResumenEstado, 1)

    def iso(d: datetime | None) -> str | None:
        return f"{d.isoformat()}Z" if d else None

    if est is None:
        return {"ok_at": None, "fallo_at": None, "fallo": None, "calculado": False}
    return {"calculado": est.ok_at is not None, "ok_at": iso(est.ok_at),
            "fallo_at": iso(est.fallo_at), "fallo": est.fallo, "duracion_ms": est.duracion_ms,
            "empresas": est.empresas, "ya_cliente_empresas": est.ya_cliente_empresas,
            "ya_cliente_contactos": est.ya_cliente_contactos,
            "origen_woo_contactos": est.origen_woo_contactos,
            "quitadas_a_mano": est.quitadas_a_mano}
