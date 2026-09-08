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

Uso (en producción, dentro del contenedor api):
    docker exec crmbo-api-1 python -m \
        scripts.factusol_discover_invoice_payment --estfac 1
    docker exec crmbo-api-1 python -m \
        scripts.factusol_discover_invoice_payment --payment-date 1-260719
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
    args = parser.parse_args(argv)

    client, ejercicio = _resolve_ejercicio(args.ejercicio)
    if args.estfac is not None:
        return list_by_estfac(client, ejercicio, args.estfac)
    if args.payment_date is not None:
        return show_payment_dates(client, ejercicio, args.payment_date)
    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
