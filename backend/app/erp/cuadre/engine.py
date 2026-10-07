"""Motor del Cuadre: corre las comprobaciones y guarda los descuadres de forma
IDEMPOTENTE.

- Identidad: `check_id + entidad_id`. Dos pasadas no duplican nada.
- Lo que una pasada ya no ve pasa a `resuelto` (nunca se borra).
- «Revisado / no es un descuadre» (con motivo) no reaparece mientras la
  huella no cambie; si cambia, vuelve a `abierto` como nuevo.
- Solo se tocan los descuadres de las comprobaciones que han CORRIDO bien en
  la pasada: una que falla deja los suyos como estaban.

El motor escribe solo en `cuadre_findings` / `cuadre_runs`: el Cuadre no
corrige datos, no escribe en FACTUSOL y no mueve estados.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.cuadre.config import check_activo, cuadre_config
from app.erp.cuadre.contexto import Contexto, FactusolNoDisponible
from app.erp.cuadre.registry import (
    FUENTE_FACTUSOL,
    FUENTES,
    SEVERIDADES,
    Comprobacion,
    Hallazgo,
    registro,
)
from app.erp.models.cuadre import (
    ESTADO_ABIERTO,
    ESTADO_RESUELTO,
    ESTADO_REVISADO,
    RUN_CON_ERRORES,
    RUN_CORRIENDO,
    RUN_EN_COLA,
    RUN_ERROR,
    RUN_OK,
    RUN_PENDIENTES,
    CuadreFinding,
    CuadreRun,
)

logger = logging.getLogger(__name__)

MOTIVO_MIN = 3
MOTIVO_MAX = 255
#: Una pasada en cola / corriendo más de esto se da por perdida (worker caído).
RUN_CADUCA_SEGUNDOS = 2 * 3600


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def comprobaciones_activas(config: dict[str, Any], fuente: str | None = None) -> list[Comprobacion]:
    return [
        c for c in sorted(registro().values(), key=lambda c: c.orden)
        if check_activo(config, c.id) and (fuente is None or c.fuente == fuente)
    ]


# --- aplicar el resultado de una comprobación ----------------------------------------


def _ambito(f: CuadreFinding) -> str | None:
    valor = _detalle(f).get("ambito")
    return str(valor) if valor else None


def aplicar(
    session: Session, comp: Comprobacion, hallazgos: list[Hallazgo], ahora: datetime,
    *, ambito: str | None = None, no_mirados: set[str] | frozenset[str] = frozenset(),
) -> dict[str, int]:
    """Funde lo que ha visto una comprobación con lo guardado. Devuelve el
    recuento (hallazgos / nuevos / reabiertos / resueltos).

    `ambito` = lo que la pasada ha podido ver (el ejercicio de FACTUSOL leído).
    Un descuadre de OTRO ámbito que no se ve no se da por resuelto: no se ha
    mirado (al cambiar de ejercicio, las facturas del anterior no se leen).
    Igual con `no_mirados`: las partes (`Hallazgo.ambito`, p. ej. una tienda
    que no ha respondido) que la pasada no ha podido mirar."""
    vistos: dict[str, Hallazgo] = {}
    for h in hallazgos:
        vistos.setdefault(str(h.entidad_id), h)
    existentes = {
        f.entidad_id: f for f in session.scalars(
            select(CuadreFinding).where(CuadreFinding.check_id == comp.id)
        )
    }
    nuevos = reabiertos = resueltos = 0
    for eid, h in vistos.items():
        huella = h.huella()
        det = h.detalle_dict()
        if h.ambito or ambito:
            det["ambito"] = h.ambito or ambito
        detalle = json.dumps(det, ensure_ascii=False, default=str)
        f = existentes.get(eid)
        if f is None:
            session.add(CuadreFinding(
                check_id=comp.id, entidad_tipo=h.entidad_tipo, entidad_id=eid,
                huella=huella, detalle_json=detalle, severidad=comp.severidad,
                estado=ESTADO_ABIERTO, primera_vez_at=ahora, ultima_vez_at=ahora,
                abierto_at=ahora,
            ))
            nuevos += 1
            continue
        if f.estado == ESTADO_RESUELTO or (f.estado == ESTADO_REVISADO and f.huella != huella):
            # Vuelve (o han cambiado los valores de un revisado): nuevo otra vez.
            f.estado = ESTADO_ABIERTO
            f.abierto_at = ahora
            f.resuelto_at = None
            f.visto_por = None
            f.motivo = None
            f.revisado_at = None
            reabiertos += 1
        f.entidad_tipo = h.entidad_tipo
        f.huella = huella
        f.detalle_json = detalle
        f.severidad = comp.severidad
        f.ultima_vez_at = ahora
    for eid, f in existentes.items():
        if eid not in vistos and f.estado != ESTADO_RESUELTO:
            if ambito and _ambito(f) not in (None, ambito):
                continue                      # de otro ejercicio: no se ha mirado
            if _ambito(f) in no_mirados:
                continue                      # esa parte no se ha podido mirar
            f.estado = ESTADO_RESUELTO
            f.resuelto_at = ahora
            resueltos += 1
    session.flush()
    return {
        "hallazgos": len(vistos), "nuevos": nuevos, "reabiertos": reabiertos,
        "resueltos": resueltos,
    }


# --- pasadas ------------------------------------------------------------------------


def nueva_pasada(
    session: Session, *, fuente: str, origen: str, lanzado_por: str | None = None,
    estado: str = RUN_EN_COLA,
) -> CuadreRun:
    run = CuadreRun(
        fuente=fuente, origen=origen, estado=estado, lanzado_por=lanzado_por,
        resumen_json="{}",
    )
    session.add(run)
    session.flush()
    return run


def ejecutar(
    session: Session,
    *,
    fuente: str,
    origen: str,
    lanzado_por: str | None = None,
    run: CuadreRun | None = None,
    client: Any = None,
    ejercicio: str | None = None,
    ahora: datetime | None = None,
) -> CuadreRun:
    """Corre las comprobaciones ACTIVAS de una fuente y guarda el resultado.
    Confirma en BD tras cada comprobación (un fallo no se lleva las demás).

    Durante la pasada la sesión no caduca los objetos al confirmar: los
    pedidos se cargan UNA vez y las comprobaciones los comparten (si no, cada
    commit los caducaría y la siguiente los releería uno a uno)."""
    antes = session.expire_on_commit
    session.expire_on_commit = False
    try:
        return _ejecutar(
            session, fuente=fuente, origen=origen, lanzado_por=lanzado_por, run=run,
            client=client, ejercicio=ejercicio, ahora=ahora,
        )
    finally:
        session.expire_on_commit = antes


def _ejecutar(
    session: Session,
    *,
    fuente: str,
    origen: str,
    lanzado_por: str | None,
    run: CuadreRun | None,
    client: Any,
    ejercicio: str | None,
    ahora: datetime | None,
) -> CuadreRun:
    config = cuadre_config(session)
    ahora = ahora or _now()
    if run is None:
        run = nueva_pasada(session, fuente=fuente, origen=origen, lanzado_por=lanzado_por,
                           estado=RUN_CORRIENDO)
    run.estado = RUN_CORRIENDO
    run.started_at = ahora
    session.commit()
    run_id = run.id

    comps = comprobaciones_activas(config, fuente)
    if fuente == FUENTE_FACTUSOL and comps and client is None:
        client, ejercicio = _factusol_client(session)
    ctx = Contexto(session, ahora=ahora, config=config, client=client, ejercicio=ejercicio)
    ambito = ejercicio if fuente == FUENTE_FACTUSOL else None

    resumen: dict[str, Any] = {}
    errores = 0
    for comp in comps:
        try:
            hallazgos = list(comp.funcion(ctx))
            resumen[comp.id] = aplicar(session, comp, hallazgos, ahora, ambito=ambito,
                                       no_mirados=ctx.no_mirados.get(comp.id, set()))
            session.commit()
        except FactusolNoDisponible as exc:
            session.rollback()
            ctx.olvidar_orm()
            errores += 1
            resumen[comp.id] = {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 — una comprobación no tumba la pasada
            session.rollback()
            ctx.olvidar_orm()
            errores += 1
            logger.exception("cuadre: la comprobación %s ha fallado", comp.id)
            resumen[comp.id] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    run = session.get(CuadreRun, run_id)
    assert run is not None  # noqa: S101
    run.finished_at = _now()
    if not comps:
        run.estado = RUN_OK
    elif errores == len(comps):
        run.estado = RUN_ERROR
        run.error = "Ninguna comprobación ha podido terminar."
    else:
        run.estado = RUN_CON_ERRORES if errores else RUN_OK
    run.resumen_json = json.dumps(resumen, ensure_ascii=False, default=str)
    session.commit()
    logger.info(
        "cuadre: pasada %s (%s, %s): %s",
        fuente, origen, run.estado,
        ", ".join(f"{k}={v.get('hallazgos', 'error')}" for k, v in resumen.items()) or "nada",
    )
    return run


def _factusol_client(session: Session) -> tuple[Any, str | None]:
    """Cliente de FACTUSOL + ejercicio en curso (None si no está configurado:
    las comprobaciones de FACTUSOL quedan con error, sin tocar sus descuadres)."""
    from app.erp.web_order_company import factusol_client_and_ejercicio  # noqa: PLC0415

    res = factusol_client_and_ejercicio(session)
    if res is None:
        return None, None
    return res


def marcar_error(session: Session, run_id: str, mensaje: str) -> None:
    run = session.get(CuadreRun, run_id)
    if run is None:
        return
    run.estado = RUN_ERROR
    run.error = mensaje[:500]
    run.finished_at = _now()
    session.commit()


# --- revisión a mano ------------------------------------------------------------------


class CuadreError(ValueError):
    """Operación no válida sobre un descuadre (la API la devuelve como 4xx)."""


def revisar(
    session: Session, finding_id: str, *, motivo: str, user_id: str | None,
) -> CuadreFinding:
    """«Revisado / no es un descuadre», con motivo corto obligatorio. Solo
    cambia el estado del DESCUADRE (no toca el pedido ni FACTUSOL)."""
    texto = " ".join(str(motivo or "").split())
    if len(texto) < MOTIVO_MIN:
        raise CuadreError("Escribe un motivo (al menos 3 caracteres).")
    if len(texto) > MOTIVO_MAX:
        raise CuadreError(f"El motivo no puede pasar de {MOTIVO_MAX} caracteres.")
    f = session.get(CuadreFinding, finding_id)
    if f is None:
        raise LookupError(finding_id)
    if f.estado != ESTADO_ABIERTO:
        raise CuadreError("Solo se puede marcar como revisado un descuadre abierto.")
    f.estado = ESTADO_REVISADO
    f.motivo = texto
    f.visto_por = user_id
    f.revisado_at = _now()
    session.flush()
    return f


def reincluir(session: Session, finding_id: str) -> CuadreFinding:
    """«Volver a incluir»: un revisado vuelve a contar como abierto."""
    f = session.get(CuadreFinding, finding_id)
    if f is None:
        raise LookupError(finding_id)
    if f.estado != ESTADO_REVISADO:
        raise CuadreError("Solo se puede volver a incluir un descuadre revisado.")
    f.estado = ESTADO_ABIERTO
    f.motivo = None
    f.visto_por = None
    f.revisado_at = None
    session.flush()
    return f


# --- lectura para la API ------------------------------------------------------------


def _detalle(f: CuadreFinding) -> dict[str, Any]:
    try:
        data = json.loads(f.detalle_json or "{}")
    except (TypeError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _iso(value: datetime | None) -> str | None:
    value = _aware(value)
    return value.isoformat() if value else None


def _run_dict(run: CuadreRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    try:
        resumen = json.loads(run.resumen_json or "{}")
    except (TypeError, ValueError):
        resumen = {}
    return {
        "id": run.id, "fuente": run.fuente, "origen": run.origen, "estado": run.estado,
        "started_at": _iso(run.started_at), "finished_at": _iso(run.finished_at),
        "created_at": _iso(run.created_at), "error": run.error, "resumen": resumen,
    }


def ultimas_pasadas(session: Session) -> dict[str, CuadreRun | None]:
    """La última pasada TERMINADA de cada fuente."""
    out: dict[str, CuadreRun | None] = {}
    for fuente in FUENTES:
        out[fuente] = session.scalars(
            select(CuadreRun)
            .where(CuadreRun.fuente == fuente, CuadreRun.finished_at.is_not(None),
                   CuadreRun.estado.in_((RUN_OK, RUN_CON_ERRORES)))
            .order_by(CuadreRun.finished_at.desc()).limit(1)
        ).first()
    return out


def pasadas_en_curso(session: Session, ahora: datetime | None = None) -> list[CuadreRun]:
    """Pasadas en cola / corriendo (las caducadas —worker caído— no cuentan)."""
    ahora = ahora or _now()
    vivas = []
    for run in session.scalars(select(CuadreRun).where(CuadreRun.estado.in_(RUN_PENDIENTES))):
        creada = _aware(run.created_at)
        if creada is not None and (ahora - creada).total_seconds() <= RUN_CADUCA_SEGUNDOS:
            vivas.append(run)
    return vivas


def _nuevo(f: CuadreFinding, ultimas: dict[str, CuadreRun | None], fuente: str) -> bool:
    run = ultimas.get(fuente)
    if run is None or run.started_at is None:
        return True
    abierto = _aware(f.abierto_at)
    return abierto is not None and abierto >= _aware(run.started_at)


def finding_dict(
    f: CuadreFinding, comp: Comprobacion | None, *, nuevo: bool,
) -> dict[str, Any]:
    det = _detalle(f)
    return {
        "id": f.id,
        "check_id": f.check_id,
        "check_titulo": comp.titulo if comp else f.check_id,
        "severidad": f.severidad,
        "estado": f.estado,
        "entidad_tipo": f.entidad_tipo,
        "entidad_id": f.entidad_id,
        "etiqueta": det.get("etiqueta") or f.entidad_id,
        "detalle": det.get("detalle") or "",
        "pista_de_arreglo": det.get("pista_de_arreglo") or "",
        "enlace": det.get("enlace"),
        "arreglo_enlace": det.get("arreglo_enlace"),
        "arreglo_boton": det.get("arreglo_boton"),
        "datos": det.get("datos") or {},
        "motivo": f.motivo,
        "visto_por": f.visto_por,
        "revisado_at": _iso(f.revisado_at),
        "primera_vez_at": _iso(f.primera_vez_at),
        "ultima_vez_at": _iso(f.ultima_vez_at),
        "abierto_at": _iso(f.abierto_at),
        "nuevo": nuevo,
    }


def _orden_severidad(sev: str) -> int:
    return SEVERIDADES.index(sev) if sev in SEVERIDADES else len(SEVERIDADES)


def listar(
    session: Session,
    *,
    check_id: str | None = None,
    severidad: str | None = None,
    solo_nuevos: bool = False,
    incluir_revisados: bool = False,
) -> list[dict[str, Any]]:
    """Descuadres abiertos (y revisados si se piden) de las comprobaciones
    ACTIVAS, por severidad y comprobación."""
    config = cuadre_config(session)
    reg = {c.id: c for c in comprobaciones_activas(config)}
    estados = [ESTADO_ABIERTO] + ([ESTADO_REVISADO] if incluir_revisados else [])
    stmt = select(CuadreFinding).where(
        CuadreFinding.estado.in_(estados), CuadreFinding.check_id.in_(sorted(reg)),
    )
    if check_id:
        stmt = stmt.where(CuadreFinding.check_id == check_id)
    if severidad:
        stmt = stmt.where(CuadreFinding.severidad == severidad)
    ultimas = ultimas_pasadas(session)
    out = []
    for f in session.scalars(stmt):
        comp = reg.get(f.check_id)
        nuevo = f.estado == ESTADO_ABIERTO and _nuevo(f, ultimas, comp.fuente if comp else "mysql")
        if solo_nuevos and not nuevo:
            continue
        out.append(finding_dict(f, comp, nuevo=nuevo))
    out.sort(key=lambda d: (
        _orden_severidad(d["severidad"]),
        reg[d["check_id"]].orden if d["check_id"] in reg else 999,
        d["estado"] != ESTADO_ABIERTO,
        d["etiqueta"],
    ))
    return out


def resumen(session: Session) -> dict[str, Any]:
    """Contadores por severidad, tarjeta por comprobación, última pasada y lo
    que está comprobándose ahora."""
    config = cuadre_config(session)
    activas = comprobaciones_activas(config)
    ultimas = ultimas_pasadas(session)
    por_check: dict[str, dict[str, int]] = {
        c.id: {"abiertos": 0, "revisados": 0, "nuevos": 0} for c in activas
    }
    reg = {c.id: c for c in activas}
    contadores = {s: 0 for s in SEVERIDADES}
    filas = session.scalars(select(CuadreFinding).where(
        CuadreFinding.estado.in_((ESTADO_ABIERTO, ESTADO_REVISADO)),
        CuadreFinding.check_id.in_(sorted(reg)),
    ))
    for f in filas:
        comp = reg[f.check_id]
        if f.estado == ESTADO_REVISADO:
            por_check[f.check_id]["revisados"] += 1
            continue
        por_check[f.check_id]["abiertos"] += 1
        contadores[f.severidad] = contadores.get(f.severidad, 0) + 1
        if _nuevo(f, ultimas, comp.fuente):
            por_check[f.check_id]["nuevos"] += 1
    terminadas = [r for r in ultimas.values() if r is not None]
    ultima = max(terminadas, key=lambda r: _aware(r.finished_at)) if terminadas else None
    return {
        "contadores": {**contadores, "total": sum(contadores.values())},
        "ultima_pasada": _run_dict(ultima),
        "ultimas_por_fuente": {k: _run_dict(v) for k, v in ultimas.items()},
        "en_curso": [_run_dict(r) for r in pasadas_en_curso(session)],
        "nocturno": {"activo": config["nocturno_activo"], "hora": config["hora"]},
        "checks": [
            {**c.as_dict(), "dias": config["checks"][c.id]["dias"], **por_check[c.id]}
            for c in activas
        ],
    }


# --- Excel -----------------------------------------------------------------------

EXCEL_COLUMNAS = [
    "Severidad", "Comprobación", "Tipo", "Entidad", "Qué no cuadra",
    "Cómo arreglarlo", "Enlace", "Primera vez", "Última vez", "Nuevo",
]


def exportar_xlsx(session: Session, *, base_url: str = "") -> bytes:
    """Excel con TODOS los descuadres abiertos (sin filtros)."""
    from io import BytesIO  # noqa: PLC0415

    from openpyxl import Workbook  # noqa: PLC0415
    from openpyxl.styles import Font  # noqa: PLC0415

    filas = listar(session)
    wb = Workbook()
    ws = wb.active
    ws.title = "Descuadres"
    ws.append(EXCEL_COLUMNAS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for d in filas:
        enlace = d.get("arreglo_enlace") or d.get("enlace") or ""
        if enlace.startswith("/"):
            enlace = f"{base_url.rstrip('/')}{enlace}"
        ws.append([
            d["severidad"], d["check_titulo"], d["entidad_tipo"], d["etiqueta"],
            d["detalle"], d["pista_de_arreglo"], enlace,
            (d["primera_vez_at"] or "")[:10], (d["ultima_vez_at"] or "")[:10],
            "sí" if d["nuevo"] else "",
        ])
    for col, ancho in zip("ABCDEFGHIJ", (10, 34, 12, 22, 70, 60, 50, 12, 12, 7), strict=True):
        ws.column_dimensions[col].width = ancho
    ws.freeze_panes = "A2"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
