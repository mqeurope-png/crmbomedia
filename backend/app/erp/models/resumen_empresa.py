"""El resumen ERP por empresa (1/11/2026): lo que tiene y lo que compra cada
empresa de BoHub, precalculado para que los filtros de contactos sean SQL.

Son tablas DERIVADAS: el recálculo (`app/services/empresa_erp.py`) las borra
y las rehace enteras a partir del histórico y del parque, con las mismas
funciones que «Dormidos y huecos». No son datos de nadie: no se editan a mano
y ninguna tabla de Bart depende de ellas. Si un recálculo falla, se quedan
las del anterior y `crm_resumen_estado` lo dice.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base


class CrmEmpresaErp(Base):
    """Una fila por empresa de BoHub vinculada a un cliente de FACTUSOL."""

    __tablename__ = "crm_empresa_erp"

    company_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    codcli: Mapped[str | None] = mapped_column(String(32))
    #: Máquinas en servicio (sin bajas; con las equivalencias de código).
    maquinas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Meses de la máquina más nueva y de la más vieja (en servicio).
    meses_nueva: Mapped[int | None] = mapped_column(Integer)
    meses_vieja: Mapped[int | None] = mapped_column(Integer)
    tiene_baja: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Lo facturado en todo el histórico (series que cuentan; abonos restan).
    gasto_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    ultima_compra: Mapped[date | None] = mapped_column(Date)
    meses_sin_comprar: Mapped[int | None] = mapped_column(Integer)
    #: Dormido / hueco de ALGUNA gama (el de cada gama, en `crm_empresa_gama`).
    dormido: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hueco: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Ha comprado: gasto > 0 en el histórico o pedidos en el ERP.
    ya_cliente: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CrmEmpresaGama(Base):
    """Por empresa y gama de sus máquinas en servicio."""

    __tablename__ = "crm_empresa_gama"

    company_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    gama: Mapped[str] = mapped_column(String(32), primary_key=True)
    maquinas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    meses_nueva: Mapped[int | None] = mapped_column(Integer)
    meses_vieja: Mapped[int | None] = mapped_column(Integer)
    dormido: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hueco: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CrmEmpresaModelo(Base):
    """Los modelos (código canónico, con las equivalencias) en servicio."""

    __tablename__ = "crm_empresa_modelo"

    company_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    modelo: Mapped[str] = mapped_column(String(64), primary_key=True)


class CrmEmpresaCompra(Base):
    """Cada artículo que ha comprado la empresa, con la familia y la clase
    que valen hoy (alias de código, familia de respaldo, clase propia)."""

    __tablename__ = "crm_empresa_compra"

    company_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    codart: Mapped[str] = mapped_column(String(64), primary_key=True)
    familia: Mapped[str | None] = mapped_column(String(32), index=True)
    clase: Mapped[str | None] = mapped_column(String(16), index=True)


class CrmResumenEstado(Base):
    """Una sola fila (`id = 1`): cómo fue el último recálculo."""

    __tablename__ = "crm_resumen_estado"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: El último recálculo que salió bien (y los datos que hay son de él).
    ok_at: Mapped[datetime | None] = mapped_column(DateTime)
    #: El último que falló, y por qué (solo el tipo de error: el mensaje
    #: podría llevar datos de clientes). Vacío si el último fue bien.
    fallo_at: Mapped[datetime | None] = mapped_column(DateTime)
    fallo: Mapped[str | None] = mapped_column(Text)
    duracion_ms: Mapped[int | None] = mapped_column(Integer)
    empresas: Mapped[int | None] = mapped_column(Integer)
    ya_cliente_empresas: Mapped[int | None] = mapped_column(Integer)
    ya_cliente_contactos: Mapped[int | None] = mapped_column(Integer)
    origen_woo_contactos: Mapped[int | None] = mapped_column(Integer)
    quitadas_a_mano: Mapped[int | None] = mapped_column(Integer)


class CrmEtiquetaAuto(Base):
    """Lo que el recálculo sabe de sus etiquetas automáticas en cada
    contacto: `puesta` (la puso él) o `quitada` (la puso él y alguien la
    quitó a mano: no la vuelve a poner). Si alguien la vuelve a añadir a mano,
    el apunte de `quitada` se borra y el contacto vuelve al régimen normal."""

    __tablename__ = "crm_etiqueta_auto"

    contact_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    etiqueta: Mapped[str] = mapped_column(String(32), primary_key=True)
    estado: Mapped[str] = mapped_column(String(16), nullable=False)
    at: Mapped[datetime | None] = mapped_column(DateTime)
