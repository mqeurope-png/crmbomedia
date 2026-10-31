"""Diagnóstico de los dominios de correo (1/11/2026). SOLO LEE: no escribe en
la base ni en FACTUSOL.

Para decidir con datos qué dominios entran en la lista de los que no
emparejan (Administración → Dominios compartidos). Un dominio es sospechoso
de ser compartido cuando:

- lo usan contactos de **varios clientes de FACTUSOL distintos**;
- tiene en una sola empresa **muchos más contactos** que cualquier empresa
  real (se compara con el percentil 99 de contactos por empresa con cliente
  de FACTUSOL);
- **parece de administración pública o educación** (gob, junta, xunta, edu,
  educa…) o de un **proveedor de correo**.

Sin nombres de personas ni de empresas: los dominios sí salen (hacen falta).
Las empresas, por id corto y código de cliente.

Uso (VPS):

    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.diagnostico_dominios [--top 60]
"""
from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

#: Etiquetas de dominio que delatan administración pública o educación.
ADMINISTRACION = {
    "gob", "gov", "gouv", "gencat", "xtec", "junta", "juntadeandalucia", "xunta", "edu",
    "educa", "educacion", "educaib", "educarm", "hezkuntza", "euskadi", "navarra", "gva",
    "jcyl", "jccm", "aragon", "caib", "carm", "juntaex", "larioja", "cantabria", "asturias",
    "gobiernodecanarias", "madrid", "diputacion", "dip", "ayto", "ayuntamiento",
    "ajuntament", "concello", "mecd", "csic", "sergas", "osakidetza", "uned",
}
CORREO = re.compile(r"^(g?mail|hotmail|outlook|live|yahoo|ymail|icloud|me|aol|gmx|web|"
                    r"proton(mail)?|tutanota|yandex|orange|free|wanadoo|telefonica|"
                    r"movistar|vodafone|terra|ono|jazztel|euskaltel|telecable|r)\.")


def _pct(valores: list[int], p: float) -> int:
    if not valores:
        return 0
    v = sorted(valores)
    return v[min(len(v) - 1, int(round(p * (len(v) - 1))))]


def _parece_admin(dominio: str) -> bool:
    return any(et in ADMINISTRACION for et in dominio.split("."))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", type=int, default=60, help="cuántos dominios listar")
    args = ap.parse_args(argv)

    from app.db.session import get_engine
    from app.models.crm import Company, Contact
    from app.services import despegue_dominios as dd
    from app.services import dominios_compartidos as dc

    with Session(get_engine()) as s:
        lista = dc.cargar(s, refrescar=True)
        codcli = {cid: (c or "").strip() or None for cid, c in s.execute(
            select(Company.id, Company.factusol_company_id))}
        por_dominio: dict[str, Counter] = defaultdict(Counter)
        empresas_de: dict[str, Counter] = defaultdict(Counter)
        por_empresa: Counter = Counter()
        total = con_empresa = 0
        for email, company_id in s.execute(select(Contact.email, Contact.company_id)):
            total += 1
            d = dc.dominio_de_correo(email)
            if company_id:
                con_empresa += 1
                por_empresa[company_id] += 1
            if not d:
                continue
            por_dominio[d]["contactos"] += 1
            if company_id:
                por_dominio[d]["con_empresa"] += 1
                empresas_de[d][company_id] += 1
        tam_reales = [n for cid, n in por_empresa.items() if codcli.get(cid)]
        p50, p99 = _pct(tam_reales, 0.5), _pct(tam_reales, 0.99)
        umbral = max(20, 3 * p99)

        print("DIAGNÓSTICO DE DOMINIOS (solo lectura)")
        print(f"  Contactos: {total} · con empresa: {con_empresa} · sin empresa: "
              f"{total - con_empresa}")
        print(f"  Contactos por empresa con cliente de FACTUSOL: mediana {p50}, "
              f"percentil 99 {p99} → «muchos contactos» = más de {umbral} en una empresa")
        print(f"  Dominios en la lista: {len(lista)}")

        filas = []
        for d, c in por_dominio.items():
            emp = empresas_de[d]
            clientes = {codcli[cid] for cid in emp if codcli.get(cid)}
            maximo = max(emp.values()) if emp else 0
            motivos = []
            if len(clientes) >= 2:
                motivos.append(f"{len(clientes)} clientes FACTUSOL")
            if maximo > umbral:
                motivos.append("muchos contactos en una empresa")
            if _parece_admin(d):
                motivos.append("administración/educación")
            if CORREO.match(d):
                motivos.append("proveedor de correo")
            filas.append((d, c["contactos"], c["con_empresa"], len(emp), len(clientes), maximo,
                          lista.tipo(d) or "", motivos))
        filas.sort(key=lambda f: (-f[1], f[0]))

        print(f"\nDOMINIOS POR CONTACTOS (los {args.top} primeros)")
        print(f"  {'dominio':34} {'contactos':>9} {'c/empresa':>9} {'empresas':>8} "
              f"{'clientes':>8} {'máx 1 emp':>9}  lista       sospecha")
        for d, n, ne, emp, cli, mx, tipo, mot in filas[:args.top]:
            print(f"  {d[:34]:34} {n:9} {ne:9} {emp:8} {cli:8} {mx:9}  {tipo or '—':10}  "
                  f"{'; '.join(mot)}")

        print("\nDOMINIOS POR CLIENTES DE FACTUSOL DISTINTOS (2 o más)")
        varios = sorted((f for f in filas if f[4] >= 2), key=lambda f: (-f[4], -f[1]))
        for d, n, _ne, emp, cli, mx, tipo, _mot in varios[:args.top]:
            print(f"  {d[:34]:34} clientes {cli:4} · empresas {emp:4} · contactos {n:6} · "
                  f"máx en una {mx:5} · {tipo or 'no está en la lista'}")
        if not varios:
            print("  (ninguno)")

        print("\nPROPUESTA: sospechosos que NO están en la lista (los decide Bart)")
        propuesta = [f for f in filas if f[7] and not f[6] and f[2] > 0]
        for d, n, ne, _emp, cli, mx, _t, mot in propuesta[:args.top]:
            print(f"  {d[:34]:34} contactos {n:6} (con empresa {ne:5}) · clientes {cli:3} · "
                  f"máx en una {mx:5} · {'; '.join(mot)}")
        if not propuesta:
            print("  (ninguno)")

        print("\nLA LISTA HOY (tipo compartido) Y LO QUE PEGA")
        for d, tipo in sorted(lista.tipos.items()):
            if tipo != dc.TIPO_COMPARTIDO:
                continue
            c = Counter()
            emp: Counter = Counter()
            for dd_, cc in por_dominio.items():
                if lista.de_la_lista(dd_) == d:
                    c.update(cc)
                    emp.update(empresas_de[dd_])
            print(f"  {d:30} contactos {c['contactos']:6} · con empresa {c['con_empresa']:6} "
                  f"· empresas {len(emp):4}")
            for cid, n in emp.most_common(5):
                cli = codcli.get(cid) or "—"
                print(f"      empresa {cid[:8]} · cliente {cli:>8} · {n} contactos")

        print("\nYA CLIENTE")
        yc = dd.ya_cliente(s)
        print(f"  Hoy: {yc['contactos']} contactos en {yc['empresas']} empresas")
        try:
            correos = dd.correos_de_factusol(s)
        except Exception as exc:  # noqa: BLE001
            correos = None
            print(f"  (sin leer F_CLI de FACTUSOL: {type(exc).__name__}; la cuenta de abajo no "
                  "descuenta a los que tienen el correo de su cliente)")
        p = dd.plan(s, correos_factusol=correos)
        afectados = dd.ya_cliente_por_empresa(s, p)
        auto = sum(x["ya_cliente_auto"] for x in afectados)
        mano = sum(x["ya_cliente_a_mano"] for x in afectados)
        print(f"  De ellos, con correo de un dominio compartido de la lista y sin otra prueba "
              f"de ser de su empresa: {auto} (los quitará el recálculo tras despegar) "
              f"+ {mano} puestos a mano (se quedan)")
        if yc["contactos"]:
            print(f"  → {round(100 * auto / yc['contactos'])} % de la etiqueta")
        for x in sorted(afectados, key=lambda x: -x["ya_cliente_auto"])[:10]:
            print(f"    empresa {x['company_id'][:8]} · cliente {x['codcli'] or '—':>8} · "
                  f"dominio {x['dominio'] or '—'} · {x['ya_cliente_auto']} YA CLIENTE")

        print("\nHUECOS POR GAMA: empresas (pestaña) frente a contactos (regla)")
        for g in dd.huecos_por_gama(s):
            razon = g["razon"] if g["razon"] is not None else "—"
            print(f"  {g['gama']:12} empresas {g['empresas_hueco']:5} · contactos "
                  f"{g['contactos_regla']:6} · razón {razon}")
        print("\nNo se ha escrito nada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
