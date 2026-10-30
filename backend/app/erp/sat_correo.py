"""El correo al taller pedido en «Mandar a SAT» (30/10/2026).

Su estado vive en `packing_json.sat_correo`: `enviado`, `fallido` (se
reintenta desde la ficha) o `pendiente_albaran` (lleva el albarán y el pedido
aún no lo tiene: sale en cuanto se crea o se vincula). Guarda los datos del
correo tal cual se pidió y quién lo pidió, para mandarlo después sin volver a
escribirlo. Si el correo falla, el pedido NO sale de la Cola SAT.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.erp.factusol_albaran import packing_of, save_packing

logger = logging.getLogger(__name__)

KEY = "sat_correo"


def bloque(order: Any) -> dict[str, Any] | None:
    b = packing_of(order).get(KEY)
    return b if isinstance(b, dict) else None


def publico(order: Any) -> dict[str, Any] | None:
    """El estado para la ficha (sin los datos del correo)."""
    b = bloque(order)
    return {k: b.get(k) for k in ("estado", "detalle", "at")} if b else None


def guardar(order: Any, estado: str, *, detalle: str | None = None,
            payload: dict[str, Any] | None = None, por: str | None = None) -> dict[str, Any]:
    """Apunta el estado. `payload`/`por` solo se ponen al pedirlo; después se
    conservan (un reintento no los cambia)."""
    packing = packing_of(order)
    previo = packing.get(KEY) if isinstance(packing.get(KEY), dict) else {}
    packing[KEY] = {
        "estado": estado, "detalle": detalle, "at": datetime.now(UTC).isoformat(),
        "payload": payload if payload is not None else previo.get("payload"),
        "por": por if por is not None else previo.get("por"),
    }
    save_packing(order, packing)
    return {"estado": estado, "detalle": detalle, "at": packing[KEY]["at"]}


#: Un «enviando» más viejo que esto se dio por cortado: se puede reintentar.
ENVIANDO_CADUCA_SEGUNDOS = 600


def reclamar(session: Session, order: Any, desde: set[str]) -> Any | None:
    """Pasa el correo a «enviando» SOLO si está en uno de los estados `desde`
    (con el pedido bloqueado mientras se mira): así dos caminos (el job del
    albarán, vincular a mano, «Enviar ahora») no mandan el mismo correo dos
    veces. Un «enviando» viejo (se cortó) cuenta como reintentable si está en
    `desde`. Devuelve el pedido recién leído si lo reclama; `None` si no."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.erp.models import Order  # noqa: PLC0415

    fila = session.scalars(select(Order).where(Order.id == order.id).with_for_update()
                           .execution_options(populate_existing=True)).one()
    b = bloque(fila) or {}
    estado = b.get("estado")
    if estado == "enviando":
        try:
            desde_at = datetime.fromisoformat(str(b.get("at")))
            viejo = (datetime.now(UTC) - desde_at).total_seconds() > ENVIANDO_CADUCA_SEGUNDOS
        except (TypeError, ValueError):
            viejo = True
        if not viejo:
            session.rollback()
            return None
    if estado not in desde:
        session.rollback()
        return None
    guardar(fila, "enviando", detalle="Enviándose: si se queda así, comprueba en Gmail "
                                      "si salió antes de reintentar.")
    session.commit()
    return fila


def detalle_de_error(exc: Exception) -> str:
    """Lo que se le dice a quien manda, sin datos del cliente."""
    from fastapi import HTTPException  # noqa: PLC0415

    if isinstance(exc, HTTPException):
        d = exc.detail
        return str(d.get("detail") if isinstance(d, dict) else d)[:300]
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str) and detail:
        return detail[:300]
    return (f"Error inesperado ({type(exc).__name__}): comprueba en Gmail si salió "
            "antes de reintentar.")


def enviar_si_pendiente(session: Session, order: Any, *, client: Any, ejercicio: str) -> None:
    """Si el correo esperaba al albarán y el pedido ya lo tiene, lo manda (como
    quien lo pidió). Lo usan el job del albarán y el vincular a mano. Nunca
    lanza: un fallo queda como `fallido` para reintentar."""
    from app.erp.order_email import send_order_email  # noqa: PLC0415
    from app.models.crm import User  # noqa: PLC0415

    b = bloque(order)
    if not b or b.get("estado") != "pendiente_albaran" or not order.factusol_albaran_number:
        return
    order = reclamar(session, order, {"pendiente_albaran"})
    if order is None:
        return   # otro camino ya lo está mandando
    datos = (bloque(order) or {}).get("payload") or {}
    user = session.get(User, b.get("por")) if b.get("por") else None
    if user is None or not datos.get("to"):
        guardar(order, "fallido", detalle="No se sabe quién lo pidió: mándalo desde la ficha.")
        session.commit()
        return
    try:
        send_order_email(
            session, client, order, ejercicio=ejercicio, current_user=user,
            to=datos["to"], cc=datos.get("cc") or None, bcc=datos.get("bcc") or None,
            subject=datos["subject"], body_text=datos["body_text"], lang=datos.get("lang", "es"),
            from_alias=datos["from_alias"], include_albaran=bool(datos.get("include_albaran")),
            include_pedido=bool(datos.get("include_pedido")), include_factura=False,
        )
    except Exception as exc:  # noqa: BLE001 — el pedido sigue en la cola
        logger.warning("sat_correo: el correo al taller del pedido %s no salió (%s)",
                       order.order_number, type(exc).__name__)
        session.rollback()
        guardar(order, "fallido", detalle=detalle_de_error(exc))
        session.commit()
        return
    guardar(order, "enviado")
    session.commit()
