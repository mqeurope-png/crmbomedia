"""Dominios de correo que NO identifican a una empresa (1/11/2026).

`gmail.com` no dice de qué empresa es nadie; `xtec.cat` tampoco: lo comparten
miles de docentes de cientos de centros. Un contacto con uno de estos dominios
nunca se vincula a una empresa por el dominio. La lista es un dato (se edita en
Administración → Dominios compartidos), no código: antes vivía como
`PERSONAL_DOMAINS` en `company_extraction.py` y la 0166 la pasó aquí.

`crm_despegue_dominio` guarda cada contacto despegado (y cada empresa
archivada) por la limpieza de los vínculos que hizo el dominio compartido, con
su empresa anterior, para poder deshacer el lote entero.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, event, insert
from sqlalchemy.orm import Mapped, mapped_column

from app.models.crm import Base

#: Proveedor de correo personal (gmail.com, hotmail.es…).
TIPO_CORREO = "correo"
#: Dominio de una organización que lo comparte mucha gente de sitios distintos
#: (consejerías de educación, administración pública).
TIPO_COMPARTIDO = "compartido"
TIPOS = (TIPO_CORREO, TIPO_COMPARTIDO)


class DominioCompartido(Base):
    __tablename__ = "dominios_compartidos"

    dominio: Mapped[str] = mapped_column(String(255), primary_key=True)
    tipo: Mapped[str] = mapped_column(String(16), nullable=False, default=TIPO_COMPARTIDO)
    nota: Mapped[str | None] = mapped_column(String(255))
    por: Mapped[str | None] = mapped_column(String(255))
    at: Mapped[datetime | None] = mapped_column(DateTime)


#: La siembra de la tabla en una base nueva (`create_all`: tests y entornos
#: locales). En producción la siembra la 0166, con su propia copia. Después
#: manda la tabla: lo que se añade o quita en la pantalla.
SEMILLA_CORREO = (
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.es", "yahoo.co.uk", "yahoo.fr",
    "hotmail.com", "hotmail.es", "hotmail.fr", "outlook.com", "outlook.es", "live.com",
    "msn.com", "icloud.com", "me.com", "protonmail.com", "proton.me", "mail.com", "aol.com",
    "gmx.com", "gmx.es", "gmx.de", "web.de", "free.fr", "orange.fr", "wanadoo.es",
    "telefonica.net", "movistar.es", "vodafone.es", "qq.com", "163.com", "naver.com",
    "yandex.com", "yandex.ru", "tutanota.com",
)
SEMILLA_COMPARTIDOS = (
    "xtec.cat", "juntadeandalucia.es", "edu.xunta.gal", "gobiernodecanarias.org",
    "edu.jccm.es", "educa.aragon.es", "educa.jcyl.es", "educa.madrid.org",
    "hezkuntza.net", "educaib.eu", "educacion.navarra.es",
)


@event.listens_for(DominioCompartido.__table__, "after_create")
def _sembrar(target, connection, **_kw) -> None:  # noqa: ANN001
    connection.execute(insert(target), [
        *({"dominio": d, "tipo": TIPO_CORREO, "nota": "Proveedor de correo personal"}
          for d in SEMILLA_CORREO),
        *({"dominio": d, "tipo": TIPO_COMPARTIDO, "nota": "Consejería de educación"}
          for d in SEMILLA_COMPARTIDOS),
    ])


class DespegueDominio(Base):
    """Un apunte por contacto despegado o empresa archivada en un lote."""

    __tablename__ = "crm_despegue_dominio"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lote: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    #: `contacto` o `empresa`.
    tipo: Mapped[str] = mapped_column(String(16), nullable=False)
    objeto_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    #: Para un contacto: la empresa de la que se despegó.
    empresa_anterior: Mapped[str | None] = mapped_column(String(36))
    dominio: Mapped[str | None] = mapped_column(String(255))
    motivo: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Para una empresa: si ya estaba archivada (la reversión la deja igual).
    archivada_antes: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    por: Mapped[str | None] = mapped_column(String(255))
    at: Mapped[datetime | None] = mapped_column(DateTime)
    revertido_at: Mapped[datetime | None] = mapped_column(DateTime)
