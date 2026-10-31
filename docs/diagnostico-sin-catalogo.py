"""Diagnóstico de SOLO LECTURA: por qué hay artículos vendidos «sin catálogo» y
artículos con catálogo cuya familia no casa (1/11/2026).

No escribe nada. No saca clientes: solo códigos de artículo, de familia y
recuentos. Se pasa dentro del contenedor de la API:

    docker compose --env-file .env.production -f docker-compose.prod.yml \\
      -f docker-compose.plesk.yml exec -T api python - < docs/diagnostico-sin-catalogo.py
"""
import re
import unicodedata
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_engine
from app.erp.historico_maquinas import series_excluidas
from app.erp.models.historico import (
    TIPOS_VENTA,
    HistoricoArticulo,
    HistoricoDocumento,
    HistoricoFamilia,
    HistoricoLinea,
)


def k(c):
    t = unicodedata.normalize("NFKD", c or "").encode("ascii", "ignore").decode()
    return t.strip().upper()


def sin_ceros(c):
    c = k(c)
    return (c.lstrip("0") or "0") if c.isdigit() else c


def fam_key(f):
    f = (f or "").strip().upper()
    return (f.lstrip("0") or "0") if f.isdigit() else f


CASOS = ("ADR1-3DVD", "ADR1-7USB-PC")

with Session(get_engine()) as s:
    lin, doc, art = HistoricoLinea, HistoricoDocumento, HistoricoArticulo
    fuera = series_excluidas(s)
    q = (select(lin.codart, func.count(lin.id)).join(doc, doc.id == lin.documento_id)
         .where(lin.es_producto.is_(True), doc.tipo.in_(TIPOS_VENTA), lin.codart.is_not(None))
         .group_by(lin.codart))
    if fuera:
        q = q.where(doc.serie.notin_(sorted(fuera)))
    vendidas = {c: n for c, n in s.execute(q) if c and c.strip()}
    catalogo = list(s.execute(select(art.ejercicio, art.codart, art.familia)))
    exactos = {c for _, c, _ in catalogo}
    por_k = {k(c) for c in exactos}
    por_ceros = {sin_ceros(c) for c in exactos}

    faltan = {c: n for c, n in vendidas.items() if k(c) not in por_k}
    print(f"1) Vendidos sin catálogo: {len(faltan)} códigos, {sum(faltan.values())} líneas")
    rec = {c: n for c, n in faltan.items() if sin_ceros(c) in por_ceros}
    print(f"   ...que casan quitando ceros a la izquierda del CÓDIGO DE ARTÍCULO: "
          f"{len(rec)} códigos, {sum(rec.values())} líneas")
    formas = Counter()
    for c in faltan:
        formas["solo dígitos" if c.strip().isdigit() else
               "con espacios" if c != c.strip() or "  " in c else
               "con minúsculas" if c != c.upper() else "otro"] += 1
    print(f"   Forma de los códigos que faltan: {dict(formas)}")

    familias = {f.codigo: f for f in s.scalars(select(HistoricoFamilia))}
    por_clave = {}
    for f in familias.values():
        por_clave.setdefault(fam_key(f.codigo), []).append(f)
    gemelas = {kk: v for kk, v in por_clave.items() if len(v) > 1}
    print(f"2) Familias con el mismo código salvo ceros: {len(gemelas)}")
    for kk, v in sorted(gemelas.items()):
        print("   ", " | ".join(f"{f.codigo!r} clase={f.clase} gama={f.gama} "
                                f"desc={'sí' if f.descripcion else 'no'}" for f in v))
    ultimo = {}
    for ej, c, fam in sorted(catalogo, key=lambda x: x[0]):
        ultimo[k(c)] = fam
    sin_clase = Counter()
    for c, fam in ultimo.items():
        f = familias.get(fam) if fam else None
        if fam and (f is None or not f.clase):
            otra = [g for g in por_clave.get(fam_key(fam), []) if g.clase]
            sin_clase["recupera con ceros" if otra else "sin familia con clase"] += 1
    print(f"3) Artículos cuya familia del último catálogo no tiene clase: {dict(sin_clase)}")

    print("4) Los dos casos:")
    for caso in CASOS:
        filas = [(ej, c, fam) for ej, c, fam in catalogo if k(c) == caso]
        en_lineas = [(c, n) for c, n in vendidas.items() if k(c) == caso]
        print(f"   {caso}: catálogo {[(ej, repr(c), repr(f)) for ej, c, f in sorted(filas)]}")
        print(f"   {caso}: líneas {[(repr(c), n) for c, n in en_lineas]}")
        parecidos = sorted({repr(c) for _, c, _ in catalogo
                            if re.sub(r"[^A-Z0-9]", "", k(c)) == re.sub(r"[^A-Z0-9]", "", caso)})
        print(f"   {caso}: en el catálogo con otra puntuación: {parecidos}")
