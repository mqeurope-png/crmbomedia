"""Sprint 0 — Prueba end-to-end de ESCRITURA en FACTUSOL (datos ficticios).

Flujo completo sobre datos de prueba, con cleanup garantizado:
  1. Crear cliente ficticio en F_CLI (EscribirRegistro).
  2. Actualizar un campo del cliente (ActualizarRegistro).
  3. Crear presupuesto: cabecera F_PRE + 2 líneas (tabla de líneas según
     descubrimiento — por defecto F_LPR, ajustar con --lines-table).
  4. Releer el presupuesto (CargaTabla con filtro).
  5. Cleanup: borrar líneas, cabecera y cliente (BorrarRegistros).

Ejecutar SOLO contra el ejercicio de pruebas acordado con Bart:

    cd backend
    python -m scripts.factusol_write_flow_test [--lines-table F_LPR]

Cada paso imprime la petición y la respuesta para volcarlas después en
docs/erp/factusol-write-flows.md (sección «Transcripción real»).
"""
from __future__ import annotations

import json
import sys

from app.integrations.factusol.client import FactusolClient, FactusolError

TEST_CODCLI = "ZZTEST01"  # prefijo ZZ para que quede al final y sea obvio
TEST_MARK = "BORRAR — PRUEBA API BOHUB"


def _show(label: str, data: object) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str)[:2000])


def main() -> int:
    lines_table = "F_LPR"
    if "--lines-table" in sys.argv:
        lines_table = sys.argv[sys.argv.index("--lines-table") + 1]

    client = FactusolClient()
    created_pre: str | None = None
    try:
        # 1. Alta de cliente ficticio.
        cli = {
            "CODCLI": TEST_CODCLI,
            "PCOCLI": TEST_MARK,
            "CIFCLI": "00000000T",
            "DOMCLI": "Calle Ficticia 1",
            "POBCLI": "Barcelona",
            "CPOCLI": "08001",
            "EMACLI": "test-api@example.invalid",
        }
        _show("EscribirRegistro F_CLI (petición)", cli)
        _show("respuesta", client.escribir_registro("F_CLI", cli))

        # 2. Actualización de un campo.
        _show("ActualizarRegistro F_CLI TELCLI", client.actualizar_registro(
            "F_CLI", {"TELCLI": "600000000"}, filtro=f"CODCLI='{TEST_CODCLI}'",
        ))

        # 3. Presupuesto: cabecera + 2 líneas. ⚠️ La numeración del
        # documento y las columnas exactas se ajustan tras el
        # descubrimiento — este payload es la hipótesis de partida.
        created_pre = "990001"  # número alto de prueba; confirmar política
        cab = {"CODPRE": created_pre, "CLIPRE": TEST_CODCLI, "TOTPRE": 0}
        _show("EscribirRegistro F_PRE (cabecera)", cab)
        _show("respuesta", client.escribir_registro("F_PRE", cab))
        for pos, (art, qty, price) in enumerate(
            [("ART-TEST-1", 1, 100.0), ("ART-TEST-2", 2, 50.0)], start=1
        ):
            line = {
                "CODLPR": created_pre, "POSLPR": pos,
                "ARTLPR": art, "CANLPR": qty, "PRELPR": price,
                "DESLPR": TEST_MARK,
            }
            _show(f"EscribirRegistro {lines_table} línea {pos}", line)
            _show("respuesta", client.escribir_registro(lines_table, line))

        # 4. Releer el presupuesto creado.
        _show("CargaTabla F_PRE (verificación)", client.carga_tabla(
            "F_PRE", filtro=f"CODPRE='{created_pre}'",
        ))
        _show(f"CargaTabla {lines_table} (verificación)", client.carga_tabla(
            lines_table, filtro=f"CODLPR='{created_pre}'",
        ))
        print("\n✔ Flujo de escritura completado — revisar transcripción.")
        return 0
    except FactusolError as exc:
        print(f"\n✖ ERROR API: {exc} (status={exc.status})\n{exc.body}")
        return 1
    finally:
        # 5. Cleanup SIEMPRE (aunque un paso intermedio fallara).
        print("\n=== CLEANUP ===")
        for tabla, filtro in [
            (lines_table, f"CODLPR='{created_pre}'" if created_pre else None),
            ("F_PRE", f"CODPRE='{created_pre}'" if created_pre else None),
            ("F_CLI", f"CODCLI='{TEST_CODCLI}'"),
        ]:
            if not filtro:
                continue
            try:
                client.borrar_registros(tabla, filtro=filtro)
                print(f"borrado {tabla} donde {filtro}")
            except FactusolError as exc:
                print(f"cleanup {tabla} falló: {exc} — borrar a mano en FACTUSOL")


if __name__ == "__main__":
    raise SystemExit(main())
