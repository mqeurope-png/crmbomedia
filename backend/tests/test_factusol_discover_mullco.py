"""Rev. 30/09/2026 — modo `--mullco` del script de discovery de cobros.

Solo lectura: reparte F_LCO por MULLCO / TRALCO y dice si cada MULLCO casa con
F_COB.CODCOB, para confirmar en producción que BoHub no debe heredar el
MULLCO de la fila plantilla. Cliente falso: nunca sale a red.
"""
from __future__ import annotations

from typing import Any

from scripts.factusol_discover_invoice_payment import discover_mullco


class _FakeClient:
    def __init__(self, tables: dict[str, list[dict[str, Any]]]):
        self.tables = tables
        self.calls: list[str] = []

    def load_table(
        self, tabla: str, *, filtro: str = "1=1", ejercicio: str = "2026"
    ) -> list[dict[str, Any]]:
        self.calls.append(tabla)
        return list(self.tables.get(tabla, []))

    def __getattr__(self, name: str) -> Any:  # cualquier escritura rompe el test
        raise AssertionError(f"discover_mullco no debe llamar a {name}")


def test_mullco_reparte_por_mullco_y_tralco_y_casa_con_f_cob(capsys) -> None:
    client = _FakeClient({
        "F_LCO": [
            {"TFALCO": "5", "CFALCO": 260001, "LINLCO": 1, "MULLCO": 51, "TRALCO": 1},
            {"TFALCO": "5", "CFALCO": 260002, "LINLCO": 1, "MULLCO": 51, "TRALCO": 1},
            {"TFALCO": "5", "CFALCO": 260108, "LINLCO": 1, "MULLCO": 0, "TRALCO": 0},
        ],
        "F_COB": [{"CODCOB": 51}],
    })
    assert discover_mullco(client, "2026") == 0
    out = capsys.readouterr().out
    assert "F_LCO: 3 líneas · F_COB: 1 filas" in out
    assert "MULLCO=51" in out and "2 líneas (TRALCO=0: 0, ≠0: 2)" in out
    assert "EN F_COB" in out and "5-260001, 5-260002" in out
    assert "MULLCO=0" in out and "1 líneas (TRALCO=0: 1, ≠0: 0)" in out
    assert "no está en F_COB" in out
    assert "SOLO LECTURA" in out
    assert client.calls == ["F_LCO", "F_COB"]
