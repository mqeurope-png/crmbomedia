"""ERP-E4 — PDF de los documentos FACTUSOL (presupuesto, pedido, albarán y
factura), multiempresa y multiidioma.

La API de DELSOL no tiene endpoints de impresión (discovery E1: todos → 404),
así que el PDF lo genera BoHub. La ESPECIFICACIÓN son los modelos reales de
`FACTUSOL_ModelosAux.accdb` (15 modelos volcados: F-555/557/558/560, F-332/
333/336, P-49/55/90/95/100, A-160/165/175): de ahí salen el inventario de
campos, las etiquetas ES/EN y los textos fijos (identidad fiscal, bancos,
reservas de dominio). Hallazgo clave del análisis: los datos fiscales de cada
empresa están escritos DENTRO de cada modelo (no en F_EMP) — por eso la serie
determina la empresa, y aquí la identidad es CONFIGURACIÓN por serie
(`factusol_series_json.companies`, editable en /erp/settings), no código.

Motor ÚNICO parametrizado por (tipo de documento, empresa emisora derivada de
la serie, idioma). Nada de una plantilla por combinación: la variación es
datos. Arquitectura de idiomas: `LABELS[lang]` con fallback a español — para
añadir de/fr/nl basta un diccionario parcial nuevo.
"""
from __future__ import annotations

import io
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    LongTable,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy.orm import Session

from app.integrations.factusol.client import FactusolClient, FactusolError
from app.integrations.factusol.documents import DOC_SPECS, visible_number
from app.integrations.factusol.quotes import (
    _factusol_date,
    _int_or_none,
    _num,
)
from app.integrations.factusol.service import coerce_serie, series_config

logger = logging.getLogger(__name__)

PAGE_W, PAGE_H = A4  # 210×297 mm, como todos los modelos

# --- fuentes ----------------------------------------------------------------
#
# Los modelos usan Calibri/Arial (propietarias). Se usa DejaVu Sans (libre,
# métricas equivalentes, acentos/ñ/€ garantizados e INCRUSTADA en el PDF); la
# imagen del backend la instala (`fonts-dejavu-core`). Sin ella se cae a
# Helvetica, cuyo WinAnsi también cubre acentos y €.

_DEJAVU_DIR = Path("/usr/share/fonts/truetype/dejavu")
FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
if (_DEJAVU_DIR / "DejaVuSans.ttf").exists():
    try:
        pdfmetrics.registerFont(TTFont("DejaVu", str(_DEJAVU_DIR / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(
            TTFont("DejaVu-Bold", str(_DEJAVU_DIR / "DejaVuSans-Bold.ttf"))
        )
        FONT, FONT_BOLD = "DejaVu", "DejaVu-Bold"
    except Exception:  # noqa: BLE001 — fuente corrupta → Helvetica
        logger.warning("factusol_pdf: no se pudo registrar DejaVu", exc_info=True)

# --- idiomas ----------------------------------------------------------------

SUPPORTED_LANGS = ("es", "en", "de", "fr", "nl")

#: Etiquetas por idioma, transcritas de los modelos reales (F-555 es / F-560
#: en, P-90/P-100, A-160/A-175). El fallback es SIEMPRE español: un idioma
#: nuevo puede empezar parcial sin romper nada.
LABELS: dict[str, dict[str, str]] = {
    "es": {
        "title_facturas": "FACTURA",
        "title_presupuestos": "PRESUPUESTO",
        "title_albaranes": "ALBARÁN",
        "title_pedidos": "PEDIDO",
        "doc_facturas": "Factura",
        "doc_presupuestos": "Presupuesto",
        "doc_albaranes": "Albarán",
        "doc_pedidos": "Pedido",
        "documento": "DOCUMENTO",
        "numero": "NÚMERO",
        "pagina": "PÁGINA",
        "fecha": "FECHA",
        "pagina_de": "{n} de {total}",
        "cliente": "CLIENTE",
        "nif": "N.I.F.",
        "forma_pago": "FORMA DE PAGO",
        "su_referencia": "SU REFERENCIA",
        "su_pedido": "Nº DE SU PEDIDO",
        "fecha_su_pedido": "FECHA DE SU PEDIDO",
        "col_articulo": "ARTÍCULO",
        "col_descripcion": "DESCRIPCIÓN",
        "col_cantidad": "CANTIDAD",
        "col_precio": "PRECIO UNIDAD",
        "col_dto": "DTO. %",
        "col_subtotal": "SUBTOTAL",
        "col_total": "TOTAL",
        "band_tipo": "TIPO",
        "band_neto": "NETO",
        "band_dto": "DESCUENTO",
        "band_portes": "PORTES",
        "band_fin": "FINANCIACIÓN",
        "band_base": "BASE",
        "band_iva": "I.V.A.",
        "band_re": "R.E.",
        "band_exento": "Exento",
        "total": "TOTAL:",
        "observaciones": "OBSERVACIONES:",
        "vencimiento": "1er VENCIMIENTO:",
        "cuenta": "DATOS BANCARIOS",
        "banco": "Banco",
        "albaran_de_linea": "Albarán {numero}",
        "albaran_fecha": "fecha {fecha}",
        "albaran_ref": "ref. {ref}",
        "direccion_entrega": "DIRECCIÓN DE ENTREGA:",
        "validez_presupuesto":
            "Presupuesto válido durante 30 días, a partir de la fecha de "
            "emisión.",
        "sin_lineas": "(sin líneas)",
    },
    "en": {
        "title_facturas": "INVOICE",
        "title_presupuestos": "QUOTATION",
        "title_albaranes": "DELIVERY NOTE",
        "title_pedidos": "ORDER",
        "doc_facturas": "Invoice",
        "doc_presupuestos": "Quotation",
        "doc_albaranes": "Delivery note",
        "doc_pedidos": "Order",
        "documento": "DOCUMENT",
        "numero": "NUMBER",
        "pagina": "PAGE",
        "fecha": "DATE",
        "pagina_de": "{n} of {total}",
        "cliente": "CUSTOMER",
        "nif": "VAT Nr",
        "forma_pago": "PAYMENT CONDITIONS",
        "su_referencia": "YOUR REFERENCE",
        "su_pedido": "YOUR ORDER Nr",
        "fecha_su_pedido": "YOUR ORDER DATE",
        "col_articulo": "ITEM",
        "col_descripcion": "DESCRIPTION",
        "col_cantidad": "QUANTITY",
        "col_precio": "UNIT PRICE",
        "col_dto": "DISCOUNT %",
        "col_subtotal": "SUBTOTAL",
        "col_total": "TOTAL",
        "band_tipo": "TAX %",
        "band_neto": "NET",
        "band_dto": "DISCOUNT",
        "band_portes": "SHIPPING",
        "band_fin": "FINANCING",
        "band_base": "BASE",
        "band_iva": "V.A.T.",
        "band_re": "R.E.",
        "band_exento": "Exempt",
        "total": "TOTAL:",
        "observaciones": "COMMENTS:",
        "vencimiento": "1st DUE DATE:",
        "cuenta": "BANK INFORMATION",
        "banco": "Bank",
        "albaran_de_linea": "Delivery note {numero}",
        "albaran_fecha": "date {fecha}",
        "albaran_ref": "ref. {ref}",
        "direccion_entrega": "DELIVERY ADDRESS:",
        "validez_presupuesto":
            "This quotation is valid for 30 days from the date of issue.",
        "sin_lineas": "(no lines)",
    },
    "de": {
        "title_facturas": "RECHNUNG",
        "title_presupuestos": "ANGEBOT",
        "title_albaranes": "LIEFERSCHEIN",
        "title_pedidos": "BESTELLUNG",
        "doc_facturas": "Rechnung",
        "doc_presupuestos": "Angebot",
        "doc_albaranes": "Lieferschein",
        "doc_pedidos": "Bestellung",
        "documento": "DOKUMENT",
        "numero": "NUMMER",
        "pagina": "SEITE",
        "fecha": "DATUM",
        "pagina_de": "{n} von {total}",
        "cliente": "KUNDE",
        "nif": "USt-IdNr.",
        "forma_pago": "ZAHLUNGSBEDINGUNGEN",
        "su_referencia": "IHRE REFERENZ",
        "su_pedido": "IHRE BESTELLNUMMER",
        "fecha_su_pedido": "IHR BESTELLDATUM",
        "col_articulo": "ARTIKEL",
        "col_descripcion": "BESCHREIBUNG",
        "col_cantidad": "MENGE",
        "col_precio": "STÜCKPREIS",
        "col_dto": "RABATT %",
        "col_subtotal": "ZWISCHENSUMME",
        "col_total": "GESAMT",
        "band_tipo": "MwSt. %",
        "band_neto": "NETTO",
        "band_dto": "RABATT",
        "band_portes": "VERSAND",
        "band_fin": "FINANZIERUNG",
        "band_base": "BASIS",
        "band_iva": "MwSt.",
        "band_re": "R.E.",
        "band_exento": "Befreit",
        "total": "GESAMT:",
        "observaciones": "ANMERKUNGEN:",
        "vencimiento": "1. FÄLLIGKEIT:",
        "cuenta": "BANKVERBINDUNG",
        "banco": "Bank",
        "albaran_de_linea": "Lieferschein {numero}",
        "albaran_fecha": "Datum {fecha}",
        "albaran_ref": "Ref. {ref}",
        "direccion_entrega": "LIEFERADRESSE:",
        "validez_presupuesto":
            "Dieses Angebot ist 30 Tage ab Ausstellungsdatum gültig.",
        "sin_lineas": "(keine Positionen)",
    },
    "fr": {
        "title_facturas": "FACTURE",
        "title_presupuestos": "DEVIS",
        "title_albaranes": "BON DE LIVRAISON",
        "title_pedidos": "COMMANDE",
        "doc_facturas": "Facture",
        "doc_presupuestos": "Devis",
        "doc_albaranes": "Bon de livraison",
        "doc_pedidos": "Commande",
        "documento": "DOCUMENT",
        "numero": "NUMÉRO",
        "pagina": "PAGE",
        "fecha": "DATE",
        "pagina_de": "{n} sur {total}",
        "cliente": "CLIENT",
        "nif": "Nº TVA",
        "forma_pago": "CONDITIONS DE PAIEMENT",
        "su_referencia": "VOTRE RÉFÉRENCE",
        "su_pedido": "Nº DE VOTRE COMMANDE",
        "fecha_su_pedido": "DATE DE VOTRE COMMANDE",
        "col_articulo": "ARTICLE",
        "col_descripcion": "DESCRIPTION",
        "col_cantidad": "QUANTITÉ",
        "col_precio": "PRIX UNITAIRE",
        "col_dto": "REMISE %",
        "col_subtotal": "SOUS-TOTAL",
        "col_total": "TOTAL",
        "band_tipo": "TVA %",
        "band_neto": "NET",
        "band_dto": "REMISE",
        "band_portes": "PORT",
        "band_fin": "FINANCEMENT",
        "band_base": "BASE",
        "band_iva": "T.V.A.",
        "band_re": "R.E.",
        "band_exento": "Exonéré",
        "total": "TOTAL :",
        "observaciones": "OBSERVATIONS :",
        "vencimiento": "1ère ÉCHÉANCE :",
        "cuenta": "COORDONNÉES BANCAIRES",
        "banco": "Banque",
        "albaran_de_linea": "Bon de livraison {numero}",
        "albaran_fecha": "date {fecha}",
        "albaran_ref": "réf. {ref}",
        "direccion_entrega": "ADRESSE DE LIVRAISON :",
        "validez_presupuesto":
            "Devis valable 30 jours à compter de la date d'émission.",
        "sin_lineas": "(aucune ligne)",
    },
    "nl": {
        "title_facturas": "FACTUUR",
        "title_presupuestos": "OFFERTE",
        "title_albaranes": "LEVERINGSBON",
        "title_pedidos": "BESTELLING",
        "doc_facturas": "Factuur",
        "doc_presupuestos": "Offerte",
        "doc_albaranes": "Leveringsbon",
        "doc_pedidos": "Bestelling",
        "documento": "DOCUMENT",
        "numero": "NUMMER",
        "pagina": "PAGINA",
        "fecha": "DATUM",
        "pagina_de": "{n} van {total}",
        "cliente": "KLANT",
        "nif": "BTW-nr",
        "forma_pago": "BETALINGSVOORWAARDEN",
        "su_referencia": "UW REFERENTIE",
        "su_pedido": "UW BESTELNUMMER",
        "fecha_su_pedido": "DATUM VAN UW BESTELLING",
        "col_articulo": "ARTIKEL",
        "col_descripcion": "OMSCHRIJVING",
        "col_cantidad": "AANTAL",
        "col_precio": "STUKSPRIJS",
        "col_dto": "KORTING %",
        "col_subtotal": "SUBTOTAAL",
        "col_total": "TOTAAL",
        "band_tipo": "BTW %",
        "band_neto": "NETTO",
        "band_dto": "KORTING",
        "band_portes": "VERZENDKOSTEN",
        "band_fin": "FINANCIERING",
        "band_base": "GRONDSLAG",
        "band_iva": "B.T.W.",
        "band_re": "R.E.",
        "band_exento": "Vrijgesteld",
        "total": "TOTAAL:",
        "observaciones": "OPMERKINGEN:",
        "vencimiento": "1e VERVALDATUM:",
        "cuenta": "BANKGEGEVENS",
        "banco": "Bank",
        "albaran_de_linea": "Leveringsbon {numero}",
        "albaran_fecha": "datum {fecha}",
        "albaran_ref": "ref. {ref}",
        "direccion_entrega": "LEVERADRES:",
        "validez_presupuesto":
            "Deze offerte is 30 dagen geldig vanaf de uitgiftedatum.",
        "sin_lineas": "(geen regels)",
    },
}


def labels_for(lang: str) -> dict[str, str]:
    base = dict(LABELS["es"])
    base.update(LABELS.get(lang, {}))
    return base


# --- empresas emisoras (identidad fiscal por serie) -------------------------
#
# Valores INICIALES extraídos de los modelos reales (los datos fiscales viven
# como texto fijo dentro de cada modelo — no en F_EMP). Editables en
# /erp/settings (`factusol_series_json.companies`): Bart corrige un IBAN sin
# despliegue. Los textos legales van POR IDIOMA con fallback al que exista.

COMPANY_DEFAULTS: dict[int, dict[str, Any]] = {
    1: {
        "nombre": "Bomedia S.L.",
        "direccion": "Via Augusta, 48, 2, 5",
        "cp_poblacion": "08006 Barcelona",
        "pais": "España",
        "telefono": "Tel. 932 010 793",
        "email": "bomedia@bomedia.net",
        "nif": "NIF B63609309",
        "banco": "Banco de Sabadell, S.A., Avda. Óscar Esplá, 37, 03007 Alicante",
        "iban": "ES33 0081 0202 13 0001171918",
        "bic": "BSABESBB",
        "legal": {
            "es": "El material suministrado es propiedad de BOMEDIA S.L. "
                  "hasta recibir la totalidad del pago correspondiente.",
        },
        "pie": {"es": "CONDICIONES GENERALES EN WWW.BOMEDIA.NET"},
        "intracom": {},
    },
    5: {
        "nombre": "Streamtec SL",
        "direccion": "C. Corsega 232, 5",
        "cp_poblacion": "08036 Barcelona",
        "pais": "España",
        "telefono": "Tel. 932022530",
        "email": "",
        "nif": "CIF B64154263",
        "banco": "Banco de Sabadell, S.A., Avda. Óscar Esplá, 37, 03007 Alicante",
        "iban": "ES11 0081 0202 1700 0125 9030",
        "bic": "BSABESBB",
        "legal": {},
        "pie": {},
        "intracom": {
            "es": "Entrega intracomunitaria, o exportación exenta de IVA",
        },
    },
    2: {
        "nombre": "MQ Europe BV",
        "direccion": "Arnould Nobelstraat 30, 0405",
        "cp_poblacion": "B3000 Leuven",
        "pais": "Belgium",
        "telefono": "",
        "email": "sales@mqeurope.com",
        "nif": "VAT nr. BE 0883.002.183 (RPR TONGEREN)",
        "banco": "Belfius Bank Zaventem Belgium",
        "iban": "BE28068245312320",
        "bic": "GKCCBEBB",
        "legal": {
            "es": "RESERVA DE DOMINIO: El material es propiedad de MQ Europe "
                  "BVBA hasta recibir la totalidad de su pago.",
            "en": "Reservation of ownership: this material is property of "
                  "MQ Europe BVBA untill reception of full payment",
        },
        "pie": {},
        "intracom": {
            "en": "INTRACOMMUNITY DELIVERY — No belgian VAT due and VAT to "
                  "be paid by the co-contractant - art. 25ter, §1er, al. 2, "
                  "3° of the Belgian VAT Code",
            "es": "INTRACOMMUNITY DELIVERY — No belgian VAT due and VAT to "
                  "be paid by the co-contractant - art. 25ter, §1er, al. 2, "
                  "3° of the Belgian VAT Code",
        },
    },
}

#: Campos de texto plano de la empresa (los editables simples de settings).
COMPANY_TEXT_FIELDS = (
    "nombre", "direccion", "cp_poblacion", "pais", "telefono", "email",
    "nif", "banco", "iban", "bic",
)
#: Campos por-idioma (dict {lang: texto}).
COMPANY_LANG_FIELDS = ("legal", "pie", "intracom")


def merge_companies(stored: Any) -> dict[str, dict[str, Any]]:
    """Defaults de los modelos reales + overrides guardados
    (`factusol_series_json.companies`), con las claves de serie como STRING
    (la forma JSON de la API de settings). Series sin default arrancan
    vacías."""
    stored = stored if isinstance(stored, dict) else {}
    out: dict[str, dict[str, Any]] = {}
    series = set(COMPANY_DEFAULTS) | {
        s for s in (coerce_serie(k) for k in stored) if s is not None
    }
    for serie in sorted(series):
        base = COMPANY_DEFAULTS.get(serie, {})
        merged: dict[str, Any] = {
            f: str(base.get(f, "") or "") for f in COMPANY_TEXT_FIELDS
        }
        for f in COMPANY_LANG_FIELDS:
            merged[f] = dict(base.get(f, {}) or {})
        override = stored.get(str(serie))
        if isinstance(override, dict):
            for f in COMPANY_TEXT_FIELDS:
                if f in override and override[f] is not None:
                    merged[f] = str(override[f])
            for f in COMPANY_LANG_FIELDS:
                sub = override.get(f)
                if isinstance(sub, dict):
                    merged[f].update({
                        str(k): str(v) for k, v in sub.items()
                    })
        out[str(serie)] = merged
    return out


def companies_config(session: Session) -> dict[int, dict[str, Any]]:
    merged = merge_companies(series_config(session).get("companies"))
    return {int(k): v for k, v in merged.items()}


def company_for_serie(session: Session, serie: int) -> dict[str, Any]:
    companies = companies_config(session)
    if serie in companies:
        return companies[serie]
    # Serie sin identidad configurada: PDF sin bloque de empresa antes que
    # inventarse una (la serie ya identifica el documento).
    return {f: "" for f in COMPANY_TEXT_FIELDS} | {
        f: {} for f in COMPANY_LANG_FIELDS
    }


def _lang_text(company: dict[str, Any], field: str, lang: str) -> str:
    """Texto legal en `lang`, cayendo a cualquier idioma disponible (mejor la
    reserva de dominio en español que perderla)."""
    sub = company.get(field) or {}
    if not isinstance(sub, dict):
        return str(sub or "")
    return str(
        sub.get(lang) or sub.get("es") or sub.get("en")
        or next((v for v in sub.values() if v), "")
    )


# --- logos ------------------------------------------------------------------
#
# Los modelos apuntan a rutas locales del PC de Bart (inaccesibles): los
# logos se suben desde /erp/settings y se guardan bajo el directorio de
# assets ya montado en producción. El PDF funciona SIN logo (hueco, no error).

_LOGO_EXTS = (".png", ".jpg", ".jpeg")


def logos_dir() -> Path:
    from app.core.config import get_settings  # noqa: PLC0415

    return Path(get_settings().email_assets_dir) / "erp-logos"


def logo_path_for_serie(serie: int) -> Path | None:
    base = logos_dir()
    for ext in _LOGO_EXTS:
        p = base / f"serie_{serie}{ext}"
        if p.exists():
            return p
    return None


# --- carga del documento (filas CRUDAS) -------------------------------------


def load_raw_document(
    client: FactusolClient, doc_type: str, *, serie: int, codigo: int,
    ejercicio: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """Cabecera + líneas CRUDAS por clave compuesta — el PDF necesita las
    bandas de IVA, observaciones y enlaces por línea que la vista normalizada
    de E3-A descarta. Mismo criterio de carga que `documents.get_document`."""
    spec = DOC_SPECS[doc_type]
    rows = client.load_table(
        spec.table, filtro=f"{spec.cod}={int(codigo)}", ejercicio=ejercicio,
    )
    header = next(
        (r for r in rows if coerce_serie(r.get(spec.tip)) == serie), None,
    )
    if header is None:
        return None
    line_rows = client.load_table(
        spec.lines_table, filtro=f"{spec.line_fk}={int(codigo)}",
        ejercicio=ejercicio,
    )
    lines = [
        r for r in line_rows
        if coerce_serie(r.get(spec.line_tip)) in (serie, None)
    ]
    lines.sort(key=lambda r: _int_or_none(r.get(f"POS{spec.lines_suffix}")) or 0)
    return header, lines


def _clean(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def _fmt_date(v: Any) -> str:
    iso = _factusol_date(v)
    if not iso:
        return _clean(v)
    y, m, d = iso.split("-")
    return f"{d}-{m}-{y}"


def _fmt_money(v: float, lang: str) -> str:
    s = f"{v:,.2f}"
    if lang != "en":
        s = s.replace(",", " ").replace(".", ",").replace(" ", ".")
    return s


def _fmt_qty(v: float, lang: str) -> str:
    s = f"{v:,.2f}".rstrip("0").rstrip(".")
    if lang != "en":
        s = s.replace(",", " ").replace(".", ",").replace(" ", ".")
    return s or "0"


def extract_document_data(
    client: FactusolClient,
    doc_type: str,
    header: dict[str, Any],
    lines: list[dict[str, Any]],
    *,
    ejercicio: str,
    fop_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Filas crudas → estructura neutra que consume el maquetador.

    Inventario de la Parte C del spec: cliente completo, número, fecha,
    forma de pago, hasta 4 bandas de IVA (la 4ª es la EXENTA — en el schema
    real NET4/BAS4 existen sin PIVA4/IIVA4), total, observaciones (OB1/OB2),
    1er vencimiento, referencia/pedido del cliente, y por línea: código,
    descripción, cantidad, precio, descuentos, subtotal, total y — en
    facturas — el albarán de origen (enlace DOC/DTP/DCO de E3-B, resuelto a
    número + fecha + referencia)."""
    spec = DOC_SPECS[doc_type]
    sfx = spec.suffix
    lsfx = spec.lines_suffix

    def h(prefix: str) -> Any:
        return header.get(f"{prefix}{sfx}")

    serie = coerce_serie(h("TIP")) or 0
    codigo = _int_or_none(h("COD")) or 0

    bands: list[dict[str, Any]] = []
    for i in (1, 2, 3, 4):
        def b(prefix: str, _i: int = i) -> float:
            return _num(header.get(f"{prefix}{_i}{sfx}"), 0.0)
        band = {
            "exenta": i == 4,
            "neto": b("NET"), "dto": b("IDTO"), "portes": b("IPOR"),
            "fin": b("IFIN"), "base": b("BAS"), "piva": b("PIVA"),
            "iva": b("IIVA"), "prec": b("PREC"), "rec": b("IREC"),
        }
        if any(abs(v) > 0.004 for k, v in band.items() if k != "exenta"):
            bands.append(band)

    # Facturas: resolver el albarán de origen de cada línea (una factura
    # puede agrupar varios albaranes — no se pierde la agrupación).
    albaran_info: dict[tuple[int, int], dict[str, Any]] = {}
    if doc_type == "facturas":
        refs = set()
        for row in lines:
            if _clean(row.get(f"DOC{lsfx}")).upper() == "A":
                alb_serie = coerce_serie(row.get(f"DTP{lsfx}"))
                alb_cod = _int_or_none(row.get(f"DCO{lsfx}"))
                if alb_serie is not None and alb_cod is not None:
                    refs.add((alb_serie, alb_cod))
        for alb_serie, alb_cod in refs:
            info: dict[str, Any] = {
                "numero": visible_number(alb_serie, alb_cod),
                "fecha": None, "ref": None,
            }
            try:
                raw = load_raw_document(
                    client, "albaranes", serie=alb_serie, codigo=alb_cod,
                    ejercicio=ejercicio,
                )
                if raw is not None:
                    info["fecha"] = _fmt_date(raw[0].get("FECALB")) or None
                    info["ref"] = _clean(raw[0].get("REFALB")) or None
            except FactusolError:  # el PDF sale igual, sin fecha/ref
                logger.warning(
                    "factusol_pdf: no se pudo resolver el albarán %s-%s",
                    alb_serie, alb_cod,
                )
            albaran_info[(alb_serie, alb_cod)] = info

    out_lines: list[dict[str, Any]] = []
    for row in lines:
        cantidad = _num(row.get(f"CAN{lsfx}"), 0.0)
        precio = _num(row.get(f"PRE{lsfx}"), 0.0)
        dtos = [
            _num(row.get(f"DT{j}{lsfx}"), 0.0) for j in (1, 2, 3)
        ]
        dto_txt = "+".join(
            _fmt_qty(d, "en") for d in dtos if abs(d) > 0.004
        )
        albaran = None
        if doc_type == "facturas" and _clean(row.get(f"DOC{lsfx}")).upper() == "A":
            key = (
                coerce_serie(row.get(f"DTP{lsfx}")),
                _int_or_none(row.get(f"DCO{lsfx}")),
            )
            albaran = albaran_info.get(key)  # type: ignore[arg-type]
        out_lines.append({
            "codart": _clean(row.get(f"ART{lsfx}")),
            "descripcion": _clean(row.get(f"DES{lsfx}")),
            "cantidad": cantidad,
            "precio": precio,
            "dto": dto_txt,
            "subtotal": cantidad * precio,
            "total": _num(row.get(f"TOT{lsfx}"), 0.0),
            "albaran": albaran,
        })

    fop_code = _clean(h("FOP"))
    fop = (fop_names or {}).get(fop_code) or (fop_names or {}).get(
        fop_code.lstrip("0") or "0"
    ) or fop_code

    return {
        "doc_type": doc_type,
        "serie": serie,
        "codigo": codigo,
        "numero": visible_number(h("TIP"), h("COD")),
        "fecha": _fmt_date(h("FEC")),
        "cliente": {
            "codigo": _clean(h("CLI")),
            "nif": _clean(h("CNI")),
            "nombre": _clean(h("CNO")),
            "domicilio": _clean(h("CDO")),
            "cp": _clean(h("CCP")),
            "poblacion": _clean(h("CPO")),
            "provincia": _clean(h("CPR")),
            "pais": _clean(h("CPA")),
            "telefono": _clean(h("TEL")),
        },
        "forma_pago": fop,
        "referencia": _clean(h("REF")),
        "pedido_cliente": _clean(h("PED")),
        "fecha_pedido_cliente": _fmt_date(h("FPE")) if h("FPE") else "",
        "observaciones": [t for t in (_clean(h("OB1")), _clean(h("OB2"))) if t],
        "vencimiento": _fmt_date(h("VEN")) if h("VEN") else "",
        "bands": bands,
        "total": _num(h("TOT"), 0.0),
        "lines": out_lines,
    }


# --- maquetación ------------------------------------------------------------

GREY = colors.HexColor("#3f3f3f")
LIGHT = colors.HexColor("#f0f0f0")
RULE = colors.HexColor("#9a9a9a")

#: Coordenadas (mm desde el borde superior) de la franja DOCUMENTO/NÚMERO/
#: PÁGINA/FECHA — compartidas entre el dibujo de cabecera y el canvas
#: numerado que estampa «página X de Y» en la segunda pasada.
_STRIP_TOP_MM = 58
_PAGE_CELL_X_MM = 164


class _NumberedCanvas(rl_canvas.Canvas):
    """Canvas de dos pasadas para «página X de Y» (el total de páginas no se
    conoce hasta el final). El resto de la cabecera lo dibuja `onPage`."""

    _pagina_de = "{n} de {total}"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saved_states: list[dict[str, Any]] = []

    def showPage(self) -> None:  # noqa: N802 — API reportlab
        self._saved_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._saved_states)
        for state in self._saved_states:
            self.__dict__.update(state)
            self.setFont(FONT, 9)
            self.setFillColor(colors.black)
            self.drawString(
                _PAGE_CELL_X_MM * mm,
                PAGE_H - (_STRIP_TOP_MM + 9.2) * mm,
                self._pagina_de.format(n=self._pageNumber, total=total),
            )
            super().showPage()
        super().save()


def _para_style(
    size: float = 9, *, bold: bool = False, leading: float | None = None,
) -> ParagraphStyle:
    return ParagraphStyle(
        name=f"p{size}{'b' if bold else ''}",
        fontName=FONT_BOLD if bold else FONT,
        fontSize=size,
        leading=leading or size * 1.25,
        textColor=colors.black,
    )


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")
    )


def generate_document_pdf(
    data: dict[str, Any],
    *,
    company: dict[str, Any],
    lang: str = "es",
    logo: Path | None = None,
    valued: bool | None = None,
) -> bytes:
    """Estructura neutra + empresa + idioma → bytes del PDF A4.

    `valued=None` aplica el criterio de los modelos: factura, presupuesto y
    pedido con importes; albarán SIN importes (el «albarán valorado» queda
    previsto pasando `valued=True`)."""
    doc_type = data["doc_type"]
    lab = labels_for(lang)
    if valued is None:
        valued = doc_type != "albaranes"
    title = lab[f"title_{doc_type}"]
    doc_label = lab[f"doc_{doc_type}"]

    # ¿Aplica el texto intracomunitario? Criterio de los modelos «SIN IVA»:
    # documento valorado sin una sola banda con IVA.
    total_iva = sum(b["iva"] for b in data["bands"])
    intracom = (
        _lang_text(company, "intracom", lang)
        if valued and abs(total_iva) < 0.005 else ""
    )
    legal = _lang_text(company, "legal", lang)
    pie = _lang_text(company, "pie", lang)

    footer_lines = _footer_lines(company, lab, legal, intracom, pie,
                                 validez=(doc_type == "presupuestos"
                                          and lab["validez_presupuesto"]) or "")
    footer_h_mm = 6 + 4.2 * len(footer_lines)

    frame = Frame(
        7 * mm,
        (footer_h_mm + 4) * mm,
        PAGE_W - 14 * mm,
        PAGE_H - (86 + footer_h_mm + 4) * mm,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )

    def on_page(cv: rl_canvas.Canvas, _doc: Any) -> None:
        _draw_header(cv, data, company, lab, title, doc_label, logo)
        _draw_footer(cv, footer_lines)

    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        title=f"{doc_label} {data['numero']}",
        author=company.get("nombre") or "BoHub",
    )
    doc.addPageTemplates([PageTemplate(id="doc", frames=[frame], onPage=on_page)])

    _NumberedCanvas._pagina_de = lab["pagina_de"]

    story: list[Any] = [_lines_table(data, lab, lang, valued=valued)]
    story.append(Spacer(1, 4 * mm))
    story.extend(_summary_flowables(data, lab, lang, valued=valued))
    doc.build(story, canvasmaker=_NumberedCanvas)
    return buf.getvalue()


def _draw_header(
    cv: rl_canvas.Canvas,
    data: dict[str, Any],
    company: dict[str, Any],
    lab: dict[str, str],
    title: str,
    doc_label: str,
    logo: Path | None,
) -> None:
    """Cabecera fija de TODAS las páginas (estructura de los modelos: logo a
    la izquierda, identidad fiscal a la derecha, franja del documento,
    bloque de cliente, N.I.F. + forma de pago)."""
    top = PAGE_H

    if logo is not None:
        try:
            cv.drawImage(
                str(logo), 8 * mm, top - 32 * mm, width=77 * mm, height=26 * mm,
                preserveAspectRatio=True, anchor="nw", mask="auto",
            )
        except Exception:  # noqa: BLE001 — logo corrupto = hueco, no error
            logger.warning("factusol_pdf: logo ilegible %s", logo)

    # Título + identidad fiscal (columna derecha, x=114 como los modelos).
    x_id = 114 * mm
    cv.setFillColor(GREY)
    cv.setFont(FONT_BOLD, 24)
    cv.drawString(x_id, top - 18 * mm, title)
    cv.setFillColor(colors.black)
    y = top - 27 * mm
    id_lines = [
        (company.get("nombre"), True),
        (company.get("direccion"), False),
        (company.get("cp_poblacion"), False),
        (company.get("pais"), False),
        (company.get("telefono"), False),
        (company.get("email"), False),
        (company.get("nif"), False),
    ]
    for text, bold in id_lines:
        text = str(text or "").strip()
        if not text:
            continue
        cv.setFont(FONT_BOLD if bold else FONT, 9)
        cv.drawString(x_id, y, text)
        y -= 4.4 * mm

    # Franja DOCUMENTO | NÚMERO | PÁGINA | FECHA.
    sy = top - _STRIP_TOP_MM * mm
    cv.setFillColor(LIGHT)
    cv.rect(112 * mm, sy - 11 * mm, 91 * mm, 11 * mm, stroke=0, fill=1)
    cv.setFillColor(GREY)
    cv.setFont(FONT_BOLD, 7.5)
    for x_mm, key in ((114, "documento"), (138, "numero"),
                      (164, "pagina"), (182, "fecha")):
        cv.drawString(x_mm * mm, sy - 3.6 * mm, lab[key])
    cv.setFillColor(colors.black)
    cv.setFont(FONT, 9)
    cv.drawString(114 * mm, sy - 9.2 * mm, doc_label)
    cv.drawString(138 * mm, sy - 9.2 * mm, data["numero"])
    # el valor de PÁGINA lo estampa _NumberedCanvas (página X de Y)
    cv.drawString(182 * mm, sy - 9.2 * mm, data["fecha"] or "—")

    # Bloque de cliente (izquierda).
    cli = data["cliente"]
    cy = top - 34 * mm
    cv.setFont(FONT_BOLD, 8)
    cv.setFillColor(GREY)
    cv.drawString(10 * mm, cy, lab["cliente"])
    cv.setFillColor(colors.black)
    cy -= 5 * mm
    cli_lines = [
        cli["nombre"],
        cli["domicilio"],
        " ".join(x for x in (cli["cp"], cli["poblacion"]) if x),
        " ".join(x for x in (cli["provincia"], cli["pais"]) if x),
        cli["telefono"],
    ]
    for text in cli_lines:
        if not text:
            continue
        cv.setFont(FONT, 9)
        cv.drawString(10 * mm, cy, text)
        cy -= 4.4 * mm

    # N.I.F. + SU REFERENCIA + FORMA DE PAGO, y — si existe — el nº/fecha
    # del pedido del cliente en su propia línea (sin solapar columnas).
    ry = top - 72 * mm
    cv.setFont(FONT_BOLD, 7.5)
    cv.setFillColor(GREY)
    cv.drawString(10 * mm, ry, lab["nif"])
    cv.drawString(64 * mm, ry, lab["su_referencia"])
    cv.drawString(134 * mm, ry, lab["forma_pago"])
    cv.setFillColor(colors.black)
    cv.setFont(FONT, 9)
    ry -= 4.6 * mm
    cv.drawString(10 * mm, ry, _fit(cli["nif"] or "—", 9, 50))
    cv.drawString(64 * mm, ry, _fit(data["referencia"] or "—", 9, 66))
    cv.drawString(134 * mm, ry, _fit(data["forma_pago"] or "—", 9, 66))
    if data["pedido_cliente"]:
        pedido = f"{lab['su_pedido']}: {data['pedido_cliente']}"
        if data["fecha_pedido_cliente"]:
            pedido += (
                f" · {lab['fecha_su_pedido']}: {data['fecha_pedido_cliente']}"
            )
        cv.setFont(FONT, 8)
        cv.drawString(10 * mm, ry - 4.4 * mm, _fit(pedido, 8, 190))

    cv.setStrokeColor(RULE)
    cv.setLineWidth(0.4)
    cv.line(7 * mm, top - 84 * mm, PAGE_W - 7 * mm, top - 84 * mm)


def _fit(text: str, size: float, max_mm: float) -> str:
    """Recorta con «…» lo que no cabe en `max_mm` — un dato largo nunca debe
    solapar la columna vecina de la cabecera."""
    limit = max_mm * mm
    if pdfmetrics.stringWidth(text, FONT, size) <= limit:
        return text
    while text and pdfmetrics.stringWidth(text + "…", FONT, size) > limit:
        text = text[:-1]
    return text + "…"


def _footer_lines(
    company: dict[str, Any],
    lab: dict[str, str],
    legal: str,
    intracom: str,
    pie: str,
    *,
    validez: str,
) -> list[tuple[str, bool]]:
    """(texto, destacado) del pie fijo: validez del presupuesto, texto
    intracomunitario, banco/IBAN/BIC, reserva de dominio y pie de
    condiciones — en TODAS las páginas, como los modelos."""
    out: list[tuple[str, bool]] = []
    if validez:
        out.append((validez, False))
    if intracom:
        out.append((intracom, True))
    banco = str(company.get("banco") or "").strip()
    iban = str(company.get("iban") or "").strip()
    bic = str(company.get("bic") or "").strip()
    if banco or iban or bic:
        out.append((lab["cuenta"], True))
        if banco:
            out.append((f"{lab['banco']}: {banco}", False))
        cuenta = " · ".join(
            x for x in (f"IBAN: {iban}" if iban else "",
                        f"BIC/Swift: {bic}" if bic else "") if x
        )
        if cuenta:
            out.append((cuenta, False))
    if legal:
        out.append((legal, False))
    if pie:
        out.append((pie, True))
    # Los textos legales largos se PARTEN en varias líneas — truncarlos
    # perdería información (la condición nº 1 del diseño).
    wrapped: list[tuple[str, bool]] = []
    for text, strong in out:
        font = FONT_BOLD if strong else FONT
        for part in simpleSplit(text, font, 7.6, 194 * mm) or [""]:
            wrapped.append((part, strong))
    return wrapped


def _draw_footer(cv: rl_canvas.Canvas, lines: list[tuple[str, bool]]) -> None:
    if not lines:
        return
    y = (6 + 4.2 * (len(lines) - 1)) * mm
    cv.setStrokeColor(RULE)
    cv.setLineWidth(0.4)
    cv.line(7 * mm, y + 4 * mm, PAGE_W - 7 * mm, y + 4 * mm)
    for text, strong in lines:
        cv.setFont(FONT_BOLD if strong else FONT, 7.6)
        cv.setFillColor(colors.black if strong else GREY)
        cv.drawString(8 * mm, y, text)
        y -= 4.2 * mm


def _lines_table(
    data: dict[str, Any], lab: dict[str, str], lang: str, *, valued: bool,
) -> LongTable:
    """Tabla de líneas con cabecera repetida en cada página (repeatRows).
    Descripciones MULTILÍNEA como Paragraph: fluyen, no se cortan. En
    facturas, fila separadora por albarán de origen (agrupación E3-B)."""
    style_head = _para_style(7.5, bold=True)
    style_cell = _para_style(8.4)
    style_group = _para_style(8, bold=True)

    if valued:
        headers = [lab["col_articulo"], lab["col_descripcion"],
                   lab["col_cantidad"], lab["col_precio"], lab["col_dto"],
                   lab["col_subtotal"], lab["col_total"]]
        widths = [23 * mm, 72 * mm, 21 * mm, 20 * mm, 14 * mm,
                  23 * mm, 23 * mm]
    else:
        headers = [lab["col_articulo"], lab["col_descripcion"],
                   lab["col_cantidad"]]
        widths = [30 * mm, 140 * mm, 26 * mm]

    rows: list[list[Any]] = [[Paragraph(h, style_head) for h in headers]]
    group_rows: list[int] = []
    current_group: str | None = None
    for line in data["lines"]:
        alb = line.get("albaran")
        if alb and alb["numero"] != current_group:
            current_group = alb["numero"]
            parts = [lab["albaran_de_linea"].format(numero=alb["numero"])]
            if alb.get("fecha"):
                parts.append(lab["albaran_fecha"].format(fecha=alb["fecha"]))
            if alb.get("ref"):
                parts.append(lab["albaran_ref"].format(ref=alb["ref"]))
            rows.append([Paragraph(" · ".join(parts), style_group)]
                        + [""] * (len(headers) - 1))
            group_rows.append(len(rows) - 1)
        cells: list[Any] = [
            Paragraph(_esc(line["codart"]), style_cell),
            Paragraph(_esc(line["descripcion"]), style_cell),
            _fmt_qty(line["cantidad"], lang),
        ]
        if valued:
            cells += [
                _fmt_money(line["precio"], lang),
                line["dto"] or "",
                _fmt_money(line["subtotal"], lang),
                _fmt_money(line["total"], lang),
            ]
        rows.append(cells)
    if len(rows) == 1:
        rows.append([Paragraph(lab["sin_lineas"], style_cell)]
                    + [""] * (len(headers) - 1))

    table = LongTable(rows, colWidths=widths, repeatRows=1)
    styles = [
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTSIZE", (0, 1), (-1, -1), 8.4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#dddddd")),
    ]
    for r in group_rows:
        styles += [
            ("SPAN", (0, r), (-1, r)),
            ("BACKGROUND", (0, r), (-1, r), colors.HexColor("#f7f7f7")),
            ("ALIGN", (0, r), (-1, r), "LEFT"),
        ]
    table.setStyle(TableStyle(styles))
    return table


def _summary_flowables(
    data: dict[str, Any], lab: dict[str, str], lang: str, *, valued: bool,
) -> list[Any]:
    """Observaciones + bandas de IVA (incl. exenta) + TOTAL + vencimiento."""
    out: list[Any] = []
    style = _para_style(8.6)

    if data["observaciones"]:
        # OB1 + OB2 bajo UNA sola etiqueta (como el modelo: dos líneas).
        obs = "<br/>".join(_esc(o) for o in data["observaciones"])
        out.append(Paragraph(
            f"<b>{lab['observaciones']}</b> {obs}", style,
        ))
    if not valued:
        return out

    bands = data["bands"]
    show_neto = any(abs(b["neto"]) > 0.004 for b in bands)
    show_dto = any(abs(b["dto"]) > 0.004 for b in bands)
    show_portes = any(abs(b["portes"]) > 0.004 for b in bands)
    show_fin = any(abs(b["fin"]) > 0.004 for b in bands)
    show_re = any(abs(b["rec"]) > 0.004 for b in bands)

    headers = [lab["band_tipo"]]
    for flag, key in ((show_neto, "band_neto"), (show_dto, "band_dto"),
                      (show_portes, "band_portes"), (show_fin, "band_fin")):
        if flag:
            headers.append(lab[key])
    headers += [lab["band_base"], lab["band_iva"]]
    if show_re:
        headers.append(lab["band_re"])

    band_rows: list[list[str]] = [headers]
    for b in bands:
        tipo = lab["band_exento"] if b["exenta"] else _fmt_qty(b["piva"], lang)
        row = [tipo]
        for flag, key in ((show_neto, "neto"), (show_dto, "dto"),
                          (show_portes, "portes"), (show_fin, "fin")):
            if flag:
                row.append(_fmt_money(b[key], lang))
        row += [_fmt_money(b["base"], lang), _fmt_money(b["iva"], lang)]
        if show_re:
            row.append(_fmt_money(b["rec"], lang))
        band_rows.append(row)
    if len(band_rows) == 1:
        band_rows.append(["—"] + [""] * (len(headers) - 1))

    col_w = [20 * mm] + [24 * mm] * (len(headers) - 1)
    bands_table = Table(band_rows, colWidths=col_w, hAlign="RIGHT")
    bands_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
        ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
        ("FONTNAME", (0, 1), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.25, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ]))
    out.append(Spacer(1, 3 * mm))
    out.append(bands_table)

    total_style = ParagraphStyle(
        name="total", fontName=FONT_BOLD, fontSize=13, leading=16,
        alignment=2,  # derecha
    )
    out.append(Spacer(1, 3 * mm))
    out.append(Paragraph(
        f"{lab['total']} {_fmt_money(data['total'], lang)} €", total_style,
    ))
    if data["vencimiento"]:
        out.append(Paragraph(
            f"<b>{lab['vencimiento']}</b> {data['vencimiento']}",
            ParagraphStyle(name="ven", fontName=FONT, fontSize=8.6,
                           leading=11, alignment=2),
        ))
    return out


# --- nombre de fichero ------------------------------------------------------


def pdf_filename(doc_type: str, data: dict[str, Any], lang: str) -> str:
    """`Factura_5-260066_LABORATORIOS_PORTA.pdf` — legible y sin sorpresas
    de encoding en cabeceras HTTP (ASCII, sin espacios)."""
    doc_label = labels_for(lang)[f"doc_{doc_type}"].replace(" ", "-")
    cliente = data["cliente"]["nombre"] or data["cliente"]["codigo"] or ""
    cliente = unicodedata.normalize("NFKD", cliente)
    cliente = cliente.encode("ascii", "ignore").decode("ascii")
    cliente = re.sub(r"[^A-Za-z0-9]+", "_", cliente).strip("_").upper()[:40]
    parts = [doc_label, data["numero"]] + ([cliente] if cliente else [])
    return "_".join(parts) + ".pdf"
