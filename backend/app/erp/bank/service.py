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
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.erp.bank.matching import (
    AMOUNT_TOL,
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
    Identidades,
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
        # La hoja de Drive de la cuenta.
        "drive_file_id": a.drive_file_id,
        "drive_pestana": a.drive_pestana,
        "drive_escribir": bool(a.drive_escribir),
        "drive_formato": a.drive_formato,
        "drive_leida_at": a.drive_leida_at.isoformat() if a.drive_leida_at else None,
        "drive_resultado": json.loads(a.drive_resultado_json) if a.drive_resultado_json else None,
    }


def account_dict(session: Session, a: BankAccount) -> dict[str, Any]:
    """`account_to_dict` con el nombre de la contrapartida ya resuelto."""
    return account_to_dict(a, contrapartida_names(session))


def list_accounts(session: Session) -> list[dict[str, Any]]:
    rows = session.scalars(select(BankAccount).order_by(BankAccount.name)).all()
    names = contrapartida_names(session)
    pendientes = dict(session.execute(
        select(BankMovement.account_id, func.count(BankMovement.id))
        .where(BankMovement.hoja_estado == HOJA_PENDIENTE)
        .group_by(BankMovement.account_id)
    ).all())
    return [
        {**account_to_dict(a, names), "drive_pendientes": int(pendientes.get(a.id, 0))}
        for a in rows
    ]


#: El identificador de un fichero de Drive dentro de su enlace
#: (`/d/<id>/`, `/file/d/<id>`, `?id=<id>`).
_DRIVE_ID_RE = re.compile(r"(?:/d/|[?&]id=)([A-Za-z0-9_-]{20,})")


def drive_file_id(value: Any) -> str | None:
    """El identificador del fichero: tal cual o sacado del enlace que se pega
    desde el navegador. Vacío → None."""
    raw = str(value or "").strip()
    if not raw:
        return None
    match = _DRIVE_ID_RE.search(raw)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,128}", raw):
        return raw
    raise ValueError(
        "Eso no parece el enlace ni el identificador de un fichero de Drive: copia el "
        "enlace de la hoja (Compartir → Copiar enlace) o el código que va tras /d/."
    )


def _datos_drive(acc: BankAccount, data: dict[str, Any]) -> None:
    if "drive_file_id" in data:
        nuevo = drive_file_id(data["drive_file_id"])
        if nuevo != acc.drive_file_id:
            # Otra hoja (p. ej. la convertida a hoja de Google): lo que se
            # supiera de la anterior no vale.
            acc.drive_formato = None
            acc.drive_leida_at = None
            acc.drive_resultado_json = None
        acc.drive_file_id = nuevo
    if "drive_pestana" in data:
        acc.drive_pestana = str(data["drive_pestana"] or "").strip()[:120] or None
    if "drive_escribir" in data and data["drive_escribir"] is not None:
        acc.drive_escribir = bool(data["drive_escribir"])


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
    _datos_drive(acc, data)
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
    hoja_antes = (acc.drive_file_id, acc.drive_pestana)
    _datos_drive(acc, data)
    ahora = datetime.now(UTC).replace(tzinfo=None)
    if not acc.drive_file_id or not acc.drive_escribir:
        # Sin hoja, o sin escribir en ella: lo pendiente ya no se escribe.
        for mov in session.scalars(select(BankMovement).where(
            BankMovement.account_id == acc.id, BankMovement.hoja_estado == HOJA_PENDIENTE,
        )):
            mov.hoja_estado, mov.hoja_estado_at = None, ahora
    elif (acc.drive_file_id, acc.drive_pestana) != hoja_antes:
        # Otra hoja (la convertida a hoja de Google) u otra pestaña: lo que no
        # se pudo escribir en la anterior se vuelve a intentar en esta.
        for mov in session.scalars(select(BankMovement).where(
            BankMovement.account_id == acc.id,
            BankMovement.status == "reconciled",
            BankMovement.hoja_estado.in_(_HOJA_REINTENTAR_AL_CAMBIAR),
        )):
            mov.hoja_estado, mov.hoja_estado_at = HOJA_PENDIENTE, ahora
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


def movimientos_de_la_cuenta(
    lectura: Lectura, account: BankAccount,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Los movimientos leídos que son de esta cuenta y, aparte, las filas que
    dicen ser de OTRA (Belfius repite la cuenta en cada fila): esas no se
    importan en esta, se enseñan."""
    movimientos: list[dict[str, Any]] = []
    otras: list[dict[str, Any]] = []
    for m in lectura.movimientos:
        propia = numero_de_cuenta(m["cuenta"]) or m["cuenta"] if m["cuenta"] else None
        if propia and not cuenta_casa(propia, account.iban):
            otras.append({
                "fila": m["source_row"],
                "motivo": f"es de otra cuenta ({m['cuenta']})",
                "valores": [v for v in (texto(x) for x in m["raw"].values()) if v],
            })
            continue
        movimientos.append(m)
    return movimientos, otras


def claves_de_movimientos(account_id: str, movimientos: list[dict[str, Any]]) -> list[str]:
    """La identidad de cada movimiento leído, en el orden del fichero: la
    clave anti-duplicados del importador. No depende de la fila: la misma
    hoja con filas nuevas por arriba da las mismas claves."""
    repeticiones: Counter[str] = Counter()
    claves: list[str] = []
    for m in movimientos:
        datos = (account_id, m["fecha_oper"], m["importe"], m["concepto"], m["saldo"])
        if m["saldo"] is not None:
            # Con saldo, dos filas idénticas son la misma (un solape al juntar
            # exportaciones): la segunda es un duplicado.
            claves.append(dedupe_key(*datos))
        elif m["referencia1"]:
            # Sin saldo (Belfius), el nº de transacción distingue dos iguales.
            claves.append(dedupe_key(*datos, referencia=m["referencia1"]))
        else:
            clave_base = dedupe_key(*datos)
            repeticiones[clave_base] += 1
            claves.append(dedupe_key(*datos, repeticion=repeticiones[clave_base]))
    return claves


def datos_de_identidad(mov: BankMovement | Any) -> dict[str, Any]:
    """Lo que `mismo_movimiento` mira de un movimiento guardado."""
    return {
        "fecha_oper": mov.fecha_oper, "importe": mov.importe, "saldo": mov.saldo,
        "concepto": mov.concepto, "referencia1": mov.referencia1,
    }


def _identidades_sin_clave(
    session: Session, account_id: str, existing: dict[str, str | None], claves: list[str],
) -> Identidades | None:
    """Los movimientos de la cuenta que ninguna clave del fichero ha casado,
    para reconocer por `mismo_movimiento` los que traen otra clave. None si
    todas casan (lo normal: no hace falta cargar nada)."""
    if all(c in existing for c in claves):
        return None
    casados = {existing[c] for c in claves if c in existing}
    filas = session.execute(
        select(BankMovement.id, BankMovement.fecha_oper, BankMovement.importe,
               BankMovement.saldo, BankMovement.concepto, BankMovement.referencia1)
        .where(BankMovement.account_id == account_id)
    ).all()
    return Identidades([(f.id, datos_de_identidad(f)) for f in filas if f.id not in casados])


def _anotacion_nueva(valor: str | None, de_bohub: str | None) -> str | None:
    """Lo que trae la hoja en `factura`/`pedido`, salvo que sea lo que BoHub
    escribió ahí: eso no es una anotación de Bart."""
    if not valor:
        return None
    if de_bohub and valor.strip() == de_bohub.strip():
        return None
    return valor


def import_statement(
    session: Session,
    *,
    content: bytes,
    filename: str,
    account_id: str | None = None,
    user_id: str | None = None,
    pestana: str | None = None,
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
    después, con el casado de siempre. `pestana`: la del libro que se lee (la
    primera si no se dice)."""
    parsed = read_statement(
        content, filename, extra=_formatos_de_cuentas(session, account_id), pestana=pestana,
    )
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

    movimientos, otras = movimientos_de_la_cuenta(lectura, account)
    no_entendidas = sorted([*lectura.no_entendidas, *otras], key=lambda x: x["fila"])

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
    nuevos: list[BankMovement] = []
    reanotados: list[BankMovement] = []
    claves = claves_de_movimientos(account.id, movimientos)
    identidades = _identidades_sin_clave(session, account.id, existing, claves)
    for m, key in zip(movimientos, claves, strict=True):
        mov_id: str | None = None
        if key in existing:
            mov_id = existing[key]
        elif identidades is not None:
            # La clave cambia si cambia el concepto (la hoja de Bart lo trae
            # cortado y el export del banco entero): mismo movimiento igual.
            mov_id = identidades.buscar(m)
        if key in existing or mov_id is not None:
            duplicates += 1
            # Bart anota el extracto y lo vuelve a subir: lo que ahora trae
            # anotado y antes no, se guarda y se casa. Lo que escribió BoHub
            # en la hoja al conciliar no cuenta: no es suyo.
            mov = session.get(BankMovement, mov_id) if mov_id else None
            if mov is None or mov.anotacion_factura or mov.anotacion_pedido:
                continue
            factura = _anotacion_nueva(m["factura"], mov.hoja_factura)
            pedido = _anotacion_nueva(m["pedido"], mov.hoja_pedido)
            if factura or pedido:
                mov.anotacion_factura = _corta(factura)
                mov.anotacion_pedido = _corta(pedido)
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
        "recolocadas": lectura.recolocadas,
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


def _con_prepago(session: Session, invoices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Marca `prepago` en las facturas cuyo pedido de BoHub consta pagado: su
    factura posterior al cobro es lo normal (`matching._posterior`)."""
    from app.erp.models import Order  # noqa: PLC0415

    claves = {(int(i["serie"]), int(i["codigo"])) for i in invoices
              if i.get("serie") is not None and i.get("codigo") is not None}
    if not claves:
        return invoices
    pagados: set[tuple[int, int]] = set()
    # El número se guarda como el código («260156») o como el visible
    # («5-260156»): valen los dos.
    numeros = {str(c) for _, c in claves} | {f"{se}-{c:06d}" for se, c in claves}
    for order in session.scalars(select(Order).where(
        Order.factusol_invoice_number.in_(numeros),
        Order.payment_status == "paid",
    )):
        numero = str(order.factusol_invoice_number or "")
        try:
            if "-" in numero:
                serie_txt, codigo_txt = numero.split("-", 1)
                clave = (int(serie_txt), int(codigo_txt))
            else:
                clave = (int(order.factusol_invoice_serie), int(numero))
        except (TypeError, ValueError):
            continue
        if clave in claves:
            pagados.add(clave)
    return [{**i, "prepago": (int(i["serie"]), int(i["codigo"])) in pagados}
            if i.get("serie") is not None and i.get("codigo") is not None else i
            for i in invoices]


def run_matching(
    session: Session,
    invoices: list[dict[str, Any]],
    *,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Genera PROPUESTAS para los movimientos pendientes de entrada. Nunca
    confirma nada. Reemplaza las propuestas anteriores no confirmadas."""
    patterns, payer_map = _rules_snapshot(session)
    invoices = _con_prepago(session, invoices)
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
        "fecha_valor": m.fecha_valor.isoformat() if m.fecha_valor else None,
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
        "hoja_estado": m.hoja_estado,
        "hoja_estado_at": m.hoja_estado_at.isoformat() if m.hoja_estado_at else None,
        "hoja_factura": m.hoja_factura,
        "hoja_pedido": m.hoja_pedido,
        "confidence": conf,
        "proposals": [_recon_dict(x) for x in proposed],
        "reconciled": [_recon_dict(x) for x in confirmed],
    }


def _ocultar_externas(item: dict[str, Any]) -> None:
    """Sin «incluir facturas externas»: las propuestas contra una factura sin
    pedido de BoHub no se ofrecen. Se quitan de `proposals`, se cuentan en
    `externas_ocultas` (para decir que las hay) y la confianza sale de lo que
    queda. Lo conciliado (`reconciled`) no se toca nunca."""
    visibles = [p for p in item["proposals"] if p.get("pedido")]
    item["externas_ocultas"] = len(item["proposals"]) - len(visibles)
    item["proposals"] = visibles
    item["confidence"] = visibles[0]["confidence"] if visibles else None


def _solo_externas(item: dict[str, Any]) -> bool:
    """Por conciliar, sin nada que ofrecer y con alguna factura externa que
    encaja: lo que hay pero se ha decidido no ver."""
    return item["status"] == "pending" and not item["proposals"] and item["externas_ocultas"] > 0


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
    movement_id: str | None = None,
    ejercicio: str | None = None,
    externas: bool = True,
    solo_externas: bool = False,
) -> dict[str, Any]:
    """Los movimientos, con sus propuestas y lo conciliado. `ejercicio`: solo
    los de ese año (fecha de operación); los contadores, también. Sin
    `externas`, las propuestas solo ofrecen facturas con pedido de BoHub
    (`_ocultar_externas`) y `counters.solo_externas` dice cuántos movimientos
    por conciliar solo casan con facturas externas; `solo_externas` deja la
    lista en esos. El extracto se enseña entero: ningún movimiento se esconde
    por la casilla."""
    from app.erp.ejercicio import limites  # noqa: PLC0415

    rango = limites(ejercicio) if ejercicio and not movement_id else None
    q = select(BankMovement)
    if movement_id:
        # «Ver el movimiento» desde la ficha del pedido: solo ese.
        q = q.where(BankMovement.id == movement_id)
    if account_id:
        q = q.where(BankMovement.account_id == account_id)
    if rango:
        q = q.where(BankMovement.fecha_oper.between(*rango))
    if desde:
        q = q.where(BankMovement.fecha_oper >= desde)
    if hasta:
        q = q.where(BankMovement.fecha_oper <= hasta)
    if status and not solo_externas:
        q = q.where(BankMovement.status == status)
    if only_incoming:
        q = q.where(BankMovement.importe > 0)
    rows = session.scalars(
        q.order_by(BankMovement.fecha_oper.desc(), BankMovement.source_row.asc())
    ).all()
    items = _con_propuestas(session, rows, externas=externas)
    if solo_externas:
        # El contador pulsado: los mismos que cuenta (por conciliar, sin
        # propuesta visible), sin que la confianza los vacíe.
        items = [i for i in items if _solo_externas(i)]
        confidence = None
    if confidence:
        if confidence == "none":
            items = [i for i in items if i["status"] == "pending" and not i["proposals"]]
        else:
            items = [i for i in items if i["confidence"] == confidence]
    total = len(items)
    # Contadores globales (misma cuenta y ejercicio, sin filtro de estado ni
    # confianza): el extracto entero, pase lo que pase con la casilla.
    base = select(BankMovement).where(BankMovement.importe > 0)
    if account_id:
        base = base.where(BankMovement.account_id == account_id)
    if rango:
        base = base.where(BankMovement.fecha_oper.between(*rango))
    all_in = session.scalars(base).all()
    counters = {
        "pending": sum(1 for m in all_in if m.status == "pending"),
        "reconciled": sum(1 for m in all_in if m.status == "reconciled"),
        "discarded": sum(1 for m in all_in if m.status == "discarded"),
        "pending_amount": round(sum(float(m.importe) for m in all_in if m.status == "pending"), 2),
        "solo_externas": 0 if externas else sum(
            1 for i in _con_propuestas(
                session, [m for m in all_in if m.status == "pending"], externas=False,
            ) if _solo_externas(i)
        ),
    }
    return {"items": items[offset : offset + limit], "total": total, "counters": counters}


def ejercicios_de_movimientos(
    session: Session, *, account_id: str | None = None,
) -> list[dict[str, Any]]:
    """Los años que tienen movimientos de entrada (los que lista Conciliación),
    con su recuento por estado, del más reciente al más antiguo. El extracto
    va más atrás que FACTUSOL: aquí manda lo que hay en el banco, no los
    ejercicios que tiene FACTUSOL."""
    anio = func.extract("year", BankMovement.fecha_oper)
    q = (select(anio, BankMovement.status, func.count(BankMovement.id),
                func.coalesce(func.sum(BankMovement.importe), 0))
         .where(BankMovement.importe > 0)
         .group_by(anio, BankMovement.status))
    if account_id:
        q = q.where(BankMovement.account_id == account_id)
    por_anio: dict[int, dict[str, Any]] = {}
    for a, estado, n, suma in session.execute(q):
        fila = por_anio.setdefault(int(a), {
            "anio": str(int(a)), "movimientos": 0, "pending": 0, "reconciled": 0,
            "discarded": 0, "pending_amount": 0.0,
        })
        fila["movimientos"] += int(n)
        if estado in ("pending", "reconciled", "discarded"):
            fila[estado] += int(n)
        if estado == "pending":
            fila["pending_amount"] = round(fila["pending_amount"] + float(suma), 2)
    return [por_anio[a] for a in sorted(por_anio, reverse=True)]


def _con_propuestas(
    session: Session, rows: list[BankMovement], *, externas: bool,
) -> list[dict[str, Any]]:
    ids = [m.id for m in rows]
    recons_by_mov: dict[str, list[BankReconciliation]] = {}
    if ids:
        for r in session.scalars(
            select(BankReconciliation).where(BankReconciliation.movement_id.in_(ids))
        ).all():
            recons_by_mov.setdefault(r.movement_id, []).append(r)
    items = [movement_to_dict(m, recons_by_mov.get(m.id, [])) for m in rows]
    # El camino de vuelta: de cada factura conciliada o propuesta, su pedido.
    pedidos = _pedidos_con_id(session, {
        (r.serie, r.codigo) for rs in recons_by_mov.values() for r in rs
    })
    for item in items:
        for r in (*item["proposals"], *item["reconciled"]):
            r["pedido"] = pedidos.get((r["serie"], r["codigo"]))
        if externas:
            item["externas_ocultas"] = 0
        else:
            _ocultar_externas(item)
    return items


# --- decisiones (la persona decide) -----------------------------------------------


def _mov(session: Session, movement_id: str) -> BankMovement:
    mov = session.get(BankMovement, movement_id)
    if mov is None:
        raise LookupError("movimiento no encontrado")
    return mov


#: `BankMovement.hoja_estado`: conciliado por una persona, falta escribirlo
#: en la hoja de Drive de la cuenta.
HOJA_PENDIENTE = "pendiente"
#: Lo que se vuelve a intentar si la cuenta pasa a otra hoja o pestaña.
_HOJA_REINTENTAR_AL_CAMBIAR = ("ocupada", "no_encontrada", "corrida", "sin_columna")


def _marcar_para_hoja(session: Session, mov: BankMovement) -> None:
    """Si la cuenta tiene su hoja de Drive y se escribe en ella, el
    movimiento queda pendiente de escribir (lo hace `drive.escribir_pendientes`,
    encolado al confirmar y en el tic diario)."""
    acc = session.get(BankAccount, mov.account_id)
    if acc is not None and acc.drive_file_id and acc.drive_escribir:
        mov.hoja_estado = HOJA_PENDIENTE
        mov.hoja_estado_at = datetime.now(UTC).replace(tzinfo=None)


def _desmarcar_para_hoja(mov: BankMovement) -> None:
    """Reabierto o descartado: lo que aún no se había escrito ya no se
    escribe. Lo ya escrito se queda en la hoja (BoHub nunca borra)."""
    if mov.hoja_estado == HOJA_PENDIENTE:
        mov.hoja_estado = None
        mov.hoja_estado_at = datetime.now(UTC).replace(tzinfo=None)


def _confirm_rows(
    session: Session, mov: BankMovement, rows: list[BankReconciliation], user_id: str | None
) -> None:
    """Lo confirma una persona («Confirmar», «Elegir / repartir», «Confirmar
    todas las de confianza alta»): queda conciliado y, si la cuenta tiene su
    hoja de Drive, pendiente de escribir en ella."""
    now = datetime.now(UTC).replace(tzinfo=None)
    for r in rows:
        r.status = "confirmed"
        r.confirmed_by_user_id = user_id
        r.confirmed_at = now
    mov.status = "reconciled"
    mov.discard_reason = None
    _marcar_para_hoja(session, mov)


def _son_alternativas(mov: BankMovement, props: list[BankReconciliation]) -> bool:
    """Varias propuestas que suman MÁS que el movimiento no son un reparto:
    son facturas candidatas para el mismo cobro (una transferencia de 100 €
    con tres facturas de 100 € del mismo cliente). Confirmarlas todas daría
    por cobradas tres facturas con un pago."""
    total = round(sum(float(p.importe) for p in props), 2)
    return len(props) > 1 and total > round(float(mov.importe), 2) + 0.01


def confirm_movement(
    session: Session, movement_id: str, user_id: str | None, *, externas: bool = True,
) -> dict[str, Any]:
    """Confirma la PROPUESTA actual. Sin propuesta → error (no se inventa).
    Sin `externas`, nada contra una factura sin pedido: si la propuesta lleva
    alguna (que en pantalla no se ve), no se confirma y se dice."""
    mov = _mov(session, movement_id)
    props = session.scalars(
        select(BankReconciliation).where(
            BankReconciliation.movement_id == mov.id,
            BankReconciliation.status == "proposed",
        )
    ).all()
    if not props:
        raise ValueError("este movimiento no tiene propuesta que confirmar")
    if not externas:
        con_pedido = _pedidos_con_id(session, {(p.serie, p.codigo) for p in props})
        if any((p.serie, p.codigo) not in con_pedido for p in props):
            raise ValueError(
                "la propuesta incluye facturas externas (solo FACTUSOL, sin pedido), que no se "
                "están enseñando: enciende «Incluir facturas externas» para verlas, o elige "
                "con «Elegir / repartir»"
            )
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
    reason: str = "elegida a mano",
    origen: str | None = None,
) -> dict[str, Any]:
    """Elegir otra factura o REPARTIR entre varias. Sustituye la propuesta y
    queda confirmado (es una decisión explícita). `learn_payer` recuerda
    pagador → cliente para futuras importaciones. `reason`/`origen`: de dónde
    salió la decisión (la ficha del pedido usa los suyos)."""
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
                reason=reason,
                status="proposed",
                origen=origen,
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
    _desmarcar_para_hoja(mov)
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
    _desmarcar_para_hoja(mov)
    session.commit()
    return movement_to_dict(mov, [])


def confirm_all_high(
    session: Session, user_id: str | None, *, account_id: str | None = None,
    externas: bool = True, ejercicio: str | None = None,
) -> int:
    """Acción EN BLOQUE (un clic de Bart): confirma las propuestas de confianza
    ALTA. Sigue siendo una decisión suya, no automática. Sin `externas`, un
    movimiento con alguna propuesta contra una factura sin pedido no entra:
    lo que no se ve en pantalla no se confirma. Con `ejercicio`, solo los
    movimientos de ese año (los que se ven)."""
    from app.erp.ejercicio import limites  # noqa: PLC0415

    q = select(BankMovement).where(BankMovement.status == "pending")
    if account_id:
        q = q.where(BankMovement.account_id == account_id)
    if ejercicio:
        q = q.where(BankMovement.fecha_oper.between(*limites(ejercicio)))
    n = 0
    for mov in session.scalars(q).all():
        props = session.scalars(
            select(BankReconciliation).where(
                BankReconciliation.movement_id == mov.id,
                BankReconciliation.status == "proposed",
            )
        ).all()
        if props and not externas:
            con_pedido = _pedidos_con_id(session, {(p.serie, p.codigo) for p in props})
            if any((p.serie, p.codigo) not in con_pedido for p in props):
                continue
        if props and all(p.confidence == "alta" for p in props) and not _son_alternativas(
            mov, props,
        ):
            _confirm_rows(session, mov, props, user_id)
            n += 1
    session.commit()
    return n


# --- desde la ficha del pedido: el movimiento que pagó su factura ---------------------

#: `BankReconciliation.origen` de lo conciliado desde la ficha del pedido.
ORIGEN_PEDIDO = "pedido"
#: Hasta cuántos movimientos candidatos se enseñan en la ficha.
MAX_CANDIDATOS_PEDIDO = 5
_ORDEN_CONFIANZA = {"alta": 0, "media": 1, "baja": 2}
#: Margen al buscar desde la fecha del pedido: una transferencia ordenada el
#: día antes del pedido web también vale.
_DIAS_ANTES_DEL_PEDIDO = 3


def _texto_para_facturas(mov: BankMovement) -> str:
    """El concepto, las referencias y lo anotado, sin los números de OTROS
    documentos («PROFORMA 526090», «pedido 1234»): esos no son facturas."""
    from app.erp.bank.parsing import _OTRO_DOCUMENTO_RE  # noqa: PLC0415

    texto_mov = " ".join(
        x for x in (mov.concepto, mov.referencia1, mov.referencia2, mov.anotacion_factura) if x
    )
    return _OTRO_DOCUMENTO_RE.sub(" ", texto_mov)


def _numero_en_movimiento(mov: BankMovement, serie: int, codigo: int) -> bool:
    """¿El concepto, las referencias o lo anotado nombran esta factura?
    «2-526081», o el código suelto «526081» (el que suelen copiar los
    clientes)."""
    from app.erp.bank.parsing import _CODIGO_SUELTO_RE, _FACTURA_RE  # noqa: PLC0415

    texto_mov = _texto_para_facturas(mov)
    for m in _FACTURA_RE.finditer(texto_mov):
        if int(m.group(1)) == serie and int(m.group(2)) == codigo:
            return True
    return any(int(m.group(1)) == codigo for m in _CODIGO_SUELTO_RE.finditer(texto_mov))


def _nombra_otra_factura(mov: BankMovement, serie: int, codigo: int) -> bool:
    """¿El movimiento nombra OTRA factura de la misma serie y no esta? («2-526081»
    o el código suelto de seis cifras con el mismo comienzo). Entonces es el
    cobro de esa otra: no se propone para esta solo por el importe."""
    from app.erp.bank.parsing import _CODIGO_SUELTO_RE, _FACTURA_RE  # noqa: PLC0415

    texto_mov = _texto_para_facturas(mov)
    for m in _FACTURA_RE.finditer(texto_mov):
        if int(m.group(1)) == serie and int(m.group(2)) != codigo:
            return True
    prefijo = str(codigo)[:2]
    return any(
        m.group(1) != str(codigo) and m.group(1)[:2] == prefijo and len(str(codigo)) == 6
        for m in _CODIGO_SUELTO_RE.finditer(texto_mov)
    )


def movimiento_pedido_dict(
    mov: BankMovement, acc: BankAccount | None, contrapartidas: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Lo que la ficha del pedido enseña de un movimiento: fecha valor (la del
    cobro), importe, cuenta con su contrapartida, y el concepto."""
    fecha_valor = mov.fecha_valor or mov.fecha_oper
    return {
        "id": mov.id,
        "fecha_oper": mov.fecha_oper.isoformat(),
        "fecha_valor": fecha_valor.isoformat(),
        "importe": float(mov.importe),
        "concepto": mov.concepto,
        "payer_name": mov.payer_name,
        "status": mov.status,
        "cuenta": {
            "id": acc.id if acc else mov.account_id,
            "nombre": acc.name if acc else None,
            "contrapartida_codigo": acc.contrapartida_codigo if acc else None,
            "contrapartida_nombre": (
                resolve_name(contrapartidas or {}, acc.contrapartida_codigo) if acc else None
            ),
        },
    }


def movimientos_de_factura(
    session: Session, factura: dict[str, Any], *, desde: date | None = None,
) -> dict[str, Any]:
    """El movimiento del banco que pagó una factura, visto desde su pedido.

    `factura` = {serie, codigo, numero, total, saldo_pendiente, cliente_nombre,
    cliente_codigo}; `desde` = la fecha del pedido (un cobro no llega antes).

    - `conciliados`: los movimientos ya conciliados con esta factura (en
      Conciliación o desde la ficha).
    - `candidatos`: movimientos de entrada aún por conciliar que pueden ser su
      cobro, con su confianza y el porqué. Primero, los que nombran la factura
      en el concepto; después, lo que ya propuso Conciliación con todas las
      facturas a la vista; y si no, el casador de siempre contra esta
      factura (importe exacto + nombre del cliente, parcial…). Lo que solo
      coincide en el importe sale en confianza baja y solo si no hay nada
      mejor. Solo cuentas de la serie de la factura (o sin serie).

    Nada se concilia aquí: solo se propone."""
    serie, codigo = int(factura["serie"]), int(factura["codigo"])
    total = float(factura.get("total") or 0)
    saldo = factura.get("saldo_pendiente")
    saldo = float(saldo) if saldo is not None else total
    nombres_cp = contrapartida_names(session)
    filas = session.execute(
        select(BankReconciliation, BankMovement, BankAccount)
        .join(BankMovement, BankMovement.id == BankReconciliation.movement_id)
        .join(BankAccount, BankAccount.id == BankMovement.account_id)
        .where(BankReconciliation.serie == serie, BankReconciliation.codigo == codigo)
    ).all()
    conciliados: list[dict[str, Any]] = []
    guardadas: dict[str, BankReconciliation] = {}
    for rec, mov, acc in filas:
        if rec.status == "confirmed":
            conciliados.append({
                **movimiento_pedido_dict(mov, acc, nombres_cp),
                "importe_conciliado": float(rec.importe),
                "origen": rec.origen,
            })
        elif mov.status == "pending":
            guardadas[mov.id] = rec

    patterns, payer_map = _rules_snapshot(session)
    inv = {
        "serie": serie, "codigo": codigo, "numero": factura.get("numero"),
        "cliente_codigo": factura.get("cliente_codigo"),
        "cliente_nombre": factura.get("cliente_nombre"),
        # Sin fecha: la de aquí sería la del PEDIDO, no la de la factura (la
        # ventana ya la pone la consulta: desde unos días antes del pedido).
        "fecha": None,
        "saldo_pendiente": saldo,
    }
    q = (
        select(BankMovement, BankAccount)
        .join(BankAccount, BankAccount.id == BankMovement.account_id)
        .where(BankMovement.status == "pending", BankMovement.importe > 0)
        .where(or_(BankAccount.serie == serie, BankAccount.serie.is_(None)))
    )
    if desde:
        q = q.where(BankMovement.fecha_oper >= desde - timedelta(days=_DIAS_ANTES_DEL_PEDIDO))
    candidatos: list[dict[str, Any]] = []
    for mov, acc in session.execute(q):
        if is_excluded(mov.concepto, mov.payer_name, patterns):
            continue
        importe = float(mov.importe)
        exacto = abs(importe - saldo) <= AMOUNT_TOL
        solo_importe = False
        if _numero_en_movimiento(mov, serie, codigo):
            confianza = "alta" if exacto else "media"
            motivo = "número de factura en el concepto" + (" + importe exacto" if exacto else "")
        elif mov.id in guardadas and guardadas[mov.id].confidence:
            confianza = guardadas[mov.id].confidence
            motivo = guardadas[mov.id].reason or "propuesto en Conciliación"
        elif _nombra_otra_factura(mov, serie, codigo):
            continue
        else:
            props = propose({
                "importe": importe, "fecha_oper": mov.fecha_oper, "concepto": mov.concepto,
                "referencia2": mov.referencia2, "payer": mov.payer_name,
            }, [inv], payer_map)
            if not props or (props[0]["serie"], props[0]["codigo"]) != (serie, codigo):
                continue
            confianza, motivo = props[0]["confidence"], props[0]["reason"]
            if motivo.startswith("importe exacto (sin confirmación"):
                # Contra UNA factura, coincidir solo en el importe dice poco.
                confianza, motivo, solo_importe = "baja", "solo coincide el importe", True
        candidatos.append({
            **movimiento_pedido_dict(mov, acc, nombres_cp),
            "confianza": confianza,
            "motivo": motivo,
            "parcial": importe < saldo - AMOUNT_TOL,
            "excede": importe > saldo + AMOUNT_TOL,
            "_solo_importe": solo_importe,
        })
    # Lo ya conciliado cubre la factura: no hay más cobro que buscar. Y con
    # algo conciliado, coincidir solo en el importe no basta.
    cubierto = sum(float(c["importe_conciliado"]) for c in conciliados)
    if conciliados and cubierto >= total - AMOUNT_TOL:
        candidatos = []
    elif conciliados or any(not c["_solo_importe"] for c in candidatos):
        candidatos = [c for c in candidatos if not c["_solo_importe"]]
    candidatos.sort(key=lambda c: (_ORDEN_CONFIANZA.get(c["confianza"], 3), c["fecha_valor"]))
    for c in candidatos:
        c.pop("_solo_importe")
    return {
        "factura": {"serie": serie, "codigo": codigo, "numero": factura.get("numero"),
                    "total": total, "saldo_pendiente": saldo},
        "conciliados": conciliados,
        "candidatos": candidatos[:MAX_CANDIDATOS_PEDIDO],
    }


def conciliar_desde_pedido(
    session: Session, *, movement_id: str, factura: dict[str, Any], saldo: float,
    user_id: str | None,
) -> BankMovement:
    """Concilia el movimiento con la factura del pedido, igual que desde
    Conciliación (el mismo hecho, una sola vez): si Conciliación ya lo
    proponía contra esta factura se confirma esa propuesta; si no, queda como
    elegido desde la ficha. Lanza ValueError si el movimiento ya es de otra
    factura, está descartado, o cubre más que lo pendiente (eso se reparte en
    Conciliación). Idempotente si ya estaba conciliado con esta factura."""
    mov = _mov(session, movement_id)
    serie, codigo = int(factura["serie"]), int(factura["codigo"])
    filas = session.scalars(
        select(BankReconciliation).where(BankReconciliation.movement_id == mov.id)
    ).all()
    if mov.status == "reconciled":
        if any(r.status == "confirmed" and (r.serie, r.codigo) == (serie, codigo) for r in filas):
            return mov
        raise ValueError("ese movimiento ya está conciliado con otra factura")
    if mov.status == "discarded":
        raise ValueError("ese movimiento está descartado («no es cobro»): reábrelo en Conciliación")
    importe = float(mov.importe)
    if importe > float(saldo) + AMOUNT_TOL:
        raise ValueError(
            f"el movimiento ({importe:.2f} €) es mayor que lo pendiente de la factura "
            f"({float(saldo):.2f} €): si paga varias facturas, repártelo en Conciliación"
        )
    propuestas = [r for r in filas if r.status == "proposed"]
    if (
        len(propuestas) == 1 and (propuestas[0].serie, propuestas[0].codigo) == (serie, codigo)
        and abs(float(propuestas[0].importe) - importe) <= AMOUNT_TOL
    ):
        _confirm_rows(session, mov, propuestas, user_id)
        session.commit()
        return mov
    reassign_movement(
        session, mov.id,
        [{"serie": serie, "codigo": codigo, "numero": factura.get("numero"),
          "importe": importe, "cliente_nombre": factura.get("cliente_nombre"),
          "cliente_codigo": factura.get("cliente_codigo")}],
        user_id, reason="confirmado desde la ficha del pedido", origen=ORIGEN_PEDIDO,
    )
    return mov


def conciliado_con_factura(
    session: Session, serie: int, codigo: int, *, movement_id: str | None = None,
) -> float:
    """Lo conciliado (confirmado) con la factura: de todos sus movimientos, o
    solo de uno (un movimiento repartido entre varias facturas cuenta solo su
    parte)."""
    q = select(func.coalesce(func.sum(BankReconciliation.importe), 0)).where(
        BankReconciliation.serie == int(serie), BankReconciliation.codigo == int(codigo),
        BankReconciliation.status == "confirmed",
    )
    if movement_id:
        q = q.where(BankReconciliation.movement_id == movement_id)
    return round(float(session.scalar(q) or 0), 2)


def conciliados_de_factura(
    session: Session, serie: int, codigo: int,
) -> list[tuple[BankMovement, float]]:
    """Los movimientos conciliados (confirmados) con la factura y lo que cada
    uno paga de ella."""
    out: dict[str, tuple[BankMovement, float]] = {}
    for rec, mov in session.execute(
        select(BankReconciliation, BankMovement)
        .join(BankMovement, BankMovement.id == BankReconciliation.movement_id)
        .where(
            BankReconciliation.serie == int(serie), BankReconciliation.codigo == int(codigo),
            BankReconciliation.status == "confirmed",
        )
    ):
        previo = out.get(mov.id, (mov, 0.0))[1]
        out[mov.id] = (mov, round(previo + float(rec.importe), 2))
    return list(out.values())


#: Días de margen entre la fecha del banco y la del cobro apuntado en FACTUSOL.
DIAS_COBRO_FACTUSOL = 7


def fechas_de_movimiento(mov: BankMovement) -> tuple[date, date]:
    """(fecha valor, fecha de operación) de un movimiento; sin fecha valor, la
    de operación."""
    return (mov.fecha_valor or mov.fecha_oper, mov.fecha_oper)


def misma_contrapartida(a: Any, b: Any) -> bool:
    x, y = str(a or "").strip(), str(b or "").strip()
    if x.isdigit() and y.isdigit():
        return int(x) == int(y)
    return bool(x) and x == y


def casar_lineas_de_cobro(
    movimientos: list[tuple[Any, ...]], lineas: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Qué línea de cobro de FACTUSOL corresponde a cada movimiento del banco:
    `{movement_id: línea}`. `movimientos` = (id, lo que paga de la factura,
    (fecha valor, fecha de operación)[, contrapartida de su cuenta]). Casa si
    el importe es el mismo (±0,01) y la fecha de la línea cae a ±7 días de
    alguna de las dos fechas. Cada línea vale para UN solo movimiento, y se
    reparten para que casen los más posibles: por orden de fin de su ventana,
    cada movimiento se queda con la primera línea libre que le cabe (con
    ventanas iguales, es el reparto óptimo). Primero, solo las líneas de la
    misma contrapartida que la cuenta del banco; después, cualquiera (pudo
    apuntarse a mano en otra cuenta)."""
    fechas_linea: dict[int, date] = {}
    for i, linea in enumerate(lineas):
        if linea.get("importe") is None:
            continue
        try:
            fechas_linea[i] = date.fromisoformat(str(linea.get("fecha") or "")[:10])
        except ValueError:
            continue
    margen = timedelta(days=DIAS_COBRO_FACTUSOL)
    orden = sorted(movimientos, key=lambda m: (max(m[2]) + margen, m[0]))
    usadas: set[int] = set()
    out: dict[str, dict[str, Any]] = {}
    for solo_misma_cuenta in (True, False):
        for mov in orden:
            mid, importe, fechas = mov[0], float(mov[1]), mov[2]
            cuenta = mov[3] if len(mov) > 3 else None
            if mid in out or (solo_misma_cuenta and not cuenta):
                continue
            libres = [
                i for i, dia in fechas_linea.items()
                if i not in usadas
                and round(abs(float(lineas[i]["importe"]) - importe), 2) <= AMOUNT_TOL
                and min(abs((dia - f).days) for f in fechas) <= DIAS_COBRO_FACTUSOL
                and (not solo_misma_cuenta
                     or misma_contrapartida(lineas[i].get("contrapartida"), cuenta))
            ]
            if libres:
                i = min(libres, key=lambda j: (
                    fechas_linea[j], int(lineas[j].get("linea") or 0), j))
                usadas.add(i)
                out[mid] = lineas[i]
    return out


# --- exportación -------------------------------------------------------------------


def _pedidos_con_id(
    session: Session, facturas: set[tuple[int, int]],
) -> dict[tuple[int, int], dict[str, str]]:
    """El pedido de BoHub (id y número) de cada factura, por su CODFAC y su
    serie; el primero si hay varios. Para ir del movimiento al pedido."""
    from app.erp.models import Order  # noqa: PLC0415

    if not facturas:
        return {}
    numeros = {str(codigo) for _, codigo in facturas}
    out: dict[tuple[int, int], dict[str, str]] = {}
    for order in session.scalars(
        select(Order).where(Order.factusol_invoice_number.in_(numeros))
        .order_by(Order.created_at)
    ).all():
        try:
            clave = (order.factusol_invoice_serie, int(order.factusol_invoice_number))
        except (TypeError, ValueError):
            continue
        if clave in facturas:
            out.setdefault(clave, {"id": order.id, "order_number": order.order_number})
    return out


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


def valores_conciliados(
    session: Session, movement_ids: list[str],
) -> dict[str, tuple[str, str | None]]:
    """Lo que va en `factura` y `pedido` de cada movimiento conciliado: sus
    facturas confirmadas (varias, separadas por coma) y el pedido de BoHub de
    esas facturas, si lo hay. Lo usan el Excel conciliado y la hoja de Drive."""
    confirmed: dict[str, list[BankReconciliation]] = {}
    if movement_ids:
        for r in session.scalars(
            select(BankReconciliation).where(
                BankReconciliation.movement_id.in_(movement_ids),
                BankReconciliation.status == "confirmed",
            ).order_by(BankReconciliation.serie, BankReconciliation.codigo)
        ).all():
            confirmed.setdefault(r.movement_id, []).append(r)
    pedidos = _pedidos_de_facturas(
        session, {(r.serie, r.codigo) for rs in confirmed.values() for r in rs},
    )
    out: dict[str, tuple[str, str | None]] = {}
    for mov_id, recons in confirmed.items():
        encontrados = [p for r in recons for p in pedidos.get((r.serie, r.codigo), [])]
        out[mov_id] = (
            ", ".join(r.numero for r in recons),
            ", ".join(dict.fromkeys(encontrados)) or None,
        )
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
    conciliados = valores_conciliados(session, [m.id for m in rows])

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
        valores = conciliados.get(m.id)
        out_row = []
        for col in columns:
            value = raw.get(col)
            col_norm = col.strip().upper()
            if valores and col_norm == fac_norm:
                value = valores[0]
            elif valores and col_norm == ped_norm and (value is None or not str(value).strip()):
                value = valores[1]
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
    totales: dict[tuple[int, int], float] | None = None,
    account_id: str | None = None,
    desde: date | None = None,
    hasta: date | None = None,
) -> tuple[str, dict[str, int]]:
    """El CSV de cobros para `scripts/registrar_cobros_lote.ps1` con lo
    CONCILIADO (confirmado) y no descartado: una fila por factura, con la
    contrapartida de la cuenta del banco en `cuenta`, `forma` «Transferencia»
    y la fecha valor del último de sus movimientos. No escribe en FACTUSOL: lo
    registra el script, que simula primero.

    El script no lleva importe: registra el SALDO entero de la factura. Por eso
    solo entra una factura si lo conciliado de ella que AÚN NO consta cobrado
    cubre su saldo pendiente en FACTUSOL ahora (`saldos` y `totales`, de
    `pending_invoices`): lo ya cobrado (total − saldo) se da por pagado con lo
    conciliado. Así, tras un cobro parcial registrado desde la ficha, la
    factura no vuelve a entrar por ese mismo movimiento. Sin el total de una
    factura, se da por no cobrado nada. Se dejan fuera, y se cuentan:
      - `parciales`: lo conciliado sin cobrar no llega a su saldo (regístralo a
        mano);
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
    # cobrarse en dos movimientos: entra si entre los dos llegan a su saldo),
    # y la fecha valor más reciente de los de cada factura.
    por_movimiento: dict[str, float] = {}
    por_factura: dict[tuple[int, int], float] = {}
    ultima_fecha: dict[tuple[int, int], date] = {}
    for r, mov, _acc in filas:
        por_movimiento[mov.id] = por_movimiento.get(mov.id, 0.0) + float(r.importe)
        clave = (r.serie, r.codigo)
        por_factura[clave] = por_factura.get(clave, 0.0) + float(r.importe)
        fecha_mov = mov.fecha_valor or mov.fecha_oper
        if clave not in ultima_fecha or fecha_mov > ultima_fecha[clave]:
            ultima_fecha[clave] = fecha_mov
    # Lo conciliado de cada factura SIN el filtro de cuenta y fechas: lo ya
    # cobrado (total − saldo) se descuenta de todo lo conciliado de ella, no
    # solo de lo que cae en el filtro (si no, el resto pagado en septiembre se
    # quedaría fuera al exportar solo septiembre).
    claves = list(por_factura)
    todo_conciliado: dict[tuple[int, int], float] = {}
    if totales and claves:
        for serie, codigo, suma in session.execute(
            select(BankReconciliation.serie, BankReconciliation.codigo,
                   func.sum(BankReconciliation.importe))
            .join(BankMovement, BankMovement.id == BankReconciliation.movement_id)
            .where(BankReconciliation.status == "confirmed",
                   BankMovement.status == "reconciled",
                   BankReconciliation.codigo.in_({c for _, c in claves}))
            .group_by(BankReconciliation.serie, BankReconciliation.codigo)
        ):
            todo_conciliado[(int(serie), int(codigo))] = float(suma or 0)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(COBROS_CSV_COLUMNS)
    # Pagada en varios movimientos, la fila sale del ÚLTIMO (su cuenta, su
    # fecha y sus observaciones): es el pago con el que queda cobrada. Si
    # vienen de cuentas con contrapartidas distintas, el script (una línea, una
    # cuenta) no puede registrarlo bien: se deja fuera.
    de_factura: dict[tuple[int, int], list[tuple[Any, BankMovement, BankAccount]]] = {}
    for r, mov, acc in filas:
        de_factura.setdefault((r.serie, r.codigo), []).append((r, mov, acc))
    for clave, suyas in de_factura.items():
        r, mov, acc = max(suyas, key=lambda t: (t[1].fecha_valor or t[1].fecha_oper,
                                                t[1].source_row or 0))
        if any(por_movimiento[m.id] > float(m.importe) + 0.01 for _, m, _a in suyas) or len(
            {str(a.contrapartida_codigo or "") for _, _m, a in suyas}
        ) > 1:
            stats["no_cuadran"] += 1
            continue
        if not acc.contrapartida_codigo:
            stats["sin_contrapartida"] += 1
            continue
        saldo = saldos.get(clave)
        if saldo is None:
            stats["no_pendientes"] += 1
            continue
        total = (totales or {}).get(clave)
        cobrado = max(float(total) - saldo, 0.0) if total is not None else 0.0
        sin_cobrar = todo_conciliado.get(clave, por_factura[clave]) - cobrado
        if por_factura[clave] < saldo - 0.01 or sin_cobrar < saldo - 0.01:
            stats["parciales"] += 1
            continue
        origen = "anotado en el extracto" if r.origen == ORIGEN_EXTRACTO else "conciliado en BoHub"
        concepto = re.sub(r"[^\x20-\x7e]", "?", mov.concepto or "")
        observaciones = (
            f"{_ascii(acc.name)} - {mov.fecha_oper.strftime('%d/%m/%Y')} - "
            f"{float(mov.importe):.2f} EUR - {origen} - {concepto}"
        )
        # La fecha del cobro es la fecha VALOR del movimiento (la que Bart
        # escribía a mano; sin ella, la de operación). Pagada en varios, la
        # del último: es cuando queda cobrada.
        writer.writerow([
            r.serie, r.codigo, acc.contrapartida_codigo, "Transferencia",
            ultima_fecha[clave].strftime("%d/%m/%Y"),
            observaciones[:_MAX_OBSERVACIONES],
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
