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

from app.erp.language import (
    SUPPORTED_LANGS,
    language_for_country,
)
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
# El mapa país→idioma y la lista de soportados viven en `app.erp.language`
# (módulo ligero compartido con el mapper de Woo — E4-fix2).

#: E4-fix1 Parte B — divisas mostrables. DISCOVERY: en el volcado VIVO de
#: F_FAC (167 columnas) NO existe columna de código de divisa; la única
#: candidata es `CAMFAC` (tipo de CAMbio), y los modelos «NO EURO» usan el
#: concepto legacy «Contramoneda» (campos *PS*/PTS* calculados al imprimir).
#: Por tanto la divisa la ELIGE el operador al descargar; los importes se
#: muestran tal cual (convertir sería alterar la contabilidad) y CAMFAC,
#: si viene relleno, se imprime como referencia.
CURRENCIES: dict[str, dict[str, str]] = {
    "EUR": {"symbol": "€", "style": ""},
    "SEK": {"symbol": "kr", "style": "sv"},
    "DKK": {"symbol": "kr", "style": "sv"},
    "NOK": {"symbol": "kr", "style": "sv"},
    "USD": {"symbol": "$", "style": "en"},
    "GBP": {"symbol": "£", "style": "en"},
    "CHF": {"symbol": "CHF", "style": "en"},
}

#: Variantes de impresión por tipo de documento (E4-fix1). Todas son formas
#: de IMPRIMIR un documento existente — nunca crean nada en FACTUSOL.
VARIANTS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "facturas": ("anticipo",),
    "presupuestos": ("proforma",),
    "albaranes": ("valorado", "devolucion"),
    "pedidos": (),
}

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
        "band_base_unica": "Base imponible",
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
        "charge_portes": "Portes",
        "charge_financiacion": "Gastos de financiación",
        "charge_iva": "IVA {piva}%",
        "title_facturas_anticipo": "FACTURA DE ANTICIPO",
        "title_presupuestos_proforma": "FACTURA PROFORMA",
        "title_albaranes_devolucion": "ALBARÁN DE DEVOLUCIÓN",
        "direccion_recogida": "DIRECCIÓN DE RECOGIDA:",
        "divisa_nota": "Importes en {code} — sin conversión",
        "cambio_label": "Tipo de cambio: {rate}",
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
        "band_base_unica": "Taxable amount",
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
        "charge_portes": "Shipping",
        "charge_financiacion": "Financing charges",
        "charge_iva": "VAT {piva}%",
        "title_facturas_anticipo": "ADVANCE PAYMENT INVOICE",
        "title_presupuestos_proforma": "PROFORMA INVOICE",
        "title_albaranes_devolucion": "TRANSPORT DOC for return of goods",
        "direccion_recogida": "Consignee:",
        "divisa_nota": "Amounts in {code} — no conversion applied",
        "cambio_label": "Exchange rate: {rate}",
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
        "band_base_unica": "Bemessungsgrundlage",
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
        "charge_portes": "Versandkosten",
        "charge_financiacion": "Finanzierungskosten",
        "charge_iva": "MwSt. {piva}%",
        "title_facturas_anticipo": "ANZAHLUNGSRECHNUNG",
        "title_presupuestos_proforma": "PROFORMARECHNUNG",
        "title_albaranes_devolucion": "RÜCKLIEFERSCHEIN",
        "direccion_recogida": "ABHOLADRESSE:",
        "divisa_nota": "Beträge in {code} — ohne Umrechnung",
        "cambio_label": "Wechselkurs: {rate}",
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
        "band_base_unica": "Base imposable",
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
        "charge_portes": "Frais de port",
        "charge_financiacion": "Frais de financement",
        "charge_iva": "TVA {piva}%",
        "title_facturas_anticipo": "FACTURE D'ACOMPTE",
        "title_presupuestos_proforma": "FACTURE PROFORMA",
        "title_albaranes_devolucion": "BON DE RETOUR",
        "direccion_recogida": "ADRESSE D'ENLÈVEMENT :",
        "divisa_nota": "Montants en {code} — sans conversion",
        "cambio_label": "Taux de change : {rate}",
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
        "band_base_unica": "Belastbaar bedrag",
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
        "charge_portes": "Verzendkosten",
        "charge_financiacion": "Financieringskosten",
        "charge_iva": "btw {piva}%",
        "title_facturas_anticipo": "VOORSCHOTFACTUUR",
        "title_presupuestos_proforma": "PROFORMAFACTUUR",
        "title_albaranes_devolucion": "RETOURBON",
        "direccion_recogida": "OPHAALADRES:",
        "divisa_nota": "Bedragen in {code} — zonder omrekening",
        "cambio_label": "Wisselkoers: {rate}",
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
        "idioma_defecto": "es",
        # E4-fix1: el banco NO es fijo por empresa — Bomedia y Streamtec
        # emiten con Sabadell o con Open Bank según el documento. Lista de
        # cuentas; una por defecto; el operador elige al descargar.
        "bancos": [
            {"nombre": "Banco de Sabadell, S.A.",
             "domicilio": "Avda. Óscar Esplá, 37, 03007 Alicante",
             "iban": "ES33 0081 0202 13 0001171918", "bic": "BSABESBB",
             "defecto": True},
            {"nombre": "Open Bank, S.A.",
             "domicilio": "Plaza Manuel Gómez Moreno, 2, 28020 Madrid",
             "iban": "ES65 0073 0100 5704 3930 5449", "bic": "OPENESMM",
             "defecto": False},
        ],
        "legal": {
            "es": "El material suministrado es propiedad de BOMEDIA S.L. "
                  "hasta recibir la totalidad del pago correspondiente.",
        },
        "pie": {"es": "CONDICIONES GENERALES EN WWW.BOMEDIA.NET"},
        "intracom": {},
        # E4-fix1 Parte C: la variante VALORADA del albarán de Bomedia se
        # titula «ALBARÁN DE ENTREGA» (modelo A-115). Configurable.
        "titulo_albaran_valorado": {"es": "ALBARÁN DE ENTREGA"},
    },
    5: {
        "nombre": "Streamtec SL",
        "direccion": "C. Corsega 232, 5",
        "cp_poblacion": "08036 Barcelona",
        "pais": "España",
        "telefono": "Tel. 932022530",
        "email": "",
        "nif": "CIF B64154263",
        "idioma_defecto": "es",
        "bancos": [
            {"nombre": "Banco de Sabadell, S.A.",
             "domicilio": "Avda. Óscar Esplá, 37, 03007 Alicante",
             "iban": "ES11 0081 0202 1700 0125 9030", "bic": "BSABESBB",
             "defecto": True},
            {"nombre": "Open Bank, S.A.",
             "domicilio": "Plaza de Santa Bárbara 2, 28004 Madrid",
             "iban": "ES23 0073 0100 5404 4814 5865", "bic": "OPENESMM",
             "defecto": False},
        ],
        "legal": {},
        "pie": {},
        "intracom": {
            "es": "Entrega intracomunitaria, o exportación exenta de IVA",
        },
        "titulo_albaran_valorado": {},
    },
    2: {
        "nombre": "MQ Europe BV",
        "direccion": "Arnould Nobelstraat 30, 0405",
        "cp_poblacion": "B3000 Leuven",
        "pais": "Belgium",
        "telefono": "",
        "email": "sales@mqeurope.com",
        "nif": "VAT nr. BE 0883.002.183 (RPR TONGEREN)",
        "idioma_defecto": "en",
        "bancos": [
            {"nombre": "Belfius Bank",
             "domicilio": "Zaventem, Belgium",
             "iban": "BE28 0682 4531 2320", "bic": "GKCCBEBB",
             "defecto": True},
        ],
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
        "titulo_albaran_valorado": {},
    },
}

#: Campos de texto plano de la empresa (los editables simples de settings).
COMPANY_TEXT_FIELDS = (
    "nombre", "direccion", "cp_poblacion", "pais", "telefono", "email",
    "nif", "idioma_defecto",
)
#: Campos por-idioma (dict {lang: texto}).
COMPANY_LANG_FIELDS = ("legal", "pie", "intracom", "titulo_albaran_valorado")


def bank_accounts(company: dict[str, Any]) -> list[dict[str, Any]]:
    """Cuentas bancarias de la empresa (lista saneada, nunca None)."""
    bancos = company.get("bancos")
    if not isinstance(bancos, list):
        return []
    out = []
    for b in bancos:
        if isinstance(b, dict) and any(
            str(b.get(k) or "").strip() for k in ("nombre", "iban", "bic")
        ):
            out.append({
                "nombre": str(b.get("nombre") or "").strip(),
                "domicilio": str(b.get("domicilio") or "").strip(),
                "iban": str(b.get("iban") or "").strip(),
                "bic": str(b.get("bic") or "").strip(),
                "defecto": bool(b.get("defecto")),
            })
    return out


def default_bank(company: dict[str, Any]) -> dict[str, Any] | None:
    accounts = bank_accounts(company)
    for account in accounts:
        if account["defecto"]:
            return account
    return accounts[0] if accounts else None


#: E4-fix1 Parte D — almacenes de recogida para el albarán de devolución.
#: Valor inicial extraído del modelo A-321; lista configurable en
#: /erp/settings (`factusol_series_json.pickup_warehouses`).
DEFAULT_PICKUP_WAREHOUSES: list[dict[str, str]] = [
    {
        "nombre": "Almacén TERLO 2000",
        "direccion": "C. Motors 12 – P.I. Comte de Sert\n"
                     "08755 Castellbisbal (Barcelona), Spain\n"
                     "Tel. 93 775 90 62 · Sr. Toni",
    },
]


def pickup_warehouses_config(stored: Any) -> list[dict[str, str]]:
    """Almacenes configurados (o el default del modelo si no hay ninguno)."""
    if not isinstance(stored, list):
        return [dict(w) for w in DEFAULT_PICKUP_WAREHOUSES]
    out = []
    for w in stored:
        if isinstance(w, dict) and str(w.get("direccion") or "").strip():
            out.append({
                "nombre": str(w.get("nombre") or "").strip(),
                "direccion": str(w.get("direccion") or "").strip(),
            })
    return out or [dict(w) for w in DEFAULT_PICKUP_WAREHOUSES]


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
        merged["bancos"] = [dict(b) for b in base.get("bancos", []) or []]
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
            if isinstance(override.get("bancos"), list):
                # La lista guardada REEMPLAZA a la de defaults (es la
                # edición completa de Bart, no un parche por posición).
                merged["bancos"] = [
                    dict(b) for b in override["bancos"] if isinstance(b, dict)
                ]
            elif any(override.get(k) for k in ("banco", "iban", "bic")):
                # Compat E4: config guardada con el banco único de la
                # primera versión → se pliega como única cuenta.
                merged["bancos"] = [{
                    "nombre": str(override.get("banco") or ""),
                    "domicilio": "",
                    "iban": str(override.get("iban") or ""),
                    "bic": str(override.get("bic") or ""),
                    "defecto": True,
                }]
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
    } | {"bancos": []}


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


def _fmt_money(v: float, lang: str, currency: str = "EUR") -> str:
    """Formato del importe según divisa e idioma. Los importes NUNCA se
    convierten — solo cambia la presentación (es/de/fr/nl 1.234,56;
    en 1,234.56; estilo nórdico 1 234,56 para las coronas)."""
    style = CURRENCIES.get(currency, {}).get("style", "")
    s = f"{v:,.2f}"
    if style == "en" or (not style and lang == "en"):
        return s
    if style == "sv":
        return s.replace(",", "\u00a0").replace(".", ",")
    return s.replace(",", "\u00a0").replace(".", ",").replace("\u00a0", ".")


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

    # ERP-F1 Parte 1 — cargos de cabecera (portes, financiación) que se
    # pintan como LÍNEA sintética del documento en vez de en el bloque de
    # totales. SOLO PRESENTACIÓN: ya están DENTRO de la base imponible que
    # calcula FACTUSOL (no se recomputa el total). Se agrupan por tipo y por
    # banda de IVA, para que la línea lleve el % que le corresponde.
    charges: list[dict[str, Any]] = []
    for kind, band_key in (("portes", "portes"), ("financiacion", "fin")):
        by_piva: dict[float, float] = {}
        for band in bands:
            amount = band[band_key]
            if abs(amount) > 0.004:
                piva = 0.0 if band["exenta"] else band["piva"]
                by_piva[piva] = by_piva.get(piva, 0.0) + amount
        for piva, amount in by_piva.items():
            charges.append({"kind": kind, "piva": piva, "amount": amount})

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
        "charges": charges,
        "total": _num(h("TOT"), 0.0),
        # E4-fix1: tipo de cambio del documento (CAMFAC — única columna
        # ligada a divisa que existe en el volcado vivo de F_FAC).
        # 0/1/vacío = sin cambio que enseñar.
        "cambio": _num(h("CAM"), 0.0),
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
    variant: str | None = None,
    bank: dict[str, Any] | None = None,
    currency: str = "EUR",
    warehouse: dict[str, Any] | None = None,
) -> bytes:
    """Estructura neutra + empresa + idioma → bytes del PDF A4.

    `valued=None` aplica el criterio de los modelos: factura, presupuesto y
    pedido con importes; albarán SIN importes.

    E4-fix1: `variant` imprime el MISMO documento de otra forma (nunca crea
    nada en FACTUSOL): `anticipo` (factura de pago a cuenta), `proforma`
    (presupuesto titulado FACTURA PROFORMA), `valorado` (albarán con
    importes) y `devolucion` (albarán de retorno, con la dirección de
    recogida del `warehouse`). `bank` es la cuenta elegida (default: la
    marcada por defecto en la empresa). `currency` solo cambia la
    presentación — los importes van tal cual están en el documento."""
    doc_type = data["doc_type"]
    lab = labels_for(lang)
    if variant is not None and variant not in VARIANTS_BY_TYPE.get(doc_type, ()):
        raise ValueError(f"Variante {variant!r} no aplica a {doc_type}")
    if valued is None:
        valued = doc_type != "albaranes" or variant == "valorado"
    if variant == "valorado":
        valued = True
    elif variant == "devolucion":
        valued = False
    title = _title_for(doc_type, variant, lab, company, lang)
    doc_label = lab[f"doc_{doc_type}"]
    if bank is None:
        bank = default_bank(company)

    # ¿Aplica el texto intracomunitario? Criterio de los modelos «SIN IVA»:
    # documento valorado sin una sola banda con IVA.
    total_iva = sum(b["iva"] for b in data["bands"])
    intracom = (
        _lang_text(company, "intracom", lang)
        if valued and abs(total_iva) < 0.005 else ""
    )
    legal = _lang_text(company, "legal", lang)
    pie = _lang_text(company, "pie", lang)

    footer_lines = _footer_lines(bank, lab, legal, intracom, pie,
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
        _draw_header(cv, data, company, lab, title, doc_label, logo,
                     variant=variant, warehouse=warehouse)
        _draw_footer(cv, footer_lines)

    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        title=f"{doc_label} {data['numero']}",
        author=company.get("nombre") or "BoHub",
    )
    doc.addPageTemplates([PageTemplate(id="doc", frames=[frame], onPage=on_page)])

    _NumberedCanvas._pagina_de = lab["pagina_de"]

    story: list[Any] = [
        _lines_table(data, lab, lang, valued=valued, currency=currency),
    ]
    story.append(Spacer(1, 4 * mm))
    story.extend(_summary_flowables(
        data, lab, lang, valued=valued, currency=currency,
    ))
    if variant == "devolucion":
        # «Barcelona a ___ de ___ de ___» del modelo A-321 — rellenada con
        # la fecha del documento, nunca en blanco.
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph(
            _esc(_fecha_en_texto(company, data["fecha"], lang)),
            _para_style(9),
        ))
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
    *,
    variant: str | None = None,
    warehouse: dict[str, Any] | None = None,
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

    # Bloque de cliente (izquierda). En el albarán de DEVOLUCIÓN (A-321)
    # se apilan DOS direcciones: la de RECOGIDA (almacén configurado) y la
    # de ENTREGA (el cliente), en compacto para caber en la cabecera.
    cli = data["cliente"]
    cli_lines = [
        cli["nombre"],
        cli["domicilio"],
        " ".join(x for x in (cli["cp"], cli["poblacion"]) if x),
        " ".join(x for x in (cli["provincia"], cli["pais"]) if x),
        cli["telefono"],
    ]
    cy = top - 32 * mm
    if variant == "devolucion":
        recogida = [
            *(str((warehouse or {}).get("nombre") or "").splitlines()),
            *(str((warehouse or {}).get("direccion") or "").splitlines()),
        ]
        cy = _draw_address_block(
            cv, cy, lab["direccion_recogida"], recogida, size=7.6,
        )
        cy -= 2 * mm
        _draw_address_block(
            cv, cy, lab["direccion_entrega"], cli_lines, size=7.6,
        )
    else:
        cv.setFont(FONT_BOLD, 8)
        cv.setFillColor(GREY)
        cv.drawString(10 * mm, cy - 2 * mm, lab["cliente"])
        cv.setFillColor(colors.black)
        cy -= 7 * mm
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


def _draw_address_block(
    cv: rl_canvas.Canvas, y: float, label: str, lines: list[str], *,
    size: float,
) -> float:
    """Etiqueta gris + líneas compactas. Devuelve la Y tras el bloque."""
    cv.setFont(FONT_BOLD, 7)
    cv.setFillColor(GREY)
    cv.drawString(10 * mm, y, label)
    cv.setFillColor(colors.black)
    y -= (size + 1) * 0.42 * mm * 1.2
    for text in lines:
        text = str(text or "").strip()
        if not text:
            continue
        cv.setFont(FONT, size)
        cv.drawString(10 * mm, y, _fit(text, size, 92))
        y -= (size + 1.2) * 0.42 * mm
    return y


_MESES_ES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def _fecha_en_texto(company: dict[str, Any], fecha: str, lang: str) -> str:
    """«Barcelona, a 26 de agosto de 2026» (modelo A-321) — la población de
    la empresa emisora + la fecha del documento, nunca en blanco."""
    poblacion = re.sub(
        r"^[0-9\s-]+", "", str(company.get("cp_poblacion") or ""),
    ).strip() or "Barcelona"
    try:
        d, m, y = fecha.split("-")
        if lang == "es":
            return f"{poblacion}, a {int(d)} de {_MESES_ES[int(m) - 1]} de {y}"
        return f"{poblacion}, {fecha}"
    except (ValueError, IndexError):
        return f"{poblacion}, {fecha}"


def _title_for(
    doc_type: str, variant: str | None, lab: dict[str, str],
    company: dict[str, Any], lang: str,
) -> str:
    """Título del documento según variante. El del albarán VALORADO es
    configurable por empresa (Bomedia: «ALBARÁN DE ENTREGA», modelo A-115);
    sin configurar cae al título normal del tipo."""
    if variant == "anticipo":
        return lab["title_facturas_anticipo"]
    if variant == "proforma":
        return lab["title_presupuestos_proforma"]
    if variant == "devolucion":
        return lab["title_albaranes_devolucion"]
    if variant == "valorado":
        return (
            _lang_text(company, "titulo_albaran_valorado", lang)
            or lab["title_albaranes"]
        )
    return lab[f"title_{doc_type}"]


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
    bank: dict[str, Any] | None,
    lab: dict[str, str],
    legal: str,
    intracom: str,
    pie: str,
    *,
    validez: str,
) -> list[tuple[str, bool]]:
    """(texto, destacado) del pie fijo: validez del presupuesto, texto
    intracomunitario, la CUENTA BANCARIA elegida (E4-fix1: el banco ya no
    es fijo por empresa), reserva de dominio y pie de condiciones — en
    TODAS las páginas, como los modelos."""
    out: list[tuple[str, bool]] = []
    if validez:
        out.append((validez, False))
    if intracom:
        out.append((intracom, True))
    bank = bank or {}
    banco = " ".join(x for x in (
        str(bank.get("nombre") or "").strip(),
        str(bank.get("domicilio") or "").strip(),
    ) if x).strip(", ")
    iban = str(bank.get("iban") or "").strip()
    bic = str(bank.get("bic") or "").strip()
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


#: ERP-F1-fix1 Parte D — abreviaturas de cabecera para las palabras largas que
#: no caben en su columna ni al cuerpo mínimo (alemán y neerlandés, sobre todo).
#: Reconocibles en su idioma, en mayúsculas como el resto de la cabecera. Se
#: buscan por el texto EXACTO de la etiqueta. Preferimos abreviar antes que
#: partir a media palabra («STÜCKPR.» en vez de «STÜCKPRE / IS»).
_HEADER_ABBR: dict[str, str] = {
    "STÜCKPREIS": "STÜCKPR.",       # de — precio unidad
    "ZWISCHENSUMME": "ZW.-SUMME",   # de — subtotal
    "STUKSPRIJS": "STUKSPR.",       # nl — precio unidad
    "OMSCHRIJVING": "OMSCHR.",      # nl — descripción (columna ancha, respaldo)
    "BESCHREIBUNG": "BESCHR.",      # de — descripción (columna ancha, respaldo)
    "DESCRIPTION": "DESCR.",        # en/fr — respaldo
    "DESCRIPCIÓN": "DESCR.",        # es — respaldo
    "QUANTITÉ": "QTÉ",              # fr
    "DISCOUNT %": "DISC. %",        # en
    "CANTIDAD": "CANT.",            # es — respaldo
}

#: Padding L/R real de las celdas de la tabla de líneas (reportlab por defecto
#: pone 6 pt a cada lado y no lo tocamos): el ancho útil de una columna es su
#: anchura menos estos 12 pt. El ajustador de cabecera mide contra ese hueco.
_CELL_PAD_LR = 6.0
#: Margen de seguridad (pt) para no fiarlo todo al último punto: si la palabra
#: llega justa al borde, reportlab podría partirla igualmente por redondeo.
_HEADER_SAFETY = 1.5


def _header_paragraph(text: str, col_width: float) -> Paragraph:
    """Cabecera de columna que NUNCA se parte a media palabra. reportlab solo
    rompe en los espacios, así que basta con que la PALABRA más larga quepa en
    el hueco útil: se reduce el cuerpo hasta un mínimo y, si aún no cabe, se usa
    una abreviatura del idioma. Verificado en los 5 idiomas por el test."""
    usable = col_width - 2 * _CELL_PAD_LR - _HEADER_SAFETY

    def longest_token(t: str, size: float) -> float:
        # reportlab parte solo en los espacios (no en guiones): cada token es
        # una unidad indivisible que debe caber entera.
        return max(
            (pdfmetrics.stringWidth(tok, FONT_BOLD, size) for tok in t.split()
             if tok),
            default=0.0,
        )

    for candidate in (text, _HEADER_ABBR.get(text, text)):
        for size in (7.5, 7.0, 6.5, 6.0):
            if longest_token(candidate, size) <= usable:
                return Paragraph(candidate, _para_style(size, bold=True))
    # Último recurso (no debería alcanzarse con las anchuras actuales): la
    # abreviatura al cuerpo mínimo. Mejor apretada que partida.
    return Paragraph(_HEADER_ABBR.get(text, text), _para_style(6.0, bold=True))


def _lines_table(
    data: dict[str, Any], lab: dict[str, str], lang: str, *, valued: bool,
    currency: str = "EUR",
) -> LongTable:
    """Tabla de líneas con cabecera repetida en cada página (repeatRows).
    Descripciones MULTILÍNEA como Paragraph: fluyen, no se cortan. En
    facturas, fila separadora por albarán de origen (agrupación E3-B)."""
    style_cell = _para_style(8.4)
    style_group = _para_style(8, bold=True)

    if valued:
        headers = [lab["col_articulo"], lab["col_descripcion"],
                   lab["col_cantidad"], lab["col_precio"], lab["col_dto"],
                   lab["col_subtotal"], lab["col_total"]]
        # ERP-F1-fix1 Parte D — se ensancha un pelín las columnas numéricas
        # (a costa de la descripción, muy holgada) para que las palabras
        # largas del alemán/neerlandés quepan sin partirse ni encoger tanto.
        widths = [23 * mm, 64 * mm, 21 * mm, 24 * mm, 16 * mm,
                  26 * mm, 22 * mm]
    else:
        headers = [lab["col_articulo"], lab["col_descripcion"],
                   lab["col_cantidad"]]
        widths = [30 * mm, 140 * mm, 26 * mm]

    rows: list[list[Any]] = [
        [_header_paragraph(h, w) for h, w in zip(headers, widths)]
    ]
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
                _fmt_money(line["precio"], lang, currency),
                line["dto"] or "",
                _fmt_money(line["subtotal"], lang, currency),
                _fmt_money(line["total"], lang, currency),
            ]
        rows.append(cells)

    # ERP-F1 Parte 1 — cargos de cabecera (portes/financiación) como LÍNEA,
    # tras los artículos reales. Sin código de artículo y en cursiva/gris
    # para que se vean como cargo, no como mercancía. Solo en documentos
    # valorados (el albarán sin importes no los muestra).
    charge_rows: list[int] = []
    if valued:
        style_charge = _para_style(8.4)
        for charge in data.get("charges", []):
            label = lab[f"charge_{charge['kind']}"]
            if charge["piva"]:
                label += f" · {lab['charge_iva'].format(piva=_fmt_qty(charge['piva'], lang))}"
            monto = _fmt_money(charge["amount"], lang, currency)
            rows.append([
                Paragraph("", style_charge),
                Paragraph(_esc(label), style_charge),
                "", "", "", monto, monto,
            ])
            charge_rows.append(len(rows) - 1)

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
    for r in charge_rows:
        # Cargo: texto en gris para distinguirlo de la mercancía.
        styles.append(("TEXTCOLOR", (0, r), (-1, r), GREY))
    if charge_rows:
        # Regla sutil encima del primer cargo, separándolos de los artículos.
        styles.append(
            ("LINEABOVE", (0, charge_rows[0]), (-1, charge_rows[0]),
             0.4, RULE),
        )
    table.setStyle(TableStyle(styles))
    return table


def _summary_flowables(
    data: dict[str, Any], lab: dict[str, str], lang: str, *, valued: bool,
    currency: str = "EUR",
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
    # ERP-F1-fix1 — criterio «sin IVA»: la SUMA de los importes de IVA es
    # cero (intracomunitarias, exentas, exportaciones). No basta mirar el
    # porcentaje: el caso real tiene banda al 21 % con importe 0.
    total_iva = sum(b["iva"] for b in bands)
    no_vat = abs(total_iva) < 0.005

    out.append(Spacer(1, 3 * mm))
    if no_vat:
        # ERP-F1-fix1 Parte B — documentos sin IVA: NO se desglosa por bandas
        # (un «21 %» con IVA 0,00 confunde). Una sola base imponible = suma de
        # las bases = total (no hay IVA que añadir). El texto legal que lo
        # justifica (entrega intracomunitaria, art. 25ter…) se imprime aparte
        # en el pie, no aquí.
        base_total = sum(b["base"] for b in bands)
        base_style = ParagraphStyle(
            name="base_unica", fontName=FONT, fontSize=9.5, leading=13,
            alignment=2,
        )
        out.append(Paragraph(
            f"{lab['band_base_unica']}&nbsp;&nbsp;&nbsp;"
            f"{_fmt_money(base_total, lang, currency)}",
            base_style,
        ))
    else:
        # ERP-F1-fix1 Parte A — fuera la columna NETO (BASE y NETO confunden y
        # en la práctica coinciden): el bloque queda con % de IVA, base, IVA y
        # R.E. (esta solo si algún valor ≠ 0). El descuento se conserva como
        # columna solo cuando existe (dato distinto de la base).
        show_dto = any(abs(b["dto"]) > 0.004 for b in bands)
        show_re = any(abs(b["rec"]) > 0.004 for b in bands)
        # ERP-F1-fix1 Parte C — se ocultan las bandas cuya base e importe de
        # IVA son ambos cero (ruido). Si tras el filtro queda una sola banda,
        # se imprime igualmente como tabla (no se colapsa a la vista de la
        # Parte B, exclusiva de los documentos sin IVA).
        visible = [
            b for b in bands
            if abs(b["base"]) > 0.004 or abs(b["iva"]) > 0.004
        ]
        if not visible:  # red de seguridad: nunca dejar el bloque vacío
            visible = bands[:1]

        headers = [lab["band_tipo"]]
        if show_dto:
            headers.append(lab["band_dto"])
        headers += [lab["band_base"], lab["band_iva"]]
        if show_re:
            headers.append(lab["band_re"])

        band_rows: list[list[str]] = [headers]
        for b in visible:
            tipo = (lab["band_exento"] if b["exenta"]
                    else _fmt_qty(b["piva"], lang))
            row = [tipo]
            if show_dto:
                row.append(_fmt_money(b["dto"], lang, currency))
            row += [_fmt_money(b["base"], lang, currency),
                    _fmt_money(b["iva"], lang, currency)]
            if show_re:
                row.append(_fmt_money(b["rec"], lang, currency))
            band_rows.append(row)

        # Parte A — anchos: repartir el espacio liberado por la columna NETO
        # para que las cifras respiren (la primera columna, el %, más estrecha).
        col_w = [20 * mm] + [28 * mm] * (len(headers) - 1)
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
        out.append(bands_table)

    total_style = ParagraphStyle(
        name="total", fontName=FONT_BOLD, fontSize=13, leading=16,
        alignment=2,  # derecha
    )
    symbol = CURRENCIES.get(currency, {}).get("symbol", currency)
    out.append(Spacer(1, 3 * mm))
    out.append(Paragraph(
        f"{lab['total']} {_fmt_money(data['total'], lang, currency)} "
        f"{symbol}",
        total_style,
    ))
    nota_style = ParagraphStyle(
        name="nota", fontName=FONT, fontSize=8.2, leading=10.5, alignment=2,
    )
    if currency != "EUR":
        # Los importes van TAL CUAL están en el documento — convertirlos
        # sería alterar la contabilidad. Se hace explícito en el PDF.
        out.append(Paragraph(
            _esc(lab["divisa_nota"].format(code=currency)), nota_style,
        ))
    cambio = data.get("cambio") or 0.0
    if cambio and abs(cambio - 1.0) > 0.0001:
        # CAMFAC (tipo de cambio) — solo como referencia.
        out.append(Paragraph(
            _esc(lab["cambio_label"].format(
                rate=_fmt_qty(cambio, lang),
            )),
            nota_style,
        ))
    if data["vencimiento"]:
        out.append(Paragraph(
            f"<b>{lab['vencimiento']}</b> {data['vencimiento']}",
            ParagraphStyle(name="ven", fontName=FONT, fontSize=8.6,
                           leading=11, alignment=2),
        ))
    return out


# --- nombre de fichero ------------------------------------------------------


def pdf_filename(
    doc_type: str, data: dict[str, Any], lang: str,
    variant: str | None = None,
) -> str:
    """`Factura_5-260066_LABORATORIOS_PORTA.pdf` — legible y sin sorpresas
    de encoding en cabeceras HTTP (ASCII, sin espacios). Las variantes
    llevan su título («Factura-de-anticipo_…»)."""
    lab = labels_for(lang)
    if variant in ("anticipo", "proforma", "devolucion"):
        key = {"anticipo": "title_facturas_anticipo",
               "proforma": "title_presupuestos_proforma",
               "devolucion": "title_albaranes_devolucion"}[variant]
        doc_label = lab[key].capitalize().replace(" ", "-")
    else:
        doc_label = lab[f"doc_{doc_type}"].replace(" ", "-")
    cliente = data["cliente"]["nombre"] or data["cliente"]["codigo"] or ""
    cliente = unicodedata.normalize("NFKD", cliente)
    cliente = cliente.encode("ascii", "ignore").decode("ascii")
    cliente = re.sub(r"[^A-Za-z0-9]+", "_", cliente).strip("_").upper()[:40]
    parts = [doc_label, data["numero"]] + ([cliente] if cliente else [])
    return "_".join(parts) + ".pdf"


# --- cascada de idioma (E4-fix1 Parte I) ------------------------------------
#
# El idioma es un DATO que el sistema conoce y arrastra, no una decisión
# manual en cada descarga. Prioridad (de mayor a menor):
#   1. selector de la descarga (lo aplica la UI — aquí no llega)
#   2. idioma guardado en el pedido del CRM ligado al documento
#   3. idioma del cliente (ficha de empresa CRM, vía su CODCLI vinculado)
#   4. idioma por defecto de la empresa EMISORA (serie)
#   5. español
#
# La «herencia por la cadena» (pedido → albarán → factura) no necesita
# escribir nada en FACTUSOL: las conversiones de E3-B copian la referencia
# común (REF*) por sufijo, así que cualquier documento de la cadena resuelve
# a su pedido del CRM por esa referencia (y las facturas, además, por el
# CODFAC vinculado al emitir).


def _find_order_for_document(
    session: Session, doc_type: str, doc: dict[str, Any],
):
    """Pedido del CRM ligado a un documento FACTUSOL: por el CODFAC
    vinculado (facturas emitidas desde BoHub) o por la referencia común
    `REF*` que comparte toda la cadena. None si no se puede ligar."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.erp.models import Order  # noqa: PLC0415
    from app.integrations.factusol.service import (  # noqa: PLC0415
        _compose_ref,
        _store_ref_prefix,
    )

    if doc_type == "facturas" and doc.get("codigo") is not None:
        order = session.scalar(select(Order).where(
            Order.factusol_invoice_number == str(doc["codigo"]),
        ))
        if order is not None:
            return order
    ref = str(doc.get("referencia") or "").strip()
    parts = ref.split("-")
    tail = parts[-1].lstrip("0") if parts else ""
    if not ref or not tail.isdigit():
        return None
    candidates = session.scalars(
        select(Order).where(Order.order_number.like(f"%{tail}")).limit(25)
    ).all()
    for order in candidates:
        try:
            composed = _compose_ref(
                order.order_number, _store_ref_prefix(session, order),
            )
        except Exception:  # noqa: BLE001 — un candidato raro no rompe nada
            continue
        if composed == ref:
            return order
    return None


def _find_company_by_codcli(session: Session, codcli: Any):
    """Empresa del CRM vinculada al cliente FACTUSOL (CODCLI, enlace C-3)."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.crm import Company  # noqa: PLC0415

    code = str(codcli or "").strip()
    if not code:
        return None
    return session.scalar(select(Company).where(
        Company.factusol_company_id == code,
    ))


def suggest_pdf_language(
    session: Session, doc_type: str, doc: dict[str, Any],
) -> dict[str, str]:
    """`{lang, source}` para preseleccionar el selector de idioma de la
    descarga. `source` dice de dónde sale la propuesta para que el operador
    distinga un dato confirmado de una deducción:
      - `pedido`      idioma guardado en el pedido del CRM
      - `cliente`     idioma explícito en la ficha de la empresa cliente
      - `pais_cliente` derivado del PAÍS de la empresa cliente (E4-fix2)
      - `empresa`     idioma por defecto de la empresa emisora (serie)
      - `defecto`     español (último recurso)"""
    order = _find_order_for_document(session, doc_type, doc)
    if order is not None and (order.language or "") in SUPPORTED_LANGS:
        return {"lang": order.language, "source": "pedido"}
    company = _find_company_by_codcli(session, doc.get("cliente_codigo"))
    if company is not None:
        # 3. idioma EXPLÍCITO del cliente (alguien lo confirmó).
        if (company.language or "") in SUPPORTED_LANGS:
            return {"lang": company.language, "source": "cliente"}
        # 4. derivado del PAÍS del cliente (E4-fix2): mejor un idioma
        # razonable que caer a la empresa emisora. Es deducción — la UI lo
        # marca «del país del cliente».
        derived = language_for_country(company.country)
        if derived in SUPPORTED_LANGS:
            return {"lang": derived, "source": "pais_cliente"}
    serie = doc.get("serie")
    if serie is not None:
        emisora = company_for_serie(session, int(serie))
        idioma = str(emisora.get("idioma_defecto") or "").strip().lower()
        if idioma in SUPPORTED_LANGS:
            return {"lang": idioma, "source": "empresa"}
    return {"lang": "es", "source": "defecto"}
