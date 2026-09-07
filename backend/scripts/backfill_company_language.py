"""ERP-E4-fix2 — rellena `companies.language` desde el PAÍS de la empresa.

Las empresas del CRM anteriores a E4-fix2 tienen el idioma vacío. Este
script lo deriva de su país con el mapa compartido (`app.erp.language`:
BE→fr, CH→en, resto→en), para que sus documentos salgan en un idioma
razonable sin esperar a que Bart los toque uno a uno.

`--dry-run` (por defecto) NO escribe: solo reporta cuántas empresas se
verían afectadas y el reparto por idioma y por país. Con `--apply` escribe.
NUNCA pisa una empresa que ya tenga idioma (Bart pudo corregir alguna a
mano). Las empresas sin país se dejan vacías y se cuentan aparte.

    docker exec crmbo-api-1 python -m scripts.backfill_company_language
    docker exec crmbo-api-1 python -m scripts.backfill_company_language --apply
"""
from __future__ import annotations

import argparse
import collections

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.erp.language import language_for_country
from app.models.crm import Company


def run(apply: bool, session: Session | None = None) -> dict[str, int]:
    """Deriva `companies.language` del país. `session` inyectable para los
    tests; en producción abre la suya sobre el engine de la app."""
    if session is None:
        with Session(get_engine()) as own:
            return run(apply, session=own)

    por_idioma: collections.Counter[str] = collections.Counter()
    por_pais: collections.Counter[str] = collections.Counter()
    sin_pais = 0
    afectadas = 0

    # Solo empresas SIN idioma: no se pisa lo ya asignado.
    rows = session.scalars(
        select(Company).where(
            or_(Company.language.is_(None), Company.language == ""),
        )
    ).all()
    for company in rows:
        lang = language_for_country(company.country)
        if lang is None:
            sin_pais += 1
            continue
        afectadas += 1
        por_idioma[lang] += 1
        por_pais[str(company.country or "").strip().upper()] += 1
        if apply:
            company.language = lang
    # Cuántas ya tenían idioma (informativo — no se tocan).
    ya_tenian = session.scalar(
        select(func.count(Company.id)).where(
            Company.language.isnot(None), Company.language != "",
        )
    ) or 0
    if apply:
        session.commit()

    modo = "APLICADO" if apply else "DRY-RUN (nada escrito)"
    print(f"== Backfill idioma de empresas — {modo} ==")
    print(f"Empresas afectadas (sin idioma, con país): {afectadas}")
    print(f"Empresas sin país (se dejan vacías):       {sin_pais}")
    print(f"Empresas que ya tenían idioma (intactas):  {ya_tenian}")
    print("Reparto por idioma:")
    for lang, n in sorted(por_idioma.items()):
        print(f"  {lang}: {n}")
    print("Reparto por país:")
    for pais, n in por_pais.most_common():
        print(f"  {pais or '∅'}: {n}")
    if not apply:
        print("\nRevisa el reparto y vuelve a ejecutar con --apply para escribir.")
    return {
        "afectadas": afectadas, "sin_pais": sin_pais, "ya_tenian": ya_tenian,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Escribe los cambios (por defecto solo dry-run).",
    )
    args = parser.parse_args()
    run(apply=args.apply)


if __name__ == "__main__":
    main()
