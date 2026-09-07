"""ERP-E4-fix1 — discovery del IDIOMA en los pedidos de WooCommerce.

Escanea los payloads CRUDOS ya almacenados en `integration_events`
(system=woocommerce) y reporta, POR TIENDA, qué campos candidatos a idioma
trae cada payload real: claves de `meta_data` (WPML/Polylang/locale),
campos de primer nivel y el país de facturación — y qué habría detectado
`detect_order_language` en cada caso.

Solo lectura. Ejecutar en el servidor:

    docker exec crmbo-api-1 python -m scripts.woo_language_discovery
"""
from __future__ import annotations

import collections
import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.erp.models import IntegrationEvent
from app.integrations.woocommerce.mapper import detect_order_language

_CANDIDATE = re.compile(r"lang|locale|wpml|pll|polylang", re.IGNORECASE)
_SAMPLE_PER_STORE = 100


def main() -> None:
    with Session(get_engine()) as session:
        events = session.scalars(
            select(IntegrationEvent)
            .where(IntegrationEvent.system == "woocommerce")
            .order_by(IntegrationEvent.created_at.desc())
            .limit(2000)
        ).all()

    por_tienda: dict[str, list[dict]] = collections.defaultdict(list)
    for ev in events:
        if len(por_tienda[ev.account_id]) >= _SAMPLE_PER_STORE:
            continue
        try:
            payload = json.loads(ev.payload_json)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            por_tienda[ev.account_id].append(payload)

    if not por_tienda:
        print("Sin payloads de WooCommerce en integration_events.")
        return

    for store, payloads in sorted(por_tienda.items()):
        print(f"\n===== Tienda {store} ({len(payloads)} payloads) =====")
        meta_keys: collections.Counter[str] = collections.Counter()
        top_keys: collections.Counter[str] = collections.Counter()
        paises: collections.Counter[str] = collections.Counter()
        detecciones: collections.Counter[tuple] = collections.Counter()
        ejemplos: dict[str, str] = {}
        for woo in payloads:
            for md in woo.get("meta_data") or []:
                key = str(md.get("key") or "")
                if _CANDIDATE.search(key):
                    meta_keys[key] += 1
                    ejemplos.setdefault(key, str(md.get("value"))[:40])
            for key, value in woo.items():
                if _CANDIDATE.search(str(key)):
                    top_keys[key] += 1
                    ejemplos.setdefault(key, str(value)[:40])
            paises[str((woo.get("billing") or {}).get("country") or "∅")] += 1
            detecciones[detect_order_language(woo)] += 1
        print("  meta_data candidatas:",
              dict(meta_keys) or "NINGUNA")
        print("  campos de 1er nivel candidatos:",
              dict(top_keys) or "NINGUNO")
        for key, ejemplo in ejemplos.items():
            print(f"    · {key} = {ejemplo!r}")
        print("  países de facturación:", dict(paises.most_common(8)))
        print("  detect_order_language →", {
            f"{lang or '∅'} ({src or 'sin fuente'})": n
            for (lang, src), n in detecciones.most_common()
        })


if __name__ == "__main__":
    main()
