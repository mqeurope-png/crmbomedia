"""Backfill SUPERVISADO de ids de pedido estables en «Seguimiento (app)» (Fase 1).

Importa el histórico de la hoja a `seguimiento_legacy` (tal cual) y PROPONE con
qué pedido real de BoHub casa cada fila. Las CLARAS se aplican solas; las
DUDOSAS (el terreno de #387) se listan para que una persona las revise antes de
escribir ningún id.

    # 1) Ver el informe, SIN escribir (por defecto): claras / dudosas / sintéticas
    python -m scripts.backfill_seguimiento_ids

    # 2) Con el detalle de TODAS las dudosas (nº, cliente, motivo, candidatos)
    python -m scripts.backfill_seguimiento_ids --verbose

    # 3) Aplicar SOLO las claras (y marcar las sintéticas); las dudosas se quedan
    python -m scripts.backfill_seguimiento_ids --apply

    # 4) Confirmar a mano unas dudosas concretas (tras revisarlas) y aplicarlas
    python -m scripts.backfill_seguimiento_ids --apply --confirm <legacy_id>,<legacy_id>

Necesita `INTEGRATION_SECRETS_KEY` (credenciales de la cuenta de servicio
cifradas en la BD) y la hoja compartida con su `client_email`. NO escribe en la
hoja de Drive: solo puebla `seguimiento_legacy` y su casado. Escribir los ids en
la propia hoja es un paso aparte (el volcado periódico, «upsert por id»).
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.erp.drive_managed import historico_manual_rows, managed_tab_titles
from app.erp.drive_sheets import (
    DriveConfigError,
    GoogleSheetsClient,
    drive_config,
)
from app.erp.models import ErpSettings
from app.erp.models.settings import ERP_SETTINGS_SINGLETON_ID
from app.erp.seguimiento_backfill import (
    apply_backfill,
    backfill_report,
    import_legacy_rows,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="aplica las CLARAS (y marca sintéticas); por defecto solo informa")
    parser.add_argument("--verbose", action="store_true",
                        help="lista TODAS las dudosas con su motivo y candidatos")
    parser.add_argument("--confirm", default="",
                        help="ids legacy (separados por comas) de dudosas a confirmar a mano")
    args = parser.parse_args()
    confirmar = {s.strip() for s in args.confirm.split(",") if s.strip()}

    with Session(get_engine()) as session:
        cfg = session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID)
        try:
            conf = drive_config(cfg)
        except DriveConfigError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if conf is None:
            print("ERROR: falta la cuenta de servicio de Google o el ID de la hoja "
                  "en Configuración ERP.", file=sys.stderr)
            return 2
        info, spreadsheet_id = conf
        pedidos_tab, _incidencias_tab = managed_tab_titles(session)

        client = GoogleSheetsClient(info, spreadsheet_id)
        valores = client.tab_values(pedidos_tab, raw=True)
        historico = historico_manual_rows(valores)
        n = import_legacy_rows(session, historico)
        print(f"Histórico importado a «seguimiento_legacy»: {n} fila(s) "
              f"(de la pestaña «{pedidos_tab}»).")
        print()

        rep = backfill_report(session)
        print(f"Casado propuesto (sobre {rep['total']} fila(s) sin resolver):")
        print(f"  claras (se aplican solas): {rep['claras']}")
        print(f"  dudosas (a revisar):       {rep['dudosas']}")
        print(f"  sintéticas (sin pedido):   {rep['sinteticas']}")
        muestra = rep["dudosas_detalle"] if args.verbose else rep["dudosas_detalle"][:20]
        if muestra:
            print()
            print(f"Dudosas ({'todas' if args.verbose else 'primeras 20'}) — revísalas "
                  "antes de aplicar sus ids:")
            for d in muestra:
                print(f"  [{d['legacy_id']}] nº={d['numero']!r} cliente={d['cliente']!r} "
                      f"· {d['motivo']}"
                      + (f" · candidatos={d['candidatos']}" if d["candidatos"] else ""))
        print()

        if not args.apply:
            session.rollback()
            print("Nada aplicado (dry-run). Revisa las dudosas y repite con --apply "
                  "(y --confirm para las dudosas que aceptes).")
            return 0

        res = apply_backfill(session, confirmar=confirmar)
        session.commit()
        print(f"✔ Aplicado: {res['aplicadas']} claras + "
              f"{res['confirmadas_a_mano']} dudosas confirmadas a mano; "
              f"{res['sinteticas']} sintéticas; {res['dudosas_pendientes']} dudosas "
              "siguen pendientes de revisión.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
