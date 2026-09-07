"""ERP-E4 — genera los PDF de muestra para la validación de diseño de Bart.

Dos modos:

- Sin argumentos: 5 muestras con datos de fixture realistas (espejo de la
  cadena validada en producción) → `docs/samples/`. Es lo que va en el PR.
- `--live TIPO SERIE CODIGO [--lang xx] [--out fichero.pdf]`: genera el PDF
  de un documento REAL contra FACTUSOL (necesita credenciales), para
  regenerar las muestras con datos vivos desde el servidor:

      docker exec crmbo-api-1 python -m scripts.factusol_pdf_samples \
          --live facturas 1 260737 --lang es --out /tmp/f.pdf
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from app.erp.factusol_pdf import (
    COMPANY_DEFAULTS,
    extract_document_data,
    generate_document_pdf,
    load_raw_document,
    pdf_filename,
)


class _FixtureClient:
    """Resuelve los albaranes de origen de las muestras sin red, aplicando
    el predicado de igualdad del `filtro` (como la API real)."""

    _F_ALB = [
        {"TIPALB": "5", "CODALB": 500004,
         "FECALB": "2026-08-21T00:00:00", "REFALB": "BOP-099917"},
        {"TIPALB": "5", "CODALB": 500005,
         "FECALB": "2026-08-25T00:00:00", "REFALB": "BOP-099918"},
    ]

    def load_table(self, tabla: str, *, filtro: str = "1=1",
                   ejercicio: str | None = None) -> list[dict[str, Any]]:
        rows = list(self._F_ALB) if tabla == "F_ALB" else []
        predicate = filtro.split(" ORDER BY ")[0].strip()
        if predicate == "1=1":
            return rows
        column, _, raw = predicate.partition("=")
        wanted = raw.strip().strip("'")
        return [r for r in rows if str(r.get(column.strip())) == wanted]


def _header(sfx: str, *, serie: str, codigo: int, **over: Any) -> dict[str, Any]:
    row = {
        f"TIP{sfx}": serie, f"COD{sfx}": codigo,
        f"FEC{sfx}": "2026-08-26T00:00:00",
        f"CLI{sfx}": 2458, f"CNI{sfx}": "B62867728",
        f"CNO{sfx}": "LABORATORIOS PORTA, S.A.",
        f"CDO{sfx}": "C/ Muñoz Seca, 3, Nave 7",
        f"CCP{sfx}": "08036", f"CPO{sfx}": "Barcelona",
        f"CPR{sfx}": "Barcelona", f"CPA{sfx}": "España",
        f"TEL{sfx}": "932 111 222",
        f"FOP{sfx}": "002", f"REF{sfx}": "BOP-099917",
        f"OB1{sfx}": "Entrega en horario de mañana (9-13h).",
        f"OB2{sfx}": "Dejar en recepción — preguntar por almacén.",
        f"VEN{sfx}": "2026-09-26T00:00:00",
        f"TOT{sfx}": 1055.80,
        f"NET1{sfx}": 500.00, f"BAS1{sfx}": 500.00,
        f"PIVA1{sfx}": 21, f"IIVA1{sfx}": 105.00,
        f"NET2{sfx}": 200.00, f"BAS2{sfx}": 200.00,
        f"PIVA2{sfx}": 10, f"IIVA2{sfx}": 20.00, f"IREC2{sfx}": 2.80,
        f"NET4{sfx}": 228.00, f"BAS4{sfx}": 228.00,  # banda EXENTA
        f"PED{sfx}": "PO-2026-771", f"FPE{sfx}": "2026-08-20T00:00:00",
    }
    row.update(over)
    return row


def _lineas(lsfx: str, codigo: int, *, con_albaran: bool) -> list[dict[str, Any]]:
    base = [
        ("IMP-501", "Impresora UV led ArtisJet Young\nIncluye kit de tintas "
         "inicial, RIP software y formación online de puesta en marcha.\n"
         "Garantía de 12 meses en piezas y desplazamiento en península.",
         1, 500.00, 0, 605.00),
        ("99cy", "Cartucho tinta cyan 220 ml", 4, 40.00, 10, 174.24),
        ("", "Portes e instalación (texto libre, línea sin artículo)",
         1, 200.00, 0, 242.00),
        ("FIL-EX", "Filtro de carbón activo — producto exento", 2, 114.00,
         0, 228.00),
    ]
    out = []
    for pos, (art, des, can, pre, dto, tot) in enumerate(base, start=1):
        row = {
            f"TIP{lsfx}": "5", f"COD{lsfx}": codigo, f"POS{lsfx}": pos,
            f"ART{lsfx}": art, f"DES{lsfx}": des, f"CAN{lsfx}": can,
            f"PRE{lsfx}": pre, f"DT1{lsfx}": dto, f"TOT{lsfx}": tot,
        }
        if con_albaran:
            row.update({
                f"DOC{lsfx}": "A", f"DTP{lsfx}": "5",
                f"DCO{lsfx}": 500004 if pos <= 2 else 500005,
            })
        out.append(row)
    return out


SAMPLES = [
    # (nombre lógico, doc_type, serie, sufijos, lang, con_albaran)
    ("factura_streamtec_es", "facturas", 5, ("FAC", "LFA"), "es", True),
    ("factura_streamtec_en", "facturas", 5, ("FAC", "LFA"), "en", True),
    ("factura_mqeurope_en", "facturas", 2, ("FAC", "LFA"), "en", True),
    ("presupuesto_streamtec_es", "presupuestos", 5, ("PRE", "LPS"), "es", False),
    ("albaran_es", "albaranes", 5, ("ALB", "LAL"), "es", False),
]


def generate_fixture_samples(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    client = _FixtureClient()
    written: list[Path] = []
    for name, doc_type, serie, (sfx, lsfx), lang, con_alb in SAMPLES:
        codigo = {"facturas": 260063, "presupuestos": 27,
                  "albaranes": 500004}[doc_type]
        header = _header(sfx, serie=str(serie), codigo=codigo)
        if serie == 2:
            # Muestra MQ: intracomunitaria (sin IVA), como sus facturas EN.
            for k in list(header):
                if k.startswith(("PIVA", "IIVA", "IREC")):
                    header[k] = 0
            header[f"TOT{sfx}"] = 928.00
        data = extract_document_data(
            client, doc_type, header, _lineas(lsfx, codigo, con_albaran=con_alb),
            ejercicio="2026", fop_names={"002": "Transferencia 30 días"},
        )
        pdf = generate_document_pdf(
            data, company=dict(COMPANY_DEFAULTS[serie]), lang=lang,
        )
        path = out_dir / f"{name}.pdf"
        path.write_bytes(pdf)
        written.append(path)
        print(f"  {path}  ({len(pdf)} bytes)")
    return written


def generate_live(doc_type: str, serie: int, codigo: int, lang: str,
                  out: Path) -> None:
    from sqlalchemy.orm import Session  # noqa: PLC0415

    from app.db.session import get_engine  # noqa: PLC0415
    from app.erp.factusol_pdf import (  # noqa: PLC0415
        company_for_serie,
        logo_path_for_serie,
    )
    from app.integrations.factusol.client import FactusolClient  # noqa: PLC0415
    from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

    client = FactusolClient.from_settings()
    with Session(get_engine()) as session:
        ejercicio = ejercicio_for(session)
        raw = load_raw_document(
            client, doc_type, serie=serie, codigo=codigo, ejercicio=ejercicio,
        )
        if raw is None:
            print(f"No existe {doc_type} {serie}-{codigo}", file=sys.stderr)
            raise SystemExit(1)
        data = extract_document_data(
            client, doc_type, raw[0], raw[1], ejercicio=ejercicio,
        )
        pdf = generate_document_pdf(
            data, company=company_for_serie(session, serie), lang=lang,
            logo=logo_path_for_serie(serie),
        )
    out.write_bytes(pdf)
    print(f"{out} ({len(pdf)} bytes) — {pdf_filename(doc_type, data, lang)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", nargs=3, metavar=("TIPO", "SERIE", "CODIGO"))
    parser.add_argument("--lang", default="es",
                        choices=("es", "en", "de", "fr", "nl"))
    parser.add_argument("--out", default="documento.pdf")
    parser.add_argument(
        "--dir", default=str(Path(__file__).resolve().parents[2]
                             / "docs" / "samples"),
    )
    args = parser.parse_args()
    if args.live:
        doc_type, serie, codigo = args.live
        generate_live(doc_type, int(serie), int(codigo), args.lang,
                      Path(args.out))
    else:
        print("Generando muestras de fixture:")
        generate_fixture_samples(Path(args.dir))


if __name__ == "__main__":
    main()
