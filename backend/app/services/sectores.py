"""Los sectores de empresa: una lista corta, no texto libre (1/11/2026).

Para que el filtro por sector agrupe de verdad (el corte que hace falta en
láser es «centro educativo» frente a empresa). Aprobada por Bart. Lo que una
empresa tuviera escrito antes se conserva tal cual mientras nadie lo cambie:
no se valida lo guardado, solo lo que se escribe nuevo.
"""
from __future__ import annotations

SECTORES: tuple[str, ...] = (
    "Centro educativo",
    "Administración pública",
    "Rotulación y señalética",
    "Imprenta y artes gráficas",
    "Personalización y regalo promocional",
    "Textil y estampación",
    "Packaging y embalaje",
    "Industria y fabricación",
    "Arquitectura, diseño y maquetas",
    "Artesanía, joyería y decoración",
    "Duplicación y audiovisual",
    "Distribuidor / revendedor",
    "Vending",
    "Maker / particular",
    "Otro",
)

_POR_CLAVE = {s.casefold(): s for s in SECTORES}


def valida_sector(nuevo: str | None, actual: str | None = None) -> str | None:
    """El sector que se guarda: uno de la lista (sin importar mayúsculas),
    vacío, o el que ya tenía la empresa (el texto anterior se conserva si no
    se toca). ValueError si es otro texto."""
    texto = (nuevo or "").strip()
    if not texto:
        return None
    if texto.casefold() in _POR_CLAVE:
        return _POR_CLAVE[texto.casefold()]
    if actual is not None and texto == actual.strip():
        return actual
    raise ValueError("sector")
