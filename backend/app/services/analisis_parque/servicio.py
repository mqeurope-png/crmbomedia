"""El análisis del parque de una empresa: leerlo, saber si toca regenerarlo,
generarlo y guardarlo (1/11/2026).

- Se genera cuando alguien abre la ficha o lo pide desde una lista, y se guarda.
- Se vuelve a generar si los datos de esa empresa han cambiado (la huella de
  `hechos.calcular`: una compra nueva, una máquina de baja, una
  reclasificación) o si el guardado tiene más de `caducidad_meses`; y con
  «Volver a generar».
- Sin cliente de FACTUSOL, o sin parque ni compras, no se llama a la IA: se
  dice que no hay datos.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.services.analisis_parque import config as cfg_mod
from app.services.analisis_parque import hechos as hx

logger = logging.getLogger(__name__)

SIN_GENERAR, AL_DIA, DESACTUALIZADO, CADUCADO = (
    "sin_generar", "al_dia", "desactualizado", "caducado")


def _ahora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(d: datetime | None) -> str | None:
    return f"{d.isoformat()}Z" if d else None


def _codigos_catalogo(session: Session) -> set[str]:
    from app.erp.models.historico import HistoricoArticulo  # noqa: PLC0415

    return {(c or "").strip().upper() for c in session.scalars(
        select(func.distinct(HistoricoArticulo.codart)))}


def leer(session: Session, company: Any, *, hoy: date | None = None) -> dict[str, Any]:
    """Lo guardado y si está al día. No llama a la IA."""
    from app.models.analisis_parque import AnalisisEmpresa  # noqa: PLC0415

    cfg = cfg_mod.configuracion(session)
    h = hx.calcular(session, company, hoy=hoy, cfg=cfg)
    fila = session.get(AnalisisEmpresa, company.id)
    out: dict[str, Any] = {
        "company_id": company.id, "datos": h.estado, "estado": SIN_GENERAR,
        "hechos_actuales": h.como_dict(), "analisis": None, "generado_at": None,
        "modelo": None, "coste_usd": None, "caducidad_meses": cfg["caducidad_meses"],
    }
    if h.estado != hx.CON_DATOS:
        # Sin datos no hay nada que generar: se dice (y no se gasta).
        out["estado"] = AL_DIA
        return out
    if fila is None:
        return out
    out.update({"analisis": json.loads(fila.contenido_json), "generado_at": _iso(fila.generado_at),
                "modelo": fila.modelo, "coste_usd": fila.coste_usd})
    if fila.huella != h.huella:
        out["estado"] = DESACTUALIZADO
    elif fila.generado_at < _ahora() - timedelta(days=int(cfg["caducidad_meses"]) * 30.44):
        out["estado"] = CADUCADO
    else:
        out["estado"] = AL_DIA
    return out


def generar(session: Session, company: Any, *, por: str, forzar: bool = False,
            hoy: date | None = None, modelo: str | None = None) -> dict[str, Any]:
    """Genera (si toca, o si `forzar`) y guarda. Devuelve lo mismo que `leer`.
    LLMNotConfiguredError / LLMError si la IA no está o falla (no se guarda nada)."""
    from app.core.config import get_settings  # noqa: PLC0415
    from app.models.analisis_parque import AnalisisEmpresa  # noqa: PLC0415
    from app.models.ia import PROPOSITO_ANALISIS_PARQUE  # noqa: PLC0415
    from app.services import llm  # noqa: PLC0415
    from app.services.analisis_parque import ia  # noqa: PLC0415
    from app.services.ia.uso import registrar_llamada  # noqa: PLC0415

    actual = leer(session, company, hoy=hoy)
    if actual["datos"] != hx.CON_DATOS or (actual["estado"] == AL_DIA and not forzar):
        return actual
    cfg = cfg_mod.configuracion(session)
    modelo = modelo or cfg["modelo"]
    h = hx.calcular(session, company, hoy=hoy, cfg=cfg)
    try:
        resultado, uso = ia.generar(h, modelo=modelo, api_key=get_settings().anthropic_api_key,
                                    codigos_catalogo=_codigos_catalogo(session))
    except llm.LLMError as exc:
        registrar_llamada(session, proposito=PROPOSITO_ANALISIS_PARQUE, modelo=modelo,
                          uso=llm.tomar_ultimo_uso(), referencia_tipo="company",
                          referencia_id=company.id, ok=False, error=type(exc).__name__)
        session.commit()
        raise
    llamada = registrar_llamada(session, proposito=PROPOSITO_ANALISIS_PARQUE, modelo=modelo,
                                uso=uso, referencia_tipo="company", referencia_id=company.id)
    contenido = {"hechos": h.como_dict(), "lectura": resultado.lectura,
                 "entrada": resultado.entrada, "descartado": resultado.descartado}
    fila = session.get(AnalisisEmpresa, company.id) or AnalisisEmpresa(company_id=company.id)
    fila.huella, fila.generado_at = h.huella, _ahora()
    fila.modelo = (uso.modelo if uso else None) or modelo
    fila.contenido_json = json.dumps(contenido, ensure_ascii=False, default=str)
    fila.tokens_entrada = uso.tokens_entrada if uso else None
    fila.tokens_salida = uso.tokens_salida if uso else None
    fila.coste_usd = llamada.coste_usd
    fila.por = por
    session.add(fila)
    session.commit()
    logger.info("analisis_parque: empresa %s, %d hechos, %d descartados, modelo %s",
                company.id[:8], len(h.hechos), len(resultado.descartado), fila.modelo)
    return leer(session, company, hoy=hoy)


#: Cuántas empresas como mucho se piden de una vez desde una lista.
MAX_LOTE = 50


def generar_lote(company_ids: list[str], *, por: str) -> dict[str, int]:
    """Para pedirlo desde una lista: una a una, en segundo plano, saltando las
    que están al día o no tienen datos. Con su propia sesión."""
    from app.db.session import get_engine  # noqa: PLC0415
    from app.models.crm import Company  # noqa: PLC0415
    from app.services import llm  # noqa: PLC0415

    out = {"generados": 0, "al_dia": 0, "sin_datos": 0, "fallidos": 0}
    with Session(get_engine()) as s:
        for cid in company_ids[:MAX_LOTE]:
            empresa = s.get(Company, cid)
            if empresa is None:
                continue
            try:
                antes = leer(s, empresa)
                if antes["datos"] != hx.CON_DATOS:
                    out["sin_datos"] += 1
                elif antes["estado"] == AL_DIA:
                    out["al_dia"] += 1
                else:
                    generar(s, empresa, por=por)
                    out["generados"] += 1
            except llm.LLMError:
                s.rollback()
                out["fallidos"] += 1
    logger.info("analisis_parque lote: %s", out)
    return out
