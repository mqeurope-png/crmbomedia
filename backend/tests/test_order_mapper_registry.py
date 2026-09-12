"""FIX regresión (#391, expuesta al desplegar #394): el mapper de `orders` no
resolvía el FK `store_id → integration_accounts` en el worker RQ.

`worker-factusol` no importa `app.main`: rq importa solo el módulo del job
(`app.integrations.factusol.jobs`). Por esa cadena `Order` queda registrado
(vía `service.py`) pero `app.models.integration_settings` NO — y el unit of
work resuelve el FK al ordenar tablas en cada flush de `orders`
(`Mapper._sorted_tables`), así que convertir una proforma en pedido (y el
albarán / pago de la Fase 2) moría con `NoReferencedTableError`. Hasta #391
el modelo se registraba por casualidad (el antiguo `convert_quote_to_order`
importaba `app.erp.api.orders`).

Estos tests corren en un SUBPROCESO con la MISMA cadena de import que el
worker: dentro del proceso de pytest `app.main` ya está importado y el fallo
no se reproduce.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

BACKEND_DIR = Path(__file__).resolve().parents[1]

#: Lo único que importa rq para ejecutar un job de FACTUSOL.
WORKER_IMPORT = "import app.integrations.factusol.jobs as jobs"


def _run(code: str, **env_extra: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **env_extra}
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=240,
        check=False,
    )


def test_order_mapper_configura_sin_error() -> None:
    """Con SOLO la cadena de import del worker: `configure_mappers()` pasa,
    `integration_accounts` está en el MetaData, todos los FK de `orders`
    resuelven y `Mapper._sorted_tables` (lo que usa el unit of work en cada
    flush de `orders`) no lanza `NoReferencedTableError`."""
    r = _run(f"""
        import sys
        {WORKER_IMPORT}
        from sqlalchemy.orm import configure_mappers
        from app.erp.models import Order
        assert "app.main" not in sys.modules, "el test no reproduce la cadena del worker"
        configure_mappers()
        tables = Order.__table__.metadata.tables
        assert "integration_accounts" in tables, sorted(tables)
        for fkc in Order.__table__.foreign_key_constraints:
            fkc.referred_table
        Order.__mapper__._sorted_tables
        print("MAPPER_OK")
    """)
    assert r.returncode == 0, r.stderr[-2500:]
    assert "MAPPER_OK" in r.stdout


def test_registro_agregado_resuelve_todos_los_fk() -> None:
    """`app.db.base` (el registro que usa alembic y `create_all` de los tests)
    tiene que dejar TODOS los FK resolubles por sí solo — le faltaban los
    modelos de plantillas de email (`email_template_folders`)."""
    r = _run("""
        from app.db.base import Base
        for table in Base.metadata.tables.values():
            for fk in table.foreign_keys:
                fk.column
        print("REGISTRY_OK", len(Base.metadata.tables))
    """)
    assert r.returncode == 0, r.stderr[-2500:]
    assert "REGISTRY_OK" in r.stdout


def test_crear_proforma_desde_ficha_empresa_ok(tmp_path: Path) -> None:
    """El flujo de la ficha de empresa, tal como lo ejecuta el worker: crear
    la proforma («Nueva proforma») y convertirla en pedido con pago y
    albarán (Fase 1 + 2), en un subproceso con la cadena de import del
    worker contra un esquema completo (sqlite). Antes del fix la conversión
    moría al hacer flush de `orders`."""
    import app.main  # noqa: F401 — registra todos los modelos para el esquema
    from app.db.base import Base
    from app.erp.models import Order
    from app.models.crm import Company

    db_path = tmp_path / "worker.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Company(id="acme", name="Acme SL", factusol_company_id="1"))
        s.commit()

    r = _run(f"""
        import json, sys
        from unittest.mock import patch
        {WORKER_IMPORT}
        assert "app.main" not in sys.modules

        class Fake:
            default_ejercicio = "2026"
            def __init__(self):
                self.tables = {{"F_PRE": [], "F_LPS": [], "F_ART": [], "F_LTA": [],
                               "F_FPA": [], "F_ALB": [], "F_LAL": [],
                               "F_CLI": [{{"CODCLI": 1, "NOFCLI": "Acme"}}]}}
                self.written = []
            def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
                rows = list(self.tables.get(tabla, []))
                pred = filtro.split(" ORDER BY ")[0].strip()
                if pred == "1=1":
                    return rows
                col, _, val = pred.partition("=")
                val = val.strip().strip("'")
                return [r for r in rows if str(r.get(col.strip())) == val]
            def write_record(self, tabla, data, *, ejercicio=None):
                self.written.append((tabla, dict(data)))
                self.tables.setdefault(tabla, []).append(dict(data))
                return {{"respuesta": "OK"}}
            def update_record(self, *a, **k):
                return {{"respuesta": "OK"}}
            def delete_records(self, *a, **k):
                return {{"respuesta": "OK"}}

        fake = Fake()
        with patch("app.integrations.factusol.client.FactusolClient.from_settings",
                   return_value=fake):
            quote = jobs.create_quote_job(
                {{"codcli": "1", "nombre": "Acme", "nif": "", "direccion": "",
                  "ciudad": "", "cp": "", "provincia": ""}},
                [{{"description": "Reparación", "quantity": 1, "unit_price": 40}}],
                referencia="Taller",
            )
            # La proforma recién creada, tal como la devolvería F_PRE/F_LPS.
            fake.tables["F_PRE"] = [{{"CODPRE": 574, "TIPPRE": "1", "CLIPRE": 1,
                                     "CNOPRE": "Acme", "ESTPRE": 0, "TOTPRE": 48.4,
                                     "REFPRE": "Taller", "NET1PRE": 40.0,
                                     "PIVA1PRE": 21.0, "FOPPRE": "002"}}]
            fake.tables["F_LPS"] = [{{"TIPLPS": "1", "CODLPS": 574, "POSLPS": 1,
                                     "ARTLPS": "", "DESLPS": "Reparación",
                                     "CANLPS": 1, "PRELPS": 40.0, "TOTLPS": 40.0,
                                     "IVALPS": 0}}]
            payment = {{"paid": True, "forma_pago": "002",
                        "forma_pago_nombre": "Transferencia", "contrapartida": "6",
                        "contrapartida_nombre": "Bomedia Sabadell",
                        "fecha": "2026-09-12"}}
            order = jobs.convert_quote_to_order_job(
                "574", payment=payment, create_albaran=True,
            )
        print(json.dumps({{
            "codpre": quote.get("codpre"),
            "order_number": order.get("order_number"),
            "albaran": (order.get("albaran") or {{}}).get("numero"),
            "albaran_error": order.get("albaran_error"),
            "written": [t for t, _ in fake.written],
        }}))
    """, DATABASE_URL=f"sqlite:///{db_path}")
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["codpre"] == "1"                      # «Nueva proforma» creada
    assert out["order_number"] == "PRO-000574"        # proforma → pedido
    assert out["albaran_error"] is None
    assert out["albaran"] == "1-000001"               # albarán Fase 2 creado
    assert out["written"] == ["F_PRE", "F_LPS", "F_ALB", "F_LAL"]
    with Session(engine) as s:
        o = s.scalar(select(Order).where(Order.order_number == "PRO-000574"))
        assert o is not None and o.company_id == "acme"
        assert o.factusol_albaran_number == "1-000001"
        assert o.payment_status.value == "paid"
