"""Address country normalisation — ISO Alpha-2 ↔ country name.

The CRM stores `address_country` as ISO Alpha-2 (e.g. "ES") and
`address_country_name` as the localised display name (e.g. "España").
Source systems are inconsistent: AgileCRM frequently ships either ISO
or country name depending on how the user typed it; Brevo usually
ships ISO but Bo-Media's import surfaced "Spain" mixed with "ES" mixed
with empty.

This module is the single normalisation surface every connector and
mapper goes through. `normalize_country(raw)` returns a
`(iso2, display_name)` pair:

- If the input is recognisable as either ISO Alpha-2 / Alpha-3 / name,
  it's mapped to the canonical pair.
- Spanish-language aliases are handled ("España", "Reino Unido",
  "Estados Unidos"…) since that's what the operators type.
- Unknown input → `(None, None)`. The caller persists what it
  receives without losing data; the importer keeps the raw string in
  the audit log when this happens.
"""
from __future__ import annotations

from functools import lru_cache
from typing import NamedTuple

try:
    import pycountry  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - dev env safety net
    pycountry = None  # type: ignore[assignment]


class CountryRecord(NamedTuple):
    iso2: str
    name: str


# Common Spanish-language aliases the operator types. Keep this
# alphabetical and lower-cased; the lookup strips + lowers the input.
_SPANISH_ALIASES: dict[str, str] = {
    "alemania": "DE",
    "andorra": "AD",
    "argentina": "AR",
    "bélgica": "BE",
    "belgica": "BE",
    "brasil": "BR",
    "chile": "CL",
    "colombia": "CO",
    "costa rica": "CR",
    "cuba": "CU",
    "dinamarca": "DK",
    "ecuador": "EC",
    "egipto": "EG",
    "españa": "ES",
    "espana": "ES",
    "estados unidos": "US",
    "estados unidos de américa": "US",
    "filipinas": "PH",
    "finlandia": "FI",
    "francia": "FR",
    "grecia": "GR",
    "guatemala": "GT",
    "honduras": "HN",
    "irlanda": "IE",
    "italia": "IT",
    "japón": "JP",
    "japon": "JP",
    "luxemburgo": "LU",
    "marruecos": "MA",
    "méxico": "MX",
    "mexico": "MX",
    "nicaragua": "NI",
    "noruega": "NO",
    "países bajos": "NL",
    "paises bajos": "NL",
    "panamá": "PA",
    "panama": "PA",
    "paraguay": "PY",
    "perú": "PE",
    "peru": "PE",
    "polonia": "PL",
    "portugal": "PT",
    "puerto rico": "PR",
    "reino unido": "GB",
    "república dominicana": "DO",
    "republica dominicana": "DO",
    "rumania": "RO",
    "rumanía": "RO",
    "rusia": "RU",
    "suecia": "SE",
    "suiza": "CH",
    "turquía": "TR",
    "turquia": "TR",
    "uruguay": "UY",
    "venezuela": "VE",
}


def normalize_country(raw: str | None) -> tuple[str | None, str | None]:
    """Return `(iso2, display_name)` for any country-ish input.

    The lookup tries, in order:
    1. ISO Alpha-2 literal (`"ES"`).
    2. ISO Alpha-3 literal (`"ESP"`).
    3. English / Spanish name (`"Spain"` / `"España"`).
    4. Common-name alias from pycountry (`"South Korea"` →
       `"Korea, Republic of"`).

    Returns `(None, None)` when the input is empty or unmatched.
    """
    if not raw:
        return (None, None)
    cleaned = raw.strip()
    if not cleaned:
        return (None, None)
    record = _lookup(cleaned)
    if record is None:
        return (None, None)
    return (record.iso2, record.name)


@lru_cache(maxsize=2048)
def _lookup(value: str) -> CountryRecord | None:
    key = value.strip()
    if not key:
        return None
    upper = key.upper()
    lower = key.lower()
    if pycountry is None:
        # Minimal fallback for environments without pycountry — keep
        # the alias table working.
        iso = _SPANISH_ALIASES.get(lower)
        if iso:
            return CountryRecord(iso, key)
        if len(upper) == 2 and upper.isalpha():
            return CountryRecord(upper, upper)
        return None
    # Direct ISO Alpha-2.
    if len(upper) == 2 and upper.isalpha():
        country = pycountry.countries.get(alpha_2=upper)
        if country is not None:
            return CountryRecord(country.alpha_2, country.name)
    # Direct ISO Alpha-3.
    if len(upper) == 3 and upper.isalpha():
        country = pycountry.countries.get(alpha_3=upper)
        if country is not None:
            return CountryRecord(country.alpha_2, country.name)
    # Spanish-language alias.
    iso = _SPANISH_ALIASES.get(lower)
    if iso:
        country = pycountry.countries.get(alpha_2=iso)
        if country is not None:
            return CountryRecord(country.alpha_2, country.name)
    # English/official name search.
    try:
        matches = pycountry.countries.search_fuzzy(key)
    except (LookupError, AttributeError):
        matches = []
    if matches:
        country = matches[0]
        return CountryRecord(country.alpha_2, country.name)
    return None
