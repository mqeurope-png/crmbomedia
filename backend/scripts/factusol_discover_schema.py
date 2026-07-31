"""Sprint 0 — Descubrimiento del esquema real de tablas FACTUSOL.

Ejecutar con credenciales en .env.local (ver client.py) desde una máquina
con acceso a la API DELSOL (el entorno CCR de desarrollo NO llega —
allowlist de red):

    cd backend
    python -m scripts.factusol_discover_schema            # todas las tablas
    python -m scripts.factusol_discover_schema F_CLI      # una tabla

Para cada tabla hace CargaTabla con filtro pequeño, infiere columnas y
tipos de los registros devueltos, y vuelca el resultado en
docs/erp/factusol-schema-DISCOVERED.md (tabla por tabla) listo para
fusionar con docs/erp/factusol-schema.md.

También cronometra 5 autenticaciones consecutivas espaciadas para validar
la renovación automática del token (flag --auth-test, 30 min).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from app.integrations.factusol.client import FactusolClient, FactusolError

#: Tablas objetivo del ERP (cabeceras + líneas). El nombre de la tabla de
#: líneas se confirma en vivo (convención esperada F_Lxx: F_LPR líneas de
#: presupuesto, F_LPE líneas de pedido, F_LPA líneas de albarán, F_LFA
#: líneas de factura).
TARGET_TABLES = [
    "F_CLI",  # clientes
    "F_CON",  # contactos por cliente (¿existe? confirmar)
    "F_ART",  # artículos
    "F_FAM",  # familias
    "F_STO",  # stock
    "F_ALM",  # almacenes
    "F_PRE",  # presupuestos (cabecera)
    "F_LPR",  # presupuestos (líneas) — confirmar nombre
    "F_PED",  # pedidos (cabecera)
    "F_LPE",  # pedidos (líneas) — confirmar nombre
    "F_ALB",  # albaranes (cabecera)
    "F_LPA",  # albaranes (líneas) — confirmar nombre
    "F_FAC",  # facturas (cabecera)
    "F_LFA",  # facturas (líneas) — confirmar nombre
    "F_FPA",  # formas de pago — confirmar
    "F_TAR",  # tarifas — confirmar
]

OUT = Path(__file__).resolve().parents[2] / "docs" / "erp" / "factusol-schema-DISCOVERED.md"


def _infer_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return f"str({len(str(value))})"


def discover_table(client: FactusolClient, tabla: str) -> str:
    try:
        rows = client.carga_tabla(tabla)
    except FactusolError as exc:
        return f"## {tabla}\n\n**ERROR** {exc.status}: {exc.body[:300]}\n"
    if not rows:
        return f"## {tabla}\n\n_Sin registros devueltos (tabla vacía o filtro requerido)._\n"
    sample = rows[0]
    lines = [f"## {tabla}\n", f"_{len(rows)} registros leídos. Columnas del primer registro:_\n",
             "| Columna | Tipo inferido | Ejemplo (recortado) |", "|---|---|---|"]
    for col, val in sample.items():
        example = str(val)[:40].replace("|", "\\|") if val is not None else ""
        lines.append(f"| `{col}` | {_infer_type(val)} | {example} |")
    return "\n".join(lines) + "\n"


def auth_renewal_test(client: FactusolClient) -> None:
    """5 auth consecutivas en ~30 min — verifica renovación automática."""
    for i in range(5):
        t0 = time.time()
        client.authenticate()
        print(f"auth {i + 1}/5 ok en {time.time() - t0:.2f}s")
        if i < 4:
            time.sleep(6 * 60)
    # Y una llamada al final con el último token.
    rows = client.carga_tabla("F_CLI")
    print(f"post-renovación: F_CLI → {len(rows)} registros")


def main() -> int:
    args = sys.argv[1:]
    client = FactusolClient()
    if "--auth-test" in args:
        auth_renewal_test(client)
        return 0
    tables = [a for a in args if not a.startswith("-")] or TARGET_TABLES
    parts = ["# FACTUSOL — esquema descubierto (CargaTabla en vivo)\n"]
    for tabla in tables:
        print(f"descubriendo {tabla}…")
        parts.append(discover_table(client, tabla))
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"→ {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
