"""ERP-E4 — idioma de los documentos: país → idioma, con normalización.

fix2 resolvía el idioma por país, pero asumía que el campo país venía en
ISO-3166 alfa-2. El dry-run del backfill en producción destapó que NO está
normalizado: llega de FACTUSOL, WooCommerce y AgileCRM en ISO2, nombre en
español, inglés o francés («ESPAÑA», «SPAIN», «FRANCE», «ALGÉRIE»…). Con el
mapa de fix2, todo lo que no fuera ISO2 caía a inglés → ~3.000 empresas
españolas habrían recibido documentos en inglés.

fix3 arregla el orden: primero se NORMALIZA cualquier valor de país a ISO2
(`normalize_country`) — sin acentos, con una tabla de alias es/en/fr/de/nl
y pycountry para el resto de nombres oficiales — y solo entonces se deriva
el idioma. Un país que no se reconoce devuelve `None` (no se inventa
idioma: la cascada sigue hacia la empresa emisora).

Módulo ligero (sin reportlab) compartido por la importación de WooCommerce
y la cascada de idioma del PDF.
"""
from __future__ import annotations

import logging
import unicodedata
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

#: Idiomas que el motor de PDF sabe rotular (ISO 639-1).
SUPPORTED_LANGS: tuple[str, ...] = ("es", "en", "de", "fr", "nl")

# --- país (ISO2) → idioma ---------------------------------------------------
#
# Decisiones de Bart (E4-fix2 + fix3). El francés es la lengua comercial en
# los territorios franceses de ultramar y en el Magreb / África francófona,
# así que van a `fr`. Andorra → español. Suiza sigue en inglés (junto con
# GB/IE y «todos los demás», que caen a `DEFAULT_COUNTRY_LANGUAGE`).

_ES_COUNTRIES = {"ES", "AD"}
_FR_COUNTRIES = {
    "FR", "BE", "LU", "MC",
    # Territorios franceses de ultramar.
    "MQ", "GP", "RE", "YT", "PF", "NC", "GF",
    # Magreb y África francófona.
    "MA", "DZ", "TN", "CI", "GA", "NE", "SN", "CM", "ML", "BF", "TG",
    "BJ", "CD", "CG", "MG",
}
_DE_COUNTRIES = {"DE", "AT"}
_NL_COUNTRIES = {"NL"}

COUNTRY_LANGUAGE: dict[str, str] = {
    **{c: "es" for c in _ES_COUNTRIES},
    **{c: "fr" for c in _FR_COUNTRIES},
    **{c: "de" for c in _DE_COUNTRIES},
    **{c: "nl" for c in _NL_COUNTRIES},
}

#: Idioma para un país reconocido (ISO2) pero fuera de los grupos de arriba
#: — «todos los demás», incluido CH, GB, IE (decisión de Bart).
DEFAULT_COUNTRY_LANGUAGE = "en"


def normalize_language(value: Any) -> str | None:
    """`es_ES` / `fr-FR` / `NL` → subtag primario en minúscula, solo si es
    uno de los idiomas soportados; `None` en cualquier otro caso."""
    raw = str(value or "").strip().lower().replace("_", "-")
    lang = raw.split("-")[0]
    return lang if lang in SUPPORTED_LANGS else None


# --- normalización del país -------------------------------------------------


def _strip_accents(text: str) -> str:
    """`ESPAÑA` → `ESPANA`, `ALGÉRIE` → `ALGERIE`, `RÉUNION` → `REUNION`.
    La ñ se descompone en n (aceptable para casar nombres de país)."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _norm_key(value: Any) -> str:
    """Clave canónica para casar: sin acentos, mayúsculas, espacios
    colapsados y sin puntuación de separación (comas, puntos)."""
    text = _strip_accents(str(value or "").strip().upper())
    for ch in (",", ".", "-", "/", "'", "’", "`"):
        text = text.replace(ch, " ")
    return " ".join(text.split())


#: Alias nombre→ISO2 en los idiomas en los que llega el campo (es/en/fr/de/
#: nl), YA en forma canónica (`_norm_key`: sin acentos, mayúsculas). Cubre
#: lo que pycountry NO resuelve (nombres no ingleses) y los casos del
#: dry-run real. pycountry aporta el resto de nombres oficiales en inglés.
_COUNTRY_ALIASES: dict[str, str] = {
    # España / Andorra
    "ESPANA": "ES", "SPAIN": "ES", "ESPAGNE": "ES", "SPANIEN": "ES",
    "SPANJE": "ES", "ESP": "ES", "ANDORRA": "AD", "ANDORRE": "AD",
    # Francia + francófonos con nombre no-inglés
    "FRANCIA": "FR", "FRANCE": "FR", "FRANKREICH": "FR", "FRANKRIJK": "FR",
    "BELGICA": "BE", "BELGIQUE": "BE", "BELGIUM": "BE", "BELGIE": "BE",
    "BELGIEN": "BE",
    "LUXEMBURGO": "LU", "LUXEMBOURG": "LU",
    "MONACO": "MC",
    "MARTINICA": "MQ", "MARTINIQUE": "MQ",
    "GUADALUPE": "GP", "GUADELOUPE": "GP",
    "REUNION": "RE", "REUNION ILE DE LA": "RE", "LA REUNION": "RE",
    "MAYOTTE": "YT",
    "POLINESIA FRANCESA": "PF", "FRENCH POLYNESIA": "PF",
    "POLYNESIE FRANCAISE": "PF",
    "NUEVA CALEDONIA": "NC", "NEW CALEDONIA": "NC",
    "NOUVELLE CALEDONIE": "NC",
    "GUAYANA FRANCESA": "GF", "FRENCH GUIANA": "GF", "GUYANE": "GF",
    "GUYANE FRANCAISE": "GF",
    "MARRUECOS": "MA", "MOROCCO": "MA", "MAROC": "MA", "MAROKKO": "MA",
    "ARGELIA": "DZ", "ALGERIA": "DZ", "ALGERIE": "DZ", "ALGERIEN": "DZ",
    "TUNEZ": "TN", "TUNISIA": "TN", "TUNISIE": "TN", "TUNESIEN": "TN",
    "COSTA DE MARFIL": "CI", "IVORY COAST": "CI", "COTE D IVOIRE": "CI",
    "COTE DIVOIRE": "CI",
    "GABON": "GA", "GABON ": "GA",
    "NIGER": "NE",
    "SENEGAL": "SN",
    "CAMERUN": "CM", "CAMEROON": "CM", "CAMEROUN": "CM",
    "MALI": "ML",
    "BURKINA FASO": "BF",
    "TOGO": "TG",
    "BENIN": "BJ",
    "REPUBLICA DEMOCRATICA DEL CONGO": "CD", "DR CONGO": "CD",
    "CONGO KINSHASA": "CD",
    "CONGO": "CG", "REPUBLICA DEL CONGO": "CG", "CONGO BRAZZAVILLE": "CG",
    "MADAGASCAR": "MG",
    # Alemania / Austria
    "ALEMANIA": "DE", "GERMANY": "DE", "ALLEMAGNE": "DE",
    "DEUTSCHLAND": "DE", "DUITSLAND": "DE",
    "AUSTRIA": "AT", "AUTRICHE": "AT", "OSTERREICH": "AT", "OOSTENRIJK": "AT",
    # Países Bajos
    "PAISES BAJOS": "NL", "NETHERLANDS": "NL", "PAYS BAS": "NL",
    "HOLANDA": "NL", "HOLLAND": "NL", "NEDERLAND": "NL", "NIEDERLANDE": "NL",
    "THE NETHERLANDS": "NL",
    # Suiza / Reino Unido / Irlanda (→ en)
    "SUIZA": "CH", "SWITZERLAND": "CH", "SUISSE": "CH", "SCHWEIZ": "CH",
    "ZWITSERLAND": "CH", "SVIZZERA": "CH",
    "REINO UNIDO": "GB", "UNITED KINGDOM": "GB", "ROYAUME UNI": "GB",
    "GREAT BRITAIN": "GB", "UK": "GB", "INGLATERRA": "GB", "ENGLAND": "GB",
    "IRLANDA": "IE", "IRELAND": "IE", "IRLANDE": "IE",
    # Otros habituales del dry-run (van a `en` por defecto, pero se
    # reconocen para no ensuciar el informe de no-reconocidos).
    "PORTUGAL": "PT",
    "ITALIA": "IT", "ITALY": "IT", "ITALIE": "IT", "ITALIEN": "IT",
    "ESTADOS UNIDOS": "US", "UNITED STATES": "US", "USA": "US",
    "EEUU": "US", "ETATS UNIS": "US",
    # China / Taiwán — variantes largas que pycountry no casa por `name`
    # (los pendientes de E4-fix3).
    "CHINA": "CN", "PEOPLE S REPUBLIC OF CHINA": "CN",
    "REPUBLICA POPULAR CHINA": "CN",
    "TAIWAN": "TW", "TAIWAN CHINA": "TW", "TAIWAN PROVINCE OF CHINA": "TW",
}

#: ISO2 válidos (pycountry). Un valor de 2 letras solo se acepta como ISO2
#: si de verdad existe — «XX» no es un país.
#: `_NUMERIC_TO_ISO2`: código ISO 3166-1 numérico («724»→ES, «040»→AT) → ISO2.
#: Se saca ENTERO de pycountry (no una tabla manual): F_CLI guarda el país en
#: PAICLI como numérico y hasta ahora solo se entendían nombres.
try:  # pragma: no cover - depende de pycountry
    import pycountry

    _ISO2_CODES: frozenset[str] = frozenset(c.alpha_2 for c in pycountry.countries)
    _NUMERIC_TO_ISO2: dict[str, str] = {
        c.numeric: c.alpha_2
        for c in pycountry.countries if getattr(c, "numeric", None)
    }
except Exception:  # noqa: BLE001 — sin pycountry, solo alias + los del mapa
    pycountry = None  # type: ignore[assignment]
    _ISO2_CODES = frozenset(COUNTRY_LANGUAGE) | {"CH", "GB", "IE", "PT", "IT", "US"}
    #: Respaldo mínimo con los numéricos vistos en producción (por si no hay
    #: pycountry). La ruta normal usa el mapa completo de arriba.
    _NUMERIC_TO_ISO2 = {
        "724": "ES", "250": "FR", "276": "DE", "528": "NL", "756": "CH",
        "040": "AT", "620": "PT", "380": "IT", "056": "BE", "826": "GB",
        "372": "IE", "442": "LU", "492": "MC", "840": "US",
    }


def _numeric_lookup(key: str) -> str | None:
    """Código ISO 3166-1 numérico → ISO2. Solo si el valor es ENTERAMENTE
    numérico y de 1 a 3 dígitos (un código postal de 5 cifras NO es un país).
    Acepta con y sin ceros a la izquierda («040» y «40» → AT)."""
    if not (key.isdigit() and 1 <= len(key) <= 3):
        return None
    return _NUMERIC_TO_ISO2.get(key.zfill(3))


def _pycountry_lookup(key: str) -> str | None:
    """Nombre oficial (inglés) → ISO2 vía pycountry, por coincidencia EXACTA
    de nombre. `key` ya viene canónico (sin acentos, mayúsculas); pycountry
    casa mejor con Title.

    No se usa `search_fuzzy`: casaba basura de 2-3 letras con países al azar
    («XX» → Malta). Un token que no es ISO2 válido ni está en los alias, y
    no coincide EXACTO con un nombre oficial, se declara no reconocido."""
    if pycountry is None or len(key) < 4:
        return None
    title = key.title()
    for finder in (
        lambda: pycountry.countries.get(name=title),
        lambda: pycountry.countries.get(official_name=title),
        lambda: pycountry.countries.get(common_name=title),
    ):
        try:
            hit = finder()
        except (KeyError, LookupError):
            hit = None
        if hit is not None:
            return hit.alpha_2
    return None


def normalize_country(
    value: Any, *, unresolved: Counter | None = None,
) -> str | None:
    """Cualquier valor del campo país → ISO-3166 alfa-2, o `None` si no se
    reconoce (NO se adivina). Orden: ISO2 válido tal cual → tabla de alias
    (es/en/fr/de/nl) → pycountry por nombre oficial inglés.

    `unresolved` (opcional): Counter donde se acumulan los valores crudos no
    reconocidos, para informar en agregado (lo usa el backfill) en vez de
    loguear uno por uno."""
    key = _norm_key(value)
    if not key:
        return None
    # Código ISO numérico (PAICLI de F_CLI): antes que nada, porque «040» no
    # es alfabético ni casa con ningún alias.
    numeric = _numeric_lookup(key)
    if numeric is not None:
        return numeric
    if len(key) == 2 and key.isalpha() and key in _ISO2_CODES:
        return key
    alias = _COUNTRY_ALIASES.get(key)
    if alias is not None:
        return alias
    iso = _pycountry_lookup(key)
    if iso is not None:
        return iso
    if unresolved is not None:
        unresolved[str(value).strip()] += 1
    return None


def language_for_country(
    value: Any, *, unresolved: Counter | None = None,
) -> str | None:
    """Idioma derivado del país. `None` si el país viene vacío o no se
    reconoce — ahí la cascada sigue hacia la empresa emisora, nunca se
    inventa. Un país reconocido SIEMPRE resuelve: al idioma de su grupo o a
    inglés (todos los demás)."""
    iso = normalize_country(value, unresolved=unresolved)
    if iso is None:
        return None
    return COUNTRY_LANGUAGE.get(iso, DEFAULT_COUNTRY_LANGUAGE)
