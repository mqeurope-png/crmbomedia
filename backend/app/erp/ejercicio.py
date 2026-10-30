"""El ejercicio: lo que acota los documentos, las facturas y los cobros.

No es un par de fechas: es el de FACTUSOL. En FACTUSOL cada ejercicio es una
base de datos aparte (`CargaTabla` lleva `ejercicio`, y uno que no existe
responde `BDNoExiste`), y BoHub lee y escribe siempre en el de Configuración
ERP (`ErpSettings.factusol_default_ejercicio`, `service.ejercicio_for`). Ese
es el **ejercicio en curso**: las pantallas arrancan en él y los cobros se
registran en él. Los anteriores se pueden ver (solo consulta) eligiéndolos.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

#: El primer ejercicio de FACTUSOL: el selector ofrece del en curso hasta este.
#: Los antiguos (2014-2022) solo responden mientras estén reabiertos en el
#: escritorio; si no, la pantalla dice que no hay base de datos.
PRIMER_EJERCICIO = 2014


def ejercicio_en_curso(session: Session) -> str:
    from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

    return ejercicio_for(session)


def ejercicios(session: Session) -> dict[str, object]:
    """El ejercicio en curso y los que ofrece el selector (del en curso hacia
    atrás). Que FACTUSOL tenga de verdad cada uno se sabe al leerlo."""
    actual = ejercicio_en_curso(session)
    anio = int(actual)
    return {"en_curso": actual,
            "ejercicios": [str(a) for a in range(anio, min(anio, PRIMER_EJERCICIO) - 1, -1)]}


def limites(ejercicio: str) -> tuple[date, date]:
    """Del 1 de enero al 31 de diciembre del ejercicio (los movimientos del
    banco, que no viven en FACTUSOL, se acotan por su fecha de operación)."""
    anio = int(ejercicio)
    return date(anio, 1, 1), date(anio, 12, 31)


def es_sin_base_de_datos(exc: Exception) -> bool:
    """¿FACTUSOL ha respondido que ese ejercicio no tiene base de datos?"""
    from app.integrations.factusol.client import RESPUESTA_BD_NO_EXISTE  # noqa: PLC0415

    return "sin base de datos" in str(exc) or RESPUESTA_BD_NO_EXISTE in str(
        getattr(exc, "body", "") or "")
