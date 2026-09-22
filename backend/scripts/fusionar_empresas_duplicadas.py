"""Fusión masiva de empresas CRM duplicadas por NIF/VAT normalizado (Lote 8 · B3).

Agrupa las fichas CRM **no archivadas** por su NIF/VAT NORMALIZADO
(`company_discovery.nif_key`: sin separadores y sin el prefijo de país de la UE,
así `ESB123` y `B123` caen en el mismo grupo — la misma normalización que el
discovery de limpieza). Por cada grupo con ≥2 fichas:

- superviviente = la ficha **vinculada a FACTUSOL**; las demás se absorben
  (reasignando pedidos/contactos/tareas/actividad/vínculo/notas) y quedan
  **ARCHIVADAS** (`is_archived`, REVERSIBLE — nunca se borran);
- va **«a revisar»** (NO se fusiona a ciegas) si el grupo es ambiguo: ninguna
  ficha vinculada a FACTUSOL, o varias vinculadas a CODCLI distintos, o nombres
  muy dispares entre sí.

FACTUSOL SIEMPRE solo lectura: este comando solo escribe en el CRM.

Uso (VPS):

    # DRY-RUN (por defecto): NO fusiona nada. Enseña el plan (a fusionar + a revisar).
    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.fusionar_empresas_duplicadas
    # aplicar de verdad: exige confirmación tecleada (APLICAR) o --yes.
    #   ANTES DE --apply: haz una copia de seguridad de la base de datos.
    ... python -m scripts.fusionar_empresas_duplicadas --apply

Opciones: --apply · --yes (salta la confirmación tecleada, para no interactivo) ·
--only NIF_KEY (un solo grupo) · --name-threshold 0.45 · --csv RUTA (plan del
dry-run).
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from sqlalchemy.orm import Session

DEFAULT_CSV = "/tmp/empresas_duplicadas_plan.csv"
CONFIRM_WORD = "APLICAR"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="fusiona de verdad (reasigna + archiva las absorbidas, "
                             "reversible); exige teclear APLICAR o pasar --yes")
    parser.add_argument("--yes", action="store_true",
                        help="salta la confirmación tecleada del --apply (no interactivo)")
    parser.add_argument("--only", default=None, metavar="NIF_KEY",
                        help="fusiona/revisa SOLO el grupo de ese NIF normalizado")
    parser.add_argument("--name-threshold", type=float, default=None,
                        help="parecido mínimo de nombre para fusionar (0-1); "
                             "por debajo, el grupo va a revisión")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="CSV del plan (dry-run)")
    parser.add_argument("--by-name", action="store_true",
                        help="OTRA pasada: agrupa por NOMBRE las fichas SIN NIF "
                             "con la que sí lo tiene (duplicados de Brevo). "
                             "Conservadora y opt-in; el cruce por NIF no las ve")
    parser.add_argument("--name-similarity", type=float, default=None,
                        help="parecido mínimo de nombre para --by-name (0-1, "
                             "por defecto 0.92)")
    args = parser.parse_args(argv)

    from app.db.session import get_engine
    from app.services.company_dedupe import (
        NAME_ONLY_SIMILARITY,
        NAME_SIMILARITY_LOW,
        _company_fk_tables,
        apply_duplicate_merges,
        plan_duplicate_merges,
        plan_name_only_merges,
    )

    threshold = args.name_threshold if args.name_threshold is not None else NAME_SIMILARITY_LOW

    # Discovery: qué tablas cuelgan de `companies.id` (todas se reasignan antes
    # de archivar; el merge se NIEGA a correr si aparece una FK no contemplada).
    fks = sorted(_company_fk_tables())
    print(f"Tablas con FK a companies.id (se reasignan todas): {', '.join(fks)}")

    with Session(get_engine()) as session:
        if args.by_name:
            similarity = (
                args.name_similarity if args.name_similarity is not None
                else NAME_ONLY_SIMILARITY
            )
            print(f"\nModo --by-name: agrupando por NOMBRE (parecido ≥ {similarity}). "
                  "Solo se fusiona cuando hay UNA ficha con NIF y las demás sin él.")
            plan = plan_name_only_merges(session, similarity=similarity)
        else:
            plan = plan_duplicate_merges(
                session, name_threshold=threshold, only_key=args.only)

        print(f"\nFichas CRM no archivadas examinadas: {plan.total_companies}")
        print(f"Grupos a FUSIONAR: {len(plan.to_merge)} "
              f"(se archivarán {plan.companies_to_archive} fichas absorbidas)")
        print(f"Grupos A REVISAR (no se tocan): {len(plan.review)}")

        if plan.to_merge:
            print("\n— A FUSIONAR (superviviente ← absorbidas) —")
            for a in plan.to_merge:
                print(f"  [{a.nif_key}] «{a.keep_name}» (CODCLI {a.keep_codcli}) "
                      f"← {', '.join(f'«{n}»' for n in a.merge_names)}")
        if plan.review:
            print("\n— A REVISAR (a mano; NO se fusiona) —")
            for r in plan.review:
                print(f"  [{r.nif_key}] {r.reason}")
                print(f"      fichas: {', '.join(f'«{n}»' for n in r.names)}")

        csv_path = _write_plan(plan, args.csv)
        print(f"\nCSV del plan (a_fusionar + a_revisar): {csv_path}")

        if not args.apply:
            print("\nDRY-RUN: no se ha fusionado nada. Revisa el plan y, cuando lo "
                  "tengas claro:")
            print("  1) HAZ UNA COPIA DE SEGURIDAD de la base de datos.")
            print("  2) python -m scripts.fusionar_empresas_duplicadas --apply")
            return 0

        if not plan.to_merge:
            print("\nNada que fusionar (no hay grupos claros). No se ha tocado nada.")
            return 0

        if not args.yes:
            print("\n⚠  Vas a fusionar y ARCHIVAR fichas (reversible, pero afecta a "
                  "pedidos/contactos/tareas).")
            print("   Asegúrate de tener una COPIA DE SEGURIDAD de la base de datos.")
            try:
                answer = input(f"   Escribe {CONFIRM_WORD} para confirmar: ").strip()
            except EOFError:
                answer = ""
            if answer != CONFIRM_WORD:
                print("Confirmación incorrecta. No se ha tocado nada.")
                return 2

        summary = apply_duplicate_merges(session, plan)
        print(f"\nFusionados {summary['merged_groups']} grupos; archivadas "
              f"{summary['companies_archived']} fichas (is_archived=1, reversible; "
              f"NADA borrado).")
        print(f"  reasignados: {summary['contacts_moved']} contactos, "
              f"{summary['orders_moved']} pedidos, {summary['tasks_moved']} tareas.")
        if summary["errors"]:
            print(f"  {len(summary['errors'])} grupo(s) con error (no fusionados):")
            for e in summary["errors"]:
                print(f"    [{e['nif_key']}] {e['error']}")
    return 0


def _write_plan(plan, path: str) -> Path:  # noqa: ANN001
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["accion", "nif_key", "keep_id", "keep_name", "keep_codcli",
                    "merge_ids", "detalle"])
        for a in plan.to_merge:
            w.writerow(["fusionar", a.nif_key, a.keep_id, a.keep_name, a.keep_codcli,
                        "|".join(a.merge_ids),
                        " ← ".join(a.merge_names)])
        for r in plan.review:
            w.writerow(["revisar", r.nif_key, "", "", "|".join(r.linked_codclis),
                        "|".join(r.company_ids), r.reason])
    return p


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
