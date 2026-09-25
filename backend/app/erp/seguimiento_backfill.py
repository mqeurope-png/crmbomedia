"""Seguimiento (app) — backfill SUPERVISADO de ids de pedido estables (Fase 1).

Sustituye el casado por Nº —frágil (`9562.0`, con/sin prefijo, cruces falsos
tipo #387 `BOPRIN-99927`)— por un `id` estable en cada fila. Este módulo:

  1. IMPORTA el histórico de la hoja a `seguimiento_legacy` tal cual (una fila
     por línea, con id sintético propio) — sin limpiar nada.
  2. PROPONE, fila a fila, con qué pedido real de BoHub casa cada una:
       - CLARO   → `proposed` (se puede aplicar solo);
       - DUDOSO  → `dubious`  (lo revisa una persona antes de escribir el id);
       - sin pedido detrás → `synthetic` (se queda con su id sintético).
  3. APLICA solo lo CLARO (y lo que una persona confirme): escribe
     `matched_order_id` + `match_status` en `seguimiento_legacy`. Nunca escribe
     un id dudoso sin confirmación.

Es la «semilla de la primera vez»: una vez cada fila lleva su id, el casado ya
no es por Nº. Idempotente: reejecutar no cambia lo ya resuelto.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erp.models import Order, SeguimientoLegacy
from app.erp.seguimiento import SEGUIMIENTO_COLUMNS_V2

_NUMERO_INDEX = SEGUIMIENTO_COLUMNS_V2.index("Nº pedido")
_CLIENTE_INDEX = SEGUIMIENTO_COLUMNS_V2.index("Cliente")
_FECHA_INDEX = SEGUIMIENTO_COLUMNS_V2.index("Fecha")

_BARE_RE = re.compile(r"\d{4,}")


def _texto(value: Any) -> str:
    return str(value if value is not None else "").strip()


def norm_numero(value: Any) -> str:
    """Nº de pedido normalizado para comparar: sin espacios, en minúsculas y sin
    el `.0` que la hoja añade a los numéricos (`9562.0` → `9562`,
    ` BOPRIN-99927 ` → `boprin-99927`)."""
    s = _texto(value).replace(" ", "").casefold()
    while s.endswith(".0"):
        s = s[:-2]
    return s


def bare_numero(value: Any) -> str:
    """El número DESNUDO (≥4 dígitos) de un Nº de pedido, o «» si no lo tiene.
    `BOPRIN-99927` → `99927`, `9562.0` → `9562`, `MANUAL-3` → «» (3 dígitos)."""
    m = _BARE_RE.search(_texto(value))
    return m.group(0) if m else ""


def _norm_cliente(value: Any) -> str:
    """Cliente normalizado, laxo: minúsculas y espacios colapsados."""
    return " ".join(_texto(value).casefold().split())


def _cliente_corrobora(a: Any, b: Any) -> bool:
    """¿El cliente de la fila y el del pedido apuntan a lo mismo? Laxo: uno
    contenido en el otro (o iguales). Si alguno está vacío, NO corrobora (no
    afirma nada, pero tampoco desmiente)."""
    na, nb = _norm_cliente(a), _norm_cliente(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


@dataclass(frozen=True)
class OrderRef:
    """Lo justo de un pedido de BoHub para casar (sin arrastrar el modelo)."""

    id: str
    order_number: str
    cliente: str = ""


@dataclass
class MatchProposal:
    """La propuesta de casado de UNA fila legacy."""

    legacy_id: str
    numero_raw: str
    cliente_raw: str
    status: str                       # proposed | dubious | synthetic
    order_id: str | None = None       # el pedido propuesto (si lo hay)
    note: str = ""
    candidates: list[str] = field(default_factory=list)  # ids candidatos (dudoso)

    @property
    def stable_id(self) -> str:
        """El id que se escribiría: el del pedido si es claro, o el sintético."""
        return self.order_id if (self.status == "proposed" and self.order_id) else self.legacy_id


#: Índice de pedidos por Nº: {clave → pedidos con ese Nº} (completo y desnudo).
_OrderIndex = dict[str, list[OrderRef]]


def _index_orders(orders: list[OrderRef]) -> tuple[_OrderIndex, _OrderIndex]:
    by_full: _OrderIndex = {}
    by_bare: _OrderIndex = {}
    for o in orders:
        full = norm_numero(o.order_number)
        if full:
            by_full.setdefault(full, []).append(o)
        bare = bare_numero(o.order_number)
        if bare:
            by_bare.setdefault(bare, []).append(o)
    return by_full, by_bare


def propose_one(
    *, legacy_id: str, numero_raw: str, cliente_raw: str,
    by_full: dict[str, list[OrderRef]], by_bare: dict[str, list[OrderRef]],
) -> MatchProposal:
    """Propone el casado de UNA fila. La regla, conservadora (los falsos
    positivos de #387 vienen de casar un desnudo con el prefijo equivocado):

      - Nº normalizado COMPLETO que casa con UN único pedido → CLARO.
      - Nº completo que casa con VARIOS → DUDOSO (¿cuál?).
      - Solo el número DESNUDO casa (la fila trae prefijo distinto, o va a pelo):
        CLARO solo si hay UN candidato Y el cliente corrobora; si no, DUDOSO.
      - Nada casa → SINTÉTICO (no hay pedido detrás)."""
    rn = norm_numero(numero_raw)
    base = {"legacy_id": legacy_id, "numero_raw": _texto(numero_raw),
            "cliente_raw": _texto(cliente_raw)}
    if not rn:
        return MatchProposal(**base, status="synthetic", note="sin nº de pedido")

    full = by_full.get(rn, [])
    if len(full) == 1:
        return MatchProposal(**base, status="proposed", order_id=full[0].id,
                             note=f"Nº exacto → {full[0].order_number}")
    if len(full) > 1:
        return MatchProposal(**base, status="dubious",
                             candidates=[o.id for o in full],
                             note=f"Nº exacto ambiguo: {len(full)} pedidos")

    bare = bare_numero(numero_raw)
    cand = by_bare.get(bare, []) if bare else []
    if not cand:
        return MatchProposal(**base, status="synthetic", note="sin pedido con ese nº")
    if len(cand) == 1 and _cliente_corrobora(cliente_raw, cand[0].cliente):
        return MatchProposal(**base, status="proposed", order_id=cand[0].id,
                             note=f"desnudo {bare} + cliente → {cand[0].order_number}")
    # Un solo candidato pero sin corroborar cliente, o varios: es justo el
    # terreno de #387 → que lo mire una persona.
    return MatchProposal(
        **base, status="dubious", candidates=[o.id for o in cand],
        note=(f"desnudo {bare} sin corroborar cliente"
              if len(cand) == 1 else f"desnudo {bare} ambiguo: {len(cand)} pedidos"),
    )


def order_refs(session: Session) -> list[OrderRef]:
    """Los pedidos de BoHub como `OrderRef` (id + Nº + cliente), para casar."""
    from app.erp.api.orders import customer_names  # noqa: PLC0415

    orders = list(session.scalars(select(Order)))
    names = customer_names(session, orders)
    refs: list[OrderRef] = []
    for o in orders:
        who = names.get(o.id) or {}
        cliente = who.get("company_name") or who.get("contact_name") or ""
        refs.append(OrderRef(id=o.id, order_number=o.order_number or "", cliente=cliente))
    return refs


def import_legacy_rows(session: Session, historico: list[list[Any]]) -> int:
    """Importa las filas del histórico a `seguimiento_legacy` TAL CUAL (una fila
    por línea, con id sintético). Idempotente por `row_index`: reejecutar no
    duplica (actualiza el contenido en bruto, respeta el casado ya resuelto).

    `historico` son las filas de datos del histórico (sin cabecera ni separador),
    en su orden. Devuelve cuántas quedan en la tabla."""
    existentes = {r.row_index: r for r in session.scalars(select(SeguimientoLegacy))}
    for idx, row in enumerate(historico):
        cells = [_texto(c) for c in row]
        numero = cells[_NUMERO_INDEX] if len(cells) > _NUMERO_INDEX else ""
        cliente = cells[_CLIENTE_INDEX] if len(cells) > _CLIENTE_INDEX else ""
        fecha = cells[_FECHA_INDEX] if len(cells) > _FECHA_INDEX else ""
        raw = json.dumps([c for c in row], ensure_ascii=False, default=str)
        fila = existentes.get(idx)
        if fila is None:
            session.add(SeguimientoLegacy(
                row_index=idx, numero_raw=numero[:64], cliente_raw=cliente[:300],
                fecha_raw=fecha[:64], raw_json=raw, match_status="pending",
            ))
        else:
            # Refresca el contenido en bruto; NO toca el casado ya resuelto.
            fila.numero_raw, fila.cliente_raw = numero[:64], cliente[:300]
            fila.fecha_raw, fila.raw_json = fecha[:64], raw
    session.flush()
    return len(session.scalars(select(SeguimientoLegacy)).all())


#: Estados «sin resolver»: se vuelven a proponer en cada pasada. `dubious` es
#: el estado que dejaban las corridas antiguas (antes de que las dudosas se
#: quedaran en `pending`); se trata igual para que no se queden fuera.
_SIN_RESOLVER = ("pending", "dubious")


def propose_backfill(session: Session) -> list[MatchProposal]:
    """Propone el casado de TODAS las filas legacy aún sin resolver (pending, o
    `dubious` de corridas antiguas), contra los pedidos de BoHub. No escribe
    nada: es el informe para revisar."""
    by_full, by_bare = _index_orders(order_refs(session))
    filas = session.scalars(
        select(SeguimientoLegacy)
        .where(SeguimientoLegacy.match_status.in_(_SIN_RESOLVER))
        .order_by(SeguimientoLegacy.row_index)
    )
    return [
        propose_one(legacy_id=f.id, numero_raw=f.numero_raw, cliente_raw=f.cliente_raw,
                    by_full=by_full, by_bare=by_bare)
        for f in filas
    ]


def backfill_report(session: Session) -> dict[str, Any]:
    """Informe (dry-run) del backfill: cuántas claras / dudosas / sintéticas y la
    LISTA de dudosas para que una persona las revise antes de aplicar ids."""
    props = propose_backfill(session)
    por_estado: dict[str, int] = {"proposed": 0, "dubious": 0, "synthetic": 0}
    for p in props:
        por_estado[p.status] = por_estado.get(p.status, 0) + 1
    dudosas = [
        {"legacy_id": p.legacy_id, "numero": p.numero_raw, "cliente": p.cliente_raw,
         "motivo": p.note, "candidatos": p.candidates}
        for p in props if p.status == "dubious"
    ]
    return {
        "total": len(props),
        "claras": por_estado["proposed"],
        "dudosas": por_estado["dubious"],
        "sinteticas": por_estado["synthetic"],
        "dudosas_detalle": dudosas,
    }


#: Valor de `--confirm` que dice «esta fila NO tiene pedido detrás».
CONFIRM_SIN_PEDIDO = "none"


def parse_confirmar(raw: str) -> dict[str, str]:
    """`--confirm` → {legacy_id: destino}. Formas admitidas, separadas por comas:

      - `ID`           → casar con el ÚNICO candidato que proponga el backfill;
      - `ID=ORDER_ID`  → casar con ese pedido concreto (revisión de una persona);
      - `ID=none`      → la fila NO tiene pedido detrás (id sintético).

    El destino vacío («») significa «el candidato propuesto»."""
    out: dict[str, str] = {}
    for trozo in (raw or "").split(","):
        trozo = trozo.strip()
        if not trozo:
            continue
        legacy_id, _sep, destino = trozo.partition("=")
        if legacy_id.strip():
            out[legacy_id.strip()] = destino.strip()
    return out


def apply_backfill(
    session: Session, *, confirmar: dict[str, str] | set[str] | None = None,
) -> dict[str, Any]:
    """APLICA el casado: escribe `matched_order_id` + `match_status` en
    `seguimiento_legacy`.

      - Las CLARAS (proposed) se confirman solas.
      - Las SINTÉTICAS (sin pedido detrás) se marcan como tal.
      - Las DUDOSAS se quedan `pending` (con su motivo y candidatos en
        `match_note`): se vuelven a proponer en cada corrida y NUNCA se
        resuelven solas —ni se casan ni se sintetizan— hasta que una persona las
        confirma.
      - `confirmar` ({legacy_id: destino}, ver `parse_confirmar`) manda sobre
        TODO lo anterior y puede SOBRESCRIBIR una fila ya resuelta (confirmed o
        synthetic): es la corrección de una persona.

    Idempotente. Devuelve el recuento de lo aplicado (y los errores de
    confirmación: id legacy o pedido inexistentes, candidato ambiguo)."""
    if isinstance(confirmar, set):
        confirmar = dict.fromkeys(confirmar, "")
    confirmar = confirmar or {}
    por_id = {f.id: f for f in session.scalars(select(SeguimientoLegacy))}
    by_full, by_bare = _index_orders(order_refs(session))
    aplicadas = confirmadas = sinteticas = pendientes = 0
    errores: list[str] = []

    for p in propose_backfill(session):
        fila = por_id.get(p.legacy_id)
        if fila is None or p.legacy_id in confirmar:
            continue          # las confirmadas a mano se resuelven abajo
        if p.status == "proposed":
            fila.matched_order_id, fila.match_status = p.order_id, "confirmed"
            fila.match_note = p.note
            aplicadas += 1
        elif p.status == "synthetic":
            fila.matched_order_id, fila.match_status = None, "synthetic"
            fila.match_note = p.note
            sinteticas += 1
        else:  # dudosa: PENDIENTE hasta que una persona la confirme
            fila.matched_order_id, fila.match_status = None, "pending"
            fila.match_note = (
                f"{p.note} · candidatos: {', '.join(p.candidates)}" if p.candidates else p.note
            )
            pendientes += 1

    if confirmar:
        explicitos = [d for d in confirmar.values() if d and d != CONFIRM_SIN_PEDIDO]
        pedidos = (
            set(session.scalars(select(Order.id).where(Order.id.in_(explicitos))))
            if explicitos else set()
        )
        for legacy_id, destino in confirmar.items():
            fila = por_id.get(legacy_id)
            if fila is None:
                errores.append(f"{legacy_id}: no existe en seguimiento_legacy")
                continue
            if destino == CONFIRM_SIN_PEDIDO:
                fila.matched_order_id, fila.match_status = None, "synthetic"
                fila.match_note = "confirmado a mano: sin pedido"
                confirmadas += 1
                continue
            if not destino:   # el candidato propuesto (ha de ser único)
                prop = propose_one(legacy_id=fila.id, numero_raw=fila.numero_raw,
                                   cliente_raw=fila.cliente_raw,
                                   by_full=by_full, by_bare=by_bare)
                cands = [prop.order_id] if prop.order_id else list(prop.candidates)
                if len(cands) != 1:
                    errores.append(
                        f"{legacy_id}: {len(cands)} candidatos — indica el pedido "
                        f"({legacy_id}=ORDER_ID) o {legacy_id}={CONFIRM_SIN_PEDIDO}"
                    )
                    continue
                destino = cands[0]
            elif destino not in pedidos:
                errores.append(f"{legacy_id}: el pedido {destino} no existe")
                continue
            fila.matched_order_id, fila.match_status = destino, "confirmed"
            fila.match_note = "confirmado a mano"
            confirmadas += 1

    session.flush()
    return {
        "aplicadas": aplicadas, "confirmadas_a_mano": confirmadas,
        "sinteticas": sinteticas, "dudosas_pendientes": pendientes,
        "errores": errores,
    }
