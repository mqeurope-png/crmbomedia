"""ERP-F4-A — orquestación de la conciliación bancaria (sin escribir en
FACTUSOL): cuentas, importación del extracto, casado, decisiones y export.
"""

from __future__ import annotations

import io
import json
import logging
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
    FILLED_COLUMNS,
    SABADELL_MAPPING,
    ParseError,
    apply_mapping,
    dedupe_key,
    detect_mapping,
    normalize_iban,
    read_statement,
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


def import_statement(
    session: Session,
    *,
    content: bytes,
    filename: str,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Lee el fichero, identifica la cuenta por el IBAN de la cabecera (avisa
    si no está dada de alta en vez de importar a ciegas), aplica el mapeo de
    la cuenta (o autodetecta Sabadell y lo guarda), deduplica y persiste.
    Lanza ParseError (visible, con nº de fila) si una fila no se interpreta."""
    parsed = read_statement(content, filename)
    iban = parsed.header.get("iban")
    account: BankAccount | None = None
    if account_id:
        account = session.get(BankAccount, account_id)
    elif iban:
        account = session.scalar(select(BankAccount).where(BankAccount.iban == iban))
    if account is None:
        return {
            "ok": False,
            "code": "unknown_account",
            "iban": iban,
            "header": parsed.header,
            "detail": (
                f"El extracto es de la cuenta {iban or '(sin IBAN en cabecera)'}, "
                "que no está dada de alta. Dala de alta primero."
            ),
            "total_rows": len(parsed.rows),
            "imported": 0,
            "duplicates": 0,
        }
    if iban and account.iban != iban:
        return {
            "ok": False,
            "code": "iban_mismatch",
            "iban": iban,
            "detail": f"El extracto es del IBAN {iban}, pero la cuenta elegida es {account.iban}.",
            "total_rows": len(parsed.rows),
            "imported": 0,
            "duplicates": 0,
        }

    mapping = json.loads(account.column_mapping_json) if account.column_mapping_json else None
    if not mapping:
        mapping = detect_mapping(parsed.columns)
        if mapping is None:
            raise ParseError(
                parsed.columns_row,
                (
                    "no se reconoce el formato de columnas y la cuenta no tiene "
                    f"mapeo guardado. Columnas: {parsed.columns}"
                ),
            )
        account.column_mapping_json = json.dumps(mapping)  # se guarda la 1ª vez
    if parsed.header:
        account.statement_header_json = json.dumps(
            {
                "header": parsed.header,
                "lines": parsed.header_lines,
                "columns": parsed.columns,
            }
        )

    movements = apply_mapping(parsed, mapping)
    existing = set(
        session.scalars(
            select(BankMovement.dedupe_key).where(BankMovement.account_id == account.id)
        ).all()
    )
    imported = duplicates = 0
    seen: set[str] = set()
    for m in movements:
        key = dedupe_key(account.id, m["fecha_oper"], m["importe"], m["concepto"], m["saldo"])
        if key in existing or key in seen:
            duplicates += 1
            continue
        seen.add(key)
        session.add(
            BankMovement(
                account_id=account.id,
                fecha_oper=m["fecha_oper"],
                fecha_valor=m["fecha_valor"],
                concepto=m["concepto"],
                importe=m["importe"],
                saldo=m["saldo"],
                referencia1=m["referencia1"],
                referencia2=m["referencia2"],
                dedupe_key=key,
                payer_name=extract_payer(m["concepto"], m["referencia2"]),
                source_file=filename[:255],
                source_row=m["source_row"],
                raw_json=json.dumps(m["raw"], default=str),
            )
        )
        imported += 1
    session.commit()
    return {
        "ok": True,
        "code": "imported",
        "account_id": account.id,
        "account_name": account.name,
        "iban": account.iban,
        "total_rows": len(movements),
        "imported": imported,
        "duplicates": duplicates,
    }


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
    # No pisar lo confirmado a mano; sí regenerar lo meramente propuesto.
    for mov in session.scalars(query).all():
        if float(mov.importe) <= 0:
            continue
        stats["candidates"] += 1
        for old in session.scalars(
            select(BankReconciliation).where(
                BankReconciliation.movement_id == mov.id,
                BankReconciliation.status == "proposed",
            )
        ).all():
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
        if props and all(p.confidence == "alta" for p in props):
            _confirm_rows(session, mov, props, user_id)
            n += 1
    session.commit()
    return n


# --- exportación -------------------------------------------------------------------


def export_statement_xlsx(
    session: Session,
    *,
    account_id: str,
    desde: date | None = None,
    hasta: date | None = None,
) -> bytes:
    """Devuelve un .xlsx NUEVO (nunca se modifica el original) con el formato
    del extracto: misma cabecera (cuenta/divisa/titular), mismas columnas y
    orden, y FACTURA/PRESUPUESTO/PEDIDO rellenas con lo CONFIRMADO. Varias
    facturas en un movimiento → separadas por coma."""
    from openpyxl import Workbook  # noqa: PLC0415

    acc = session.get(BankAccount, account_id)
    if acc is None:
        raise LookupError("cuenta no encontrada")
    header = json.loads(acc.statement_header_json) if acc.statement_header_json else {}
    columns: list[str] = header.get("columns") or list(SABADELL_MAPPING.values())
    mapping = (
        json.loads(acc.column_mapping_json) if acc.column_mapping_json else dict(SABADELL_MAPPING)
    )

    q = select(BankMovement).where(BankMovement.account_id == acc.id)
    if desde:
        q = q.where(BankMovement.fecha_oper >= desde)
    if hasta:
        q = q.where(BankMovement.fecha_oper <= hasta)
    rows = session.scalars(
        q.order_by(BankMovement.fecha_oper.desc(), BankMovement.source_row.asc())
    ).all()
    ids = [m.id for m in rows]
    confirmed: dict[str, list[str]] = {}
    if ids:
        for r in session.scalars(
            select(BankReconciliation).where(
                BankReconciliation.movement_id.in_(ids),
                BankReconciliation.status == "confirmed",
            )
        ).all():
            confirmed.setdefault(r.movement_id, []).append(r.numero)

    wb = Workbook()
    ws = wb.active
    ws.title = "Extracto"
    for line in header.get("lines") or [
        f"Cuenta: {acc.iban}",
        f"Divisa: {acc.currency}",
        "Titular:",
    ]:
        ws.append([line])
    ws.append([])
    ws.append(columns)
    factura_col = mapping.get("factura", "FACTURA")
    for m in rows:
        raw = json.loads(m.raw_json) if m.raw_json else {}
        out_row = []
        for col in columns:
            value = raw.get(col)
            if col.strip().upper() == str(factura_col).strip().upper() and m.id in confirmed:
                value = ", ".join(confirmed[m.id])
            out_row.append(value)
        ws.append(out_row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


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
