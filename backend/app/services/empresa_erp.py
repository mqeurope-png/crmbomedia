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

from sqlalchemy import and_, case, delete, func, insert, select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

YA_CLIENTE = "YA CLIENTE"
ORIGEN_WOO = "ORIGEN WOO"
#: Clave interna de cada etiqueta automática y su `ContactTag.source`.
ETIQUETAS = {YA_CLIENTE: "ya_cliente", ORIGEN_WOO: "origen_woo"}
PUESTA, QUITADA = "puesta", "quitada"


def _ahora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


#: Lo que se guarda de cada artículo comprado: código del catálogo, familia,
#: clase y modelo (canónico del grupo de equivalentes).
Compra = tuple[str, str | None, str | None, str]


def _compras(session: Session, eq: Any) -> tuple[dict[str, dict[str, Compra]], dict[str, date]]:
    """Por cliente (`clave_cliente`): los artículos comprados, uno por código
    (sin mayúsculas ni tildes, como la colación de MySQL), y la fecha de la
    última compra. Facturas y albaranes de las series que cuentan, unidades
    positivas.

    - La familia es la del catálogo del ejercicio MÁS RECIENTE en que la
      empresa lo compró (si cambió de familia, manda la de ahora; y siempre
      la misma, no la que salga primero).
    - Un código equivalente de una máquina marcada es máquina, como en el
      parque y en «Dormidos y huecos».
    - Una línea sin fecha cuenta el 1 de enero de su ejercicio, como en la
      pestaña."""
    from app.erp import (
        historico_alias,  # noqa: PLC0415
        parque,  # noqa: PLC0415
    )
    from app.erp.historico_maquinas import clase_efectiva_py, series_excluidas  # noqa: PLC0415
    from app.erp.models.historico import (  # noqa: PLC0415
        CLASE_MAQUINA,
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
    maquinas = parque._codigos_maquina(session, eq)
    fam_expr = al_fam.expr_articulo(art)
    cod_lin = al_art.expr(lin.codart)
    codigo = func.coalesce(art.codart, cod_lin)
    stmt = (
        select(doc.codcli, codigo, fam_expr, fam.clase, propia.clase, func.max(doc.fecha),
               func.max(case((doc.fecha.is_(None), lin.ejercicio))), func.max(lin.ejercicio))
        .select_from(lin)
        .join(doc, doc.id == lin.documento_id)
        .outerjoin(art, and_(art.ejercicio == lin.ejercicio, art.codart == cod_lin))
        .outerjoin(fam, fam.codigo == fam_expr)
        .outerjoin(propia, propia.codart == codigo)
        .where(lin.es_producto.is_(True), doc.tipo.in_(TIPOS_VENTA), lin.cantidad > 0,
               lin.codart.is_not(None))
        .group_by(doc.codcli, codigo, fam_expr, fam.clase, propia.clase)
    )
    if fuera := series_excluidas(session):
        stmt = stmt.where(doc.serie.notin_(sorted(fuera)))
    elegido: dict[tuple[str, str], tuple[bool, str, str]] = {}
    compras: dict[str, dict[str, Compra]] = {}
    ultima: dict[str, date] = {}
    for (codcli, codart, familia, clase_fam, clase_propia, fecha, ej_sin_fecha,
         ej) in session.execute(stmt):
        cli = parque.clave_cliente(codcli)
        if not cli or not codart or not str(codart).strip():
            continue
        codart = str(codart).strip()
        k = historico_alias._k(codart)
        if k in maquinas:
            clase = CLASE_MAQUINA
        else:
            clase = clase_efectiva_py(clase_propia, clase_fam)
        # Manda la familia CONOCIDA más reciente: si el artículo ya no está
        # en el catálogo de su último ejercicio, la de antes (como el respaldo).
        orden = (familia is not None, str(ej or ""), familia or "")
        if (cli, k) not in elegido or orden > elegido[(cli, k)]:
            elegido[(cli, k)] = orden
            compras.setdefault(cli, {})[k] = (codart, familia, clase, eq.canonico(codart))
        cuando = fecha
        if ej_sin_fecha and str(ej_sin_fecha).isdigit():
            sin = date(int(ej_sin_fecha), 1, 1)
            cuando = max(cuando, sin) if cuando else sin
        if cuando and (cli not in ultima or cuando > ultima[cli]):
            ultima[cli] = cuando
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
    from app.erp import historico_alias, parque  # noqa: PLC0415
    from app.erp import historico_equivalencias as equivalencias  # noqa: PLC0415
    from app.erp import parque_consultas as pc  # noqa: PLC0415
    from app.erp.models.historico import ParqueMaquinaManual  # noqa: PLC0415
    from app.erp.models.orders import Order  # noqa: PLC0415
    from app.models.crm import Company  # noqa: PLC0415

    hoy = hoy or pc._hoy()
    informe = pc.informe(session, hoy=hoy)
    # «Tiene baja», con la misma regla de la ficha (la baja de la máquina o
    # la heredada de su albarán) y las máquinas a mano dadas de baja.
    con_baja: set[str] = set()
    activo = pc._parque_activo(session, con_baja=con_baja)
    for codcli in session.scalars(
            select(Company.factusol_company_id)
            .join(ParqueMaquinaManual, ParqueMaquinaManual.company_id == Company.id)
            .where(ParqueMaquinaManual.baja_at.is_not(None))):
        if cli := parque.clave_cliente(codcli):
            con_baja.add(cli)
    gasto = pc._gasto_por_cliente(session)
    eq = equivalencias.cargar(session)
    al_art = historico_alias.articulos(session)
    compras, ultima = _compras(session, eq)
    empresas = _empresas_por_cliente(session)
    con_pedidos = {c for c in session.scalars(
        select(Order.company_id).where(Order.company_id.is_not(None)).distinct()) if c}
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
        # Por gama, sin mayúsculas ni tildes (la clave de la tabla, como la
        # colación de MySQL): con la grafía de la primera que aparece.
        por_gama: dict[str, tuple[str, list[dict[str, Any]]]] = {}
        for m in maquinas:
            if m["gama"]:
                por_gama.setdefault(historico_alias._k(m["gama"]), (m["gama"], []))[1].append(m)
        # El modelo: el código del catálogo (alias resuelto) y su canónico.
        mods = {eq.canonico(al_art.resuelve(m["codart"])) for m in maquinas if m.get("codart")}
        total = round(float(gasto.get(cli, 0.0)), 2)
        u = ultima.get(cli)
        for cid in empresas[cli]:
            estados = [por_gama_estado.get((cli, g), {}) for g, _ms in por_gama.values()]
            filas.append({
                "company_id": cid, "codcli": cli, "maquinas": len(maquinas),
                "meses_nueva": meses_nueva, "meses_vieja": meses_vieja,
                "tiene_baja": cli in con_baja, "gasto_total": total, "ultima_compra": u,
                "meses_sin_comprar": pc._meses(u, hoy) if u else None,
                "dormido": any(e.get("dormido") for e in estados),
                "hueco": any(e.get("hueco") for e in estados),
                "ya_cliente": total > 0 or cid in con_pedidos,
            })
            for g, ms in por_gama.values():
                fs = [m["fecha"] for m in ms if m["fecha"]]
                e = por_gama_estado.get((cli, g), {})
                gamas.append({
                    "company_id": cid, "gama": g, "maquinas": len(ms),
                    "meses_nueva": pc._meses(max(fs), hoy) if fs else None,
                    "meses_vieja": pc._meses(min(fs), hoy) if fs else None,
                    "dormido": bool(e.get("dormido")), "hueco": bool(e.get("hueco")),
                })
            modelos.extend({"company_id": cid, "modelo": m[:64]} for m in sorted(mods) if m)
            for _k, (codart, familia, clase, modelo) in sorted(compras.get(cli, {}).items()):
                compra_filas.append({"company_id": cid, "codart": codart[:64],
                                     "familia": familia, "clase": clase,
                                     "modelo": (modelo or None) and modelo[:64]})
    # Empresas sin cliente de FACTUSOL pero con pedidos en el ERP: también
    # han comprado (YA CLIENTE), aunque no tengan histórico.
    con_fila = {f["company_id"] for f in filas}
    for cid in sorted(con_pedidos - con_fila):
        filas.append({"company_id": cid, "codcli": None, "maquinas": 0, "meses_nueva": None,
                      "meses_vieja": None, "tiene_baja": False, "gasto_total": 0,
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


def recuento_ya_cliente(session: Session) -> dict[str, int]:
    """Los contactos con la etiqueta YA CLIENTE AHORA y las empresas de esos
    contactos (1/11/2026). Es la cifra de la cabecera de Contactos y la del
    script del despegue: antes la cabecera daba las empresas que cumplen,
    tengan o no contactos, y no cuadraba."""
    from app.models.crm import Contact, ContactTag, Tag  # noqa: PLC0415

    tag_id = session.scalar(select(Tag.id).where(
        Tag.name_normalized == YA_CLIENTE.strip().lower()))
    if tag_id is None:
        return {"contactos": 0, "empresas": 0}
    contactos = session.scalar(select(func.count()).select_from(ContactTag).where(
        ContactTag.tag_id == tag_id)) or 0
    empresas = session.scalar(select(func.count(func.distinct(Contact.company_id))).select_from(
        ContactTag).join(Contact, Contact.id == ContactTag.contact_id).where(
        ContactTag.tag_id == tag_id, Contact.company_id.is_not(None))) or 0
    return {"contactos": int(contactos), "empresas": int(empresas)}


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
    from app.models.crm import Contact, ContactTag  # noqa: PLC0415

    clave = ETIQUETAS[nombre]
    fuente = f"auto:{clave}"
    tag = _tag(session, nombre)
    deseados = set(deseados)
    # Los apuntes de contactos borrados no cuentan (ni como «quitada a mano»).
    session.execute(delete(CrmEtiquetaAuto).where(
        CrmEtiquetaAuto.etiqueta == clave, CrmEtiquetaAuto.contact_id.not_in(select(Contact.id))))
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
    # Las que puso él y ya no cumplen: se quitan (solo las suyas: las que
    # llevan su `source`, tengan apunte o no).
    for cid, ap in apuntes.items():
        if cid in deseados or ap.estado != PUESTA:
            continue
        if presentes.get(cid) == fuente:
            session.execute(delete(ContactTag).where(ContactTag.contact_id == cid,
                                                     ContactTag.tag_id == tag.id))
            retiradas += 1
        session.delete(ap)
    for cid, src in presentes.items():
        if cid not in deseados and cid not in apuntes and src == fuente:
            session.execute(delete(ContactTag).where(ContactTag.contact_id == cid,
                                                     ContactTag.tag_id == tag.id))
            retiradas += 1
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
        est.ya_cliente_empresas = recuento_ya_cliente(session)["empresas"]
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
    # La etiqueta, como está AHORA (también lo quitado o puesto a mano desde el
    # último recálculo): contactos con ella y las empresas de esos contactos.
    ya = recuento_ya_cliente(session)
    return {"calculado": est.ok_at is not None, "ok_at": iso(est.ok_at),
            "fallo_at": iso(est.fallo_at), "fallo": est.fallo, "duracion_ms": est.duracion_ms,
            "empresas": est.empresas, "ya_cliente_empresas": ya["empresas"],
            "ya_cliente_contactos": ya["contactos"],
            "origen_woo_contactos": est.origen_woo_contactos,
            "quitadas_a_mano": est.quitadas_a_mano}
