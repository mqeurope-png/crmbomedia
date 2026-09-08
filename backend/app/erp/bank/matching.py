"""ERP-F4-A — casado automático de abonos bancarios con facturas pendientes.

Regla firme: NADA se concilia solo. Este módulo PROPONE (con confianza y el
porqué); la persona decide. Preferible dejar un movimiento sin propuesta a
asociarlo a la factura equivocada.

Candidatos: solo movimientos de ENTRADA (importe > 0) no excluidos. Las
exclusiones (TPV, traspasos, anulaciones, pasarelas, devoluciones…) son
reglas CONFIGURABLES (se siembran como defaults en la tabla de reglas), no
van cableadas aquí más que como valor inicial.

Casado contra facturas con saldo pendiente (ESTFAC 0 o 1):
  1. importe exacto = saldo de UNA factura            → fuerte
  2. importe = suma de varias facturas del cliente     → una transferencia salda varias
  3. importe < saldo de una factura del cliente        → cobro parcial
  4. nombre del pagador ~ nombre del cliente (tolerante: acentos rotos,
     REFERENCIA 2 truncada a ~16, sin formas societarias)
  5. fecha: el cobro es ≥ la fecha de la factura; muy lejana baja confianza
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from datetime import date
from itertools import combinations
from typing import Any

#: Exclusiones por defecto (valor INICIAL de las reglas configurables).
DEFAULT_EXCLUSION_PATTERNS: list[str] = [
    "ABONO TPV",
    "SERVICIO DE TPV",
    "ANUL ADEUDO",
    "ANUL.ADEUDO",
    "TRASPASO",
    "SCALAPAY",
    "PAYPAL",
    "DEVOLUCION",
    "INTERES",
    "COMISION",
    "LIQUIDACION TPV",
]

#: Formas societarias que se ignoran al comparar nombres.
_LEGAL_FORMS = {
    "SL",
    "SLU",
    "SA",
    "SAU",
    "SAS",
    "SARL",
    "SRL",
    "GMBH",
    "BV",
    "LTD",
    "LLC",
    "AG",
    "SCP",
    "CB",
    "INC",
    "CORP",
    "SPA",
    "IP",
    "SLL",
    "SCCL",
    "KG",
    "OHG",
    "EURL",
    "PLC",
    "NV",
    "AB",
    "OY",
    "AS",
    "APS",
    "SRO",
    "ZOO",
    "SP",
}

#: Tolerancia de importes (redondeo bancario).
AMOUNT_TOL = 0.01
#: Máximo de facturas en una combinación (evita explosión combinatoria).
MAX_COMBO = 3
#: Máximo de días de retraso «normal» del cobro respecto a la factura; más
#: allá NO se descarta, solo baja la confianza.
STALE_DAYS = 365


def normalize_name(text: Any) -> str:
    """Normalización AGRESIVA: sin acentos, sin signos, mayúsculas, sin formas
    societarias. Los acentos «rotos» del extracto («Armb-nder») se limpian
    porque el guion cae como signo y las letras se comparan tolerantes."""
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).upper()
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    tokens = [t for t in s.split() if t and t.replace(".", "") not in _LEGAL_FORMS]
    return " ".join(tokens)


def _letters(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text)


def names_match(a: Any, b: Any) -> bool:
    """¿El pagador casa con el cliente? Tolerante a: acentos rotos (una letra
    caída), REFERENCIA 2 truncada (~16 chars → prefijo) y formas societarias."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return False
    la, lb = _letters(na), _letters(nb)
    if len(la) < 4 or len(lb) < 4:
        return False
    if la == lb:
        return True
    # Truncado (REFERENCIA 2): uno es prefijo del otro (≥ 6 letras útiles).
    short, long_ = (la, lb) if len(la) <= len(lb) else (lb, la)
    if len(short) >= 6 and long_.startswith(short):
        return True
    # Acentos rotos / una letra caída: similitud alta sobre las letras.
    if difflib.SequenceMatcher(None, la, lb).ratio() >= 0.86:
        return True
    # Mismo primer token significativo + resto muy parecido.
    ta, tb = na.split(), nb.split()
    if ta and tb and ta[0] == tb[0] and len(ta[0]) >= 4:
        rest_a, rest_b = _letters(" ".join(ta[1:])), _letters(" ".join(tb[1:]))
        if not rest_a or not rest_b:
            return True
        if difflib.SequenceMatcher(None, rest_a, rest_b).ratio() >= 0.75:
            return True
    return False


_PAYER_RE = re.compile(
    r"(?:ABONO\s+)?TRANSFERENCIA\s+(?:DE|DESDE|RECIBIDA\s+DE)\s+(.+?)\s*$",
    re.IGNORECASE,
)


def extract_payer(concepto: Any, referencia2: Any = None) -> str | None:
    """«ABONO TRANSFERENCIA DE Lorenz, Wolfgang» → «Lorenz, Wolfgang». Si el
    concepto no lo trae, REFERENCIA 2 (truncada a ~16) sirve de pista."""
    text = str(concepto or "").strip()
    match = _PAYER_RE.search(text)
    if match:
        payer = match.group(1).strip(" .-·")
        if payer:
            return payer
    ref2 = str(referencia2 or "").strip()
    return ref2 or None


def is_excluded(concepto: Any, payer: Any, patterns: list[str]) -> str | None:
    """Devuelve el patrón que excluye el movimiento (o None). Los patrones
    llegan ya de la tabla de reglas (defaults + los aprendidos)."""
    hay = normalize_name(concepto) + " " + normalize_name(payer)
    hay_raw = re.sub(r"\s+", " ", str(concepto or "").upper())
    for pattern in patterns:
        p = normalize_name(pattern)
        if p and (p in hay or pattern.upper() in hay_raw):
            return pattern
    return None


def _invoice_date(inv: dict[str, Any]) -> date | None:
    raw = inv.get("fecha")
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str) and len(raw) >= 10:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None
    return None


def _date_ok(mov_date: date, inv: dict[str, Any]) -> tuple[bool, bool]:
    """(cobro ≥ factura, cobro «reciente»). Anterior a la factura → no
    plausible; muy posterior → plausible pero baja la confianza."""
    d = _invoice_date(inv)
    if d is None:
        return True, True
    if mov_date < d:
        return False, False
    return True, (mov_date - d).days <= STALE_DAYS


def _same(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) <= AMOUNT_TOL


def _prop(inv: dict[str, Any], importe: float, confidence: str, reason: str) -> dict[str, Any]:
    return {
        "serie": int(inv["serie"]),
        "codigo": int(inv["codigo"]),
        "numero": inv.get("numero"),
        "cliente_nombre": inv.get("cliente_nombre"),
        "cliente_codigo": inv.get("cliente_codigo"),
        "importe": round(float(importe), 2),
        "confidence": confidence,
        "reason": reason,
    }


def propose(
    movement: dict[str, Any],
    invoices: list[dict[str, Any]],
    payer_client_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Propuesta(s) para un movimiento de ENTRADA. Lista vacía = sin propuesta
    razonable (mejor que inventar). Varias filas = reparto entre facturas.

    `payer_client_map`: {pagador normalizado → CODCLI} aprendido de decisiones
    anteriores; si aplica, restringe (y refuerza) al cliente aprendido."""
    amount = round(float(movement.get("importe") or 0), 2)
    if amount <= 0:
        return []
    mov_date = movement.get("fecha_oper")
    payer = movement.get("payer") or extract_payer(
        movement.get("concepto"),
        movement.get("referencia2"),
    )
    pending = [
        inv
        for inv in invoices
        if (inv.get("saldo_pendiente") or 0) > AMOUNT_TOL
        and (mov_date is None or _date_ok(mov_date, inv)[0])
    ]
    if not pending:
        return []

    learned_client = None
    if payer_client_map and payer:
        learned_client = payer_client_map.get(normalize_name(payer))

    def name_ok(inv: dict[str, Any]) -> bool:
        if learned_client and str(inv.get("cliente_codigo") or "") == str(learned_client):
            return True
        return bool(payer) and names_match(payer, inv.get("cliente_nombre"))

    def fresh(inv: dict[str, Any]) -> bool:
        return mov_date is None or _date_ok(mov_date, inv)[1]

    # 1) importe exacto contra UNA factura.
    exact = [inv for inv in pending if _same(inv["saldo_pendiente"], amount)]
    exact_named = [inv for inv in exact if name_ok(inv)]
    if len(exact_named) == 1:
        inv = exact_named[0]
        conf = "alta" if fresh(inv) else "media"
        return [
            _prop(
                inv,
                amount,
                conf,
                "importe exacto + nombre del cliente"
                + ("" if fresh(inv) else " (cobro muy posterior a la factura)"),
            )
        ]
    if len(exact_named) > 1:
        return [
            _prop(inv, amount, "baja", "importe exacto y nombre, pero varias facturas candidatas")
            for inv in exact_named[:3]
        ]
    if len(exact) == 1:
        return [_prop(exact[0], amount, "media", "importe exacto (sin confirmación de nombre)")]
    if len(exact) > 1:
        return [
            _prop(inv, amount, "baja", "solo coincide el importe; varias facturas candidatas")
            for inv in exact[:3]
        ]

    # 2) suma de varias facturas del MISMO cliente (hasta MAX_COMBO).
    by_client: dict[str, list[dict[str, Any]]] = {}
    for inv in pending:
        by_client.setdefault(
            str(inv.get("cliente_codigo") or inv.get("cliente_nombre") or ""), []
        ).append(inv)
    combos_found: list[list[dict[str, Any]]] = []
    for key, invs in by_client.items():
        if not key or len(invs) < 2:
            continue
        invs_sorted = sorted(invs, key=lambda i: str(i.get("fecha") or ""))[:12]
        for k in range(2, min(MAX_COMBO, len(invs_sorted)) + 1):
            for combo in combinations(invs_sorted, k):
                if _same(sum(float(i["saldo_pendiente"]) for i in combo), amount):
                    combos_found.append(list(combo))
    if combos_found:
        named = [c for c in combos_found if name_ok(c[0])]
        chosen = (
            named[0] if len(named) == 1 else (combos_found[0] if len(combos_found) == 1 else None)
        )
        if chosen is not None:
            conf = "alta" if (named and len(named) == 1) else "media"
            nums = ", ".join(str(i.get("numero")) for i in chosen)
            return [
                _prop(
                    i,
                    i["saldo_pendiente"],
                    conf,
                    f"suma de {len(chosen)} facturas del cliente ({nums})"
                    + ("" if named else " (sin confirmación de nombre)"),
                )
                for i in chosen
            ]
        # varias combinaciones plausibles → no inventar
        return []

    # 3) cobro PARCIAL: importe < saldo de una factura del cliente (por nombre).
    partial = [
        inv
        for inv in pending
        if float(inv["saldo_pendiente"]) > amount + AMOUNT_TOL and name_ok(inv)
    ]
    if len(partial) == 1:
        inv = partial[0]
        pct = amount / float(inv["saldo_pendiente"])
        reason = (
            f"cobro parcial ({amount:.2f} de {float(inv['saldo_pendiente']):.2f} "
            "pendientes) + nombre del cliente"
        )
        return [_prop(inv, amount, "media" if fresh(inv) and pct >= 0.05 else "baja", reason)]
    if len(partial) > 1:
        # el más reciente pendiente es el candidato más plausible, en baja
        inv = sorted(partial, key=lambda i: str(i.get("fecha") or ""), reverse=True)[0]
        return [
            _prop(
                inv,
                amount,
                "baja",
                "cobro parcial; varias facturas del cliente con saldo (se propone la más reciente)",
            )
        ]

    return []
