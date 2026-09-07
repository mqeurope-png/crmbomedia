"""ERP-F1-fix2 — corrige `companies.country` (y, si procede, el idioma) de las
empresas del CRM vinculadas a FACTUSOL, tomando como verdad el `PAICLI` del
cliente en F_CLI.

Diagnóstico (Bart, prod): ~600 empresas extranjeras estaban marcadas como
España porque el sync de importación tenía una tabla parcial de códigos de
país y caía a España por defecto. En el backfill de idioma (E4-fix3) esas
empresas recibieron español.

Para cada empresa CRM vinculada (`factusol_company_id` = CODCLI):
  1. Lee `PAICLI` de su cliente en F_CLI y lo normaliza a ISO2 (ahora el
     normalizador entiende el numérico).
  2. Si el país normalizado difiere del que tiene el CRM → corrige
     `companies.country` a ISO2.
  3. Re-deriva el idioma SOLO si era un valor deducido, no puesto a mano:
     si el `language` actual coincide con el que produciría el país ANTIGUO
     (o está vacío) se considera derivado y se recalcula con el país nuevo;
     si no coincide, alguien lo puso a mano y NO se toca.

`--dry-run` (por defecto) NO escribe y enseña una muestra de 20 casos
concretos. Con `--apply` escribe. Si `PAICLI` no se puede normalizar, la
empresa se deja EXACTAMENTE como está (nunca se inventa ni se cae a España).

    docker exec crmbo-api-1 python -m scripts.fix_company_country_from_factusol
    docker exec crmbo-api-1 python -m scripts.fix_company_country_from_factusol --apply
"""
from __future__ import annotations

import argparse
import collections
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.erp.language import language_for_country, normalize_country
from app.models.crm import Company

#: Muestra a enseñar en dry-run para revisar a ojo antes de aplicar.
SAMPLE_SIZE = 20


def _paicli_index(client: Any, ejercicio: str) -> dict[str, str]:
    """CODCLI (str) → PAICLI crudo, leyendo F_CLI de una sola vez (un `IN` por
    cada empresa saturaría el token de 3 min de DELSOL — mismo criterio que
    bulk-match)."""
    rows = client.load_table("F_CLI", filtro="1=1", ejercicio=ejercicio)
    index: dict[str, str] = {}
    for row in rows:
        codcli = row.get("CODCLI")
        if codcli is None:
            continue
        index[str(codcli)] = str(row.get("PAICLI") or "").strip()
    return index


def run(
    apply: bool,
    *,
    session: Session | None = None,
    client: Any | None = None,
    ejercicio: str | None = None,
) -> dict[str, Any]:
    """Corrige país/idioma desde FACTUSOL. `session` y `client` inyectables
    para los tests; en producción los abre sobre la config de la app."""
    if session is None:
        with Session(get_engine()) as own:
            return run(apply, session=own, client=client, ejercicio=ejercicio)
    if client is None:  # pragma: no cover - ruta de producción
        from app.integrations.factusol.client import FactusolClient
        from app.integrations.factusol.service import ejercicio_for

        client = FactusolClient.from_settings()
        ejercicio = ejercicio or ejercicio_for(session)
    ejercicio = ejercicio or "2026"

    paicli_by_codcli = _paicli_index(client, ejercicio)

    companies = session.scalars(
        select(Company).where(Company.factusol_company_id.isnot(None))
    ).all()

    pais_corregidos = 0
    idiomas_cambiados = 0
    idiomas_respetados = 0            # país corregido pero idioma manual
    sin_cliente = 0                   # vinculada pero sin fila/PAICLI útil
    sin_paicli_reconocible = 0        # PAICLI presente pero no normalizable
    ya_correctas = 0                  # país ya coincide con FACTUSOL
    antes: collections.Counter[str] = collections.Counter()
    despues: collections.Counter[str] = collections.Counter()
    muestra: list[dict[str, str]] = []

    for company in companies:
        codcli = str(company.factusol_company_id or "").strip()
        paicli = paicli_by_codcli.get(codcli)
        if not paicli:
            sin_cliente += 1
            continue
        new_iso = normalize_country(paicli)
        if new_iso is None:
            sin_paicli_reconocible += 1
            continue
        old_raw = (company.country or "").strip()
        old_iso = normalize_country(old_raw)
        if new_iso == old_iso:
            ya_correctas += 1
            continue

        # País a corregir.
        old_lang = (company.language or "").strip()
        old_derived = language_for_country(old_iso) if old_iso else None
        new_derived = language_for_country(new_iso)
        # ¿El idioma actual era DEDUCIDO (o estaba vacío)? Entonces se
        # recalcula; si estaba puesto a mano (no coincide con el deducido del
        # país antiguo) se respeta.
        derived = (not old_lang) or (old_lang == old_derived)
        new_lang = new_derived if derived else old_lang

        pais_corregidos += 1
        antes[old_iso or "?"] += 1
        despues[new_iso] += 1
        lang_changes = derived and new_lang and new_lang != old_lang
        if lang_changes:
            idiomas_cambiados += 1
        elif not derived:
            idiomas_respetados += 1

        if len(muestra) < SAMPLE_SIZE:
            muestra.append({
                "empresa": company.name,
                "pais": f"{old_raw or '∅'} → {new_iso}",
                "idioma": (
                    f"{old_lang or '∅'} → {new_lang}"
                    + ("" if lang_changes or not derived else " (=)")
                    + (" [a mano, intacto]" if not derived else "")
                ),
            })

        if apply:
            company.country = new_iso
            if lang_changes:
                company.language = new_lang

    if apply:
        session.commit()

    modo = "APLICADO" if apply else "DRY-RUN (nada escrito)"
    print(f"== Corrección país/idioma desde FACTUSOL — {modo} ==")
    print(f"Empresas vinculadas revisadas:                {len(companies)}")
    print(f"Países corregidos:                            {pais_corregidos}")
    print(f"  · idiomas recalculados (eran deducidos):    {idiomas_cambiados}")
    print(f"  · idiomas respetados (puestos a mano):      {idiomas_respetados}")
    print(f"Ya correctas (país coincide):                 {ya_correctas}")
    print(f"Sin cliente/ PAICLI en F_CLI:                 {sin_cliente}")
    print(f"PAICLI presente pero no reconocible:          {sin_paicli_reconocible}")
    print("Reparto de los corregidos ANTES (ISO2/∅):")
    for pais, n in antes.most_common():
        print(f"  {pais}: {n}")
    print("Reparto de los corregidos DESPUÉS (ISO2):")
    for pais, n in despues.most_common():
        print(f"  {pais}: {n}")
    if not apply:
        print(f"\nMuestra de {min(SAMPLE_SIZE, len(muestra))} casos "
              "(revisa a ojo antes de --apply):")
        for row in muestra:
            print(f"  · {row['empresa']}: país {row['pais']}; "
                  f"idioma {row['idioma']}")
        print("\nRevisa la muestra; luego --apply.")
    return {
        "pais_corregidos": pais_corregidos,
        "idiomas_cambiados": idiomas_cambiados,
        "idiomas_respetados": idiomas_respetados,
        "ya_correctas": ya_correctas,
        "sin_cliente": sin_cliente,
        "sin_paicli_reconocible": sin_paicli_reconocible,
        "muestra": muestra,
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
