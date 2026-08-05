"""BoHub ERP — caché local de líneas de proforma. **OBSOLETA desde C-4-fix3.**

Se creó en C-4 (migración 0088) porque dábamos por hecho que `F_PRE` era
mono-línea: habíamos buscado la tabla de líneas como `F_LPRE`, `F_LPR`, `F_LPP`…
sin acertar, y concluimos que no existía.

**Existe: es `F_LPS`** (`F_LPS.CODLPS = F_PRE.CODPRE`), descubierta en vivo el
2026-08-05. Desde C-4-fix3 las líneas se leen y escriben ahí, que además
funciona con las proformas creadas en el FACTUSOL de escritorio — cosa que esta
caché nunca pudo hacer.

La tabla **no se borra**: pudo guardar filas de proformas creadas desde el CRM
entre los PR #309 y #312. Ya no se lee ni se escribe. Retirarla es una limpieza
posterior, cuando conste que no hace falta.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base


class FactusolQuoteLineCache(Base):
    """Una línea del desglose de una proforma creada desde el CRM."""

    __tablename__ = "factusol_quote_lines_cache"
    __table_args__ = (
        UniqueConstraint("factusol_codpre", "ejercicio", "position",
                         name="uq_factusol_quote_line_position"),
        Index("idx_factusol_quote_lines_codpre", "factusol_codpre", "ejercicio"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    #: CODPRE de F_PRE. Numérico en FACTUSOL; aquí texto para no depender del
    #: tipo que devuelva la API (int en unas tablas, str en otras).
    factusol_codpre: Mapped[str] = mapped_column(String(24), nullable=False)
    ejercicio: Mapped[str] = mapped_column(String(4), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Código de artículo F_ART (`ARTLPC` en la nomenclatura de líneas de
    #: FACTUSOL). Vacío en líneas libres: servicios, portes, reparaciones.
    artlpc: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    discount_pct: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, default=0
    )
    line_total: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    iva_pct: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=21)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
