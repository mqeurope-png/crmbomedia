"""Reponer los campos personalizados que la sincronización borraba.

Hasta el arreglo de octubre de 2026, cada sincronización de Brevo o de
Agile sustituía entero el JSON de campos personalizados del contacto y se
llevaba lo demás. Este script repone lo que escribió BoHub mismo —los
formularios web y el paso «Modificar campo» de los workflows— a partir de
su rastro, y SOLO las claves que faltan hoy: nunca pisa un valor ni una
clave quitada a mano (`app.services.reponer_campos`). Las respuestas de los
formularios se leen con el mapeo de hoy de cada formulario: revisa los
nombres de la lista antes de --apply.

    # 1) Informe, SIN escribir (por defecto).
    python -m scripts.reponer_campos_personalizados --probar

    # 2) Reponer las claves de la lista (con rastro en la auditoría).
    python -m scripts.reponer_campos_personalizados --apply

Lo de Brevo y lo de Agile no sale de aquí: vuelve con una sincronización
completa, que ya no borra nada (Administración → Cuentas de integración →
«Sync completa» → «Re-fetch completo (full sync)»). El informe dice
cuántos contactos están en las dos. No escribe en Brevo ni en Agile.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from sqlalchemy.orm import Session

from app.db.session import get_engine

_MUESTRA = 20


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--probar", action="store_true",
                      help="solo informa (por defecto): no escribe nada")
    modo.add_argument("--apply", action="store_true",
                      help="repone las claves de la lista (solo las que siguen faltando)")
    args = parser.parse_args()

    from app.services.reponer_campos import aplicar, planificar  # noqa: PLC0415

    with Session(get_engine()) as session:
        plan = planificar(session)
        por_clave: Counter[str] = Counter()
        por_origen: Counter[str] = Counter()
        for r in plan.reposiciones:
            por_clave.update(r.claves.keys())
            por_origen.update(r.origen.values())
        print(f"Contactos a los que les falta algo que escribió BoHub: "
              f"{len(plan.reposiciones)} ({sum(por_clave.values())} claves; "
              f"de formularios {por_origen['formulario']}, "
              f"de workflows {por_origen['workflow']})")
        for clave, cuantos in por_clave.most_common():
            print(f"  {clave}: {cuantos}")
        if plan.reposiciones:
            print(f"\nLos primeros {min(_MUESTRA, len(plan.reposiciones))} contactos:")
            for r in plan.reposiciones[:_MUESTRA]:
                print(f"  {r.contact_id}: {', '.join(sorted(r.claves))}")
        print("(Las respuestas de los formularios se leen con el mapeo de hoy de cada "
              "formulario: si alguna clave no te suena, revísala antes de --apply.)")
        print(f"\nNo se tocan (lo guardado no es un objeto JSON): {plan.no_es_objeto}")
        print(f"\nContactos que están en Brevo y en Agile: {plan.en_brevo_y_agile}. "
              "Lo suyo vuelve con «Sync completa» → «Re-fetch completo (full sync)» "
              "en Administración → Cuentas de integración.")

        if not args.apply:
            print("\nNada escrito. Para reponerlas: --apply.")
            return 0
        # Se cierra la lectura del informe: `aplicar` vuelve a leer cada
        # contacto, bloqueado, y escribe por lotes.
        session.rollback()
        try:
            hechos = aplicar(session, plan, actor_email="script:reponer_campos_personalizados")
        except Exception as exc:  # noqa: BLE001 — lo escrito por lotes se queda; se puede relanzar
            session.rollback()
            print(f"ERROR: {exc}. Lo ya repuesto se queda; relanza --apply para "
                  "seguir (solo repone lo que siga faltando).", file=sys.stderr)
            return 1
    print(f"\n✔ Campos personalizados repuestos en {hechos} contactos "
          "(con rastro en la auditoría).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
