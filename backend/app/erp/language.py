"""ERP-E4-fix2 — idioma de los documentos: mapa país → idioma, ÚNICO y
compartido.

El discovery en producción (`scripts/woo_language_discovery.py`) confirmó
que NINGUNA de las tres tiendas envía el idioma en el pedido (sin WPML/
Polylang, sin `locale`): lo único disponible es el país. Bart decidió
resolver por país con una tabla que NO deja ambigüedades — prefiere un
idioma razonable a un campo vacío (Suiza → inglés, Bélgica → francés, y
cualquier país no listado → inglés).

Este mapa lo usan por igual la detección en la importación de WooCommerce
(`woocommerce.mapper.detect_order_language`) y la cascada de resolución de
idioma del PDF (`factusol_pdf.suggest_pdf_language`). Vive aquí, en un
módulo ligero (sin dependencias pesadas), para que ambos lo importen sin
duplicarlo ni arrastrar reportlab al mapper.
"""
from __future__ import annotations

from typing import Any

#: Idiomas que el motor de PDF sabe rotular (ISO 639-1).
SUPPORTED_LANGS: tuple[str, ...] = ("es", "en", "de", "fr", "nl")

#: País (ISO 3166-1 alfa-2) → idioma. Decisión de Bart (E4-fix2): sin
#: ambigüedad. BE→fr y CH→en son elecciones deliberadas, no deducciones
#: neutras. Cualquier país NO listado cae a `DEFAULT_COUNTRY_LANGUAGE`.
COUNTRY_LANGUAGE: dict[str, str] = {
    "ES": "es",
    "FR": "fr",
    "DE": "de",
    "AT": "de",
    "NL": "nl",
    "BE": "fr",
    "CH": "en",
    "GB": "en",
    "IE": "en",
}

#: Idioma para un país CON valor pero fuera del mapa. «Cualquier otro país
#: → en» (E4-fix2): mejor inglés que vacío.
DEFAULT_COUNTRY_LANGUAGE = "en"


def normalize_language(value: Any) -> str | None:
    """`es_ES` / `fr-FR` / `NL` → subtag primario en minúscula, solo si es
    uno de los idiomas soportados; `None` en cualquier otro caso."""
    raw = str(value or "").strip().lower().replace("_", "-")
    lang = raw.split("-")[0]
    return lang if lang in SUPPORTED_LANGS else None


def language_for_country(country: Any) -> str | None:
    """Idioma derivado del país (ISO alfa-2, tolera minúsculas/espacios).

    `None` SOLO si el país viene vacío — ahí no hay nada de lo que deducir y
    la cascada sigue su curso. Un país con valor SIEMPRE resuelve: al idioma
    del mapa, o a inglés si no está listado (decisión E4-fix2)."""
    code = str(country or "").strip().upper()
    if not code:
        return None
    return COUNTRY_LANGUAGE.get(code, DEFAULT_COUNTRY_LANGUAGE)
