"""Cuadre de un hueco (1/11/2026): cuántos clientes da la pestaña «Dormidos y
huecos» y cuántos contactos da el filtro de contactos «uno por empresa», y por
qué no coinciden. SOLO LEE.

Ejemplo, el que se comprobó a mano: hueco de láser, gasto ≥ 5.000 € y máquina
de ≥ 24 meses.

    docker compose -f docker-compose.prod.yml exec api \\
        python -m scripts.cuadre_huecos --gama laser --gasto 5000 --meses 24

La pestaña cuenta CLIENTES de FACTUSOL; el filtro, CONTACTOS de empresas de
BoHub. Entre uno y otro:

- clientes sin empresa en BoHub (no tienen a quién escribir);
- códigos de cliente vinculados a dos o más empresas (cuentan dos veces);
- empresas sin ningún contacto (no salen en una lista de contactos);
- la antigüedad: en la pestaña es la de la máquina MÁS RECIENTE DE ESA GAMA;
  en el filtro, «Meses de la máquina más nueva / más vieja» mira todas las
  máquinas de la empresa, de cualquier gama. Se dan las dos lecturas.

Sin nombres: clientes por código, empresas por id corto.
"""
from __future__ import annotations

import argparse
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gama", required=True, help="código de la gama (p. ej. laser)")
    ap.add_argument("--gasto", type=float, default=None, help="gasto total mínimo (€)")
    ap.add_argument("--meses", type=int, default=None,
                    help="meses mínimos de la máquina (la más reciente de la gama)")
    ap.add_argument("--detalle", action="store_true", help="lista los códigos de cada grupo")
    args = ap.parse_args(argv)

    from app.db.session import get_engine
    from app.erp import parque
    from app.erp import parque_consultas as pc
    from app.models.crm import Company, Contact
    from app.services.segments import un_por_empresa
    from app.services.segments.engine import build_filter

    with Session(get_engine()) as s:
        tab = pc.filtrar(pc.informe(s), gama=args.gama, meses_min=args.meses,
                         gasto_min=args.gasto)
        clientes = sorted({h["codcli"] for h in tab["huecos"]})
        empresas_de: dict[str, list[str]] = defaultdict(list)
        for cid, codcli in s.execute(select(Company.id, Company.factusol_company_id).where(
                Company.factusol_company_id.is_not(None), Company.is_archived.is_(False))):
            if cli := parque.clave_cliente(codcli):
                empresas_de[cli].append(cid)
        sin_empresa = [c for c in clientes if c not in empresas_de]
        repetidos = {c: empresas_de[c] for c in clientes if len(empresas_de.get(c, [])) > 1}
        empresas = sorted({e for c in clientes for e in empresas_de.get(c, [])})
        con_contactos = set(s.scalars(select(Contact.company_id).where(
            Contact.company_id.in_(empresas)).distinct())) if empresas else set()
        sin_contactos = [e for e in empresas if e not in con_contactos]

        print(f"CUADRE · hueco de {args.gama}"
              + (f" · gasto ≥ {args.gasto:g} €" if args.gasto is not None else "")
              + (f" · máquina de ≥ {args.meses} meses" if args.meses is not None else ""))
        print(f"\n  Pestaña «Dormidos y huecos»: {len(clientes)} clientes de FACTUSOL")
        print(f"  − sin empresa en BoHub: {len(sin_empresa)}")
        print(f"  + empresas de más por códigos repetidos: "
              f"{sum(len(v) - 1 for v in repetidos.values())} "
              f"({len(repetidos)} códigos en {sum(len(v) for v in repetidos.values())} empresas)")
        print(f"  = empresas de BoHub: {len(empresas)}")
        print(f"  − empresas sin ningún contacto: {len(sin_contactos)}")
        print(f"  = contactos esperados, uno por empresa: {len(empresas) - len(sin_contactos)}")
        if args.detalle:
            print(f"\n  Sin empresa: {', '.join(sin_empresa) or '—'}")
            for c, es in sorted(repetidos.items()):
                print(f"  Código {c} repetido en: {', '.join(e[:8] for e in es)}")
            print(f"  Empresas sin contactos: {', '.join(e[:8] for e in sin_contactos) or '—'}")

        def regla(campo_meses: str | None) -> dict:
            hijos = [{"type": "rule", "field": "compra_hueco", "comparator": "in",
                      "value": [args.gama]}]
            if args.gasto is not None:
                hijos.append({"type": "rule", "field": "compra_gasto_total", "comparator": "gte",
                              "value": args.gasto})
            if args.meses is not None and campo_meses:
                hijos.append({"type": "rule", "field": campo_meses, "comparator": "gte",
                              "value": args.meses})
            return {"operator": "AND", "children": hijos}

        print("\n  Lo que da el filtro de contactos «uno por empresa»:")
        lecturas = [("sin regla de antigüedad", None)]
        if args.meses is not None:
            lecturas += [("con «Meses de la máquina más vieja ≥»", "parque_meses_vieja"),
                         ("con «Meses de la máquina más nueva ≥»", "parque_meses_nueva")]
        for texto, campo in lecturas:
            n = s.scalar(select(func.count()).select_from(Contact).where(
                un_por_empresa.clausula(build_filter(regla(campo))))) or 0
            print(f"    {texto}: {n} contactos")
        print("\n  (La pestaña mide la antigüedad con la máquina más reciente DE ESA GAMA; las "
              "reglas de meses del filtro, con todas las máquinas de la empresa.)")
        print("\nNo se ha escrito nada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
