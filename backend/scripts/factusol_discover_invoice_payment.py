"""ERP-F3 — discovery SOLO LECTURA del estado de cobro de las facturas (ESTFAC)
y de una posible FECHA de cobro en F_FAC. No escribe nada.

Dos modos:

  --estfac N       Lista las facturas con ESTFAC=N (nº, cliente, fecha, importe,
                   referencia). Sirve para identificar el valor 1, aún sin
                   mapear (13 facturas en el sondeo), y validar que 0/2 son
                   pendiente/cobrada.

  --payment-date   Para unas cuantas facturas cobradas y pendientes, vuelca las
                   columnas candidatas a fecha de cobro (FPEFAC, FROFAC, COBFAC)
                   junto a ESTFAC, para ver si alguna trae fecha real (frente al
                   vacío de FACTUSOL, 1900-01-01). Acepta números concretos:
                   `--payment-date 1-260719 1-260720`.

  --collections    ERP-F3-fix1: prepara el terreno de la conciliación bancaria.
                   Documenta la relación F_COB↔F_LCO, cómo se numera LINLCO, qué
                   es FALLCO (¿vencimiento?) y qué cuentas conoce F_BAN (sin
                   exponer los IBAN completos). SOLO LECTURA.

Uso (en producción, dentro del contenedor api):
    docker exec crmbo-api-1 python -m \
        scripts.factusol_discover_invoice_payment --estfac 1
    docker exec crmbo-api-1 python -m \
        scripts.factusol_discover_invoice_payment --payment-date 1-260719
    docker exec crmbo-api-1 python -m \
        scripts.factusol_discover_invoice_payment --collections
"""
from __future__ import annotations

import argparse
from typing import Any

#: Columnas candidatas a «fecha de cobro» vistas en el volcado de F_FAC. El
#: vacío de FACTUSOL es 1900-01-01T00:00:00 (o cadena vacía).
_DATE_CANDIDATES = ("FPEFAC", "FROFAC", "COBFAC")
_EMPTY_DATES = {"", "1900-01-01T00:00:00", "1900-01-01", "1899-12-30T00:00:00"}


def _s(row: dict[str, Any], col: str) -> str:
    return str(row.get(col) or "").strip()


def _estado_str(value: Any) -> str:
    v = str(value).strip() if value is not None else ""
    if v.endswith(".0") and v[:-2].isdigit():
        v = v[:-2]
    return v


def _numero(row: dict[str, Any]) -> str:
    from app.integrations.factusol.documents import visible_number  # noqa: PLC0415
    return visible_number(row.get("TIPFAC"), row.get("CODFAC"))


def _load_faturas(client: Any, ejercicio: str) -> list[dict[str, Any]]:
    return client.load_table(
        "F_FAC", filtro="1=1 ORDER BY CODFAC DESC", ejercicio=ejercicio,
    )


def list_by_estfac(client: Any, ejercicio: str, estfac: str) -> int:
    rows = _load_faturas(client, ejercicio)
    target = _estado_str(estfac)
    hits = [r for r in rows if _estado_str(r.get("ESTFAC")) == target]
    print(f"== Facturas con ESTFAC={target} ({len(hits)} de {len(rows)}) ==")
    print(f"{'NÚMERO':<12} {'FECHA':<12} {'IMPORTE':>12}  CLIENTE / REF")
    for r in hits:
        fecha = _s(r, "FECFAC")[:10]
        total = _s(r, "TOTFAC")
        print(f"{_numero(r):<12} {fecha:<12} {total:>12}  "
              f"{_s(r, 'CNOFAC')} · {_s(r, 'REFFAC')}")
    print("\nSOLO LECTURA — no se ha escrito nada.")
    return 0


def show_payment_dates(
    client: Any, ejercicio: str, numeros: list[str],
) -> int:
    rows = _load_faturas(client, ejercicio)
    wanted = {n.strip() for n in numeros if n.strip()}
    hits = [r for r in rows if _numero(r) in wanted] if wanted else rows[:20]
    print("== Columnas candidatas a fecha de cobro en F_FAC ==")
    print(f"Candidatas: {', '.join(_DATE_CANDIDATES)}  (vacío FACTUSOL = "
          "1900-01-01)")
    for r in hits:
        estfac = _estado_str(r.get("ESTFAC"))
        parts = []
        for col in _DATE_CANDIDATES:
            raw = _s(r, col)
            flag = "" if raw in _EMPTY_DATES or not raw else "  <-- FECHA REAL"
            parts.append(f"{col}={raw or '∅'}{flag}")
        print(f"{_numero(r):<12} ESTFAC={estfac:<2}  " + "  ".join(parts))
    print("\nSi alguna columna trae fecha en una factura COBRADA (ESTFAC=2) y "
          "no en una PENDIENTE, es la fecha de cobro. SOLO LECTURA.")
    return 0


def _mask_iban(value: str) -> str:
    """Enseña solo los 4 primeros y 4 últimos del IBAN (nunca el completo)."""
    v = value.replace(" ", "")
    return v if len(v) <= 8 else f"{v[:4]}…{v[-4:]}"


def discover_collections(client: Any, ejercicio: str) -> int:
    """Documenta F_LCO / F_COB / F_BAN para diseñar el registro de cobros."""
    lco = client.load_table("F_LCO", filtro="1=1", ejercicio=ejercicio)
    cob = client.load_table("F_COB", filtro="1=1", ejercicio=ejercicio)
    ban = client.load_table("F_BAN", filtro="1=1", ejercicio=ejercicio)

    print("== F_LCO (cobros) — columnas de la 1ª fila ==")
    if lco:
        print("  ", ", ".join(sorted(lco[0].keys())))
    print(f"F_LCO: {len(lco)} filas · F_COB: {len(cob)} filas · "
          f"F_BAN: {len(ban)} filas\n")

    # 1) Relación F_COB ↔ F_LCO. Buscamos una clave común entre las columnas.
    print("== 1) Relación F_COB ↔ F_LCO ==")
    lco_cols = set(lco[0].keys()) if lco else set()
    # Candidata: ¿F_LCO referencia un CODCOB de F_COB? (por nombre de columna)
    ref_cols = [c for c in lco_cols if "COB" in c.upper()]
    print(f"  Columnas de F_LCO que mencionan COB: {ref_cols or '(ninguna)'}")
    cob_ids = {_s(r, "CODCOB") for r in cob if _s(r, "CODCOB")}
    for ref in ref_cols:
        vals = {_s(r, ref) for r in lco if _s(r, ref)}
        inter = vals & cob_ids
        print(f"  F_LCO.{ref}: {len(vals)} valores; {len(inter)} coinciden con "
              f"F_COB.CODCOB → {'ENLAZA con F_COB' if inter else 'no enlaza'}")
    if not ref_cols:
        print("  (F_LCO no referencia CODCOB por nombre; el enlace real hay que "
              "confirmarlo con los datos: comparar importes/fechas por factura.)")
    print()

    # 2) Numeración de LINLCO (¿correlativo por factura o global?).
    print("== 2) LINLCO: ¿correlativo por factura o global? ==")
    by_invoice: dict[tuple[str, str], list[int]] = {}
    all_lin: list[int] = []
    for r in lco:
        key = (_s(r, "TFALCO"), _s(r, "CFALCO"))
        lin = r.get("LINLCO")
        try:
            n = int(str(lin).strip().split(".")[0])
        except (TypeError, ValueError):
            continue
        by_invoice.setdefault(key, []).append(n)
        all_lin.append(n)
    multi = {k: v for k, v in by_invoice.items() if len(v) > 1}
    per_invoice_from_1 = all(
        sorted(v) == list(range(1, len(v) + 1)) for v in multi.values()
    ) if multi else None
    print(f"  Facturas con >1 cobro: {len(multi)}")
    if multi:
        muestra = list(multi.items())[:5]
        for key, lins in muestra:
            print(f"    {key[0]}-{key[1]}: LINLCO={sorted(lins)}")
        veredicto = ("CORRELATIVO POR FACTURA (1..N)" if per_invoice_from_1
                     else "NO correlativo 1..N por factura")
        print(f"  LINLCO parece {veredicto}")
    lo = min(all_lin) if all_lin else "∅"
    hi = max(all_lin) if all_lin else "∅"
    print(f"  Rango global de LINLCO: {lo}..{hi}\n")

    # 3) Qué es FALLCO (¿vencimiento?) frente a FECLCO (fecha del cobro).
    print("== 3) FECLCO (cobro) vs FALLCO (¿vencimiento?) ==")
    print(f"  {'FACTURA':<14} {'FORMA':<6} {'FECLCO':<12} {'FALLCO':<12} gap")
    for r in lco[:15]:
        fec = _s(r, "FECLCO")[:10]
        fal = _s(r, "FALLCO")[:10]
        gap = ""
        if fec and fal and fec not in _EMPTY_DATES and fal not in _EMPTY_DATES:
            gap = "FALLCO ≥ FECLCO" if fal >= fec else "FALLCO < FECLCO"
        print(f"  {_s(r,'TFALCO')}-{_s(r,'CFALCO'):<10} {_s(r,'CPALCO'):<6} "
              f"{fec or '∅':<12} {fal or '∅':<12} {gap}")
    print("  (Si FALLCO ≥ FECLCO de forma consistente y la separación depende "
          "de la forma de pago, es el VENCIMIENTO.)\n")

    # 4) F_BAN — cuentas que conoce FACTUSOL (IBAN enmascarado).
    print("== 4) F_BAN (cuentas bancarias) ==")
    for r in ban:
        iban = _s(r, "IBABAN") or _s(r, "CUEBAN")
        print(f"  ENT={_s(r,'ENTBAN')} BIC={_s(r,'BICBAN')} "
              f"IBAN={_mask_iban(iban) if iban else '∅'} "
              f"DOM={_s(r,'DOMBAN')}")
    print("\nSOLO LECTURA — no se ha escrito nada. Esto alimenta el diseño de "
          "la escritura de cobros de la conciliación bancaria.")
    return 0


def dump_lco_rows(client: Any, ejercicio: str, numeros: list[str]) -> int:
    """F-4-B-fix: vuelca las filas REALES de F_LCO de facturas ya cobradas
    (todas las columnas, con valor y TIPO tal como los devuelve DELSOL) para
    contrastarlas con el registro que manda `register_invoice_collection`
    cuando `EscribirRegistro` responde `BDEscribirRegistroError`. Enseña
    también las filas de F_COB con el mismo importe (por si hubiera enlace
    implícito). SOLO LECTURA."""
    from app.integrations.factusol.service import coerce_serie  # noqa: PLC0415

    lco = client.load_table("F_LCO", filtro="1=1", ejercicio=ejercicio)
    cob = client.load_table("F_COB", filtro="1=1", ejercicio=ejercicio)
    print(f"F_LCO: {len(lco)} filas · F_COB: {len(cob)} filas · ejercicio {ejercicio}\n")
    for numero in numeros:
        head, _, tail = numero.partition("-")
        serie = coerce_serie(head)
        codigo = tail.strip().lstrip("0") or "0"
        rows = [
            r for r in lco
            if coerce_serie(r.get("TFALCO")) == serie
            and str(r.get("CFALCO") or "").split(".")[0].lstrip("0") == codigo
        ]
        print(f"== F_LCO de {numero}: {len(rows)} línea(s) ==")
        for r in rows:
            for col in sorted(r):
                v = r[col]
                print(f"  {col:<10} {type(v).__name__:<6} {v!r}")
            print("  " + "-" * 40)
            imp = r.get("IMPLCO")
            matches = [c for c in cob if c.get("IMPCOB") == imp or c.get("IMPLCO") == imp]
            print(f"  F_COB con el mismo importe ({imp!r}): {len(matches)}")
            for c in matches[:3]:
                keys = [k for k in sorted(c)
                        if k.upper().startswith(("COD", "CPA", "FEC", "IMP"))]
                print("   ", {k: c[k] for k in keys})
        print()
    print("SOLO LECTURA — no se ha escrito nada.")
    return 0


def _resolve_ejercicio(arg: str | None) -> tuple[Any, str]:
    from sqlalchemy.orm import Session  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415
    from app.integrations.factusol.client import FactusolClient  # noqa: PLC0415
    from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

    client = FactusolClient.from_settings()
    if arg:
        return client, arg
    with Session(get_engine()) as session:
        return client, ejercicio_for(session)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ejercicio", default=None)
    parser.add_argument("--estfac", default=None,
                        help="Lista las facturas con ese ESTFAC (p. ej. 1).")
    parser.add_argument("--payment-date", nargs="*", default=None,
                        metavar="NUMERO",
                        help="Vuelca las columnas de fecha candidatas.")
    parser.add_argument("--collections", action="store_true",
                        help="Documenta F_LCO/F_COB/F_BAN (conciliación).")
    parser.add_argument("--lco-row", nargs="+", default=None, metavar="NUMERO",
                        help="Vuelca las filas REALES de F_LCO de esas facturas "
                             "(valor y tipo por columna), p. ej. 5-260004.")
    args = parser.parse_args(argv)

    client, ejercicio = _resolve_ejercicio(args.ejercicio)
    if args.estfac is not None:
        return list_by_estfac(client, ejercicio, args.estfac)
    if args.payment_date is not None:
        return show_payment_dates(client, ejercicio, args.payment_date)
    if args.collections:
        return discover_collections(client, ejercicio)
    if args.lco_row:
        return dump_lco_rows(client, ejercicio, args.lco_row)
    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
