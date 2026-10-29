"""ERP-F4-A — orquestación de la conciliación bancaria (sin escribir en
FACTUSOL): cuentas, importación del extracto, casado, decisiones y export.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import unicodedata
from collections import Counter
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.erp.bank.matching import (
    DEFAULT_EXCLUSION_PATTERNS,
    extract_payer,
    is_excluded,
    normalize_name,
    propose,
)
from app.erp.bank.parsing import (
    COLUMNA_SIN_ROTULO,
    FILLED_COLUMNS,
    SABADELL_MAPPING,
    Formato,
    Lectura,
    ParseError,
    cuenta_casa,
    dedupe_key,
    formato_de_mapping,
    interpretar,
    leer_anotacion,
    normalize_iban,
    numero_de_cuenta,
    read_statement,
    texto,
)
from app.erp.contrapartidas import contrapartida_names
from app.erp.models import (
    BankAccount,
    BankLearnedRule,
    BankMovement,
    BankReconciliation,
)
from app.integrations.factusol.catalogs import normalize_code, resolve_name

logger = logging.getLogger(__name__)

# --- cuentas ------------------------------------------------------------------


def account_to_dict(a: BankAccount, contrapartidas: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "id": a.id,
        "name": a.name,
        "bank_name": a.bank_name,
        "iban": a.iban,
        "bic": a.bic,
        "currency": a.currency,
        "serie": a.serie,
        # ERP-F5: contrapartida de cobro enlazada (código + nombre del catálogo).
        "contrapartida_codigo": a.contrapartida_codigo,
        "contrapartida_nombre": resolve_name(contrapartidas or {}, a.contrapartida_codigo),
        "column_mapping": json.loads(a.column_mapping_json) if a.column_mapping_json else None,
        "has_statement_header": bool(a.statement_header_json),
    }


def account_dict(session: Session, a: BankAccount) -> dict[str, Any]:
    """`account_to_dict` con el nombre de la contrapartida ya resuelto."""
    return account_to_dict(a, contrapartida_names(session))


def list_accounts(session: Session) -> list[dict[str, Any]]:
    rows = session.scalars(select(BankAccount).order_by(BankAccount.name)).all()
    names = contrapartida_names(session)
    return [account_to_dict(a, names) for a in rows]


def _contrapartida_code(session: Session, value: Any) -> str | None:
    """Código de contrapartida validado contra el catálogo configurable
    (vacío → None; desconocido → ValueError, no se guarda a ciegas)."""
    raw = str(value or "").strip()
    if not raw:
        return None
    names = contrapartida_names(session)
    if resolve_name(names, raw) is None:
        raise ValueError(f"Contrapartida desconocida: {raw}. Dala de alta en /erp/settings.")
    return normalize_code(raw)


def create_account(session: Session, data: dict[str, Any]) -> BankAccount:
    iban = normalize_iban(data.get("iban"))
    if not iban:
        raise ValueError("IBAN obligatorio")
    if session.scalar(select(BankAccount).where(BankAccount.iban == iban)):
        raise ValueError(f"Ya existe una cuenta con el IBAN {iban}")
    acc = BankAccount(
        name=str(data.get("name") or "").strip() or iban,
        bank_name=(data.get("bank_name") or None),
        iban=iban,
        bic=(data.get("bic") or None),
        currency=str(data.get("currency") or "EUR").upper()[:3],
        serie=data.get("serie"),
        contrapartida_codigo=_contrapartida_code(session, data.get("contrapartida_codigo")),
        column_mapping_json=json.dumps(data["column_mapping"])
        if data.get("column_mapping")
        else None,
    )
    session.add(acc)
    session.commit()
    session.refresh(acc)
    return acc


def update_account(session: Session, account_id: str, data: dict[str, Any]) -> BankAccount:
    acc = session.get(BankAccount, account_id)
    if acc is None:
        raise LookupError("cuenta no encontrada")
    for key in ("name", "bank_name", "bic", "serie"):
        if key in data:
            setattr(acc, key, data[key] if data[key] not in ("",) else None)
    if "currency" in data and data["currency"]:
        acc.currency = str(data["currency"]).upper()[:3]
    if "iban" in data and data["iban"]:
        acc.iban = normalize_iban(data["iban"])
    if "contrapartida_codigo" in data:
        acc.contrapartida_codigo = _contrapartida_code(session, data["contrapartida_codigo"])
    if "column_mapping" in data:
        acc.column_mapping_json = (
            json.dumps(data["column_mapping"]) if data["column_mapping"] else None
        )
    session.commit()
    session.refresh(acc)
    return acc


def delete_account(session: Session, account_id: str) -> None:
    acc = session.get(BankAccount, account_id)
    if acc is None:
        raise LookupError("cuenta no encontrada")
    session.delete(acc)
    session.commit()


def suggested_accounts_from_fban(client: Any, ejercicio: str) -> list[dict[str, Any]]:
    """Las cuentas que conoce FACTUSOL (F_BAN) como SUGERENCIA de alta — no se
    imponen. Columnas vistas en el discovery: IBABAN, BICBAN, CUEBAN, ENTBAN,
    DOMBAN."""
    try:
        rows = client.load_table("F_BAN", filtro="1=1", ejercicio=ejercicio)
    except Exception as exc:  # noqa: BLE001 — sugerencia best-effort
        logger.warning("F_BAN no disponible para sugerir cuentas: %s", exc)
        return []
    out = []
    for r in rows:
        iban = normalize_iban(r.get("IBABAN") or r.get("CUEBAN") or "")
        if not iban:
            continue
        out.append(
            {
                "name": str(r.get("ENTBAN") or "").strip() or iban,
                "bank_name": str(r.get("ENTBAN") or "").strip() or None,
                "iban": iban,
                "bic": str(r.get("BICBAN") or "").strip() or None,
                "currency": "EUR",
            }
        )
    return out


# --- importación ---------------------------------------------------------------

#: Cuántas filas no entendidas se devuelven con su detalle (el total siempre).
MAX_NO_ENTENDIDAS = 500


def _cuenta_del_extracto(parsed: Any, lectura: Lectura) -> str | None:
    """De qué cuenta es el extracto: la de la cabecera (IBAN o número antiguo)
    o, si el banco la repite en cada fila (Belfius), la más frecuente."""
    numero = parsed.header.get("numero_cuenta")
    if numero:
        return str(numero)
    if lectura.cuentas:
        return numero_de_cuenta(lectura.cuentas.most_common(1)[0][0])
    return None


def _formatos_de_cuentas(session: Session, account_id: str | None) -> tuple[Formato, ...]:
    """El mapeo guardado a mano en una cuenta, como formato más (un banco que
    no está en `FORMATOS`)."""
    if not account_id:
        return ()
    acc = session.get(BankAccount, account_id)
    if acc is None or not acc.column_mapping_json:
        return ()
    try:
        mapping = json.loads(acc.column_mapping_json)
    except ValueError:
        return ()
    if not isinstance(mapping, dict) or not all(isinstance(v, str) for v in mapping.values()):
        return ()
    return (formato_de_mapping(acc.name, mapping),)


def _corta(value: str | None, n: int = 255) -> str | None:
    return value[:n] if value else None


def _resumen_texto(imported: int, no_entendidas: list[dict[str, Any]]) -> str:
    """«1.240 movimientos importados, 3 filas no entendidas (139, 144, 802)»."""
    n = len(no_entendidas)
    texto_imp = f"{imported:,}".replace(",", ".") + (
        " movimiento importado" if imported == 1 else " movimientos importados")
    if not n:
        return texto_imp
    filas = ", ".join(str(x["fila"]) for x in no_entendidas[:12])
    if n > 12:
        filas += ", …"
    plural = "" if n == 1 else "s"
    return f"{texto_imp}, {n} fila{plural} no entendida{plural} ({filas})"


def import_statement(
    session: Session,
    *,
    content: bytes,
    filename: str,
    account_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Lee el fichero, identifica la cuenta (avisa si no está dada de alta en
    vez de importar a ciegas), deduplica y persiste.

    La cuenta sale del número de la cabecera, sea IBAN o el número antiguo
    (Sabadell «0081-0202-17-0001259030»), o de la cuenta que el banco repite
    en cada fila (Belfius `Rekening`); se compara con las dadas de alta por
    `cuenta_casa`. Una fila que no se entiende no aborta: se importa el resto
    y se devuelven las no entendidas con su fila y su motivo. Solo un fichero
    sin fila de columnas reconocible lanza ParseError.

    Lo que viene anotado en la columna `factura` entra casado
    (`_casar_anotaciones`). Nada más se concilia: el resto solo se PROPONE
    después, con el casado de siempre."""
    parsed = read_statement(content, filename, extra=_formatos_de_cuentas(session, account_id))
    lectura = interpretar(parsed, parsed.mapping)
    numero = _cuenta_del_extracto(parsed, lectura)
    account: BankAccount | None = None
    if account_id:
        account = session.get(BankAccount, account_id)
    elif numero:
        account = next(
            (a for a in session.scalars(select(BankAccount)) if cuenta_casa(numero, a.iban)),
            None,
        )
    base = {
        "total_rows": len(parsed.rows),
        "imported": 0,
        "duplicates": 0,
        "formato": parsed.formato,
    }
    if account is None:
        return {
            **base,
            "ok": False,
            "code": "unknown_account",
            "iban": numero,
            "header": parsed.header,
            "detail": (
                f"El extracto es de la cuenta {numero}, que no está dada de alta. "
                "Dala de alta primero (pestaña Cuentas)."
                if numero else
                "El extracto no dice de qué cuenta es. Elige la cuenta a mano antes de importar."
            ),
        }
    if numero and not cuenta_casa(numero, account.iban):
        return {
            **base,
            "ok": False,
            "code": "iban_mismatch",
            "iban": numero,
            "detail": (
                f"El extracto es de la cuenta {numero}, pero la cuenta elegida es {account.iban}."
            ),
        }

    # Una fila que dice ser de OTRA cuenta (Belfius la repite en cada fila) no
    # se importa en esta: se enseña.
    movimientos: list[dict[str, Any]] = []
    no_entendidas = list(lectura.no_entendidas)
    for m in lectura.movimientos:
        propia = numero_de_cuenta(m["cuenta"]) or m["cuenta"] if m["cuenta"] else None
        if propia and not cuenta_casa(propia, account.iban):
            no_entendidas.append({
                "fila": m["source_row"],
                "motivo": f"es de otra cuenta ({m['cuenta']})",
                "valores": [v for v in (texto(x) for x in m["raw"].values()) if v],
            })
            continue
        movimientos.append(m)
    no_entendidas.sort(key=lambda x: x["fila"])

    account.column_mapping_json = json.dumps(parsed.mapping)
    account.statement_header_json = json.dumps(
        {
            "header": parsed.header,
            "lines": parsed.header_lines,
            "columns": parsed.columns,
            "formato": parsed.formato,
        }
    )

    existing: dict[str, str | None] = dict(
        session.execute(
            select(BankMovement.dedupe_key, BankMovement.id)
            .where(BankMovement.account_id == account.id)
        ).all()
    )
    imported = duplicates = 0
    repeticiones: Counter[str] = Counter()
    nuevos: list[BankMovement] = []
    reanotados: list[BankMovement] = []
    for m in movimientos:
        datos = (account.id, m["fecha_oper"], m["importe"], m["concepto"], m["saldo"])
        if m["saldo"] is not None:
            # Con saldo, dos filas idénticas son la misma (un solape al juntar
            # exportaciones): la segunda es un duplicado.
            key = dedupe_key(*datos)
        elif m["referencia1"]:
            # Sin saldo (Belfius), el nº de transacción distingue dos iguales.
            key = dedupe_key(*datos, referencia=m["referencia1"])
        else:
            clave_base = dedupe_key(*datos)
            repeticiones[clave_base] += 1
            key = dedupe_key(*datos, repeticion=repeticiones[clave_base])
        if key in existing:
            duplicates += 1
            # Bart anota el extracto y lo vuelve a subir: lo que ahora trae
            # anotado y antes no, se guarda y se casa.
            mov_id = existing[key]
            mov = session.get(BankMovement, mov_id) if mov_id else None
            if (
                mov is not None and (m["factura"] or m["pedido"])
                and not (mov.anotacion_factura or mov.anotacion_pedido)
            ):
                mov.anotacion_factura = _corta(m["factura"])
                mov.anotacion_pedido = _corta(m["pedido"])
                reanotados.append(mov)
            continue
        existing[key] = None
        mov = BankMovement(
            account_id=account.id,
            fecha_oper=m["fecha_oper"],
            fecha_valor=m["fecha_valor"],
            concepto=m["concepto"],
            importe=m["importe"],
            saldo=m["saldo"],
            referencia1=_corta(m["referencia1"]),
            referencia2=_corta(m["referencia2"]),
            dedupe_key=key,
            payer_name=_corta(extract_payer(m["concepto"], m["referencia2"])),
            source_file=filename[:255],
            source_row=m["source_row"],
            raw_json=json.dumps(m["raw"], default=str),
            anotacion_factura=_corta(m["factura"]),
            anotacion_pedido=_corta(m["pedido"]),
        )
        session.add(mov)
        nuevos.append(mov)
        imported += 1
    session.flush()
    # Un movimiento que ya está conciliado o descartado no se toca por lo que
    # se anote después: lo decidido en la pantalla manda.
    pendientes = [m for m in reanotados if m.status == "pending" and not session.scalar(
        select(func.count(BankReconciliation.id)).where(
            BankReconciliation.movement_id == m.id,
            BankReconciliation.status == "confirmed",
        ))]
    for mov in pendientes:
        for old in session.scalars(select(BankReconciliation).where(
            BankReconciliation.movement_id == mov.id,
            BankReconciliation.status == "proposed",
        )).all():
            session.delete(old)
    session.flush()
    anotadas = _casar_anotaciones(session, account, [*nuevos, *pendientes], user_id)
    anotadas["nuevas_en_reimportados"] = len(reanotados)
    session.commit()
    return {
        **base,
        "ok": True,
        "code": "imported",
        "account_id": account.id,
        "account_name": account.name,
        "iban": account.iban,
        "total_rows": len(lectura.movimientos) + len(lectura.no_entendidas),
        "imported": imported,
        "duplicates": duplicates,
        "continuaciones": lectura.continuaciones,
        "no_entendidas_total": len(no_entendidas),
        "no_entendidas": no_entendidas[:MAX_NO_ENTENDIDAS],
        "anotadas": anotadas,
        "resumen": _resumen_texto(imported, no_entendidas),
    }


#: Lo que se dice de una conciliación que venía anotada en el extracto.
ORIGEN_EXTRACTO = "extracto"


def _casar_anotaciones(
    session: Session, account: BankAccount, movimientos: list[BankMovement],
    user_id: str | None,
) -> dict[str, int]:
    """La columna `factura` que Bart rellena a mano no es una propuesta: es la
    respuesta. Si la celda es solo el número (o los números) de factura, el
    movimiento entra CONCILIADO con esas facturas, confianza alta y el porqué
    («venía anotado en el extracto»); lo confirmó él al anotarlo, y quien
    importa el fichero lo hace suyo. Con una nota al lado («parcial»,
    «confirmar», «50 %») entra como PROPUESTA para revisar. Solo cobros
    (importe > 0): en un pago la columna anota facturas de proveedor. Varias
    facturas en un movimiento: el importe se reparte a partes iguales (se ve
    en el porqué; reasignar lo corrige)."""
    stats = {"confirmadas": 0, "propuestas": 0, "sin_factura": 0}
    now = datetime.now(UTC).replace(tzinfo=None)
    for mov in movimientos:
        if float(mov.importe) <= 0 or not mov.anotacion_factura:
            continue
        anotacion = leer_anotacion(mov.anotacion_factura, serie_cuenta=account.serie)
        if anotacion is None or not anotacion.facturas:
            stats["sin_factura"] += 1
            continue
        n = len(anotacion.facturas)
        total = round(float(mov.importe), 2)
        parte = round(total / n, 2)
        reparto = (
            f"; importe repartido a partes iguales entre {n} facturas" if n > 1 else ""
        )
        if anotacion.limpia:
            reason = f"Venía anotado en el extracto («{anotacion.texto}»){reparto}"
            estado, confianza = "confirmed", "alta"
        else:
            reason = (
                f"Venía anotado en el extracto con una nota: «{anotacion.texto}». "
                f"Revísalo antes de confirmar{reparto}"
            )
            estado, confianza = "proposed", "media"
        for i, (serie, codigo) in enumerate(anotacion.facturas):
            importe = parte if i < n - 1 else round(total - parte * (n - 1), 2)
            session.add(BankReconciliation(
                movement_id=mov.id,
                serie=serie,
                codigo=codigo,
                numero=f"{serie}-{codigo:06d}",
                importe=importe,
                confidence=confianza,
                reason=reason[:2000],
                status=estado,
                origen=ORIGEN_EXTRACTO,
                confirmed_by_user_id=user_id if estado == "confirmed" else None,
                confirmed_at=now if estado == "confirmed" else None,
            ))
        if estado == "confirmed":
            mov.status = "reconciled"
            stats["confirmadas"] += 1
        else:
            stats["propuestas"] += 1
    return stats


# --- reglas aprendidas ---------------------------------------------------------


def ensure_default_rules(session: Session) -> None:
    """Siembra las exclusiones por defecto la primera vez (editables después).
    Configurables: no cableadas en el código."""
    if session.scalar(select(func.count(BankLearnedRule.id))):
        return
    for p in DEFAULT_EXCLUSION_PATTERNS:
        session.add(BankLearnedRule(kind="exclude_pattern", pattern=p, note="por defecto"))
    session.commit()


def list_rules(session: Session) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(BankLearnedRule).order_by(BankLearnedRule.kind, BankLearnedRule.pattern)
    ).all()
    return [
        {
            "id": r.id,
            "kind": r.kind,
            "pattern": r.pattern,
            "client_codcli": r.client_codcli,
            "client_nombre": r.client_nombre,
            "note": r.note,
        }
        for r in rows
    ]


def create_rule(
    session: Session, data: dict[str, Any], user_id: str | None = None
) -> BankLearnedRule:
    kind = str(data.get("kind") or "")
    if kind not in ("exclude_pattern", "payer_to_client"):
        raise ValueError("kind inválido")
    pattern = str(data.get("pattern") or "").strip()
    if not pattern:
        raise ValueError("pattern obligatorio")
    rule = BankLearnedRule(
        kind=kind,
        pattern=pattern if kind == "exclude_pattern" else normalize_name(pattern),
        client_codcli=data.get("client_codcli"),
        client_nombre=data.get("client_nombre"),
        note=data.get("note"),
        created_by_user_id=user_id,
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


def delete_rule(session: Session, rule_id: str) -> None:
    rule = session.get(BankLearnedRule, rule_id)
    if rule is None:
        raise LookupError("regla no encontrada")
    session.delete(rule)
    session.commit()


def _rules_snapshot(session: Session) -> tuple[list[str], dict[str, str]]:
    ensure_default_rules(session)
    patterns: list[str] = []
    payer_map: dict[str, str] = {}
    for r in session.scalars(select(BankLearnedRule)).all():
        if r.kind == "exclude_pattern":
            patterns.append(r.pattern)
        elif r.kind == "payer_to_client" and r.client_codcli:
            payer_map[normalize_name(r.pattern)] = str(r.client_codcli)
    return patterns, payer_map


# --- facturas pendientes (FACTUSOL, solo lectura) ------------------------------


def pending_invoices(client: Any, ejercicio: str) -> list[dict[str, Any]]:
    """Facturas con saldo pendiente (ESTFAC 0/1) con su saldo real de F_LCO
    (F3-fix1). Una sola lectura de F_FAC + una de F_LCO."""
    from app.integrations.factusol.collections import payment_annotator  # noqa: PLC0415
    from app.integrations.factusol.documents import list_documents  # noqa: PLC0415

    annotate = payment_annotator(client, ejercicio=ejercicio)
    out: list[dict[str, Any]] = []
    for estado in ("0", "1"):
        page = list_documents(
            client,
            "facturas",
            ejercicio=ejercicio,
            estado=estado,
            annotate=annotate,
            limit=200,
            offset=0,
        )
        total = page["total"]
        items = list(page["items"])
        offset = len(items)
        while offset < total:
            more = list_documents(
                client,
                "facturas",
                ejercicio=ejercicio,
                estado=estado,
                annotate=annotate,
                limit=200,
                offset=offset,
            )
            items.extend(more["items"])
            offset += len(more["items"]) or total
        out.extend(items)
    return [
        {
            "serie": d["serie"],
            "codigo": d["codigo"],
            "numero": d["numero"],
            "cliente_codigo": d.get("cliente_codigo"),
            "cliente_nombre": d.get("cliente_nombre"),
            "fecha": d.get("fecha"),
            "total": d.get("total"),
            "saldo_pendiente": d.get("saldo_pendiente")
            if d.get("saldo_pendiente") is not None
            else d.get("total"),
            "estado": d.get("estado"),
        }
        for d in out
        if isinstance(d.get("codigo"), int) and d.get("serie") is not None
    ]


# --- casado ----------------------------------------------------------------------


def run_matching(
    session: Session,
    invoices: list[dict[str, Any]],
    *,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Genera PROPUESTAS para los movimientos pendientes de entrada. Nunca
    confirma nada. Reemplaza las propuestas anteriores no confirmadas."""
    patterns, payer_map = _rules_snapshot(session)
    query = select(BankMovement).where(BankMovement.status == "pending")
    if account_id:
        query = query.where(BankMovement.account_id == account_id)
    stats = {
        "candidates": 0,
        "excluded": 0,
        "proposed": 0,
        "no_proposal": 0,
        "by_confidence": {"alta": 0, "media": 0, "baja": 0},
    }
    stats["anotadas"] = 0
    # No pisar lo confirmado a mano; sí regenerar lo meramente propuesto. Lo
    # que venía anotado en el extracto (con una nota que revisar) tampoco: es
    # la respuesta de Bart, no una propuesta de la máquina.
    for mov in session.scalars(query).all():
        if float(mov.importe) <= 0:
            continue
        stats["candidates"] += 1
        propuestas = session.scalars(
            select(BankReconciliation).where(
                BankReconciliation.movement_id == mov.id,
                BankReconciliation.status == "proposed",
            )
        ).all()
        if any(p.origen == ORIGEN_EXTRACTO for p in propuestas):
            stats["anotadas"] += 1
            continue
        for old in propuestas:
            session.delete(old)
        if is_excluded(mov.concepto, mov.payer_name, patterns):
            stats["excluded"] += 1
            continue
        props = propose(
            {
                "importe": float(mov.importe),
                "fecha_oper": mov.fecha_oper,
                "concepto": mov.concepto,
                "referencia2": mov.referencia2,
                "payer": mov.payer_name,
            },
            invoices,
            payer_map,
        )
        if not props:
            stats["no_proposal"] += 1
            continue
        stats["proposed"] += 1
        stats["by_confidence"][props[0]["confidence"]] += 1
        for p in props:
            session.add(
                BankReconciliation(
                    movement_id=mov.id,
                    serie=p["serie"],
                    codigo=p["codigo"],
                    numero=p["numero"] or f"{p['serie']}-{p['codigo']:06d}",
                    cliente_nombre=p.get("cliente_nombre"),
                    importe=p["importe"],
                    confidence=p["confidence"],
                    reason=p["reason"],
                    status="proposed",
                )
            )
    session.commit()
    return stats


# --- listado + contadores --------------------------------------------------------


def _recon_dict(r: BankReconciliation) -> dict[str, Any]:
    return {
        "id": r.id,
        "serie": r.serie,
        "codigo": r.codigo,
        "numero": r.numero,
        "cliente_nombre": r.cliente_nombre,
        "importe": float(r.importe),
        "confidence": r.confidence,
        "reason": r.reason,
        "status": r.status,
        "origen": r.origen,
    }


def movement_to_dict(m: BankMovement, recons: list[BankReconciliation]) -> dict[str, Any]:
    proposed = [x for x in recons if x.status == "proposed"]
    confirmed = [x for x in recons if x.status == "confirmed"]
    conf = proposed[0].confidence if proposed else None
    return {
        "id": m.id,
        "account_id": m.account_id,
        "fecha_oper": m.fecha_oper.isoformat(),
        "concepto": m.concepto,
        "importe": float(m.importe),
        "saldo": float(m.saldo) if m.saldo is not None else None,
        "referencia1": m.referencia1,
        "referencia2": m.referencia2,
        "payer_name": m.payer_name,
        "status": m.status,
        "discard_reason": m.discard_reason,
        "anotacion_factura": m.anotacion_factura,
        "anotacion_pedido": m.anotacion_pedido,
        "confidence": conf,
        "proposals": [_recon_dict(x) for x in proposed],
        "reconciled": [_recon_dict(x) for x in confirmed],
    }


def list_movements(
    session: Session,
    *,
    account_id: str | None = None,
    desde: date | None = None,
    hasta: date | None = None,
    confidence: str | None = None,
    status: str | None = None,
    only_incoming: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    q = select(BankMovement)
    if account_id:
        q = q.where(BankMovement.account_id == account_id)
    if desde:
        q = q.where(BankMovement.fecha_oper >= desde)
    if hasta:
        q = q.where(BankMovement.fecha_oper <= hasta)
    if status:
        q = q.where(BankMovement.status == status)
    if only_incoming:
        q = q.where(BankMovement.importe > 0)
    rows = session.scalars(
        q.order_by(BankMovement.fecha_oper.desc(), BankMovement.source_row.asc())
    ).all()
    ids = [m.id for m in rows]
    recons_by_mov: dict[str, list[BankReconciliation]] = {}
    if ids:
        for r in session.scalars(
            select(BankReconciliation).where(BankReconciliation.movement_id.in_(ids))
        ).all():
            recons_by_mov.setdefault(r.movement_id, []).append(r)
    items = [movement_to_dict(m, recons_by_mov.get(m.id, [])) for m in rows]
    if confidence:
        if confidence == "none":
            items = [i for i in items if i["status"] == "pending" and not i["proposals"]]
        else:
            items = [i for i in items if i["confidence"] == confidence]
    total = len(items)
    # Contadores globales (misma cuenta/rango, sin filtro de estado/confianza).
    base = select(BankMovement).where(BankMovement.importe > 0)
    if account_id:
        base = base.where(BankMovement.account_id == account_id)
    all_in = session.scalars(base).all()
    counters = {
        "pending": sum(1 for m in all_in if m.status == "pending"),
        "reconciled": sum(1 for m in all_in if m.status == "reconciled"),
        "discarded": sum(1 for m in all_in if m.status == "discarded"),
        "pending_amount": round(sum(float(m.importe) for m in all_in if m.status == "pending"), 2),
    }
    return {"items": items[offset : offset + limit], "total": total, "counters": counters}


# --- decisiones (la persona decide) -----------------------------------------------


def _mov(session: Session, movement_id: str) -> BankMovement:
    mov = session.get(BankMovement, movement_id)
    if mov is None:
        raise LookupError("movimiento no encontrado")
    return mov


def _confirm_rows(
    session: Session, mov: BankMovement, rows: list[BankReconciliation], user_id: str | None
) -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    for r in rows:
        r.status = "confirmed"
        r.confirmed_by_user_id = user_id
        r.confirmed_at = now
    mov.status = "reconciled"
    mov.discard_reason = None


def _son_alternativas(mov: BankMovement, props: list[BankReconciliation]) -> bool:
    """Varias propuestas que suman MÁS que el movimiento no son un reparto:
    son facturas candidatas para el mismo cobro (una transferencia de 100 €
    con tres facturas de 100 € del mismo cliente). Confirmarlas todas daría
    por cobradas tres facturas con un pago."""
    total = round(sum(float(p.importe) for p in props), 2)
    return len(props) > 1 and total > round(float(mov.importe), 2) + 0.01


def confirm_movement(session: Session, movement_id: str, user_id: str | None) -> dict[str, Any]:
    """Confirma la PROPUESTA actual. Sin propuesta → error (no se inventa)."""
    mov = _mov(session, movement_id)
    props = session.scalars(
        select(BankReconciliation).where(
            BankReconciliation.movement_id == mov.id,
            BankReconciliation.status == "proposed",
        )
    ).all()
    if not props:
        raise ValueError("este movimiento no tiene propuesta que confirmar")
    if _son_alternativas(mov, props):
        raise ValueError(
            "las propuestas son alternativas (cada una por el importe entero): elige "
            "una con «Elegir / repartir»"
        )
    _confirm_rows(session, mov, props, user_id)
    session.commit()
    return movement_to_dict(mov, props)


def reassign_movement(
    session: Session,
    movement_id: str,
    targets: list[dict[str, Any]],
    user_id: str | None,
    *,
    learn_payer: bool = False,
) -> dict[str, Any]:
    """Elegir otra factura o REPARTIR entre varias. Sustituye la propuesta y
    queda confirmado (es una decisión explícita). `learn_payer` recuerda
    pagador → cliente para futuras importaciones."""
    mov = _mov(session, movement_id)
    if not targets:
        raise ValueError("indica al menos una factura")
    total = round(sum(float(t.get("importe") or 0) for t in targets), 2)
    if abs(total - float(mov.importe)) > 0.01:
        raise ValueError(
            f"el reparto ({total:.2f}) no suma el importe del movimiento ({float(mov.importe):.2f})"
        )
    for old in session.scalars(
        select(BankReconciliation).where(BankReconciliation.movement_id == mov.id)
    ).all():
        session.delete(old)
    rows = []
    for t in targets:
        serie, codigo = int(t["serie"]), int(t["codigo"])
        rows.append(
            BankReconciliation(
                movement_id=mov.id,
                serie=serie,
                codigo=codigo,
                numero=t.get("numero") or f"{serie}-{codigo:06d}",
                cliente_nombre=t.get("cliente_nombre"),
                importe=round(float(t["importe"]), 2),
                confidence=None,
                reason="elegida a mano",
                status="proposed",
            )
        )
        session.add(rows[-1])
    _confirm_rows(session, mov, rows, user_id)
    if learn_payer and mov.payer_name and targets[0].get("cliente_codigo"):
        create_rule(
            session,
            {
                "kind": "payer_to_client",
                "pattern": mov.payer_name,
                "client_codcli": str(targets[0]["cliente_codigo"]),
                "client_nombre": targets[0].get("cliente_nombre"),
                "note": "aprendida al conciliar a mano",
            },
            user_id,
        )
    session.commit()
    return movement_to_dict(mov, rows)


def discard_movement(
    session: Session,
    movement_id: str,
    reason: str,
    user_id: str | None,
    *,
    learn: bool = False,
) -> dict[str, Any]:
    """«No es un cobro de cliente» (con motivo). `learn` recuerda el pagador/
    concepto como exclusión para no volver a preguntarlo."""
    mov = _mov(session, movement_id)
    for old in session.scalars(
        select(BankReconciliation).where(BankReconciliation.movement_id == mov.id)
    ).all():
        session.delete(old)
    mov.status = "discarded"
    mov.discard_reason = (reason or "no es cobro de cliente")[:255]
    if learn:
        pattern = mov.payer_name or mov.concepto[:80]
        create_rule(
            session,
            {
                "kind": "exclude_pattern",
                "pattern": pattern,
                "note": f"aprendida: {mov.discard_reason}",
            },
            user_id,
        )
    session.commit()
    return movement_to_dict(mov, [])


def reopen_movement(session: Session, movement_id: str) -> dict[str, Any]:
    mov = _mov(session, movement_id)
    for old in session.scalars(
        select(BankReconciliation).where(BankReconciliation.movement_id == mov.id)
    ).all():
        session.delete(old)
    mov.status = "pending"
    mov.discard_reason = None
    session.commit()
    return movement_to_dict(mov, [])


def confirm_all_high(
    session: Session, user_id: str | None, *, account_id: str | None = None
) -> int:
    """Acción EN BLOQUE (un clic de Bart): confirma las propuestas de confianza
    ALTA. Sigue siendo una decisión suya, no automática."""
    q = select(BankMovement).where(BankMovement.status == "pending")
    if account_id:
        q = q.where(BankMovement.account_id == account_id)
    n = 0
    for mov in session.scalars(q).all():
        props = session.scalars(
            select(BankReconciliation).where(
                BankReconciliation.movement_id == mov.id,
                BankReconciliation.status == "proposed",
            )
        ).all()
        if props and all(p.confidence == "alta" for p in props) and not _son_alternativas(
            mov, props,
        ):
            _confirm_rows(session, mov, props, user_id)
            n += 1
    session.commit()
    return n


# --- exportación -------------------------------------------------------------------


def _pedidos_de_facturas(
    session: Session, facturas: set[tuple[int, int]],
) -> dict[tuple[int, int], list[str]]:
    """El pedido de BoHub de cada factura, por su CODFAC y su serie. Un pedido
    sin la serie guardada (los facturados antes de la 0105) no vale: las
    series 1 y 5 numeran igual y se colaría el de otra empresa."""
    from app.erp.models import Order  # noqa: PLC0415

    if not facturas:
        return {}
    numeros = {str(codigo) for _, codigo in facturas}
    out: dict[tuple[int, int], list[str]] = {}
    for order in session.scalars(
        select(Order).where(Order.factusol_invoice_number.in_(numeros))
    ).all():
        codigo = int(order.factusol_invoice_number)
        for serie, cod in facturas:
            if cod == codigo and order.factusol_invoice_serie == serie:
                out.setdefault((serie, cod), []).append(order.order_number)
    return out


def export_statement_xlsx(
    session: Session,
    *,
    account_id: str,
    desde: date | None = None,
    hasta: date | None = None,
) -> bytes:
    """Devuelve un .xlsx NUEVO (nunca se modifica el original) con el formato
    del extracto: misma cabecera (cuenta/divisa/titular), mismas columnas y
    orden, y las columnas de Bart rellenas con lo CONFIRMADO: `factura` con
    las facturas (varias → separadas por coma) y `pedido` con lo que ya traía
    anotado o, si no, el pedido de BoHub de esas facturas. Si el extracto no
    traía esas columnas, se añaden al final. Lo no conciliado sale tal cual,
    con lo que Bart hubiera anotado."""
    from openpyxl import Workbook  # noqa: PLC0415

    acc = session.get(BankAccount, account_id)
    if acc is None:
        raise LookupError("cuenta no encontrada")
    header = json.loads(acc.statement_header_json) if acc.statement_header_json else {}
    columns: list[str] = list(header.get("columns") or list(SABADELL_MAPPING.values()))
    mapping = (
        json.loads(acc.column_mapping_json) if acc.column_mapping_json else dict(SABADELL_MAPPING)
    )
    factura_col = mapping.get("factura")
    pedido_col = mapping.get("pedido")
    if not factura_col:
        factura_col = "factura"
        columns.append(factura_col)
    if not pedido_col:
        pedido_col = "pedido"
        columns.append(pedido_col)

    q = select(BankMovement).where(BankMovement.account_id == acc.id)
    if desde:
        q = q.where(BankMovement.fecha_oper >= desde)
    if hasta:
        q = q.where(BankMovement.fecha_oper <= hasta)
    rows = session.scalars(
        q.order_by(BankMovement.fecha_oper.desc(), BankMovement.source_row.asc())
    ).all()
    ids = [m.id for m in rows]
    confirmed: dict[str, list[BankReconciliation]] = {}
    if ids:
        for r in session.scalars(
            select(BankReconciliation).where(
                BankReconciliation.movement_id.in_(ids),
                BankReconciliation.status == "confirmed",
            )
        ).all():
            confirmed.setdefault(r.movement_id, []).append(r)
    pedidos = _pedidos_de_facturas(
        session, {(r.serie, r.codigo) for rs in confirmed.values() for r in rs},
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "Extracto"
    # El preámbulo del extracto tal cual; si el banco no lo trae (Belfius:
    # columnas en la primera fila), tampoco. Sin ningún extracto importado
    # aún, el de siempre.
    lines = header.get("lines")
    if lines is None:
        lines = [f"Cuenta: {acc.iban}", f"Divisa: {acc.currency}", "Titular:"]
    for line in lines:
        ws.append([line])
    if lines:
        ws.append([])
    ws.append([None if c.startswith(COLUMNA_SIN_ROTULO) else c for c in columns])
    fac_norm = str(factura_col).strip().upper()
    ped_norm = str(pedido_col).strip().upper()
    for m in rows:
        raw = json.loads(m.raw_json) if m.raw_json else {}
        recons = confirmed.get(m.id, [])
        out_row = []
        for col in columns:
            value = raw.get(col)
            col_norm = col.strip().upper()
            if recons and col_norm == fac_norm:
                value = ", ".join(r.numero for r in recons)
            elif recons and col_norm == ped_norm and (value is None or not str(value).strip()):
                encontrados = [p for r in recons for p in pedidos.get((r.serie, r.codigo), [])]
                value = ", ".join(dict.fromkeys(encontrados)) or None
            out_row.append(value)
        ws.append(out_row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


#: Columnas del CSV que lee `scripts/registrar_cobros_lote.ps1`.
COBROS_CSV_COLUMNS = ("serie", "codigo", "cuenta", "forma", "fecha", "observaciones")
#: Margen para las observaciones (el script las manda a un campo de 255; en
#: Windows PowerShell 5.1 un carácter no ASCII puede ocupar más).
_MAX_OBSERVACIONES = 200


def export_cobros_csv(
    session: Session,
    *,
    saldos: dict[tuple[int, int], float],
    account_id: str | None = None,
    desde: date | None = None,
    hasta: date | None = None,
) -> tuple[str, dict[str, int]]:
    """El CSV de cobros para `scripts/registrar_cobros_lote.ps1` con lo
    CONCILIADO (confirmado) y no descartado: una fila por factura, con la
    contrapartida de la cuenta del banco en `cuenta`, `forma` «Transferencia»
    y la fecha del movimiento. No escribe en FACTUSOL: lo registra el script,
    que simula primero.

    El script no lleva importe: registra el SALDO entero de la factura. Por eso
    solo entra una factura si lo conciliado de ella cuadra con su saldo
    pendiente en FACTUSOL ahora (`saldos`, de `pending_invoices`). Se dejan
    fuera, y se cuentan:
      - `parciales`: lo conciliado no llega a su saldo (regístralo a mano);
      - `no_pendientes`: no está pendiente en FACTUSOL (ya cobrada, o de otro
        ejercicio);
      - `no_cuadran`: el movimiento tiene conciliado más que su importe;
      - `sin_contrapartida`: la cuenta del banco no tiene contrapartida.
    El fichero va en UTF-8 con BOM y sin caracteres raros en las
    observaciones, que Windows PowerShell 5.1 lee bien."""
    q = (
        select(BankReconciliation, BankMovement, BankAccount)
        .join(BankMovement, BankMovement.id == BankReconciliation.movement_id)
        .join(BankAccount, BankAccount.id == BankMovement.account_id)
        .where(
            BankReconciliation.status == "confirmed",
            BankMovement.status == "reconciled",
            BankMovement.importe > 0,
        )
    )
    if account_id:
        q = q.where(BankMovement.account_id == account_id)
    if desde:
        q = q.where(BankMovement.fecha_oper >= desde)
    if hasta:
        q = q.where(BankMovement.fecha_oper <= hasta)
    filas = session.execute(
        q.order_by(BankMovement.fecha_oper.asc(), BankMovement.source_row.asc())
    ).all()
    stats = {"incluidas": 0, "parciales": 0, "no_pendientes": 0, "no_cuadran": 0,
             "sin_contrapartida": 0}
    # Lo conciliado de cada movimiento y de cada factura (una factura puede
    # cobrarse en dos movimientos: entra si entre los dos llegan a su saldo).
    por_movimiento: dict[str, float] = {}
    por_factura: dict[tuple[int, int], float] = {}
    for r, mov, _acc in filas:
        por_movimiento[mov.id] = por_movimiento.get(mov.id, 0.0) + float(r.importe)
        clave = (r.serie, r.codigo)
        por_factura[clave] = por_factura.get(clave, 0.0) + float(r.importe)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(COBROS_CSV_COLUMNS)
    vistas: set[tuple[int, int]] = set()
    for r, mov, acc in filas:
        clave = (r.serie, r.codigo)
        if clave in vistas:
            continue
        vistas.add(clave)
        if por_movimiento[mov.id] > float(mov.importe) + 0.01:
            stats["no_cuadran"] += 1
            continue
        if not acc.contrapartida_codigo:
            stats["sin_contrapartida"] += 1
            continue
        saldo = saldos.get(clave)
        if saldo is None:
            stats["no_pendientes"] += 1
            continue
        if por_factura[clave] < saldo - 0.01:
            stats["parciales"] += 1
            continue
        origen = "anotado en el extracto" if r.origen == ORIGEN_EXTRACTO else "conciliado en BoHub"
        concepto = re.sub(r"[^\x20-\x7e]", "?", mov.concepto or "")
        observaciones = (
            f"{_ascii(acc.name)} - {mov.fecha_oper.strftime('%d/%m/%Y')} - "
            f"{float(mov.importe):.2f} EUR - {origen} - {concepto}"
        )
        writer.writerow([
            r.serie, r.codigo, acc.contrapartida_codigo, "Transferencia",
            mov.fecha_oper.strftime("%d/%m/%Y"), observaciones[:_MAX_OBSERVACIONES],
        ])
        stats["incluidas"] += 1
    return "\ufeff" + buf.getvalue(), stats


def _ascii(value: str) -> str:
    """Sin acentos ni símbolos: lo que Windows PowerShell 5.1 lee igual."""
    sin_acentos = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^\x20-\x7e]", "", sin_acentos)


__all__ = [
    "FILLED_COLUMNS",
    "ParseError",
    "account_dict",
    "account_to_dict",
    "confirm_all_high",
    "confirm_movement",
    "create_account",
    "create_rule",
    "delete_account",
    "delete_rule",
    "discard_movement",
    "ensure_default_rules",
    "export_cobros_csv",
    "export_statement_xlsx",
    "import_statement",
    "list_accounts",
    "list_movements",
    "list_rules",
    "movement_to_dict",
    "pending_invoices",
    "reassign_movement",
    "reopen_movement",
    "run_matching",
    "suggested_accounts_from_fban",
    "update_account",
]
