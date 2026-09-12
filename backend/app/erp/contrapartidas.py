"""ERP-F5 — contrapartidas de cobro (destino donde entra el dinero).

Es lo que FACTUSOL pide en su diálogo «Apunte de cobro» y lo que guarda en
`F_COB.CPACOB` / `F_LCO.CPALCO`. NO es la forma de pago (F3-fix1 lo resolvía
contra ese catálogo: incorrecto). Verificado con cobros reales: la
contrapartida cuadra con la SERIE de la factura (1-260004 → 6 Bomedia
Sabadell; 5-260004 → 8 Streamtec Sabadell; 4-260004 → 3 Lambert Open Bank).

La tabla de FACTUSOL con este catálogo NO se ha localizado (~44 nombres
sondeados) y no se sigue buscando: el catálogo es CONFIGURABLE en
`/erp/settings` (blob `factusol_series_json`, claves `contrapartidas` y
`paypal_contrapartidas_by_store`), precargado con las 14 de Bart. Lo esencial
es que al registrar un cobro (F-4-B) se envíe el CÓDIGO correcto.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.integrations.factusol.catalogs import names_index, normalize_code, resolve_name

#: Las 14 contrapartidas de Bart (código → descripción), tal como las lista
#: el diálogo de cobro de FACTUSOL. Editables en /erp/settings.
DEFAULT_CONTRAPARTIDAS: list[tuple[int, str]] = [
    (1, "Bomedia Open Bank"),
    (2, "MQ Europe Belfius"),
    (3, "Lambert Open Bank"),
    (4, "Lambert Sabadell"),
    (5, "Lambert Santander"),
    (6, "Bomedia Sabadell"),
    (7, "Streamtec Open Banc"),
    (8, "Streamtec Sabadell"),
    (9, "Caja Bomedia"),
    (10, "Cash"),
    (11, "Paypal Bomedia"),
    (12, "Paypal MQ Europe"),
    (13, "Abono"),
    (14, "Paypal Streamtec"),
]

#: Tiendas (origen del pedido) → contrapartida PayPal. No viene de ningún
#: extracto: se deduce del origen del pedido. Configurable.
PAYPAL_STORES: list[tuple[str, str]] = [
    ("artisjet", "artisJet"),
    ("boprint", "boprint"),
    ("fluxlasers", "fluxlasers"),
]
DEFAULT_PAYPAL_BY_STORE: dict[str, str] = {
    "artisjet": "12",
    "boprint": "14",
    "fluxlasers": "14",
}
#: Alias de slug de tienda que usa el resto de la app (`flux` en Woo).
_STORE_ALIASES = {"flux": "fluxlasers", "flux-lasers": "fluxlasers", "artisjetspain": "artisjet"}


def normalize_store(store: Any) -> str:
    key = str(store or "").strip().lower()
    return _STORE_ALIASES.get(key, key)


def _stored_series(session: Session) -> dict[str, Any]:
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    return series_config(session)


def contrapartidas_config(raw: Any) -> list[dict[str, str]]:
    """Catálogo normalizado (`[{codigo, nombre}]`, ordenado por código) a
    partir de lo guardado; si no hay nada válido, las 14 por defecto."""
    items: list[dict[str, str]] = []
    if isinstance(raw, list):
        seen: set[str] = set()
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            codigo = normalize_code(entry.get("codigo"))
            nombre = str(entry.get("nombre") or "").strip()
            if not codigo or not nombre or codigo in seen:
                continue
            seen.add(codigo)
            items.append({"codigo": codigo, "nombre": nombre})
    if not items:
        items = [{"codigo": str(c), "nombre": n} for c, n in DEFAULT_CONTRAPARTIDAS]
    items.sort(key=lambda it: (not it["codigo"].isdigit(), int(it["codigo"])
                               if it["codigo"].isdigit() else 0, it["codigo"]))
    return items


def validate_contrapartidas(raw: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Valida lo que llega del PATCH de ajustes: código numérico > 0 único y
    nombre no vacío. Lanza ValueError con el motivo."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in raw:
        codigo_raw = str((entry or {}).get("codigo") or "").strip()
        nombre = str((entry or {}).get("nombre") or "").strip()
        if not codigo_raw and not nombre:
            continue  # fila vacía del formulario
        if not codigo_raw.isdigit() or int(codigo_raw) <= 0:
            raise ValueError(f"código de contrapartida inválido: {codigo_raw!r}")
        codigo = normalize_code(codigo_raw)
        if codigo in seen:
            raise ValueError(f"código de contrapartida repetido: {codigo}")
        if not nombre:
            raise ValueError(f"la contrapartida {codigo} no tiene descripción")
        seen.add(codigo)
        out.append({"codigo": codigo, "nombre": nombre})
    if not out:
        raise ValueError("el catálogo de contrapartidas no puede quedar vacío")
    return out


def contrapartidas(session: Session) -> list[dict[str, str]]:
    return contrapartidas_config(_stored_series(session).get("contrapartidas"))


def contrapartida_names(session: Session) -> dict[str, str]:
    return names_index(contrapartidas(session))


def resolve_contrapartida(session: Session, code: Any) -> str | None:
    return resolve_name(contrapartida_names(session), code)


def _norm_account_name(text: Any) -> str:
    """«Bomedia (Sabadell)» → «bomedia sabadell»: minúsculas, sin paréntesis
    ni puntuación, espacios colapsados — para casar el nombre tal como viene
    en el Excel de conciliación con el del catálogo."""
    import re  # noqa: PLC0415

    lowered = str(text or "").lower()
    lowered = re.sub(r"[()\[\]{},;:/\\\-_·.]+", " ", lowered)
    return " ".join(lowered.split())


def resolve_contrapartida_code(session: Session, cuenta: Any) -> str | None:
    """ERP-F4-B — código de contrapartida a partir de un CÓDIGO («6», «006»)
    o de un NOMBRE de cuenta tal como aparece en el Excel de conciliación
    («Bomedia (Sabadell)», «Paypal MQ Europe»). Tolera paréntesis y el orden
    de las palabras. `None` si no casa con ninguna del catálogo: nunca se
    registra un cobro contra una cuenta adivinada."""
    raw = str(cuenta or "").strip()
    if not raw:
        return None
    items = contrapartidas(session)
    code = normalize_code(raw)
    if code.isdigit():
        return code if any(normalize_code(it["codigo"]) == code for it in items) else None
    wanted = _norm_account_name(raw)
    for it in items:
        if _norm_account_name(it["nombre"]) == wanted:
            return normalize_code(it["codigo"])
    wanted_tokens = set(wanted.split())
    for it in items:
        if set(_norm_account_name(it["nombre"]).split()) == wanted_tokens:
            return normalize_code(it["codigo"])
    return None


#: Palabra de la empresa emisora por serie, para sugerir la cuenta del cobro
#: (serie 5 → «Streamtec Sabadell», 1 → «Bomedia Sabadell», 2 → «MQ Europe
#: Belfius»). Los nombres configurados en Ajustes (`series_names`) mandan.
_SERIE_COMPANY_WORDS: dict[int, str] = {1: "bomedia", 5: "streamtec", 2: "mq europe"}
_BANK_PREFERENCE = ("sabadell", "belfius", "open bank", "open banc", "santander")


def suggest_contrapartida(
    session: Session, *, serie: int | None, forma_nombre: Any = None,
    store: Any = None,
) -> dict[str, str] | None:
    """Cuenta SUGERIDA por defecto para el modal de cobro (solo una sugerencia:
    el operador la cambia si quiere). PayPal → la contrapartida PayPal de la
    tienda (o la de la empresa emisora); si no, la cuenta bancaria de la
    empresa emisora de la serie (Sabadell / Belfius antes que el resto)."""
    from app.integrations.factusol.service import series_names  # noqa: PLC0415

    items = contrapartidas(session)
    if not items:
        return None
    word = None
    if serie is not None:
        configured = series_names(session).get(int(serie))
        word = (
            _norm_account_name(configured).split(" ")[0] if configured
            else _SERIE_COMPANY_WORDS.get(int(serie))
        )
    forma = _norm_account_name(forma_nombre)
    if "paypal" in forma:
        hit = paypal_contrapartida_for_store(session, store) if store else None
        if hit:
            return hit
        for it in items:
            name = _norm_account_name(it["nombre"])
            if "paypal" in name and word and word in name:
                return {"codigo": normalize_code(it["codigo"]), "nombre": it["nombre"]}
    if not word:
        return None
    candidates = [
        it for it in items
        if word in _norm_account_name(it["nombre"])
        and "paypal" not in _norm_account_name(it["nombre"])
    ]
    for bank in _BANK_PREFERENCE:
        for it in candidates:
            if bank in _norm_account_name(it["nombre"]):
                return {"codigo": normalize_code(it["codigo"]), "nombre": it["nombre"]}
    if candidates:
        it = candidates[0]
        return {"codigo": normalize_code(it["codigo"]), "nombre": it["nombre"]}
    return None


def paypal_by_store_config(raw: Any) -> dict[str, str]:
    """`{tienda → código}` guardado, completado con los valores iniciales para
    las tiendas que no tengan nada."""
    out = dict(DEFAULT_PAYPAL_BY_STORE)
    if isinstance(raw, dict):
        for store, code in raw.items():
            key = normalize_store(store)
            value = normalize_code(code)
            if key and value:
                out[key] = value
    return out


def paypal_contrapartida_for_store(session: Session, store: Any) -> dict[str, str] | None:
    """Contrapartida PayPal de una tienda (`{codigo, nombre}`), o None si la
    tienda no está mapeada."""
    key = normalize_store(store)
    if not key:
        return None
    mapping = paypal_by_store_config(_stored_series(session).get("paypal_contrapartidas_by_store"))
    codigo = mapping.get(key)
    if not codigo:
        return None
    return {"codigo": codigo, "nombre": resolve_contrapartida(session, codigo) or codigo}


__all__ = [
    "DEFAULT_CONTRAPARTIDAS",
    "DEFAULT_PAYPAL_BY_STORE",
    "PAYPAL_STORES",
    "contrapartida_names",
    "contrapartidas",
    "contrapartidas_config",
    "normalize_store",
    "paypal_by_store_config",
    "paypal_contrapartida_for_store",
    "resolve_contrapartida",
    "resolve_contrapartida_code",
    "validate_contrapartidas",
]
