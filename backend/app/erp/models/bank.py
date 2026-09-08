"""ERP-F4-A — conciliación bancaria: cuentas, movimientos del extracto,
conciliaciones (propuestas/confirmadas) y reglas aprendidas.

Este PR NO escribe en FACTUSOL: registrar el cobro (F_COB + F_LCO) va en
F-4-B. Aquí se importa el extracto, se CASA con las facturas pendientes, la
persona confirma, y se devuelve el Excel con las columnas rellenas.
"""
from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base, TimestampMixin


def _uuid() -> str:
    return str(uuid4())


class BankAccount(TimestampMixin, Base):
    """Cuenta bancaria propia (Bart tiene más que las 2 que conoce F_BAN)."""

    __tablename__ = "bank_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    bank_name: Mapped[str | None] = mapped_column(String(120))
    #: IBAN normalizado (sin espacios, mayúsculas). Único: identifica la
    #: cuenta al importar un extracto.
    iban: Mapped[str] = mapped_column(String(34), nullable=False, unique=True)
    bic: Mapped[str | None] = mapped_column(String(11))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    #: Empresa emisora (serie FACTUSOL) a la que pertenece la cuenta.
    serie: Mapped[int | None] = mapped_column(Integer)
    # ERP-F5: contrapartida de cobro de FACTUSOL (destino del dinero) a la
    # que se enlaza esta cuenta: Sabadell Bomedia → 6, Belfius MQ → 2,
    # Sabadell Streamtec → 8. Es lo que F-4-B usará al registrar el cobro.
    contrapartida_codigo: Mapped[str | None] = mapped_column(String(10))
    #: Mapeo de columnas del extracto de ESTE banco (JSON): se guarda la
    #: primera vez y se reutiliza. Vacío → autodetección (Sabadell por
    #: defecto).
    column_mapping_json: Mapped[str | None] = mapped_column(Text)
    #: Cabecera del último extracto (cuenta/divisa/titular) para reproducirla
    #: tal cual en el Excel exportado.
    statement_header_json: Mapped[str | None] = mapped_column(Text)


class BankMovement(TimestampMixin, Base):
    """Un movimiento del extracto. Se guarda ENTERO (incl. referencias y la
    fila original) para casar y para exportar sin perder nada."""

    __tablename__ = "bank_movements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("bank_accounts.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    fecha_oper: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    fecha_valor: Mapped[date | None] = mapped_column(Date)
    concepto: Mapped[str] = mapped_column(Text, nullable=False, default="")
    importe: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    saldo: Mapped[float | None] = mapped_column(Numeric(14, 2))
    referencia1: Mapped[str | None] = mapped_column(String(255))
    referencia2: Mapped[str | None] = mapped_column(String(255))
    #: Clave anti-duplicados: cuenta + fecha oper + importe + concepto + saldo.
    #: Reimportar un rango solapado no duplica.
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: Pagador extraído del concepto/REFERENCIA 2 («ABONO TRANSFERENCIA DE X»).
    payer_name: Mapped[str | None] = mapped_column(String(255))
    #: pending | reconciled | discarded
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    discard_reason: Mapped[str | None] = mapped_column(String(255))
    source_file: Mapped[str | None] = mapped_column(String(255))
    #: Posición en el fichero de origen (para exportar en el mismo orden).
    source_row: Mapped[int | None] = mapped_column(Integer)
    #: Fila original completa (JSON) — se exporta tal cual, sin reformatear.
    raw_json: Mapped[str | None] = mapped_column(Text)


class BankReconciliation(TimestampMixin, Base):
    """Asociación movimiento → factura FACTUSOL (clave COMPUESTA serie+código).
    Varias filas por movimiento = reparto entre varias facturas. `status`:
    proposed (la máquina propone) | confirmed (Bart confirmó). NADA se
    concilia solo: solo `confirmed` cuenta como conciliado."""

    __tablename__ = "bank_reconciliations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    movement_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("bank_movements.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    serie: Mapped[int] = mapped_column(Integer, nullable=False)
    codigo: Mapped[int] = mapped_column(Integer, nullable=False)
    numero: Mapped[str] = mapped_column(String(20), nullable=False)
    cliente_nombre: Mapped[str | None] = mapped_column(String(255))
    #: Parte del movimiento asignada a esta factura.
    importe: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    #: alta | media | baja | None (elegida a mano)
    confidence: Mapped[str | None] = mapped_column(String(8))
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed", index=True)
    confirmed_by_user_id: Mapped[str | None] = mapped_column(String(36))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)


class BankLearnedRule(TimestampMixin, Base):
    """Lo que Bart decide se recuerda. `kind`:
      - exclude_pattern: concepto/pagador que NO es cobro de cliente
        (TPV, TRASPASO, Scalapay…). Los defaults se siembran aquí, no van
        cableados en el código: son editables.
      - payer_to_client: pagador normalizado → cliente FACTUSOL (CODCLI)."""

    __tablename__ = "bank_learned_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    #: Patrón normalizado (sin acentos, mayúsculas) contra concepto o pagador.
    pattern: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Para payer_to_client: CODCLI del cliente FACTUSOL.
    client_codcli: Mapped[str | None] = mapped_column(String(36))
    client_nombre: Mapped[str | None] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(String(255))
    created_by_user_id: Mapped[str | None] = mapped_column(String(36))
