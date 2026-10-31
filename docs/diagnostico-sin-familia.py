"""Diagnóstico de SOLO LECTURA: artículos del catálogo con la familia VACÍA
(1/11/2026). No escribe nada. No saca clientes: solo códigos de artículo,
nombres del catálogo, ejercicios y recuentos. Se pasa dentro del contenedor:

    docker compose --env-file .env.production -f docker-compose.prod.yml \\
      -f docker-compose.plesk.yml exec -T api python - < docs/diagnostico-sin-familia.py
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
    HistoricoLinea,
    HistoricoReglaArticulo,
)


def k(c):
    t = unicodedata.normalize("NFKD", c or "").encode("ascii", "ignore").decode()
    return t.strip().upper()


def vacia(f):
    return f is None or not str(f).strip()


#: Palabras que, por el NOMBRE, suenan a consumible. Solo para medir: no se
#: usan para asignar nada.
CONSUMIBLE = ("TINTA", "INK", "CARTUCHO", "CARTRIDGE", "FILTRO", "FILTER", "PRIMER",
              "BARNIZ", "VARNISH", "LIMPIADOR", "CLEANER", "CLEANING", "FILM", "PAPEL",
              "PAPER", "VINILO", "VINYL", "LAMINA", "CELOFAN", "BOBINA", "ROLLO", "DVD",
              "CD-R", "CDR", "TONER", "RIBBON", "CINTA", "FLUSH", "SOLVENT", "DTF")

with Session(get_engine()) as s:
    art, lin, doc = HistoricoArticulo, HistoricoLinea, HistoricoDocumento
    filas = list(s.execute(select(art.ejercicio, art.codart, art.descripcion, art.familia)))
    ejercicios = sorted({e for e, *_ in filas})
    por_cod: dict[str, list] = {}
    for ej, c, d, f in filas:
        por_cod.setdefault(k(c), []).append((ej, c, d, f))

    fuera = series_excluidas(s)
    q = (select(lin.codart, func.count(lin.id)).join(doc, doc.id == lin.documento_id)
         .where(lin.es_producto.is_(True), doc.tipo.in_(TIPOS_VENTA)).group_by(lin.codart))
    if fuera:
        q = q.where(doc.serie.notin_(sorted(fuera)))
    lineas = Counter()
    for c, n in s.execute(q):
        lineas[k(c)] += int(n)

    # «Sin familia» = el catálogo MÁS RECIENTE del artículo la tiene vacía.
    sin = {}
    for kc, rs in por_cod.items():
        ult = max(rs, key=lambda r: r[0])
        if vacia(ult[3]):
            sin[kc] = rs
    print(f"Ejercicios volcados: {ejercicios}")
    print(f"1) Artículos cuya familia del último catálogo está vacía: {len(sin)} "
          f"· líneas vendidas: {sum(lineas[c] for c in sin)}")

    # De cuántos ejercicios viene cada uno, y en cuáles está vacía.
    n_ej = Counter(len({r[0] for r in rs}) for rs in sin.values())
    print(f"2) Por número de ejercicios en que aparece: {dict(sorted(n_ej.items()))}")
    vacia_en = Counter(r[0] for rs in sin.values() for r in rs if vacia(r[3]))
    llena_en = Counter(r[0] for rs in por_cod.values() for r in rs if not vacia(r[3]))
    total_en = Counter(r[0] for rs in por_cod.values() for r in rs)
    print("   Por ejercicio: artículos con familia vacía / con familia / total del catálogo")
    for ej in ejercicios:
        n_vacia = sum(1 for rs in por_cod.values() for r in rs if r[0] == ej and vacia(r[3]))
        print(f"   {ej}: {n_vacia} vacías / {llena_en[ej]} con familia / {total_en[ej]}"
              f" (de los «sin familia»: {vacia_en[ej]})")

    # Se recuperan: tienen familia en algún otro ejercicio.
    recup = {c: max((r for r in rs if not vacia(r[3])), key=lambda r: r[0])
             for c, rs in sin.items() if any(not vacia(r[3]) for r in rs)}
    print(f"3) Se recuperan con la familia de otro ejercicio: {len(recup)} artículos "
          f"· {sum(lineas[c] for c in recup)} líneas")
    for c, r in sorted(recup.items(), key=lambda x: -lineas[x[0]])[:10]:
        print(f"   {r[1]!r}: familia {r[3]!r} en {r[0]} · {lineas[c]} líneas")

    resto = {c: rs for c, rs in sin.items() if c not in recup}
    print(f"4) No se recuperan por ningún lado: {len(resto)} artículos "
          f"· {sum(lineas[c] for c in resto)} líneas. Los 20 más vendidos:")
    for c, rs in sorted(resto.items(), key=lambda x: -lineas[x[0]])[:20]:
        ult = max(rs, key=lambda r: r[0])
        print(f"   {ult[1]!r} · {ult[2]!r} · {lineas[c]} líneas · ejercicios "
              f"{sorted({r[0] for r in rs})[0]}–{sorted({r[0] for r in rs})[-1]}")

    patron = re.compile(r"(?<![A-Z0-9])(" + "|".join(re.escape(p) for p in CONSUMIBLE) + ")")
    con = {c for c in resto if patron.search(k(max(resto[c], key=lambda r: r[0])[2]))}
    print(f"5) De los que no se recuperan, por el nombre sonarían a consumible: {len(con)} "
          f"artículos · {sum(lineas[c] for c in con)} líneas (solo medido, no se aplica)")
    pistas = s.scalar(select(func.count(HistoricoReglaArticulo.id)))
    print(f"   (pistas de máquina en BoHub, para comparar: {pistas})")
