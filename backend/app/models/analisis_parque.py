"""El análisis del parque de una empresa, guardado (1/11/2026).

Se genera cuando alguien abre la ficha (o lo pide desde una lista), no para
todas las empresas. Se vuelve a generar si cambian los datos de entrada (la
`huella`: una compra nueva, una máquina de baja, una reclasificación) o si
tiene más meses que la caducidad configurada. Ver
`app/services/analisis_parque/`.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base


class AnalisisEmpresa(Base):
    __tablename__ = "crm_analisis_empresa"

    company_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True)
    huella: Mapped[str] = mapped_column(String(64), nullable=False)
    generado_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    modelo: Mapped[str | None] = mapped_column(String(80))
    #: Los hechos que vio la IA, su lectura, «por dónde entrar» y lo descartado.
    contenido_json: Mapped[str] = mapped_column(
        Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=False)
    tokens_entrada: Mapped[int | None] = mapped_column(Integer)
    tokens_salida: Mapped[int | None] = mapped_column(Integer)
    coste_usd: Mapped[float | None] = mapped_column(Float)
    por: Mapped[str | None] = mapped_column(String(255))
