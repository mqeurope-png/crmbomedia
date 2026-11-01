"""Comparar el análisis del parque con varios modelos (1/11/2026). No guarda el
análisis ni toca la ficha; solo deja anotadas las llamadas en «Uso de la IA»
(cuestan dinero, poco). No escribe en FACTUSOL.

Para decidir el modelo con datos y no con opinión: los mismos hechos
calculados, con cada modelo, uno detrás de otro, con lo que costó cada uno.

    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.comparar_analisis_parque \\
        --cliente 1234 --cliente 5678 --cliente 9012 \\
        [--modelo claude-sonnet-5-5 --modelo claude-haiku-5-5] [--salida /tmp/comparar.md]

Sin `--modelo`, compara Sonnet 5.5 y Haiku 5.5. Con `--salida`, además lo deja
en un fichero Markdown en el servidor, para pegarlo.

Sin nombres: cada cliente va por su código de FACTUSOL. Lo que sale son los
hechos (artículos, gamas, fechas) y lo que escribe cada modelo.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

MODELOS_DEFECTO = ("claude-sonnet-5-5", "claude-haiku-5-5")


def _bloque(nombre: str) -> str:
    return {"tiene": "Qué tiene", "consume": "Qué consume", "no_compra": "Qué no compra",
            "cambio": "Qué ha cambiado", "no_se_sabe": "Lo que no se sabe"}[nombre]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cliente", action="append", required=True,
                    help="código de cliente de FACTUSOL (se puede repetir)")
    ap.add_argument("--modelo", action="append", help="modelo (se puede repetir)")
    ap.add_argument("--salida", help="también a este fichero Markdown (en el servidor)")
    args = ap.parse_args(argv)

    from app.core.config import get_settings
    from app.db.session import get_engine
    from app.erp import parque
    from app.models.crm import Company
    from app.models.ia import PROPOSITO_ANALISIS_PARQUE
    from app.services import llm
    from app.services.analisis_parque import config as cfg_mod
    from app.services.analisis_parque import hechos as hx
    from app.services.analisis_parque import ia
    from app.services.analisis_parque.servicio import _codigos_catalogo
    from app.services.ia.coste import coste_usd
    from app.services.ia.uso import registrar_llamada

    modelos = args.modelo or list(MODELOS_DEFECTO)
    lineas: list[str] = []

    def p(texto: str = "") -> None:
        print(texto)
        lineas.append(texto)

    with Session(get_engine()) as s:
        cfg = cfg_mod.configuracion(s)
        catalogo = _codigos_catalogo(s)
        for cli in args.cliente:
            clave = parque.clave_cliente(cli)
            empresas = [e for e in s.scalars(select(Company).where(
                Company.factusol_company_id.is_not(None))) if
                parque.clave_cliente(e.factusol_company_id) == clave]
            p(f"\n## Cliente {cli}")
            if not empresas:
                p("Sin empresa en BoHub con ese código.")
                continue
            if len(empresas) > 1:
                p(f"(El código está en {len(empresas)} empresas; se usa la primera, "
                  f"{empresas[0].id[:8]}.)")
            h = hx.calcular(s, empresas[0], cfg=cfg)
            if h.estado != hx.CON_DATOS:
                p("Sin datos (sin parque ni compras): no se llama a la IA.")
                continue
            p("\n### Hechos calculados (los mismos para todos los modelos)")
            for b in hx.BLOQUES:
                xs = [x for x in h.hechos if x.bloque == b]
                if xs:
                    p(f"\n**{_bloque(b)}**")
                    for x in xs:
                        p(f"- {x.id} · {x.texto}")
            for modelo in modelos:
                p(f"\n### {modelo}")
                try:
                    r, uso = ia.generar(h, modelo=modelo, api_key=get_settings().anthropic_api_key,
                                        codigos_catalogo=catalogo)
                except llm.LLMError as exc:
                    p(f"Falló: {type(exc).__name__}.")
                    continue
                registrar_llamada(s, proposito=PROPOSITO_ANALISIS_PARQUE, modelo=modelo, uso=uso,
                                  referencia_tipo="comparar", referencia_id=empresas[0].id)
                s.commit()
                for x in r.lectura:
                    p(f"- {x['texto']} [{', '.join(x['citas'])}]")
                if r.entrada:
                    prods = ", ".join(f"{x['id']} {x['codart']}" for x in r.entrada["productos"])
                    p(f"- **Por dónde entrar:** {r.entrada['texto']} "
                      f"[{', '.join(r.entrada['citas'])}]" + (f" ({prods})" if prods else ""))
                else:
                    p("- **Por dónde entrar:** — (sin un dato en el que apoyarla)")
                for d in r.descartado:
                    p(f"- _Descartado:_ {d}")
                if uso:
                    usd = coste_usd(uso.modelo, uso.tokens_entrada, uso.tokens_salida,
                                    uso.tokens_cache_lectura, uso.tokens_cache_escritura)
                    p(f"- _Tokens: {uso.tokens_entrada} de entrada, {uso.tokens_salida} de "
                      f"salida · {usd:.4f} $_")
    if args.salida:
        with open(args.salida, "w", encoding="utf-8") as f:
            f.write("\n".join(lineas) + "\n")
        print(f"\nTambién en {args.salida} (solo en el servidor).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
