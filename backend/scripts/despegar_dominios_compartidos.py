"""Despegar los contactos que un dominio compartido (xtec.cat,
juntadeandalucia.es…) pegó a una empresa (1/11/2026). No escribe en FACTUSOL.

Qué se despega y qué se queda: `app/services/despegue_dominios.py`. En
corto: un contacto con correo de un dominio `compartido` de la lista se
despega de su empresa salvo que haya una prueba de que es de ella (cliente de
FACTUSOL, su correo es el del cliente en F_CLI, la conciliación por correo lo
llevó allí, pedido, oferta o vínculo puesto a mano). `company_id` a nulo, con
un apunte por contacto; las empresas que se quedan vacías, sin cliente de
FACTUSOL y sin CIF se archivan. Todo en un lote que se deshace entero.

Lee F_CLI de FACTUSOL (solo lectura) para la prueba del correo. Si no se puede
leer, se para; `--sin-factusol` sigue sin esa prueba (no recomendable).

Uso (VPS), siempre en este orden:

    # 1. Ver qué haría, por empresa (no escribe nada). --csv guarda en el
    #    servidor el mismo listado con el nombre de cada empresa, para Bart.
    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.despegar_dominios_compartidos --probar [--csv /tmp/despegue.csv]
    # 2. Hacerlo (exige --yes). Recalcula después el resumen por empresa y
    #    las etiquetas, y dice qué vistas y segmentos han cambiado de tamaño.
    ... python -m scripts.despegar_dominios_compartidos --apply --yes
    # Deshacer un lote entero:
    ... python -m scripts.despegar_dominios_compartidos --revertir LOTE --yes
    # Ver los lotes:
    ... python -m scripts.despegar_dominios_compartidos --lotes

En la consola no salen nombres de personas ni de empresas: las empresas van por
id corto, código de cliente de FACTUSOL y dominio.
"""
from __future__ import annotations

import argparse
import csv
import sys

from sqlalchemy.orm import Session

POR = "script despegar_dominios_compartidos"


def _imprimir_plan(s: Session, p, *, completo: bool) -> None:  # noqa: ANN001
    from app.services import despegue_dominios as dd

    quedan = p.se_quedan
    print(f"Contactos con empresa: {p.contactos_con_empresa}")
    print(f"Se despegan: {p.a_despegar} · se quedan con su empresa: {sum(quedan.values())} "
          f"({', '.join(f'{k} {v}' for k, v in quedan.most_common()) or '—'})")
    print(f"Empresas que se archivan (vacías, sin cliente de FACTUSOL): {len(p.a_archivar)}")
    if not p.con_factusol:
        print("ATENCIÓN: sin leer F_CLI (--sin-factusol): no se ha mirado el correo de los "
              "clientes de FACTUSOL.")
    print("\nPOR EMPRESA")
    print(f"  {'empresa':8} {'cliente':>8} {'dominio de la empresa':26} {'contactos':>9} "
          f"{'despegan':>8} {'quedan':>6}  motivos de los que quedan          archiva")
    for e in p.empresas if completo else p.empresas[:40]:
        motivos = ", ".join(f"{k} {v}" for k, v in e.quedan.most_common()) or "—"
        print(f"  {e.company_id[:8]} {e.codcli or '—':>8} {(e.dominio or '—')[:26]:26} "
              f"{e.total:9} {len(e.despegar):8} {sum(e.quedan.values()):6}  "
              f"{motivos[:34]:34} {'sí' if e.archivar else ''}")
    if not completo and len(p.empresas) > 40:
        print(f"  … y {len(p.empresas) - 40} empresas más (--completo para verlas todas)")
    print("\nPOR DOMINIO (se despegan)")
    for d, n in p.por_dominio.most_common():
        print(f"  {d:34} {n}")
    yc = dd.ya_cliente(s)
    afect = dd.ya_cliente_por_empresa(s, p)
    auto = sum(x["ya_cliente_auto"] for x in afect)
    print(f"\nYA CLIENTE hoy: {yc['contactos']} contactos en {yc['empresas']} empresas. "
          f"El recálculo de después quitará la etiqueta a {auto} de los que se despegan "
          f"(las puestas a mano se quedan).")
    riesgo = dd.en_riesgo(s, p)
    print("\nVISTAS Y SEGMENTOS con contactos que se despegan (pueden encoger si miran la "
          "empresa):")
    for x in riesgo[:30]:
        print(f"  {x['tipo']:8} {x['id'][:8]} «{x['nombre'][:40]}»: {x['contactos']} contactos, "
              f"{x['se_despegan']} se despegan")
    if not riesgo:
        print("  (ninguno)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    modo = ap.add_mutually_exclusive_group(required=True)
    modo.add_argument("--probar", action="store_true", help="ver qué haría (no escribe)")
    modo.add_argument("--apply", action="store_true", help="hacerlo (exige --yes)")
    modo.add_argument("--revertir", metavar="LOTE", help="deshacer un lote (exige --yes)")
    modo.add_argument("--lotes", action="store_true", help="listar los lotes")
    ap.add_argument("--yes", action="store_true", help="confirmación de --apply / --revertir")
    ap.add_argument("--sin-factusol", action="store_true",
                    help="no leer F_CLI (se pierde la prueba del correo del cliente)")
    ap.add_argument("--completo", action="store_true", help="todas las empresas en el listado")
    ap.add_argument("--csv", help="(con --probar) listado por empresa CON nombres, al servidor")
    args = ap.parse_args(argv)

    from app.db.session import get_engine
    from app.services import despegue_dominios as dd
    from app.services import empresa_erp

    with Session(get_engine()) as s:
        if args.lotes:
            for x in dd.lotes(s):
                print(f"  {x['lote']}  {x['at']}  contactos {x['contactos']} · empresas "
                      f"{x['empresas']} · revertidos {x['revertidos']}")
            return 0
        if args.revertir:
            if not args.yes:
                print("Falta --yes.", file=sys.stderr)
                return 2
            antes = dd.ya_cliente(s)
            r = dd.revertir(s, args.revertir, por=POR)
            print(f"Revertido el lote {args.revertir}: {r['contactos']} contactos vuelven a su "
                  f"empresa, {r['empresas']} empresas desarchivadas. No tocados: "
                  f"{r['con_otra_empresa']} ya tienen otra empresa, "
                  f"{r['sin_empresa_anterior']} cuya empresa ya no existe.")
            empresa_erp.recalcular(s)
            print(f"YA CLIENTE: {antes['contactos']} → {dd.ya_cliente(s)['contactos']} contactos")
            return 0

        correos = None
        if not args.sin_factusol:
            try:
                correos = dd.correos_de_factusol(s)
            except Exception as exc:  # noqa: BLE001
                print(f"No se ha podido leer F_CLI de FACTUSOL ({type(exc).__name__}). "
                      "Sin esa prueba podrían despegarse contactos que son del cliente. "
                      "Reinténtalo, o pasa --sin-factusol si de verdad quieres seguir.",
                      file=sys.stderr)
                return 3
            print(f"F_CLI leído (solo lectura): {len(correos)} clientes con correo.")
        p = dd.plan(s, correos_factusol=correos)
        print("DESPEGUE DE DOMINIOS COMPARTIDOS" + (" — PRUEBA, no escribe nada"
                                                     if args.probar else ""))
        _imprimir_plan(s, p, completo=args.completo or args.probar)
        if args.probar:
            if args.csv:
                with open(args.csv, "w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    w.writerow(["company_id", "nombre", "cliente", "dominio", "contactos",
                                "se_despegan", "se_quedan", "motivos", "se_archiva"])
                    for e in p.empresas:
                        w.writerow([e.company_id, e.nombre, e.codcli or "", e.dominio or "",
                                    e.total, len(e.despegar), sum(e.quedan.values()),
                                    "; ".join(f"{k} {v}" for k, v in e.quedan.items()),
                                    "sí" if e.archivar else ""])
                print(f"\nListado con nombres en {args.csv} (solo en el servidor).")
            print("\nNo se ha escrito nada. Para hacerlo: --apply --yes")
            return 0
        if not args.yes:
            print("Falta --yes.", file=sys.stderr)
            return 2
        if not p.a_despegar:
            print("Nada que despegar.")
            return 0
        tam_antes = dd.tamanos(s)
        yc_antes = dd.ya_cliente(s)
        gamas_antes = {g["gama"]: g for g in dd.huecos_por_gama(s)}
        lote = dd.aplicar(s, p, por=POR)
        print(f"\nAplicado. Lote {lote}: {p.a_despegar} contactos despegados, "
              f"{len(p.a_archivar)} empresas archivadas. Para deshacerlo: --revertir {lote} --yes")
        print("Recalculando el resumen por empresa y las etiquetas…")
        empresa_erp.recalcular(s)
        yc = dd.ya_cliente(s)
        print(f"YA CLIENTE: {yc_antes['contactos']} contactos en {yc_antes['empresas']} empresas "
              f"→ {yc['contactos']} en {yc['empresas']}")
        print("\nHUECOS POR GAMA (empresas · contactos antes → después · razón)")
        for g in dd.huecos_por_gama(s):
            a = gamas_antes.get(g["gama"], {})
            print(f"  {g['gama']:12} {g['empresas_hueco']:5} · {a.get('contactos_regla', '—')} → "
                  f"{g['contactos_regla']} · {g['razon']}")
        cambios = dd.cambios_de_tamano(tam_antes, dd.tamanos(s))
        print("\nVISTAS Y SEGMENTOS QUE CAMBIAN MUCHO (más del 20 % y de 20 contactos)")
        for x in cambios:
            print(f"  {x['tipo']:8} {x['id'][:8]} «{x['nombre'][:40]}»: {x['antes']} → "
                  f"{x['despues']}")
        if not cambios:
            print("  (ninguno)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
