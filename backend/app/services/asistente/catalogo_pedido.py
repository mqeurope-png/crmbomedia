"""Los productos del catálogo que pide quien envía, vengan o no a cuento del
hilo.

El CATÁLOGO de un borrador es una preselección por lo que trata el correo. El
28/10/2026, sobre la avería de un Ador 20W, se pidió «incluye en fila la ficha
de la artisJet 6090 Trust y la de la MBO 6090 versión desktop»: el CATÁLOGO
llevaba diez FLUX y no la Trust, y su ficha salió del índice de las webs, sin
precio, aunque el catálogo la tiene a 20.900 €. **El catálogo manda siempre**:
lo que se pide se busca en el catálogo ENTERO del compositor (no se carga
entero en el prompt: solo lo pedido) y, si está, va su ficha con su precio de
venta.

- En el primer borrador, las que nombra la instrucción ya entran en el
  CATÁLOGO con su propio cupo (`catalogo.para_asistente`); aquí se añaden las
  que el plan pide por su nombre o por el enlace de su página
  (`consulta_fuentes`, `resumen["catalogo_pedidos"]`).
- En una corrección, el CATÁLOGO es el del primer borrador: lo pedido que no
  está en él va en un bloque aparte de la petición, con su `P#`.

Solo lee el catálogo.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

ELECCION = "instruccion"


def origenes(session: Session, *, instruccion: str | None, material: Any = None,
             con_menciones: bool = False, ya: Iterable[str] = ()) -> list[str]:
    """Los productos del catálogo (`origen_id`) que pide quien envía y aún no
    están entre los del correo (`ya`): los que la consulta reconoció como del
    catálogo (por el nombre que da el plan o por el enlace de su página) y,
    con `con_menciones` (una corrección sin plan), los que la instrucción
    nombra de verdad (`catalogo.nombrados_en`)."""
    from app.services.conocimiento import catalogo as catalogo_kb  # noqa: PLC0415

    resumen = getattr(material, "resumen", None) or {}
    salida = [str(o) for o in resumen.get("catalogo_pedidos") or [] if o]
    if con_menciones and instruccion:
        salida += catalogo_kb.nombrados_en(instruccion, catalogo_kb._maquinas_por_producto(session))
    excluidos = {str(o) for o in ya if o}
    return [o for o in dict.fromkeys(salida) if o not in excluidos]


def productos(session: Session, origen_ids: list[str], *, idioma: str,
              ahora: datetime | None = None) -> list[dict[str, Any]]:
    """Sus fichas como las ve el asistente (en el idioma del correo, con el
    precio de venta), marcadas como pedidas por la instrucción."""
    from app.services.conocimiento import catalogo as catalogo_kb  # noqa: PLC0415
    from app.services.conocimiento.config import configuracion  # noqa: PLC0415

    if not origen_ids:
        return []
    return [{**p, "eleccion": ELECCION} for p in catalogo_kb.maquinas_por_origen(
        session, origen_ids, idioma=idioma, ahora=ahora or datetime.now(UTC),
        config=configuracion(session))]
