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
- un artículo que ya tiene clase propia (lo decidió alguien), NO se toca, ni
  el equivalente de una máquina marcada (cuenta como máquina);
- si la descripción dice garantía en unos años y en otros no (el código se
  reutilizó), tampoco: sale como dudoso, para decidirlo a mano;
- lo que pone lleva su firma y queda apuntado (en el blob de `ErpSettings`,
  clave `garantias_como_otro`): si alguien lo quita después, otra pasada NO
  lo vuelve a poner; lo que se deja fuera con `--excluir` queda fuera también
  en las siguientes;
- `--revertir` quita lo que puso el script, salvo lo que alguien haya cambiado
  después.

Antes de escribir, dice cuántos son y cuánto se mueve el ritmo de compra de los
clientes de la gama (láser por defecto; el mismo cálculo que la ficha), y los
dormidos y huecos de cada gama, antes y después. Para medir el «después» sin
escribir, lo aplica dentro de una transacción y la deshace.

Uso (VPS), siempre en este orden:

    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.garantias_como_otro --probar [--gama laser]
    # Si --probar lista algo que no es una garantía, se deja fuera (y se
    # recuerda para la próxima vez): --excluir CODART [CODART ...]
    ... python -m scripts.garantias_como_otro --apply --yes [--excluir ...]
    # Deshacerlo:
    ... python -m scripts.garantias_como_otro --revertir --yes

Sin nombres de clientes: van por código de FACTUSOL. Los artículos, por código
y descripción (son productos).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, datetime
from statistics import median
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

POR = "script garantias_como_otro (1/11/2026)"
CLASE = "otro"
#: Dónde se apunta lo que puso y lo que se dejó fuera (blob de ErpSettings).
REGISTRO = "garantias_como_otro"
#: Lo que dice «garantía» en la descripción (sin tildes ni mayúsculas): «Gar.»,
#: «GAR», «Garant.», «Gtía.», «garantía», «garantie» (no «garantiert»),
#: «garanzia», «warranty», «guarantee». No «garantizado» ni «guaranteed»: un
#: filtro «de calidad garantizada» sigue siendo un filtro.
PATRON = re.compile(r"\b(?:gar|garant|gtia)\b|\bgarantia|\bgarantie(?!rt)|\bgaranzia"
                    r"|\bwarranty|\bguarantee(?!d)")
#: …salvo si va negada o es lo que cubre: «sin garantía», «fuera de garantía»,
#: «cabezal en garantía», «bajo garantía», «kit con garantía».
NEGADO = re.compile(r"\b(?:sin|fuera de|en|bajo|con)\s+$")
CONSUMIBLE_O_RECAMBIO = ("consumible", "recambio")


def _normal(t: str | None) -> str:
    """Sin tildes ni mayúsculas, y con los espacios juntos («fuera  de»)."""
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return " ".join(t.split())


def palabra_de_garantia(descripcion: str | None) -> str | None:
    """La palabra que la hace garantía, o None."""
    t = _normal(descripcion)
    for m in PATRON.finditer(t):
        if not NEGADO.search(t[:m.start()]):
            return m.group(0)
    return None


# --- el apunte de lo hecho ---------------------------------------------------------------


def _registro(session: Session) -> dict[str, list[str]]:
    from app.integrations.factusol.service import series_config

    raw = series_config(session).get(REGISTRO)
    raw = raw if isinstance(raw, dict) else {}
    return {k: sorted({_clave(c) for c in raw.get(k) or [] if isinstance(c, str)})
            for k in ("puestos", "excluidos")}


def _guardar_registro(session: Session, *, puestos: Any = (), excluidos: Any = (),
                      quitar: Any = ()) -> None:
    """Añade (o quita) al apunte, junto al resto de ajustes del blob, sin
    tocarlos. Se llama justo antes del commit y lee el blob de nuevo, con
    cerrojo: si alguien ha guardado Ajustes ERP mientras el script medía (son
    minutos), no se pisa lo suyo. Si el blob no se puede leer, no se escribe
    (se perderían los demás ajustes): error."""
    from app.erp.models import ERP_SETTINGS_SINGLETON_ID, ErpSettings

    cfg = session.get(ErpSettings, ERP_SETTINGS_SINGLETON_ID, with_for_update=True,
                      populate_existing=True)
    if cfg is None:
        cfg = ErpSettings(id=ERP_SETTINGS_SINGLETON_ID)
        session.add(cfg)
    datos = json.loads(cfg.factusol_series_json) if cfg.factusol_series_json else {}
    if not isinstance(datos, dict):
        raise ValueError("los ajustes del ERP no se pueden leer")
    actual = datos.get(REGISTRO) if isinstance(datos.get(REGISTRO), dict) else {}

    def _de(k: str) -> set[str]:
        return {_clave(c) for c in actual.get(k) or [] if isinstance(c, str)}

    quitar = {_clave(c) for c in quitar}
    datos[REGISTRO] = {
        "puestos": sorted((_de("puestos") | {_clave(c) for c in puestos}) - quitar),
        "excluidos": sorted(_de("excluidos") | {_clave(c) for c in excluidos})}
    cfg.factusol_series_json = json.dumps(datos)
    session.flush()


def _clave(codart: str | None) -> str:
    from app.erp.historico_alias import _k

    return _k(codart)


# --- qué artículos -----------------------------------------------------------------------


def candidatos(session: Session, excluir: set[str] | None = None) -> dict[str, Any]:
    """Los artículos de garantía que HOY cuentan como consumible o recambio en
    algún año (por su familia de ese año, sin clase propia), y aparte todo lo
    que no se toca y por qué."""
    from app.erp import historico_alias, parque
    from app.erp import historico_equivalencias as equivalencias
    from app.erp.historico_maquinas import CLASE_NO_MAQUINA, clase_efectiva_py
    from app.erp.models.historico import (
        HistoricoArticulo,
        HistoricoArticuloClase,
        HistoricoFamilia,
    )

    al_fam = historico_alias.familias(session)
    familias = {f.codigo: f for f in session.scalars(select(HistoricoFamilia))}
    propias = {_clave(p.codart): p for p in session.scalars(select(HistoricoArticuloClase))}
    maquinas = parque._codigos_maquina(session, equivalencias.cargar(session))
    reg = _registro(session)
    excluidos = set(reg["excluidos"]) | {_clave(c) for c in excluir or ()}
    por_codigo: dict[str, list[Any]] = defaultdict(list)
    for a in session.scalars(select(HistoricoArticulo).order_by(HistoricoArticulo.ejercicio)):
        por_codigo[_clave(a.codart)].append(a)

    out: dict[str, list[dict[str, Any]]] = {k: [] for k in (
        "tocar", "ya_script", "ya", "no_maquina", "maquina", "quitados", "fuera", "dudosos")}
    for k, arts in sorted(por_codigo.items()):
        palabras = {(a.descripcion or "").strip(): palabra_de_garantia(a.descripcion)
                    for a in arts}
        if not any(palabras.values()):
            continue
        ultimo = arts[-1]
        # Las familias (de cada año) en las que cuenta como consumible o recambio.
        cuenta: dict[str, Any] = {}
        for a in arts:
            f = familias.get(al_fam.del_articulo(a.codart, a.familia) or "")
            if f is not None and clase_efectiva_py(None, f.clase) in CONSUMIBLE_O_RECAMBIO:
                cuenta[f.codigo] = f.gama
        fila = {"codart": ultimo.codart.strip(), "descripcion": ultimo.descripcion,
                "palabra": next(p for p in palabras.values() if p),
                "familias": sorted(cuenta), "gamas": sorted({g for g in cuenta.values() if g})}
        p = propias.get(k)
        if p is not None and p.por == POR and p.clase == CLASE:
            out["ya_script"].append(fila)
            continue
        if not cuenta:
            continue                     # no cuenta como consumible ni recambio: nada que hacer
        if p is not None:
            out["no_maquina" if p.clase == CLASE_NO_MAQUINA else "ya"].append(
                {**fila, "clase": p.clase})
        elif equivalencias.clave(ultimo.codart) in maquinas:
            out["maquina"].append(fila)
        elif k in reg["puestos"]:
            out["quitados"].append(fila)
        elif k in excluidos:
            out["fuera"].append(fila)
        elif not all(palabras.values()):
            out["dudosos"].append({**fila, "descripciones": sorted(palabras)})
        else:
            out["tocar"].append(fila)
    vistos = {_clave(f["codart"]) for filas in out.values() for f in filas}
    out["excluir_desconocidos"] = sorted(c for c in excluir or ()
                                         if _clave(c) not in vistos)
    return out


def _lineas_de_factura(session: Session, codigos: list[str]) -> int:
    """Cuántas líneas de factura llevan esos artículos (con el alias resuelto;
    sin abonos ni series que no cuentan). Solo informativo."""
    from app.erp import historico_alias
    from app.erp.historico_maquinas import series_excluidas
    from app.erp.models.historico import TIPO_FACTURA, HistoricoDocumento, HistoricoLinea

    if not codigos:
        return 0
    claves = {_clave(c) for c in codigos}
    al_art = historico_alias.articulos(session)
    stmt = (select(HistoricoLinea.codart, HistoricoLinea.cantidad)
            .join(HistoricoDocumento, HistoricoDocumento.id == HistoricoLinea.documento_id)
            .where(HistoricoDocumento.tipo == TIPO_FACTURA))
    if fuera := series_excluidas(session):
        stmt = stmt.where(HistoricoDocumento.serie.notin_(sorted(fuera)))
    return sum(1 for codart, cantidad in session.execute(stmt)
               if (cantidad or 0) > 0 and _clave(al_art.resuelve(codart)) in claves)


# --- la medida ---------------------------------------------------------------------------


def medir(session: Session, gama: str) -> dict[str, Any]:
    """El ritmo de compra de consumible y recambio de la gama de cada cliente
    con máquina de esa gama (el de la ficha: todas sus compras, también
    albaranes sin facturar y líneas a 0 €), y los dormidos y huecos de cada
    gama."""
    from app.erp import parque_consultas as pc
    from app.services.analisis_parque import hechos

    activo = pc._parque_activo(session)
    clientes = sorted(cli for cli, ms in activo.items() if any(m["gama"] == gama for m in ms))
    ritmo: dict[str, int | None] = {}
    for cli in clientes:
        fechas = sorted({c["fecha"] for c in hechos._compras(session, cli)
                         if c["gama"] == gama and c["fecha"]})
        ritmo[cli] = hechos.cada_meses(fechas)
    inf = pc.informe(session)
    return {"clientes": len(clientes), "ritmo": ritmo,
            "dormidos": Counter(d["gama"] for d in inf["dormidos"]),
            "huecos": Counter(h["gama"] for h in inf["huecos"])}


def _cada(v: float | None) -> str:
    if not v:
        return "sin ritmo"
    return "cada 1 mes" if v == 1 else f"cada {v:g} meses"


def _informe(antes: dict[str, Any], despues: dict[str, Any], gama: str,
             nombres: dict[str, str]) -> None:
    cambian = [(cli, antes["ritmo"].get(cli), despues["ritmo"].get(cli))
               for cli in sorted(set(antes["ritmo"]) | set(despues["ritmo"]))
               if antes["ritmo"].get(cli) != despues["ritmo"].get(cli)]
    con_antes = [v for v in antes["ritmo"].values() if v]
    con_despues = [v for v in despues["ritmo"].values() if v]
    print(f"\nRITMO DE COMPRA de consumible y recambio de {nombres.get(gama, gama)} "
          "(el de la ficha):")
    print(f"  clientes con máquina de la gama: {antes['clientes']} → {despues['clientes']}")
    print(f"  con ritmo (3 compras o más): {len(con_antes)} → {len(con_despues)}")
    if con_antes and con_despues:
        print(f"  mediana de los ritmos: {_cada(median(con_antes))} → "
              f"{_cada(median(con_despues))}")
    print(f"  clientes cuyo ritmo cambia: {len(cambian)}")
    for cli, a, d in cambian[:40]:
        print(f"    cliente {cli}: {_cada(a)} → {_cada(d)}")
    if len(cambian) > 40:
        print(f"    … y {len(cambian) - 40} más")
    print("\nDORMIDOS Y HUECOS (antes → después):")
    gamas = sorted(set(antes["dormidos"]) | set(antes["huecos"]) | set(despues["dormidos"])
                   | set(despues["huecos"]) | {gama})
    for g in gamas:
        da, dd = antes["dormidos"].get(g, 0), despues["dormidos"].get(g, 0)
        ha, hd = antes["huecos"].get(g, 0), despues["huecos"].get(g, 0)
        if g == gama or (da, ha) != (dd, hd):
            print(f"  {nombres.get(g, g):20} dormidos {da} → {dd}   huecos {ha} → {hd}")


def _listar(titulo: str, filas: list[dict[str, Any]], *, extra: str | None = None) -> None:
    if not filas:
        return
    print(f"\n{titulo}: {len(filas)}")
    for t in filas:
        cola = f"  [{t[extra]}]" if extra and t.get(extra) else ""
        print(f"  {t['codart']:16} «{t['palabra']}»  familia {','.join(t['familias']) or '—':8} "
              f"{(t['descripcion'] or '')[:60]}{cola}")
        for d in t.get("descripciones") or []:
            print(f"      · {d[:70]}")


# --- escribir ----------------------------------------------------------------------------


def _aplicar(session: Session, tocar: list[dict[str, Any]]) -> None:
    from app.erp.models.historico import HistoricoArticuloClase

    ahora = datetime.now(UTC).replace(tzinfo=None)
    for t in tocar:
        session.add(HistoricoArticuloClase(codart=t["codart"], clase=CLASE, por=POR, at=ahora))
    session.flush()


def _recalcular(session: Session) -> int:
    """Como cuando Bart cambia una clase en Familias: se programa en la cola
    (con su cerrojo, sin cruzarse con el nocturno). Sin cola, aquí mismo."""
    from app.services import empresa_erp, empresa_erp_job

    if empresa_erp_job.encolar("garantías como Otro", ya=True) != "sin_cola":
        print("Recálculo del resumen por empresa programado: lo hace el worker en unos "
              "minutos (su estado, junto al botón «Recalcular ahora»).")
        return 0
    print("Sin cola: recalculando el resumen por empresa aquí…")
    try:
        empresa_erp.recalcular(session)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        print(f"El recálculo ha fallado ({type(exc).__name__}). El cambio YA está guardado: "
              "lanza «Recalcular ahora».", file=sys.stderr)
        return 1
    print("Recalculado.")
    return 0


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
                    help="artículos que la lista trae y no son garantías: se dejan como "
                         "están (con --apply, también en las próximas pasadas)")
    args = ap.parse_args(argv)

    from app.db.session import get_engine
    from app.erp import historico_clasificacion as cl
    from app.erp.models.historico import HistoricoArticuloClase

    if (args.apply or args.revertir) and not args.yes:
        print("Falta --yes.", file=sys.stderr)
        return 2
    with Session(get_engine()) as s:
        if args.revertir:
            mias = HistoricoArticuloClase.por == POR, HistoricoArticuloClase.clase == CLASE
            codigos = list(s.scalars(select(HistoricoArticuloClase.codart).where(*mias)))
            s.execute(delete(HistoricoArticuloClase).where(*mias))
            _guardar_registro(s, quitar=codigos)
            s.commit()
            print(f"Revertido: {len(codigos)} artículos vuelven a la clase de su familia.")
            return _recalcular(s) if codigos else 0

        nombres = {g["codigo"]: g["nombre"] for g in cl.gamas(s)}
        por_nombre = {_normal(n): c for c, n in nombres.items()}
        gama = args.gama if args.gama in nombres else por_nombre.get(_normal(args.gama))
        if gama is None:
            print(f"No hay ninguna gama «{args.gama}». Las que hay: "
                  f"{', '.join(sorted(nombres)) or '—'}.", file=sys.stderr)
            return 2
        c = candidatos(s, set(args.excluir))
        tocar = c["tocar"]
        print("GARANTÍAS COMO «OTRO»" + (" — PRUEBA, no escribe nada" if args.probar else ""))
        print(f"Artículos de garantía que hoy cuentan como consumible o recambio: {len(tocar)} "
              f"({_lineas_de_factura(s, [t['codart'] for t in tocar])} líneas de factura)")
        for t in tocar:
            print(f"  {t['codart']:16} «{t['palabra']}»  familia {','.join(t['familias']):8} "
                  f"gama {','.join(t['gamas']) or '—':10} {(t['descripcion'] or '')[:60]}")
        _listar("Ya con «Otro» de una pasada anterior de este script", c["ya_script"])
        _listar("Con clase propia que puso alguien (no se tocan)", c["ya"], extra="clase")
        _listar("Marcadas «no es una máquina»: siguen contando como su familia. No se "
                "tocan; si son garantías, ponles «Otro» en Familias", c["no_maquina"])
        _listar("Equivalentes de una máquina marcada: cuentan como máquina (no se tocan)",
                c["maquina"])
        _listar("Las puso este script y alguien las quitó después (no se vuelven a poner)",
                c["quitados"])
        _listar("Dejadas fuera (--excluir, de ahora o de antes)", c["fuera"])
        _listar("Dudosas: la descripción dice garantía unos años y otros no (el código se "
                "reutilizó). No se tocan: decide a mano", c["dudosos"])
        suyas = {_clave(t["codart"]) for t in c["ya_script"]} & {_clave(x) for x in args.excluir}
        if suyas:
            print(f"\nOjo: --excluir no quita la «Otro» que ya puso este script a "
                  f"{', '.join(sorted(suyas))}: para eso, --revertir o a mano en Familias.")
        if c["excluir_desconocidos"]:
            print(f"\nOjo: --excluir con códigos que no están en ninguna lista (se ignoran): "
                  f"{', '.join(c['excluir_desconocidos'])}")
        antes = medir(s, gama)
        _aplicar(s, tocar)
        despues = medir(s, gama)
        _informe(antes, despues, gama, nombres)
        if args.probar:
            s.rollback()
            print("\nNo se ha escrito nada. Para hacerlo: --apply --yes"
                  + (f" --excluir {' '.join(args.excluir)}" if args.excluir else ""))
            return 0
        # Todo lo excluido que está en alguna lista, se recuerda (también lo que
        # hoy no se toca por otro motivo: si mañana cambia, sigue fuera).
        desconocidos = {_clave(x) for x in c["excluir_desconocidos"]}
        _guardar_registro(s, puestos=[t["codart"] for t in tocar],
                          excluidos=[x for x in args.excluir if _clave(x) not in desconocidos])
        s.commit()
        print(f"\nHecho: {len(tocar)} artículos con clase propia «Otro» (firmados «{POR}»).")
        r = _recalcular(s) if tocar else 0
        print("Deshacerlo: --revertir --yes")
        return r


if __name__ == "__main__":
    raise SystemExit(main())
