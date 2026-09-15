"""Archivar (REVERSIBLE, nunca borrado) las empresas del CRM que NO están en
FACTUSOL y NO tienen negocio vivo. FACTUSOL solo lectura.

Regla: NO en FACTUSOL (ni por CODCLI válido ni por NIF/VAT normalizado) Y sin
pedidos, sin proformas, sin tareas y sin actividad/email reciente (ventana
`--recent-days`, 180 por defecto). Un contacto suelto NO protege.

Uso (VPS):

    # DRY-RUN (por defecto): NO archiva nada. Recuento + CSV de candidatas.
    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.archive_companies
    # aplicar de verdad (marca is_archived; NO borra): exige --apply Y --yes
    ... python -m scripts.archive_companies --apply --yes

Opciones: --recent-days 180 · --csv RUTA (candidatas del dry-run) ·
--manifest RUTA (ids archivados del apply) · --reason "texto".
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from sqlalchemy.orm import Session

DEFAULT_CSV = "/tmp/empresas_a_archivar.csv"
DEFAULT_MANIFEST = "/tmp/empresas_archivadas_manifest.csv"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="archiva de verdad (marca is_archived, NO borra); exige --yes")
    parser.add_argument("--yes", action="store_true", help="confirmación explícita del --apply")
    parser.add_argument("--recent-days", type=int, default=180)
    parser.add_argument("--csv", default=DEFAULT_CSV, help="CSV de candidatas (dry-run)")
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST,
                        help="CSV de ids archivados (apply)")
    parser.add_argument("--reason", default=None, help="motivo guardado en archived_reason")
    args = parser.parse_args(argv)

    from app.db.session import get_engine
    from app.erp.company_cleanup import DEFAULT_ARCHIVE_REASON, apply_archive, plan_archive
    from app.integrations.factusol.client import FactusolClient
    from app.integrations.factusol.service import ejercicio_for

    reason = args.reason or DEFAULT_ARCHIVE_REASON
    with Session(get_engine()) as session:
        client = FactusolClient.from_settings()
        ejercicio = ejercicio_for(session)
        print(f"Leyendo F_CLI (ejercicio {ejercicio})… FACTUSOL solo lectura.")
        fcli = client.load_table("F_CLI", filtro="1=1", ejercicio=ejercicio)
        print(f"  {len(fcli)} clientes en F_CLI")
        plan = plan_archive(session, fcli, recent_days=args.recent_days)

        print(f"\nCandidatas a archivar (regla: no en FACTUSOL y sin negocio vivo, "
              f"ventana {args.recent_days} días): {plan.count}")
        for bucket, label in (
            ("sin_nif_sin_nada", "sin NIF y sin nada"),
            ("sin_nif_solo_contacto", "sin NIF, solo contacto (no protege)"),
            ("con_nif_sin_nada", "con NIF y sin nada"),
            ("con_nif_solo_contacto", "con NIF, solo contacto (no protege)"),
        ):
            print(f"  {label}: {plan.buckets.get(bucket, 0)}")

        csv_path = _write_candidates(plan, args.csv)
        print(f"\nCSV de candidatas (una fila por empresa): {csv_path}")

        if not args.apply:
            print("\nDRY-RUN: no se ha archivado nada. Revisa el CSV y, cuando lo tengas claro:")
            print("  python -m scripts.archive_companies --apply --yes")
            return 0
        if not args.yes:
            print("\n--apply requiere también --yes (confirmación). No se ha tocado nada.")
            return 2

        n = apply_archive(session, plan, reason=reason)
        session.commit()
        manifest = _write_manifest(plan, args.manifest)
        print(f"\nArchivadas {n} empresas (is_archived=1; NADA borrado; reversible con "
              f"«Restaurar»). Manifiesto: {manifest}")
    return 0


def _write_candidates(plan, path: str) -> Path:  # noqa: ANN001
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["company_id", "name", "nif", "bucket", "reason"])
        for c in plan.candidates:
            w.writerow([c.company_id, c.name, c.nif, c.bucket, c.reason])
    return p


def _write_manifest(plan, path: str) -> Path:  # noqa: ANN001
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["company_id", "name", "nif", "bucket"])
        for c in plan.candidates:
            w.writerow([c.company_id, c.name, c.nif, c.bucket])
    return p


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
