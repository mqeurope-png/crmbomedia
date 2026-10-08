"""Auditoría de IVA por pareja emisor → cliente (**SOLO LECTURA**).

El régimen de IVA depende de la pareja «quién factura → a quién», y BoHub
emitía dando España por supuesta. Esto revisa lo que ha salido por una serie
cuya empresa NO es española (hoy la 2, MQ EUROPE BV, Bélgica):

1. Fichas F_CLI de sus clientes cuyo régimen no cuadra con esa pareja.
2. Proformas con el IVA descuadrado (revisables: se vuelven a emitir).
3. Albaranes y facturas YA EMITIDOS con el IVA descuadrado.

NO escribe nada: ni fichas, ni documentos, ni la base de BoHub. El informe se
entrega y se para — las rectificativas y los abonos los decide administración.

Uso (VPS):

    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.auditoria_iva_emisor

Opciones: --serie N (por defecto, todas las series con emisor NO español) ·
--ejercicio AAAA · --csv RUTA · --detalle N (filas por bloque en pantalla).
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from sqlalchemy.orm import Session

DEFAULT_CSV = "/tmp/auditoria_iva_emisor.csv"
_FIELDS = [
    "bloque", "serie", "emisor", "emisor_pais", "tipo", "numero", "fecha",
    "codcli", "cliente", "pais_cliente", "fuente_pais", "nif", "nif_iva",
    "regimen_ficha", "regimen_esperado", "coincide_con_ficha",
    "base", "iva", "total", "problema",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serie", type=int, default=None,
                        help="auditar solo esta serie (por defecto, todas las "
                             "que emite una empresa no española)")
    parser.add_argument("--ejercicio", default=None, help="ejercicio FACTUSOL")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="CSV de hallazgos")
    parser.add_argument("--detalle", type=int, default=40,
                        help="filas por bloque en pantalla (el CSV lleva todas)")
    args = parser.parse_args(argv)

    from app.db.session import get_engine
    from app.erp.factusol_pdf import issuer_companies
    from app.integrations.factusol.auditoria_iva import (
        DEFAULT_ISSUER_ISO2,
        Emisor,
        auditar,
        informe_texto,
    )
    from app.integrations.factusol.client import FactusolClient
    from app.integrations.factusol.service import ejercicio_for

    with Session(get_engine()) as session:
        ejercicio = args.ejercicio or ejercicio_for(session)
        emisores = [
            Emisor(serie=int(e["serie"]), nombre=str(e["nombre"]),
                   pais_iso2=e["pais_iso2"])
            for e in issuer_companies(session)
        ]
        if args.serie is not None:
            objetivo = [e for e in emisores if e.serie == args.serie]
            if not objetivo:
                print(f"La serie {args.serie} no tiene empresa configurada "
                      f"(ver /erp/settings). Series: "
                      f"{', '.join(str(e.serie) for e in emisores)}")
                return 2
        else:
            # Una serie SIN país configurado no se audita: se juzgaría
            # suponiendo España, que es justo el fallo. Se dice en pantalla
            # para que se le ponga `pais_iso2` en /erp/settings.
            sin_pais = [e for e in emisores if not e.pais_iso2]
            if sin_pais:
                print("Series sin país configurado (no se auditan; ponles "
                      "«País en ISO2» en /erp/settings): "
                      + ", ".join(f"{e.serie} {e.nombre}" for e in sin_pais))
            objetivo = [e for e in emisores
                        if e.pais_iso2 and e.pais_iso2 != DEFAULT_ISSUER_ISO2]
            if not objetivo:
                print("Ninguna empresa emisora con país configurado fuera de "
                      "España: la pareja siempre sale de España y no hay nada "
                      "que auditar.")
                return 0

        client = FactusolClient.from_settings()
        filas: list[dict[str, str]] = []
        try:
            for emisor in objetivo:
                resultado = auditar(session, client, ejercicio=ejercicio,
                                    emisor=emisor)
                print(informe_texto(resultado, detalle=args.detalle))
                print()
                filas.extend(_csv_rows(resultado))
        finally:
            # El CSV se escribe aunque reviente el segundo emisor: lo ya
            # revisado no se pierde.
            ruta = _write_csv(filas, args.csv)
            print(f"CSV con TODOS los hallazgos: {ruta}")
            print("SOLO LECTURA: no se ha escrito nada en FACTUSOL ni en BoHub.")
    return 0


def _csv_rows(resultado: dict) -> list[dict[str, str]]:
    emisor = resultado["emisor"]
    comun = {"serie": str(emisor.serie), "emisor": emisor.nombre,
             "emisor_pais": emisor.pais_iso2 or ""}
    filas: list[dict[str, str]] = []
    for f in resultado["fichas"]:
        filas.append({
            **comun, "bloque": "ficha", "tipo": "F_CLI", "numero": "",
            "fecha": "", "codcli": f["codcli"], "cliente": f["cliente"],
            "pais_cliente": f["pais_iso2"] or "", "fuente_pais": f["fuente_pais"],
            "nif": f["nif"], "nif_iva": f["nif_iva"] or "",
            "regimen_ficha": f["regimen_ficha"] or "",
            "regimen_esperado": f["regimen_esperado"] or "",
            "coincide_con_ficha": "",
            "base": "", "iva": "", "total": "", "problema": f["problema"],
        })
    for d in resultado["documentos"]:
        filas.append({
            **comun,
            "bloque": "emitido" if d["emitido"] else "proforma",
            "tipo": d["tipo_label"], "numero": d["numero"], "fecha": d["fecha"],
            "codcli": d["codcli"], "cliente": d["cliente"],
            "pais_cliente": "", "fuente_pais": "", "nif": "", "nif_iva": "",
            "regimen_ficha": d["regimen_ficha"] or "",
            "regimen_esperado": d["regimen_esperado"] or "",
            "coincide_con_ficha": "sí" if d["coincide_con_ficha"] else "no",
            "base": f"{d['base']:.2f}", "iva": f"{d['iva']:.2f}",
            "total": f"{d['total']:.2f}", "problema": d["problema"],
        })
    return filas


def _write_csv(rows: list[dict[str, str]], path: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_FIELDS, delimiter=";")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return p


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
