"""Lo que el diálogo único de «Registrar cobro» sabe de una factura, además de
su estado en FACTUSOL (29/10/2026). Es el mismo diálogo y el mismo contexto se
entre por la ficha del pedido, por Conciliación o por ERP · Documentos:

- `externa`: la factura no tiene pedido de BoHub detrás (solo FACTUSOL).
- `movimiento`: el movimiento del banco identificado como su cobro: el que se
  pide (`movement_id`, p. ej. desde Conciliación o la ficha) o, si no, el ya
  conciliado con ella cuyo cobro aún no está en FACTUSOL. Con él, la fecha que
  se propone es su fecha valor y la cuenta, la contrapartida de su cuenta.
- `hoja`: si con ese movimiento también se escribirá la factura en la hoja de
  Drive de la cuenta (y en cuál). Una segunda escritura: nunca una sorpresa.
- `cobrado_con`: contra qué se cobró (las líneas de cobro de FACTUSOL y el
  movimiento del banco de cada una, si lo hay).

Solo lee: nada aquí escribe ni en FACTUSOL ni en la hoja.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.bank import service as bank_service
from app.erp.models import BankAccount, BankMovement
from app.integrations.factusol.catalogs import resolve_name


def _hoja(acc: BankAccount, numero: str) -> dict[str, Any] | None:
    from app.erp.bank.drive import FORMATO_XLSX  # noqa: PLC0415

    if not acc.drive_file_id or not acc.drive_escribir or acc.drive_formato == FORMATO_XLSX:
        return None
    return {"cuenta": acc.name, "pestana": acc.drive_pestana, "factura": numero}


def contexto_cobro(
    session: Session, *, serie: int, codigo: int, numero: str,
    lineas: list[dict[str, Any]], ya_cobrada: bool, movement_id: str | None = None,
) -> dict[str, Any]:
    """`externa`, `movimiento`, `hoja` y `cobrado_con` de la factura (ver el
    módulo). `lineas`: sus líneas de cobro en FACTUSOL, ya leídas."""
    serie, codigo = int(serie), int(codigo)
    nombres = bank_service.contrapartida_names(session)
    externa = not bank_service._pedidos_con_id(session, {(serie, codigo)})
    conciliados = bank_service.conciliados_de_factura(session, serie, codigo)
    ids_cuentas = {m.account_id for m, _ in conciliados}
    cuentas = {a.id: a for a in session.scalars(
        select(BankAccount).where(BankAccount.id.in_(ids_cuentas)))} if ids_cuentas else {}
    # El movimiento que se pide, aunque aún no esté conciliado: si su cobro
    # ya es una línea de FACTUSOL, solo se concilia (y el diálogo lo dice).
    pedido = session.get(BankMovement, movement_id) if movement_id else None
    a_casar = list(conciliados)
    if pedido is not None and all(m.id != pedido.id for m, _ in conciliados):
        a_casar.append((pedido, float(pedido.importe)))
        acc_pedido = session.get(BankAccount, pedido.account_id)
        if acc_pedido is not None:
            cuentas[acc_pedido.id] = acc_pedido
    casados = bank_service.casar_lineas_de_cobro(
        [(m.id, imp, bank_service.fechas_de_movimiento(m),
          getattr(cuentas.get(m.account_id), "contrapartida_codigo", None))
         for m, imp in a_casar],
        lineas,
    )

    movimiento: dict[str, Any] | None = None
    hoja: dict[str, Any] | None = None
    elegido: tuple[BankMovement, float] | None = None
    if pedido is not None:
        propio = next((imp for m, imp in conciliados if m.id == pedido.id), float(pedido.importe))
        elegido = (pedido, propio)
    elif not ya_cobrada:
        sueltos = sorted(
            ((m, imp) for m, imp in conciliados if m.id not in casados),
            key=lambda x: bank_service.fechas_de_movimiento(x[0]),
        )
        elegido = sueltos[0] if sueltos else None
    if elegido is not None:
        mov, propio = elegido
        acc = session.get(BankAccount, mov.account_id)
        movimiento = {
            **bank_service.movimiento_pedido_dict(mov, acc, nombres),
            "importe_factura": round(propio, 2),
            "conciliado": any(m.id == mov.id for m, _ in conciliados),
            "ya_en_factusol": mov.id in casados,
        }
        hoja = _hoja(acc, numero) if acc is not None else None

    por_linea = {id(linea): mid for mid, linea in casados.items()}
    movs = {m.id: m for m, _ in a_casar}
    cobrado_con = []
    for linea in lineas:
        mid = por_linea.get(id(linea))
        mov = movs.get(mid) if mid else None
        acc = cuentas.get(mov.account_id) if mov is not None else None
        cobrado_con.append({
            "fecha": str(linea.get("fecha") or "")[:10] or None,
            "importe": float(linea.get("importe") or 0),
            "cuenta": resolve_name(nombres, linea.get("contrapartida"))
            or linea.get("contrapartida"),
            "movimiento": (bank_service.movimiento_pedido_dict(mov, acc, nombres)
                           if mov is not None else None),
        })
    return {"externa": externa, "movimiento": movimiento, "hoja": hoja,
            "cobrado_con": cobrado_con}
