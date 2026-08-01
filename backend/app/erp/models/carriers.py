"""BoHub ERP — Transportistas (Fase A migración 0080 + Fase B migración 0081).

Catálogo de carriers (Genei, DSV, manual…). `adapter_class` apunta al
adaptador Python cuando el carrier tenga API (Fase B+); NULL = flujo manual
(el operario pega el tracking a mano).

Fase B añade las credenciales del adaptador (Genei: user/password JWT +
addressId origen; DSV se queda como manual/portal).
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Boolean, String, Text
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

    # BoHub ERP Fase B (migración 0081). Credenciales del adaptador Genei
    # (user/password JSON cifrado con Fernet — Genei da JWT tras Login),
    # URL base de la API (https://apiv2.genei.es), secret opcional de
    # webhooks firmados y addressId origen (1304422 para Bomedia SAT).
    # Todo nullable — carriers manuales/portal (DSV) los dejan vacíos.
    api_credentials_encrypted: Mapped[str | None] = mapped_column(Text)
    api_base_url: Mapped[str | None] = mapped_column(String(255))
    webhook_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    default_address_id: Mapped[str | None] = mapped_column(String(64))
