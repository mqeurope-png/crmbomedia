"""Las garantías no son consumible (1/11/2026). No escribe en FACTUSOL.

Dentro de familias clasificadas como Consumible o Recambio (la 251, «Consumible
· Láser», mezcla filtros y garantías) hay artículos de garantía que se venden
con cada máquina: «Flux Gar. Las · 1 año de garantía limitada». Contados como
consumible, inventan un ritmo de compra («cada 1 mes») y falsean dormidos y
huecos.

Este script les da CLASE PROPIA «Otro», con el mecanismo que ya existe
(`historico_articulos_clase`, lo mismo que «No es una máquina» o la casilla
de Familias). Así:

- la familia no se toca (la clasificación de Bart sigue igual);
- un artículo que ya tiene clase propia (lo decidió Bart) NO se toca;
- todo lo que pone lleva su firma, y `--revertir` lo quita (vuelven a la clase
  de su familia), salvo lo que alguien haya cambiado después.

Antes de escribir, dice cuántos son y cuánto se mueve el ritmo de compra de los
clientes de la gama (láser por defecto), y los dormidos y huecos, antes y
después. Para medir el «después» sin escribir, lo aplica dentro de una
transacción y la deshace.

Uso (VPS), siempre en este orden:

    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.garantias_como_otro --probar [--gama laser]
    ... python -m scripts.garantias_como_otro --apply --yes
    # Si --probar lista algo que no es una garantía, se deja fuera (también
    # al aplicar): --excluir CODART [CODART ...]
    # Deshacerlo:
    ... python -m scripts.garantias_como_otro --revertir --yes

Sin nombres de clientes: van por código de FACTUSOL. Los artículos, por código
y descripción (son productos).
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from datetime import UTC, datetime
from statistics import median
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

POR = "script garantias_como_otro (1/11/2026)"
CLASE = "otro"
#: Lo que dice «garantía» en la descripción (sin tildes ni mayúsculas): «Gar.»,
#: «Garant.», «garantía», «warranty», «garantie». No «garantizado»: un filtro
#: «de calidad garantizada» sigue siendo un filtro.
PATRON = re.compile(r"\bgar\.|\bgarant\.|\bgarantia|\bgarantie|\bwarranty|\bgaranzia")


def _normal(t: str | None) -> str:
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()


def candidatos(session: Session, excluir: set[str] | None = None) -> dict[str, Any]:
    """Los artículos de garantía que HOY cuentan como consumible o recambio
    (por su familia, sin clase propia), los que ya tienen clase propia (esos
    no se tocan) y los que se han dejado fuera con `--excluir`."""
    excluir = {c.strip().upper() for c in excluir or ()}
    from app.erp import historico_alias
    from app.erp.historico_maquinas import clase_efectiva_py
    from app.erp.models.historico import (
        HistoricoArticulo,
        HistoricoArticuloClase,
        HistoricoFamilia,
    )

    al_fam = historico_alias.familias(session)
    familias = {f.codigo: f for f in session.scalars(select(HistoricoFamilia))}
    propias = {p.codart.strip().upper(): p for p in session.scalars(select(HistoricoArticuloClase))}
    ultimo: dict[str, HistoricoArticulo] = {}
    for a in session.scalars(select(HistoricoArticulo).order_by(HistoricoArticulo.ejercicio)):
        ultimo[a.codart.strip().upper()] = a
    tocar, ya, fuera = [], [], []
    for k, a in sorted(ultimo.items()):
        if not PATRON.search(_normal(a.descripcion)):
            continue
        if k in excluir:
            fuera.append({"codart": a.codart.strip(), "descripcion": a.descripcion})
            continue
        f = familias.get(al_fam.del_articulo(a.codart, a.familia) or "")
        if k in propias:
            ya.append({"codart": a.codart, "descripcion": a.descripcion,
                       "clase": propias[k].clase})
            continue
        if clase_efectiva_py(None, f.clase if f else None) in ("consumible", "recambio"):
            tocar.append({"codart": a.codart.strip(), "descripcion": a.descripcion,
                          "familia": f.codigo if f else None, "gama": f.gama if f else None})
    return {"tocar": tocar, "ya": ya, "fuera": fuera}


def _lineas_vendidas(session: Session, codigos: list[str]) -> int:
    from app.erp.models.historico import TIPOS_VENTA, HistoricoDocumento, HistoricoLinea

    if not codigos:
        return 0
    return session.scalar(
        select(func.count(HistoricoLinea.id))
        .join(HistoricoDocumento, HistoricoDocumento.id == HistoricoLinea.documento_id)
        .where(func.upper(HistoricoLinea.codart).in_([c.upper() for c in codigos]),
               HistoricoDocumento.tipo.in_(TIPOS_VENTA))) or 0


def medir(session: Session, gama: str) -> dict[str, Any]:
    """El ritmo de compra de consumible y recambio de la gama de cada cliente
    con máquina de esa gama (la mediana entre compras, en meses, con 3 compras
    o más), y los dormidos y huecos de la gama."""
    from app.erp import parque_consultas as pc

    activo = pc._parque_activo(session)
    clientes = {cli for cli, ms in activo.items() if any(m["gama"] == gama for m in ms)}
    compras = pc._compras_por_gama(session)
    ritmo: dict[str, int | None] = {}
    for cli in clientes:
        fechas = sorted({f for f, _t in (compras.get((cli, gama)) or {}).get("facturado", [])})
        if len(fechas) >= 3:
            dias = [(b - a).days for a, b in zip(fechas, fechas[1:], strict=False)]
            ritmo[cli] = max(1, round(median(dias) / 30.44))
        else:
            ritmo[cli] = None
    inf = pc.informe(session)
    return {"clientes": len(clientes), "ritmo": ritmo,
            "dormidos": sum(1 for d in inf["dormidos"] if d["gama"] == gama),
            "huecos": sum(1 for h in inf["huecos"] if h["gama"] == gama)}


def _aplicar(session: Session, tocar: list[dict[str, Any]]) -> None:
    from app.erp.models.historico import HistoricoArticuloClase

    ahora = datetime.now(UTC).replace(tzinfo=None)
    for t in tocar:
        session.add(HistoricoArticuloClase(codart=t["codart"], clase=CLASE, por=POR, at=ahora))
    session.flush()


def _cada(v: float | None) -> str:
    if not v:
        return "sin ritmo"
    return "cada 1 mes" if v == 1 else f"cada {v:g} meses"


def _informe(antes: dict[str, Any], despues: dict[str, Any], gama: str) -> None:
    cambian = [(cli, antes["ritmo"].get(cli), despues["ritmo"].get(cli))
               for cli in sorted(antes["ritmo"])
               if antes["ritmo"].get(cli) != despues["ritmo"].get(cli)]
    con_antes = [v for v in antes["ritmo"].values() if v]
    con_despues = [v for v in despues["ritmo"].values() if v]
    print(f"\nRITMO DE COMPRA de los {antes['clientes']} clientes con máquina de {gama}:")
    print(f"  con ritmo (3+ compras): {len(con_antes)} → {len(con_despues)}")
    if con_antes and con_despues:
        print(f"  mediana de los ritmos: {_cada(median(con_antes))} → "
              f"{_cada(median(con_despues))}")
    print(f"  clientes cuyo ritmo cambia: {len(cambian)}")
    for cli, a, d in cambian[:40]:
        print(f"    cliente {cli}: {_cada(a)} → {_cada(d)}")
    if len(cambian) > 40:
        print(f"    … y {len(cambian) - 40} más")
    print(f"\nDORMIDOS de {gama}: {antes['dormidos']} → {despues['dormidos']}")
    print(f"HUECOS de {gama}: {antes['huecos']} → {despues['huecos']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    modo = ap.add_mutually_exclusive_group(required=True)
    modo.add_argument("--probar", action="store_true", help="ver qué haría (no escribe)")
    modo.add_argument("--apply", action="store_true", help="hacerlo (exige --yes)")
    modo.add_argument("--revertir", action="store_true", help="deshacerlo (exige --yes)")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--gama", default="laser", help="la gama cuyo ritmo se mide (laser)")
    ap.add_argument("--excluir", nargs="+", default=[], metavar="CODART",
                    help="artículos que --probar lista y no son garantías: se dejan como están")
    args = ap.parse_args(argv)

    from app.db.session import get_engine
    from app.erp.models.historico import HistoricoArticuloClase
    from app.services import empresa_erp

    if (args.apply or args.revertir) and not args.yes:
        print("Falta --yes.", file=sys.stderr)
        return 2
    with Session(get_engine()) as s:
        if args.revertir:
            n = s.execute(delete(HistoricoArticuloClase).where(
                HistoricoArticuloClase.por == POR, HistoricoArticuloClase.clase == CLASE)).rowcount
            s.commit()
            print(f"Revertido: {n or 0} artículos vuelven a la clase de su familia. "
                  "Recalculando el resumen por empresa…")
            empresa_erp.recalcular(s)
            return 0
        c = candidatos(s, set(args.excluir))
        tocar = c["tocar"]
        print("GARANTÍAS COMO «OTRO»" + (" — PRUEBA, no escribe nada" if args.probar else ""))
        print(f"Artículos de garantía que hoy cuentan como consumible o recambio: {len(tocar)} "
              f"({_lineas_vendidas(s, [t['codart'] for t in tocar])} líneas vendidas)")
        for t in tocar:
            print(f"  {t['codart']:16} familia {t['familia'] or '—':6} gama {t['gama'] or '—':10} "
                  f"{(t['descripcion'] or '')[:60]}")
        if c["ya"]:
            print(f"\nCon clase propia ya puesta (lo decidió alguien: no se tocan): {len(c['ya'])}")
            for t in c["ya"]:
                print(f"  {t['codart']:16} {t['clase']:10} {(t['descripcion'] or '')[:60]}")
        if c["fuera"]:
            print(f"\nDejados fuera con --excluir: {len(c['fuera'])}")
            for t in c["fuera"]:
                print(f"  {t['codart']:16} {(t['descripcion'] or '')[:60]}")
        antes = medir(s, args.gama)
        _aplicar(s, tocar)
        despues = medir(s, args.gama)
        _informe(antes, despues, args.gama)
        if args.probar:
            s.rollback()
            print("\nNo se ha escrito nada. Para hacerlo: --apply --yes")
            return 0
        s.commit()
        print(f"\nHecho: {len(tocar)} artículos con clase propia «Otro» (firmados «{POR}»). "
              "Recalculando el resumen por empresa…")
        empresa_erp.recalcular(s)
        print("Deshacerlo: --revertir --yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
