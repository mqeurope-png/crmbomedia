"""ERP-F5 — contrapartidas de cobro (destino donde entra el dinero).

Es lo que FACTUSOL pide en su diálogo «Apunte de cobro» y lo que guarda en
`F_COB.CPACOB` / `F_LCO.CPALCO`. NO es la forma de pago (F3-fix1 lo resolvía
contra ese catálogo: incorrecto). Verificado con cobros reales: la
contrapartida cuadra con la SERIE de la factura (1-260004 → 6 Bomedia
Sabadell; 5-260004 → 8 Streamtec Sabadell; 4-260004 → 3 Lambert Open Bank).

La tabla de FACTUSOL con este catálogo NO se ha localizado (~44 nombres
sondeados) y no se sigue buscando: el catálogo es CONFIGURABLE en
`/erp/settings` (blob `factusol_series_json`, claves `contrapartidas` y
`contrapartida_rules`), precargado con las 14 de Bart. Lo esencial es que al
registrar un cobro (F-4-B) se envíe el CÓDIGO correcto.

Contrapartida SUGERIDA (solo una sugerencia, editable):
  1. reglas tienda × método de pago (`contrapartida_rules`), de arriba abajo,
     primera que casa: tienda (o todas) + método de pago de WooCommerce (id del
     gateway o título en la tienda; también la forma de pago de FACTUSOL),
     coincidencia exacta o «contiene», sin distinguir mayúsculas. Una regla cuya
     contrapartida no está en el catálogo NO se aplica (nunca se sugiere una
     cuenta con la que el cobro daría 400);
  2. si ninguna casa, la cuenta bancaria de la empresa emisora de la serie.
Las reglas iniciales son las de PayPal por tienda de antes (artisJet → 12;
boprint y fluxlasers → 14; `paypal_contrapartidas_by_store`, que queda como
dato heredado) más artisJet + tarjeta de Mollie («Carte») → 15.
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

#: Tiendas (origen del pedido) que se pueden elegir en las reglas.
STORES: list[tuple[str, str]] = [
    ("artisjet", "artisJet"),
    ("boprint", "boprint"),
    ("fluxlasers", "fluxlasers"),
]
#: Nombre heredado (PayPal por tienda).
PAYPAL_STORES = STORES
STORE_LABELS: dict[str, str] = dict(STORES)
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


def suggest_contrapartida_explained(
    session: Session, *, serie: int | None, forma_nombre: Any = None,
    store: Any = None, payment_method: Any = None, payment_method_title: Any = None,
) -> tuple[dict[str, str] | None, str | None]:
    """Cuenta SUGERIDA para el cobro y POR QUÉ (solo una sugerencia: el
    operador la cambia si quiere).

    1. Reglas tienda × método de pago, de arriba abajo (primera que casa),
       contra el título del método en la tienda («Carte»), el id del gateway
       (`mollie_wc_gateway_creditcard`) y la forma de pago de FACTUSOL. Las
       reglas con una contrapartida que no está en el catálogo se saltan.
    2. PayPal sin regla para su tienda → la PayPal de la empresa emisora.
    3. Si no, la cuenta bancaria de la empresa emisora de la serie
       (Sabadell / Belfius antes que el resto)."""
    from app.integrations.factusol.service import series_names  # noqa: PLC0415

    items = contrapartidas(session)
    if not items:
        return None, None
    by_code = {normalize_code(it["codigo"]): it["nombre"] for it in items}
    textos = [payment_method_title, payment_method, forma_nombre]
    metodo_visible = (str(payment_method_title or "").strip()
                      or str(payment_method or "").strip()
                      or str(forma_nombre or "").strip())

    for rule in contrapartida_rules(session):
        codigo = normalize_code(rule["contrapartida"])
        if codigo not in by_code:
            continue    # la cuenta aún no existe en el catálogo: no se sugiere
        hit = rule_matches(rule, store=store, textos=textos)
        if hit is None:
            continue
        partes = []
        tienda = store_label(rule.get("tienda") or store) if (rule.get("tienda") or store) else ""
        if tienda:
            partes.append(f"tienda {tienda}")
        partes.append(f"método {metodo_visible or hit}")
        return {"codigo": codigo, "nombre": by_code[codigo]}, " · ".join(partes)

    word = None
    if serie is not None:
        configured = series_names(session).get(int(serie))
        word = (
            _norm_account_name(configured).split(" ")[0] if configured
            else _SERIE_COMPANY_WORDS.get(int(serie))
        )
    if any("paypal" in _norm_text(t) for t in textos):
        for it in items:
            name = _norm_account_name(it["nombre"])
            if "paypal" in name and word and word in name:
                return (
                    {"codigo": normalize_code(it["codigo"]), "nombre": it["nombre"]},
                    f"método {metodo_visible} · PayPal de la serie {serie}",
                )
    if not word:
        return None, None
    candidates = [
        it for it in items
        if word in _norm_account_name(it["nombre"])
        and "paypal" not in _norm_account_name(it["nombre"])
    ]
    motivo = f"cuenta de la serie {serie}"
    for bank in _BANK_PREFERENCE:
        for it in candidates:
            if bank in _norm_account_name(it["nombre"]):
                return {"codigo": normalize_code(it["codigo"]), "nombre": it["nombre"]}, motivo
    if candidates:
        it = candidates[0]
        return {"codigo": normalize_code(it["codigo"]), "nombre": it["nombre"]}, motivo
    return None, None


def suggest_contrapartida(
    session: Session, *, serie: int | None, forma_nombre: Any = None,
    store: Any = None, payment_method: Any = None, payment_method_title: Any = None,
) -> dict[str, str] | None:
    """Solo la cuenta sugerida (`{codigo, nombre}`); ver
    `suggest_contrapartida_explained` para el porqué."""
    return suggest_contrapartida_explained(
        session, serie=serie, forma_nombre=forma_nombre, store=store,
        payment_method=payment_method, payment_method_title=payment_method_title,
    )[0]


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


# --- reglas tienda × método de pago → contrapartida -------------------------

#: Coincidencias admitidas en una regla.
MATCH_EXACT = "exacta"
MATCH_CONTAINS = "contiene"
MATCH_KINDS = (MATCH_EXACT, MATCH_CONTAINS)

#: Tarjeta vía Mollie en artisJet Europe («Carte» en la tienda; gateway
#: `mollie_wc_gateway_creditcard`): entra por Mollie en Belfius → 15 (Tarjetas
#: Mollie Belfius). Confirmado por Bart: solo artisJet usa Mollie.
MOLLIE_CARD_RULES: list[dict[str, str]] = [
    {"tienda": "artisjet", "metodo": "Carte", "coincidencia": MATCH_EXACT,
     "contrapartida": "15"},
    {"tienda": "artisjet", "metodo": "mollie_wc_gateway_creditcard",
     "coincidencia": MATCH_EXACT, "contrapartida": "15"},
]


def default_contrapartida_rules(paypal_raw: Any = None) -> list[dict[str, str]]:
    """Reglas iniciales: las de PayPal por tienda de antes (con lo que hubiera
    guardado Bart) + la tarjeta de Mollie de artisJet."""
    paypal = [
        {"tienda": store, "metodo": "paypal", "coincidencia": MATCH_CONTAINS,
         "contrapartida": code}
        for store, code in paypal_by_store_config(paypal_raw).items()
    ]
    return paypal + [dict(r) for r in MOLLIE_CARD_RULES]


def _norm_text(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _rule_from(entry: Any) -> dict[str, str] | None:
    """Regla normalizada, o None si no vale (se ignora al leer)."""
    if not isinstance(entry, dict):
        return None
    tienda = normalize_store(entry.get("tienda"))
    if tienda in ("todas", "*"):
        tienda = ""
    metodo = str(entry.get("metodo") or "").strip()[:80]
    coincidencia = str(entry.get("coincidencia") or MATCH_CONTAINS).strip().lower()
    codigo = normalize_code(entry.get("contrapartida"))
    if not metodo or coincidencia not in MATCH_KINDS or not codigo.isdigit():
        return None
    return {"tienda": tienda, "metodo": metodo, "coincidencia": coincidencia,
            "contrapartida": codigo}


def contrapartida_rules_config(series: dict[str, Any] | None) -> list[dict[str, str]]:
    """Reglas guardadas (en su orden) o, si nunca se guardaron, las iniciales
    (PayPal por tienda migrado + Mollie). Una lista guardada vacía = sin
    reglas (Bart las quitó todas)."""
    series = series or {}
    raw = series.get("contrapartida_rules")
    if isinstance(raw, list):
        return [r for r in (_rule_from(e) for e in raw) if r is not None]
    return default_contrapartida_rules(series.get("paypal_contrapartidas_by_store"))


def validate_contrapartida_rules(raw: list[Any]) -> list[dict[str, str]]:
    """Valida lo que llega del PATCH de ajustes (el orden importa: primera
    regla que casa). Filas vacías se ignoran. Lanza ValueError con el motivo.
    La contrapartida debe ser un código numérico; si no está en el catálogo, la
    regla se guarda pero no se aplica hasta que exista."""
    out: list[dict[str, str]] = []
    for i, entry in enumerate(raw, start=1):
        entry = entry if isinstance(entry, dict) else {}
        metodo = str(entry.get("metodo") or "").strip()
        codigo_raw = str(entry.get("contrapartida") or "").strip()
        if not metodo and not codigo_raw:
            continue  # fila vacía del formulario
        if not metodo:
            raise ValueError(f"regla {i}: falta el método de pago")
        if len(metodo) > 80:
            raise ValueError(f"regla {i}: el método de pago es demasiado largo")
        coincidencia = str(entry.get("coincidencia") or MATCH_CONTAINS).strip().lower()
        if coincidencia not in MATCH_KINDS:
            raise ValueError(f"regla {i}: coincidencia inválida {coincidencia!r}")
        if not codigo_raw.isdigit() or int(codigo_raw) <= 0:
            raise ValueError(f"regla {i}: contrapartida inválida {codigo_raw!r}")
        tienda = normalize_store(entry.get("tienda"))
        if tienda in ("todas", "*"):
            tienda = ""
        out.append({"tienda": tienda, "metodo": metodo, "coincidencia": coincidencia,
                    "contrapartida": normalize_code(codigo_raw)})
    return out


def contrapartida_rules(session: Session) -> list[dict[str, str]]:
    return contrapartida_rules_config(_stored_series(session))


def rule_matches(
    rule: dict[str, str], *, store: Any, textos: list[Any],
) -> str | None:
    """¿Casa la regla con la tienda y alguno de los textos del método de pago
    (título en la tienda, id del gateway, forma de pago)? Devuelve el texto que
    casó, o None. Sin distinguir mayúsculas; exacta o «contiene»."""
    if rule.get("tienda") and rule["tienda"] != normalize_store(store):
        return None
    patron = _norm_text(rule.get("metodo"))
    if not patron:
        return None
    for texto in textos:
        norm = _norm_text(texto)
        if not norm:
            continue
        if rule.get("coincidencia") == MATCH_EXACT:
            if norm == patron:
                return str(texto).strip()
        elif patron in norm:
            return str(texto).strip()
    return None


def store_label(store: Any) -> str:
    key = normalize_store(store)
    return STORE_LABELS.get(key, key)


__all__ = [
    "DEFAULT_CONTRAPARTIDAS",
    "DEFAULT_PAYPAL_BY_STORE",
    "MATCH_CONTAINS",
    "MATCH_EXACT",
    "MOLLIE_CARD_RULES",
    "PAYPAL_STORES",
    "STORES",
    "contrapartida_rules",
    "contrapartida_rules_config",
    "default_contrapartida_rules",
    "rule_matches",
    "store_label",
    "suggest_contrapartida_explained",
    "validate_contrapartida_rules",
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
