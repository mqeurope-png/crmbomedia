"""Diagnóstico del histórico de ventas en FACTUSOL (30/10/2026) — SOLO LECTURA.

Contesta con datos reales lo que hace falta saber antes de traer a BoHub las
líneas de venta de todos los ejercicios:

  1. ¿Las líneas de factura (F_LFA) y de albarán (F_LAL) traen el código de
     artículo (ARTLFA / ARTLAL)? Por ejercicio, en % de líneas, y en la factura
     más antigua y la más reciente.
  2. ¿Qué ejercicios tiene la API? (El escritorio enseña 2014-2026; la API puede
     no tenerlos todos: responde BDNoExiste.) ¿Cuántos códigos vendidos ya no
     están en el catálogo de su año o en el actual?
  3. ¿Cuánto hay? Documentos y líneas por ejercicio.
  4. ¿Cuánto tarda? Segundos de lectura por ejercicio y en total.
  5. ¿Hay catálogo (F_ART) y familias (F_FAM)?

Garantías:
  - SOLO LECTURA. En FACTUSOL solo usa CargaTabla (leer). No abre la base de
    datos de BoHub: las credenciales salen del entorno del contenedor
    (`FactusolClient.from_settings()`, las mismas variables que usa la api).
  - SIN DATOS DE CLIENTES. Solo números, porcentajes, años, nombres de campo y
    códigos de familia. Ni nombres, ni NIF, ni importes, ni números de
    documento.
  - CORTO. Una tabla por ejercicio y un resumen: cabe en un mensaje.

Uso (sin argumentos ni variables extra; dentro del contenedor de la api):

    docker exec -i crmbo-api-1 python - \
        < /opt/crmbo/backend/scripts/diag_historico_ventas_factusol.py

o, si el script ya está en la imagen:

    docker exec crmbo-api-1 python scripts/diag_historico_ventas_factusol.py
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, "/app")

from app.integrations.factusol.client import FactusolClient  # noqa: E402

PRIMER_EJERCICIO = 2014
#: (cabecera, líneas, sufijo de línea, sufijo de cabecera)
TIPOS = {
    "fac": ("F_FAC", "F_LFA", "LFA", "FAC"),
    "alb": ("F_ALB", "F_LAL", "LAL", "ALB"),
}


def leer(
    client: FactusolClient, tabla: str, ejercicio: str,
) -> tuple[list[dict], float, str | None]:
    t0 = time.monotonic()
    try:
        filas = client.load_table(tabla, filtro="1=1", ejercicio=ejercicio)
        return filas, time.monotonic() - t0, None
    except Exception as exc:  # noqa: BLE001 — se informa, no se para
        texto = str(exc)
        if "BDNoExiste" in texto or "sin base de datos" in texto:
            return [], time.monotonic() - t0, "BDNoExiste"
        return [], time.monotonic() - t0, type(exc).__name__


def pct(parte: int, total: int) -> str:
    return f"{100 * parte / total:.1f}%" if total else "—"


def con_codigo(fila: dict, campo: str) -> bool:
    return bool(str(fila.get(campo) or "").strip())


def main() -> None:
    client = FactusolClient.from_settings()
    en_curso = max(int(client.default_ejercicio or time.strftime("%Y")), int(time.strftime("%Y")))
    total_s = 0.0

    # 5) Catálogo y familias del ejercicio en curso.
    arts, seg, err = leer(client, "F_ART", str(en_curso))
    total_s += seg
    codart_actual = {str(a.get("CODART") or "").strip() for a in arts}
    fams, seg2, err2 = leer(client, "F_FAM", str(en_curso))
    total_s += seg2
    print(f"# Histórico de ventas en FACTUSOL · ejercicio en curso {en_curso}")
    con_familia = sum(1 for a in arts if con_codigo(a, "FAMART"))
    con_sku = sum(1 for a in arts if con_codigo(a, "EQUART"))
    familias = {str(a.get("FAMART") or "").strip() for a in arts} - {""}
    print(f"F_ART {en_curso}: {len(arts)} artículos"
          f" · con familia (FAMART) {pct(con_familia, len(arts))}"
          f" · familias distintas {len(familias)}"
          f" · con SKU comercial (EQUART) {pct(con_sku, len(arts))}"
          + (f" · ERROR {err}" if err else ""))
    print(f"F_FAM {en_curso}: {len(fams)} familias"
          f" · campos: {', '.join(sorted(fams[0].keys())[:12]) if fams else '—'}"
          + (f" · ERROR {err2}" if err2 else ""))
    print()

    cab = ("ejercicio | api | facturas | líneas fac | % con ARTLFA | art. distintos"
           " | albaranes | líneas alb | % con ARTLAL | vendidos no en F_ART de su año"
           " | ni en el actual | segundos")
    print(cab)
    print("-" * len(cab))

    muestras: dict[str, str] = {}
    campos: dict[str, str] = {}
    sin_api: list[str] = []
    for anio in range(PRIMER_EJERCICIO, en_curso + 1):
        ej = str(anio)
        seg_ej = 0.0
        arts_ej, s, e = leer(client, "F_ART", ej)
        seg_ej += s
        if e == "BDNoExiste":
            sin_api.append(ej)
            print(f"{ej} | NO | — | — | — | — | — | — | — | — | — | {seg_ej:.1f}")
            total_s += seg_ej
            continue
        codart_ej = {str(a.get("CODART") or "").strip() for a in arts_ej}
        fila: dict[str, str] = {}
        vendidos: set[str] = set()
        for tipo, (t_cab, t_lin, suf, csuf) in TIPOS.items():
            cabs, s1, e1 = leer(client, t_cab, ej)
            lins, s2, e2 = leer(client, t_lin, ej)
            seg_ej += s1 + s2
            art = f"ART{suf}"
            con = [r for r in lins if con_codigo(r, art)]
            codigos = {str(r.get(art)).strip() for r in con}
            vendidos |= codigos
            fila[f"{tipo}_docs"] = str(len(cabs)) if not e1 else f"ERR {e1}"
            fila[f"{tipo}_lin"] = str(len(lins)) if not e2 else f"ERR {e2}"
            fila[f"{tipo}_pct"] = pct(len(con), len(lins))
            fila[f"{tipo}_dist"] = str(len(codigos))
            if tipo == "fac" and lins:
                # Qué campos traen las líneas (solo si existen; sin valores).
                claves = set(lins[0].keys())
                campos[ej] = " ".join(
                    f"{c}{'✓' if c in claves else '✗'}"
                    for c in (art, f"DES{suf}", f"CAN{suf}", f"PRE{suf}", f"TOT{suf}")
                ) + f" ({len(claves)} campos)"
            if tipo == "fac" and cabs and lins and (ej == str(en_curso) or not muestras):
                # 1) La factura más antigua (del primer año con datos) y la más
                # reciente (del en curso): cuántas líneas y cuántas con código.
                orden = sorted(cabs, key=lambda r: str(r.get(f"FEC{csuf}") or ""))
                docu = orden[0] if not muestras else orden[-1]
                sus = [r for r in lins
                       if str(r.get(f"COD{suf}")) == str(docu.get(f"COD{csuf}"))
                       and str(r.get(f"TIP{suf}")) == str(docu.get(f"TIP{csuf}"))]
                etiqueta = "más antigua" if not muestras else "más reciente"
                muestras[etiqueta] = (
                    f"{str(docu.get(f'FEC{csuf}'))[:7]}: {len(sus)} líneas, "
                    f"{sum(1 for r in sus if con_codigo(r, art))} con {art}")
        no_su_anio = len(vendidos - codart_ej) if arts_ej else "—"
        no_actual = len(vendidos - codart_actual) if codart_actual else "—"
        total_s += seg_ej
        print(f"{ej} | sí | {fila['fac_docs']} | {fila['fac_lin']} | {fila['fac_pct']}"
              f" | {fila['fac_dist']} | {fila['alb_docs']} | {fila['alb_lin']} | {fila['alb_pct']}"
              f" | {no_su_anio} | {no_actual} | {seg_ej:.1f}")

    print()
    print(f"Ejercicios que la API NO tiene: {', '.join(sin_api) or 'ninguno'}")
    for etiqueta, texto in muestras.items():
        print(f"Factura {etiqueta}: {texto}")
    if campos:
        primero, ultimo = min(campos), max(campos)
        print(f"Campos de F_LFA en {primero}: {campos[primero]}")
        if ultimo != primero:
            print(f"Campos de F_LFA en {ultimo}: {campos[ultimo]}")
    print(f"Lectura completa: {total_s:.0f} s")


if __name__ == "__main__":
    main()
