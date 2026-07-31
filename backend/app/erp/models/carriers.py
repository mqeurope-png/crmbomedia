"""BoHub ERP — Transportistas (Fase A, migración 0080).

Catálogo de carriers (Genei, DSV, manual…). `adapter_class` apunta al
adaptador Python cuando el carrier tenga API (Fase B+); NULL = flujo manual
(el operario pega el tracking a mano).
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, TimestampMixin


class Carrier(TimestampMixin, Base):
    __tablename__ = "carriers"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    has_api: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    adapter_class: Mapped[str | None] = mapped_column(String(128))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
