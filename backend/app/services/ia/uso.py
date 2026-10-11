"""El registro de las llamadas a la IA (`ia_llamadas`) y su resumen para la
pantalla: cuántas, cuántos tokens, cuánto han costado, por propósito, por
modelo y por día; y qué pasó con los borradores del asistente.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ia import BorradorAsistente, LlamadaIA
from app.services.ia.coste import coste_usd
from app.services.llm import Uso


def registrar_llamada(
    session: Session, *, proposito: str, modelo: str | None, uso: Uso | None,
    usuario_id: str | None = None, referencia_tipo: str | None = None,
    referencia_id: str | None = None, ok: bool = True, error: str | None = None,
) -> LlamadaIA:
    """Anota una llamada. `uso` es lo que devolvió la API (tokens y duración);
    sin él (una llamada fallida) se anota a cero con el error."""
    nombre = (uso.modelo if uso and uso.modelo else modelo) or "desconocido"
    entrada = uso.tokens_entrada if uso else 0
    salida = uso.tokens_salida if uso else 0
    cache_lectura = uso.tokens_cache_lectura if uso else 0
    cache_escritura = uso.tokens_cache_escritura if uso else 0
    fila = LlamadaIA(
        proposito=proposito, modelo=nombre[:80], tokens_entrada=entrada, tokens_salida=salida,
        tokens_cache_lectura=cache_lectura, tokens_cache_escritura=cache_escritura,
        coste_usd=coste_usd(nombre, entrada, salida, cache_lectura, cache_escritura),
        duracion_ms=uso.duracion_ms if uso else None, usuario_id=usuario_id,
        referencia_tipo=referencia_tipo, referencia_id=(referencia_id or "")[:64] or None,
        ok=ok, error=(error or "")[:255] or None,
    )
    session.add(fila)
    session.flush()
    return fila


def _agrupado(session: Session, columna: Any, desde: datetime) -> list[dict[str, Any]]:
    filas = session.execute(
        select(
            columna, func.count(), func.coalesce(func.sum(LlamadaIA.tokens_entrada), 0),
            func.coalesce(func.sum(LlamadaIA.tokens_salida), 0),
            func.coalesce(func.sum(LlamadaIA.tokens_cache_lectura), 0),
            func.coalesce(func.sum(LlamadaIA.coste_usd), 0.0),
        ).where(LlamadaIA.created_at >= desde).group_by(columna).order_by(func.count().desc())
    ).all()
    return [
        {"clave": str(c), "llamadas": int(n), "tokens_entrada": int(te), "tokens_salida": int(ts),
         "tokens_cache_lectura": int(tc), "coste_usd": round(float(coste), 4)}
        for c, n, te, ts, tc, coste in filas
    ]


def conversaciones(session: Session, desde: datetime) -> dict[str, Any]:
    """Lo que cuesta un borrador TERMINADO de verdad: el primero más sus
    retoques. Se agrupan los turnos por conversación; una conversación está
    terminada cuando alguno de sus turnos se envió."""
    filas = session.execute(
        select(BorradorAsistente.id, BorradorAsistente.conversacion_id, BorradorAsistente.turno,
               BorradorAsistente.estado, BorradorAsistente.coste_usd)
        .where(BorradorAsistente.created_at >= desde)
    ).all()
    por_conversacion: dict[str, dict[str, Any]] = {}
    for bid, cid, _turno, estado, coste in filas:
        c = por_conversacion.setdefault(cid or bid, {"turnos": 0, "coste": 0.0, "enviada": False})
        c["turnos"] += 1
        c["coste"] += float(coste or 0)
        if estado in ("enviado_tal_cual", "enviado_editado"):
            c["enviada"] = True
    terminadas = [c for c in por_conversacion.values() if c["enviada"]]
    coste_terminadas = sum(c["coste"] for c in terminadas)
    return {
        "total": len(por_conversacion),
        "terminadas": len(terminadas),
        "correcciones": sum(1 for _, _, turno, _, _ in filas if (turno or 0) > 0),
        "coste_terminadas_usd": round(coste_terminadas, 4),
        "coste_medio_terminada_usd": (
            round(coste_terminadas / len(terminadas), 4) if terminadas else 0.0),
        "turnos_medios_terminada": (
            round(sum(c["turnos"] for c in terminadas) / len(terminadas), 2) if terminadas
            else 0.0),
    }


def resumen(session: Session, *, dias: int = 30, ahora: datetime | None = None) -> dict[str, Any]:
    """Lo que enseña la pantalla de la IA: números reales de los últimos
    `dias` días."""
    ahora = ahora or datetime.now(UTC)
    desde = ahora - timedelta(days=dias)
    total = session.execute(
        select(
            func.count(), func.coalesce(func.sum(LlamadaIA.tokens_entrada), 0),
            func.coalesce(func.sum(LlamadaIA.tokens_salida), 0),
            func.coalesce(func.sum(LlamadaIA.coste_usd), 0.0),
        ).where(LlamadaIA.created_at >= desde)
    ).one()
    por_dia_filas = session.execute(
        select(func.date(LlamadaIA.created_at), func.count(),
               func.coalesce(func.sum(LlamadaIA.coste_usd), 0.0))
        .where(LlamadaIA.created_at >= desde)
        .group_by(func.date(LlamadaIA.created_at)).order_by(func.date(LlamadaIA.created_at))
    ).all()
    estados = session.execute(
        select(BorradorAsistente.estado, func.count())
        .where(BorradorAsistente.created_at >= desde).group_by(BorradorAsistente.estado)
    ).all()
    ultimas = session.scalars(
        select(LlamadaIA).where(LlamadaIA.created_at >= desde)
        .order_by(LlamadaIA.created_at.desc()).limit(20)
    ).all()
    borradores = {
        "generados": 0, "regenerados": 0, "descartados": 0, "enviados_tal_cual": 0,
        "enviados_editados": 0,
    }
    _clave = {
        "generado": "generados", "regenerado": "regenerados", "descartado": "descartados",
        "enviado_tal_cual": "enviados_tal_cual", "enviado_editado": "enviados_editados",
    }
    for estado, n in estados:
        if estado in _clave:
            borradores[_clave[estado]] = int(n)
    borradores["total"] = sum(borradores.values())
    return {
        "dias": dias,
        "llamadas": int(total[0]),
        "tokens_entrada": int(total[1]),
        "tokens_salida": int(total[2]),
        "coste_usd": round(float(total[3]), 4),
        "por_proposito": _agrupado(session, LlamadaIA.proposito, desde),
        "por_modelo": _agrupado(session, LlamadaIA.modelo, desde),
        "por_dia": [
            {"dia": str(d), "llamadas": int(n), "coste_usd": round(float(c), 4)}
            for d, n, c in por_dia_filas
        ],
        "borradores": borradores,
        "conversaciones": conversaciones(session, desde),
        "ultimas": [
            {
                "id": u.id, "cuando": u.created_at.isoformat() if u.created_at else None,
                "proposito": u.proposito, "modelo": u.modelo,
                "tokens_entrada": u.tokens_entrada, "tokens_salida": u.tokens_salida,
                "tokens_cache_lectura": u.tokens_cache_lectura or 0,
                "tokens_cache_escritura": u.tokens_cache_escritura or 0,
                "coste_usd": round(float(u.coste_usd), 6), "duracion_ms": u.duracion_ms,
                "usuario_id": u.usuario_id, "referencia_tipo": u.referencia_tipo,
                "referencia_id": u.referencia_id, "ok": u.ok, "error": u.error,
            }
            for u in ultimas
        ],
    }
