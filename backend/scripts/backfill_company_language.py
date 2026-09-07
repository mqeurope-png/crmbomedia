"""ERP-E4-fix2/fix3 — rellena `companies.language` desde el PAÍS de la empresa.

Las empresas del CRM anteriores a E4-fix2 tienen el idioma vacío. Este
script lo deriva de su país con el mapa compartido (`app.erp.language`).

fix3: el país NO está normalizado (llega de FACTUSOL/Woo/Agile como ISO2 o
como nombre en varios idiomas), así que primero se normaliza a ISO2
(`normalize_country`) y solo entonces se deriva el idioma — antes «ESPAÑA»
caía a inglés. El dry-run informa del reparto por idioma, del reparto por
país YA NORMALIZADO (ISO2, legible) y de los valores de país que NO se han
podido reconocer, con recuento, para ampliar la tabla de alias antes de
aplicar.

`--dry-run` (por defecto) NO escribe. Con `--apply` escribe. NUNCA pisa una
empresa que ya tenga idioma (Bart pudo corregir alguna a mano). Las
empresas sin país reconocible se dejan vacías y se cuentan aparte.

    docker exec crmbo-api-1 python -m scripts.backfill_company_language
    docker exec crmbo-api-1 python -m scripts.backfill_company_language --apply
"""
from __future__ import annotations

import argparse
import collections

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.erp.language import language_for_country, normalize_country
from app.models.crm import Company


def run(apply: bool, session: Session | None = None) -> dict[str, int]:
    """Deriva `companies.language` del país. `session` inyectable para los
    tests; en producción abre la suya sobre el engine de la app."""
    if session is None:
        with Session(get_engine()) as own:
            return run(apply, session=own)

    por_idioma: collections.Counter[str] = collections.Counter()
    por_pais: collections.Counter[str] = collections.Counter()
    #: valores crudos de país que no se han podido normalizar (agregado).
    no_reconocidos: collections.Counter[str] = collections.Counter()
    sin_pais = 0
    afectadas = 0

    # Solo empresas SIN idioma: no se pisa lo ya asignado.
    rows = session.scalars(
        select(Company).where(
            or_(Company.language.is_(None), Company.language == ""),
        )
    ).all()
    for company in rows:
        raw = (company.country or "").strip()
        iso = normalize_country(raw, unresolved=no_reconocidos)
        if iso is None:
            # Sin país o país no reconocido: se deja vacía (la cascada de
            # idioma sigue por la empresa emisora). Los valores con texto
            # pero no reconocidos ya quedaron contados en `no_reconocidos`.
            sin_pais += 1
            continue
        lang = language_for_country(iso)  # iso ya normalizado
        afectadas += 1
        por_idioma[lang] += 1
        por_pais[iso] += 1
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

    no_reconocidos_total = sum(no_reconocidos.values())
    modo = "APLICADO" if apply else "DRY-RUN (nada escrito)"
    print(f"== Backfill idioma de empresas — {modo} ==")
    print(f"Empresas afectadas (país reconocido):         {afectadas}")
    print(f"Empresas sin país o país no reconocido:       {sin_pais}")
    print(f"  · de ellas, con texto NO reconocido:        {no_reconocidos_total}")
    print(f"Empresas que ya tenían idioma (intactas):     {ya_tenian}")
    print("Reparto por idioma:")
    for lang, n in por_idioma.most_common():
        print(f"  {lang}: {n}")
    print("Reparto por país (ISO2 normalizado):")
    for pais, n in por_pais.most_common():
        print(f"  {pais}: {n}")
    if no_reconocidos:
        print("Países NO reconocidos (amplía la tabla de alias si el "
              "recuento es alto):")
        for pais, n in no_reconocidos.most_common(50):
            print(f"  {pais!r}: {n}")
    if not apply:
        print("\nRevisa el reparto y los no reconocidos; luego --apply.")
    return {
        "afectadas": afectadas, "sin_pais": sin_pais, "ya_tenian": ya_tenian,
        "no_reconocidos": no_reconocidos_total,
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
