"""Sprint 0 — Conciliación SKU WooCommerce ↔ CODART FACTUSOL.

Extrae el inventario de ambos lados, cruza y produce un reporte:
  - match exacto por SKU (case-insensitive, normalizando guiones/espacios)
  - match fuzzy por descripción (difflib, umbral 0.84) para los sin SKU
  - ambiguos (varios candidatos) y huérfanos (sin candidato)

    cd backend
    python -m scripts.sku_conciliation                # live (credenciales)
    python -m scripts.sku_conciliation --demo         # datos de ejemplo

Salida: docs/erp/sku-conciliation-report.md + CSV con los pares propuestos
(semilla de la tabla `product_sku_mapping`, ver docs/erp/sku-conciliation.md).
Sin dependencias nuevas (difflib de stdlib).
"""
from __future__ import annotations

import csv
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

FUZZY_THRESHOLD = 0.84
DOCS = Path(__file__).resolve().parents[2] / "docs" / "erp"

#: Datos demo para validar el algoritmo sin credenciales (formato real:
#: woo = {sku, name, store}; factusol = {codart, desart}).
DEMO_WOO = [
    {"sku": "SKU-MBO-3050", "name": "MBO Laser 3050 80W", "store": "mbolasers"},
    {"sku": "SKU-MBO-6090", "name": "MBO Laser 6090 100W", "store": "mbolasers"},
    {"sku": "", "name": "ArtisJet 5000U impresora UV", "store": "mbolasers"},
    {"sku": "FLUX-BEAMO", "name": "Flux Beamo 30W", "store": "mbolasers"},
    {"sku": "SKU-ROTATIVO", "name": "Accesorio rotativo universal", "store": "mbolasers"},
]
DEMO_FACTUSOL = [
    {"codart": "MBO3050", "desart": "LASER MBO 3050 80W"},
    {"codart": "MBO6090", "desart": "LASER MBO 6090 100W"},
    {"codart": "ARTIS5000U", "desart": "ARTISJET 5000U IMPRESORA UV"},
    {"codart": "ROT01", "desart": "ROTATIVO UNIVERSAL"},
    {"codart": "TINTA01", "desart": "TINTA UV CMYK 500ML"},
]


def norm_sku(s: str) -> str:
    """SKU-MBO-3050 → mbo3050 (quita prefijo SKU, separadores y case)."""
    s = re.sub(r"^sku[-_ ]?", "", (s or "").strip().lower())
    return re.sub(r"[-_ .]", "", s)


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, norm_text(a), norm_text(b)).ratio()


def conciliate(woo: list[dict], factusol: list[dict]) -> dict:
    by_codart_norm = {norm_sku(f["codart"]): f for f in factusol}
    exact, fuzzy, ambiguous, orphans = [], [], [], []
    for p in woo:
        target = norm_sku(p.get("sku") or "")
        if target and target in by_codart_norm:
            exact.append((p, by_codart_norm[target], 1.0))
            continue
        # Fuzzy por descripción contra DESART.
        scored = sorted(
            ((f, similarity(p["name"], f["desart"])) for f in factusol),
            key=lambda x: x[1], reverse=True,
        )
        top = [(f, sc) for f, sc in scored[:3] if sc >= FUZZY_THRESHOLD]
        if len(top) == 1:
            fuzzy.append((p, top[0][0], top[0][1]))
        elif len(top) > 1:
            ambiguous.append((p, top))
        else:
            orphans.append(p)
    matched_codarts = {f["codart"] for _, f, _ in exact + fuzzy}
    factusol_orphans = [f for f in factusol if f["codart"] not in matched_codarts]
    return {
        "exact": exact, "fuzzy": fuzzy, "ambiguous": ambiguous,
        "orphans": orphans, "factusol_orphans": factusol_orphans,
    }


def write_report(result: dict, *, woo_total: int, fac_total: int) -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    md = DOCS / "sku-conciliation-report.md"
    lines = [
        "# Reporte de conciliación SKU (generado por scripts/sku_conciliation.py)\n",
        f"- Productos WooCommerce: **{woo_total}** · Artículos FACTUSOL: **{fac_total}**",
        f"- Match exacto por SKU: **{len(result['exact'])}**",
        f"- Match fuzzy por descripción (≥{FUZZY_THRESHOLD}): **{len(result['fuzzy'])}**",
        f"- Ambiguos (revisar a mano): **{len(result['ambiguous'])}**",
        f"- Huérfanos Woo (sin candidato en FACTUSOL): **{len(result['orphans'])}**",
        f"- Huérfanos FACTUSOL (sin producto Woo): **{len(result['factusol_orphans'])}**\n",
        "## Fuzzy propuestos (confirmar)\n",
        "| Woo SKU | Woo nombre | CODART | DESART | Similitud |", "|---|---|---|---|---|",
    ]
    for p, f, sc in result["fuzzy"]:
        lines.append(
            f"| {p.get('sku') or '—'} | {p['name']} | {f['codart']} "
            f"| {f['desart']} | {sc:.2f} |"
        )
    lines += ["\n## Ambiguos\n", "| Woo | Candidatos |", "|---|---|"]
    for p, top in result["ambiguous"]:
        cands = "; ".join(f"{f['codart']} ({sc:.2f})" for f, sc in top)
        lines.append(f"| {p.get('sku') or p['name']} | {cands} |")
    lines += ["\n## Huérfanos Woo (crear en FACTUSOL antes de facturar)\n"]
    lines += [f"- {p.get('sku') or '—'} · {p['name']} ({p['store']})" for p in result["orphans"]]
    md.write_text("\n".join(lines), encoding="utf-8")

    with (DOCS / "sku-conciliation-pairs.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["woo_sku", "woo_store_id", "factusol_codart", "matched_by", "score"])
        for p, f, sc in result["exact"]:
            w.writerow([p.get("sku"), p["store"], f["codart"], "auto", f"{sc:.2f}"])
        for p, f, sc in result["fuzzy"]:
            w.writerow([p.get("sku"), p["store"], f["codart"], "auto", f"{sc:.2f}"])
    print(f"→ {md}\n→ {DOCS / 'sku-conciliation-pairs.csv'}")


def main() -> int:
    if "--demo" in sys.argv:
        woo, factusol = DEMO_WOO, DEMO_FACTUSOL
    else:
        from app.integrations.factusol.client import FactusolClient
        from app.integrations.woocommerce.client import WooClient

        woo_client = WooClient()
        woo = [
            {"sku": p.get("sku") or "", "name": p.get("name") or "", "store": "mbolasers"}
            for p in woo_client.iter_all_products()
        ]
        fac_client = FactusolClient()
        factusol = [
            {"codart": r.get("CODART", ""), "desart": r.get("DESART", "")}
            for r in fac_client.carga_tabla("F_ART")
        ]
    result = conciliate(woo, factusol)
    write_report(result, woo_total=len(woo), fac_total=len(factusol))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
