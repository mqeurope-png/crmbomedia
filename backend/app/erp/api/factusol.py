"""BoHub ERP — API auxiliar de FACTUSOL (Fase C · C-2-fix2).

Catálogos de solo lectura que alimentan el modal de emisión de factura (formas
de pago). Se cachean en proceso unos minutos para no re-autenticar en DELSOL en
cada apertura del modal. Las escrituras NO viven aquí (van por la cola
serializada `factusol:writes`).
"""
from __future__ import annotations

import io
import json
import logging
import time
import zipfile
from typing import Any

from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.erp.api.deps import (
    require_erp_admin,
    require_erp_edit,
    require_erp_view,
)
from app.erp.factusol_albaran import PaymentIn
from app.models.crm import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/erp/factusol", tags=["erp-factusol"])

#: Cache en proceso {ejercicio: (expira_epoch, items)} de formas de pago.

#: E3-B-fix3 — el mapeo ESTALB ya es incondicional (confirmado en el
#: escritorio); la correlación ESTALB↔factura-hija queda SOLO como
#: diagnóstico en el log. Cache: {ejercicio: expira_epoch} para no repetir
#: el diagnóstico en cada listado.
_ESTALB_DIAG_CACHE: dict[str, float] = {}
_ESTALB_DIAG_TTL_SECONDS = 3600  # 1 h


def _log_estalb_diagnostic(
    client, ejercicio: str, *, force_refresh: bool = False,
) -> None:
    """Diagnóstico best-effort: loguea los albaranes cuyo ESTALB no cuadra
    con tener (o no) factura hija — documentos descuadrados (marcados a
    mano, facturas copiadas sin convertir…). NUNCA condiciona las etiquetas
    ni tumba la vista."""
    from app.integrations.factusol.chain import load_chain_index  # noqa: PLC0415
    from app.integrations.factusol.documents import (  # noqa: PLC0415
        estalb_correlation_mismatches,
    )

    now = time.time()
    if _ESTALB_DIAG_CACHE.get(ejercicio, 0) > now and not force_refresh:
        return
    _ESTALB_DIAG_CACHE[ejercicio] = now + _ESTALB_DIAG_TTL_SECONDS
    try:
        index = load_chain_index(
            client, ejercicio=ejercicio, force_refresh=force_refresh,
        )
        alb_rows = client.load_table("F_ALB", filtro="1=1", ejercicio=ejercicio)
        facturados = {
            (serie, codigo)
            for (doc, serie, codigo) in index.children["facturas"]
            if doc == "A"
        }
        mismatches = estalb_correlation_mismatches(alb_rows, facturados)
    except Exception as exc:  # noqa: BLE001 — el diagnóstico nunca estorba
        logger.debug("factusol: diagnóstico ESTALB no disponible: %s", exc)
        return
    if mismatches:
        logger.warning(
            "factusol: diagnóstico ESTALB — %d albarán(es) descuadrado(s) "
            "(el mapeo Pendiente/Facturado se aplica igualmente): %s",
            len(mismatches), "; ".join(mismatches[:20]),
        )


def _first(row: dict[str, Any], *cols: str) -> Any:
    for c in cols:
        v = row.get(c)
        if v not in (None, ""):
            return v
    return None


def _fop_names(client, ejercicio: str) -> dict[str, str]:
    """`{codigo → nombre}` de formas de pago. ERP-F5: se lee de **F_FPA**
    (`CODFPA` → `DESFPA`). La tabla que se usaba desde C-2-fix2, F_FOP, está
    VACÍA en producción — por eso el detalle decía «Código 002». Indexa el
    código tal cual y sin ceros a la izquierda (`'002'` y `'2'`). Best-effort:
    dict vacío si FACTUSOL no responde (el caller pinta el código crudo)."""
    from app.integrations.factusol.catalogs import payment_method_names  # noqa: PLC0415

    try:
        return payment_method_names(client, ejercicio=ejercicio)
    except Exception as exc:  # noqa: BLE001
        logger.warning("factusol F_FPA (formas de pago) falló: %s", exc)
        return {}


def _attach_invoice_collections(
    client, ejercicio: str, doc: dict[str, Any], serie: int, codigo: int,
    *, contrapartidas: dict[str, str] | None = None,
) -> None:
    """ERP-F3-fix1 — añade al detalle de una factura sus COBROS (F_LCO), el
    total cobrado y el saldo pendiente. Solo lectura. Best-effort: un fallo de
    F_LCO no tumba el detalle (cobros vacíos). Registra un aviso si el saldo no
    cuadra con el ESTFAC, pero enseña SIEMPRE el dato real.

    ERP-F5: `CPALCO` es la CONTRAPARTIDA de cobro (no la forma de pago); se
    resuelve con el catálogo configurable que llega en `contrapartidas`."""
    from app.integrations.factusol.catalogs import resolve_name  # noqa: PLC0415
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.collections import (  # noqa: PLC0415
        balance_mismatch,
        invoice_collections,
        load_collections_index,
    )

    doc["cobros"] = []
    doc["total_cobrado"] = 0.0
    doc["saldo_pendiente"] = doc.get("total")
    try:
        index = load_collections_index(client, ejercicio=ejercicio)
    except FactusolError as exc:
        logger.warning("factusol cobros F_LCO %s-%s KO: %s", serie, codigo, exc)
        return
    summary = invoice_collections(index, serie, codigo, doc.get("total"))
    names = contrapartidas or {}
    for cobro in summary["cobros"]:
        cobro["contrapartida_nombre"] = resolve_name(names, cobro.get("contrapartida"))
    doc["cobros"] = summary["cobros"]
    doc["total_cobrado"] = summary["total_cobrado"]
    doc["saldo_pendiente"] = summary["saldo_pendiente"]
    warn = balance_mismatch(
        doc.get("estado"), summary["saldo_pendiente"], doc.get("total"),
    )
    if warn:
        logger.warning("factusol cobros %s: %s", doc.get("numero"), warn)


#: Nombres de serie por defecto si aún no se han configurado en /erp/settings.
#: Confirmados por Bart (ERP-E2). Hay más series en uso para otras cosas; el
#: selector las lista igualmente como «Serie N» para no bloquear al operador.
#: ERP-F5: la serie 4 es Lambert — identificada por la contrapartida de sus
#: cobros (4-260004 → «3 Lambert Open Bank»).
FALLBACK_SERIES_NAMES: dict[int, str] = {
    1: "Bomedia",
    2: "MQ Europe",
    4: "Lambert",
    5: "Streamtec",
}


@router.get("/series")
def series(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Series de facturación = empresas emisoras, para el selector del modal.

    En FACTUSOL la serie NO es una columna: identifica la empresa y va
    codificada en el rango del número de documento (serie N ⇒ `Nxxxxx`). Los
    nombres son configurables en `/erp/settings`; si no hay nada guardado se
    usan los conocidos y el resto se muestran como «Serie N»."""
    from app.integrations.factusol.service import (  # noqa: PLC0415
        VALID_SERIES,
        default_serie,
        series_names,
    )

    configured = series_names(session)
    predeterminada = default_serie(session)
    items = [
        {
            "serie": n,
            "nombre": configured.get(n) or FALLBACK_SERIES_NAMES.get(n)
            or f"Serie {n}",
            "is_default": n == predeterminada,
            # Sin nombre propio = serie que nadie ha reclamado todavía; la UI
            # las ordena detrás para que las de Bart salgan arriba.
            "is_known": n in configured or n in FALLBACK_SERIES_NAMES,
        }
        for n in VALID_SERIES
    ]
    return {"items": items, "default": predeterminada}


# --- explorador de documentos (ERP-E3-A, solo lectura) -----------------------


@router.get("/documents/{doc_type}")
def list_factusol_documents(
    doc_type: str,
    codcli: str | None = Query(default=None),
    serie: int | None = Query(default=None, ge=1, le=9),
    estado: str | None = Query(default=None, max_length=10),
    fecha_desde: str | None = Query(default=None, max_length=10),
    fecha_hasta: str | None = Query(default=None, max_length=10),
    q: str | None = Query(default=None, max_length=120),
    cliente_q: str | None = Query(default=None, max_length=120),
    ciclo: str | None = Query(
        default=None, pattern="^(pendiente|con_albaran|facturado)$",
    ),
    fresh_ciclo: bool = Query(default=False),
    sort: str = Query(
        default="numero", pattern="^(numero|cliente|fecha|total|saldo)$",
    ),
    dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Listado EN VIVO de pedidos/presupuestos/albaranes/facturas de
    FACTUSOL, filtrable. Solo lectura — este endpoint no escribe nada.

    E3-B: cada fila lleva `ciclo` (hijos albarán/factura, origen y estado del
    ciclo PRE→ALB→FAC, vía los enlaces `DOC/DTP/DCO` de las líneas), y
    `ciclo=` filtra por ese estado. La anotación es best-effort: si el índice
    no se puede cargar, el listado sale sin `ciclo` — salvo que se pidiera
    filtrar por él, que entonces sí es un error.

    E3-B-fix1: `ciclo.estado_label` viene etiquetado POR TIPO (un albarán
    «pendiente» es «Sin facturar», nunca «sin albarán»); `fresh_ciclo=1`
    salta el cache del índice (lo manda la UI justo tras crear un documento
    para repintar la columna al momento). E3-B-fix3: el ESTALB nativo sale
    como Pendiente/Facturado incondicionalmente (confirmado en el
    escritorio); la correlación con las facturas hijas queda como
    diagnóstico en el log."""
    _ = current_user
    from app.integrations.factusol.chain import cycle_annotator  # noqa: PLC0415
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.documents import (  # noqa: PLC0415
        DOC_SPECS,
        ciclo_estado_label,
        list_documents,
    )

    if doc_type not in DOC_SPECS:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Tipo de documento desconocido: {doc_type!r}",
        )
    client, ejercicio = _client_and_ejercicio(session)
    annotate = None
    try:
        base_annotate = cycle_annotator(
            client, doc_type, ejercicio=ejercicio, force_refresh=fresh_ciclo,
        )

        def annotate(docs: list[dict[str, Any]]) -> None:
            base_annotate(docs)
            for doc in docs:
                doc_ciclo = doc.get("ciclo")
                if doc_ciclo is not None:
                    doc_ciclo["estado_label"] = ciclo_estado_label(
                        doc_type, doc_ciclo.get("estado"),
                    )
    except FactusolError as exc:
        if ciclo:
            raise _factusol_gateway_error(exc, "factusol_cycle_failed") from exc
        logger.warning("factusol ciclo no disponible (%s); listado sin "
                       "anotar: %s", doc_type, exc)
    # ERP-F3-fix1 — en facturas, anotar además el saldo pendiente (F_LCO leída
    # UNA vez, sin N+1). Se COMPONE con el anotador del ciclo. Best-effort: si
    # F_LCO no carga, el listado sale sin saldo (nunca tumba la lista).
    if doc_type == "facturas":
        try:
            from app.integrations.factusol.collections import (  # noqa: PLC0415
                payment_annotator,
            )

            pay_annotate = payment_annotator(client, ejercicio=ejercicio)
            prev_annotate = annotate

            def annotate(docs: list[dict[str, Any]]) -> None:
                if prev_annotate is not None:
                    prev_annotate(docs)
                pay_annotate(docs)
        except FactusolError as exc:
            logger.warning("factusol cobros no disponibles; listado de "
                           "facturas sin saldo: %s", exc)
    if doc_type == "albaranes":
        _log_estalb_diagnostic(client, ejercicio, force_refresh=fresh_ciclo)
    try:
        return list_documents(
            client, doc_type, ejercicio=ejercicio,
            codcli=codcli, serie=serie, estado=estado,
            fecha_desde=fecha_desde, fecha_hasta=fecha_hasta,
            q=q, cliente_q=cliente_q, sort=sort, direction=dir,
            limit=limit, offset=offset,
            annotate=annotate, ciclo=ciclo,
        )
    except FactusolError as exc:
        logger.warning("factusol documents/%s KO: %s", doc_type, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "factusol_list_failed", "detail": str(exc)[:200],
        }) from exc


# --- rutas de ESTADO de jobs (literal) — DECLARADAS ANTES de la genérica ---
#
# BUG: `/documents/facturas/payment-status/{job_id}` y `…/collection-status/
# {job_id}` tienen la misma forma que `/documents/{doc_type}/{serie}/{codigo}`
# (serie:int). Starlette casa en orden de declaración: si la genérica va antes,
# «payment-status» se intenta parsear como serie → 422 y el polling del cobro
# (F-3) y del registro de cobro (F-4-B) nunca llega a su ruta. Se declaran aquí,
# antes, manteniendo las URL (sin tocar frontend ni scripts).


@router.get("/documents/facturas/payment-status/{job_id}")
def invoice_payment_status(
    job_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Polling del job de marcado de cobro (mismo contrato: pending / finished
    (+result) / failed (+error))."""
    _ = current_user, session
    return _rq_quote_status(job_id)


@router.get("/documents/facturas/collection-status/{job_id}")
def invoice_collection_status(
    job_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Polling del job de registro de cobro (pending / finished (+result) /
    failed (+error))."""
    _ = current_user, session
    return _rq_quote_status(job_id)


@router.get("/documents/{doc_type}/{serie}/{codigo}")
def get_factusol_document(
    doc_type: str,
    serie: int,
    codigo: int,
    fresh_ciclo: bool = Query(default=False),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Detalle read-only de un documento con sus líneas. La clave es
    COMPUESTA (serie, código): el código solo es único por serie.

    `fresh_ciclo=1` (E3-B-fix1): re-lee el índice del ciclo saltando su
    cache — lo manda la UI justo después de crear un documento para que el
    badge/avisos se repinten al momento."""
    _ = current_user
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.documents import (  # noqa: PLC0415
        DOC_SPECS,
        get_document,
    )

    if doc_type not in DOC_SPECS:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Tipo de documento desconocido: {doc_type!r}",
        )
    client, ejercicio = _client_and_ejercicio(session)
    if doc_type == "albaranes":
        _log_estalb_diagnostic(client, ejercicio, force_refresh=fresh_ciclo)
    try:
        doc = get_document(
            client, doc_type, serie=serie, codigo=codigo, ejercicio=ejercicio,
        )
    except FactusolError as exc:
        logger.warning("factusol documents/%s detalle KO: %s", doc_type, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "factusol_detail_failed", "detail": str(exc)[:200],
        }) from exc
    if doc is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No existe el documento {serie}-{codigo} en {doc_type}",
        )
    # E3-A-fix1 / ERP-F5 — forma de pago con nombre (catálogo F_FPA; '11' y
    # '011' resuelven igual).
    from app.integrations.factusol.catalogs import resolve_name  # noqa: PLC0415

    doc["forma_pago_nombre"] = (
        resolve_name(_fop_names(client, ejercicio), doc.get("forma_pago"))
        if str(doc.get("forma_pago") or "").strip() else None
    )
    # ERP-F3-fix1 — COBROS y saldo pendiente de la factura (F_LCO, solo
    # lectura). Best-effort: si F_LCO no carga, el detalle sigue sirviendo.
    # ERP-F5: cada cobro lleva su CONTRAPARTIDA (catálogo configurable).
    if doc_type == "facturas":
        from app.erp.contrapartidas import contrapartida_names  # noqa: PLC0415

        _attach_invoice_collections(
            client, ejercicio, doc, serie, codigo,
            contrapartidas=contrapartida_names(session),
        )
    # E3-B — posición en el ciclo PRE→ALB→FAC (best-effort: el detalle sigue
    # sirviendo aunque el índice del ciclo no cargue). E3-B-fix1: la etiqueta
    # del estado va con la semántica del TIPO (albarán «pendiente» = «Sin
    # facturar»).
    doc["ciclo"] = None
    try:
        from app.integrations.factusol.chain import (  # noqa: PLC0415
            cycle_of,
            load_chain_index,
        )
        from app.integrations.factusol.documents import (  # noqa: PLC0415
            ciclo_estado_label,
        )

        doc["ciclo"] = cycle_of(
            load_chain_index(
                client, ejercicio=ejercicio, force_refresh=fresh_ciclo,
            ),
            doc_type, serie, codigo,
        )
        if doc["ciclo"] is not None:
            doc["ciclo"]["estado_label"] = ciclo_estado_label(
                doc_type, doc["ciclo"].get("estado"),
            )
    except FactusolError as exc:
        logger.warning("factusol ciclo del detalle %s %s-%s KO: %s",
                       doc_type, serie, codigo, exc)
    # E4-fix1 — idioma sugerido para el PDF (cascada pedido → cliente →
    # empresa emisora → español) con su procedencia, para preseleccionar el
    # selector de la descarga. Best-effort.
    try:
        from app.erp.factusol_pdf import suggest_pdf_language  # noqa: PLC0415

        doc["pdf_lang"] = suggest_pdf_language(session, doc_type, doc)
    except Exception as exc:  # noqa: BLE001 — la sugerencia nunca tumba nada
        logger.warning("factusol pdf_lang del detalle KO: %s", exc)
        doc["pdf_lang"] = {"lang": "es", "source": "defecto"}
    return doc


@router.get("/documents/{doc_type}/{serie}/{codigo}/pdf")
def download_document_pdf(
    doc_type: str,
    serie: int,
    codigo: int,
    lang: str = Query(default="es", pattern="^(es|en|de|fr|nl)$"),
    variant: str | None = Query(
        default=None, pattern="^(anticipo|proforma|valorado|devolucion)$",
    ),
    bank: int | None = Query(default=None, ge=0, le=20),
    currency: str = Query(default="EUR", pattern="^[A-Z]{3}$"),
    warehouse: int = Query(default=0, ge=0, le=20),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> Response:
    """ERP-E4 — PDF del documento (A4), generado por BoHub: la API de DELSOL
    no imprime. Identidad fiscal = la de la EMPRESA de la serie del documento
    (configurable en /erp/settings); `lang` cambia las etiquetas, nunca los
    datos. Generación síncrona: solo compone un PDF, no escribe nada.

    E4-fix1: `variant` imprime el mismo documento de otra forma (anticipo /
    proforma / valorado / devolución — validada contra el tipo), `bank`
    elige la cuenta bancaria de la empresa (índice; default la marcada),
    `currency` cambia solo la PRESENTACIÓN de los importes (nunca los
    convierte) y `warehouse` es el almacén de recogida de la devolución."""
    _ = current_user
    from app.erp.factusol_pdf import (  # noqa: PLC0415
        VARIANTS_BY_TYPE,
        bank_accounts,
        company_for_serie,
        extract_document_data,
        generate_document_pdf,
        load_raw_document,
        logo_path_for_serie,
        pdf_filename,
        pickup_warehouses_config,
    )
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.documents import DOC_SPECS  # noqa: PLC0415
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    if doc_type not in DOC_SPECS:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Tipo de documento desconocido: {doc_type!r}",
        )
    if variant is not None and variant not in VARIANTS_BY_TYPE.get(doc_type, ()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {
            "code": "variant_not_supported",
            "detail": f"La variante {variant!r} no aplica a {doc_type}.",
        })
    company = company_for_serie(session, serie)
    accounts = bank_accounts(company)
    selected_bank = None
    if bank is not None and accounts:
        selected_bank = accounts[min(bank, len(accounts) - 1)]
    selected_warehouse = None
    if variant == "devolucion":
        warehouses = pickup_warehouses_config(
            series_config(session).get("pickup_warehouses"),
        )
        selected_warehouse = warehouses[min(warehouse, len(warehouses) - 1)]
    client, ejercicio = _client_and_ejercicio(session)
    try:
        raw = load_raw_document(
            client, doc_type, serie=serie, codigo=codigo, ejercicio=ejercicio,
        )
        if raw is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"No existe el documento {serie}-{codigo} en {doc_type}",
            )
        data = extract_document_data(
            client, doc_type, raw[0], raw[1], ejercicio=ejercicio,
            fop_names=_fop_names(client, ejercicio),
        )
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_pdf_failed") from exc
    pdf = generate_document_pdf(
        data,
        company=company,
        lang=lang,
        logo=logo_path_for_serie(serie),
        variant=variant,
        bank=selected_bank,
        currency=currency,
        warehouse=selected_warehouse,
    )
    filename = pdf_filename(doc_type, data, lang, variant)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Descarga de facturas en LOTE (ZIP), por serie+número ------------------
#
# Reutiliza el MISMO motor E4 que la descarga individual (identidad fiscal por
# serie, `lang` cambia etiquetas, nunca datos). Solo lectura: genera y empaqueta
# PDFs, no marca ni envía ni escribe en FACTUSOL. Funciona por serie+código
# aunque la factura NO esté enlazada a ningún pedido de BoHub (facturas de flux
# hechas a mano): el PDF sale de FACTUSOL, no del pedido.


class InvoicePdfZipItem(BaseModel):
    serie: int = Field(..., ge=0, le=99)
    codigo: int = Field(..., ge=0)
    #: Idioma opcional por factura; si falta, cae al `lang` del lote o, si
    #: tampoco, a la cascada de idioma de esa factura.
    lang: str | None = Field(default=None, pattern="^(es|en|de|fr|nl)$")


class InvoicePdfZipIn(BaseModel):
    items: list[InvoicePdfZipItem] = Field(..., min_length=1, max_length=200)
    #: Idioma común del lote (si un item no trae el suyo). Sin él → cascada.
    lang: str | None = Field(default=None, pattern="^(es|en|de|fr|nl)$")


def _factura_pdf_bytes(
    session: Session, client: Any, ejercicio: str, *,
    serie: int, codigo: int, lang: str | None, fop_names: dict[str, str],
) -> tuple[bytes, str] | None:
    """(bytes del PDF, nombre legible) de una factura por serie+código con el
    motor E4, o None si esa factura no existe en FACTUSOL. `lang=None` usa la
    cascada de idioma de la factura. No escribe nada."""
    from app.erp.factusol_pdf import (  # noqa: PLC0415
        company_for_serie,
        extract_document_data,
        generate_document_pdf,
        load_raw_document,
        logo_path_for_serie,
        pdf_filename,
        suggest_pdf_language,
    )

    raw = load_raw_document(
        client, "facturas", serie=serie, codigo=codigo, ejercicio=ejercicio,
    )
    if raw is None:
        return None
    data = extract_document_data(
        client, "facturas", raw[0], raw[1], ejercicio=ejercicio,
        fop_names=fop_names,
    )
    resolved = lang or suggest_pdf_language(session, "facturas", data)["lang"]
    pdf = generate_document_pdf(
        data,
        company=company_for_serie(session, serie),
        lang=resolved,
        logo=logo_path_for_serie(serie),
    )
    return pdf, pdf_filename("facturas", data, resolved)


@router.post("/documents/facturas/pdf-zip")
def download_facturas_pdf_zip(
    payload: InvoicePdfZipIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> Response:
    """ZIP con los PDF de varias facturas (selección múltiple), por serie+número.
    Mismo motor E4 que la descarga individual; solo lectura (no marca, no envía,
    no escribe en FACTUSOL). Las facturas que no existen se saltan y se listan en
    `_no_encontradas.txt` dentro del ZIP; si NINGUNA existe, 404."""
    _ = current_user
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    client, ejercicio = _client_and_ejercicio(session)
    try:
        fop_names = _fop_names(client, ejercicio)
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_pdf_failed") from exc

    buffer = io.BytesIO()
    added = 0
    missing: list[str] = []
    used_names: set[str] = set()
    seen: set[tuple[int, int]] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in payload.items:
            key = (item.serie, item.codigo)
            if key in seen:
                continue  # deduplica serie+código repetidos en la selección
            seen.add(key)
            try:
                result = _factura_pdf_bytes(
                    session, client, ejercicio,
                    serie=item.serie, codigo=item.codigo,
                    lang=item.lang or payload.lang, fop_names=fop_names,
                )
            except FactusolError as exc:
                raise _factusol_gateway_error(exc, "factusol_pdf_failed") from exc
            if result is None:
                missing.append(f"{item.serie}-{item.codigo}")
                continue
            pdf, filename = result
            # Evita colisiones de nombre (dos facturas del mismo cliente): el
            # número serie-código ya es único, pero por si acaso desempata.
            unique = filename
            n = 2
            while unique in used_names:
                unique = filename[:-4] + f"_{n}.pdf"
                n += 1
            used_names.add(unique)
            zf.writestr(unique, pdf)
            added += 1
        if missing:
            zf.writestr(
                "_no_encontradas.txt",
                "Facturas no encontradas en FACTUSOL (se omiten):\n"
                + "\n".join(missing) + "\n",
            )
    if added == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "no_invoices_found",
            "detail": "Ninguna de las facturas seleccionadas existe en FACTUSOL.",
            "no_encontradas": missing,
        })
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="facturas_pdf.zip"'},
    )


# --- ERP-F1 Parte 2 — enviar la factura por email -------------------------


@router.get("/documents/facturas/{serie}/{codigo}/email-preview")
def invoice_email_preview(
    serie: int,
    codigo: int,
    lang: str | None = Query(default=None, pattern="^(es|en|de|fr|nl)$"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Datos para la PREVISUALIZACIÓN obligatoria antes de enviar la factura
    por email: destinatario, asunto, idioma (+ procedencia), cuerpo editable
    y nombre del adjunto. No envía nada ni genera el PDF."""
    from app.erp.invoice_email import build_invoice_email_preview  # noqa: PLC0415

    client, ejercicio = _client_and_ejercicio(session)
    preview = build_invoice_email_preview(
        session, client, serie=serie, codigo=codigo, ejercicio=ejercicio,
        current_user=current_user, lang_override=lang,
        fop_names=_fop_names(client, ejercicio),
    )
    if preview.get("error") == "not_found":
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No existe la factura {serie}-{codigo}",
        )
    return preview


class InvoiceEmailPayload(BaseModel):
    """Envío de la factura por email. `confirm` OBLIGATORIO: enviar un correo
    a un cliente es irreversible, no puede ser un clic accidental."""

    confirm: bool = False
    to: list[str] = Field(default_factory=list)
    subject: str = Field(min_length=1, max_length=500)
    body_text: str = Field(min_length=1)
    lang: str = Field(pattern="^(es|en|de|fr|nl)$")
    from_alias: str = Field(min_length=3, max_length=255)
    reply_to_message_id: str | None = None
    bank: int | None = Field(default=None, ge=0, le=20)
    variant: str | None = Field(default=None, pattern="^(anticipo)$")


@router.post("/documents/facturas/{serie}/{codigo}/email", status_code=201)
def send_invoice_email_endpoint(
    serie: int,
    codigo: int,
    payload: InvoiceEmailPayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Envía la factura por email (síncrono, como el resto de la app). El
    idioma vale para el PDF adjunto y el cuerpo; se responde al hilo del
    pedido si `reply_to_message_id` viene, si no correo nuevo. Registra el
    envío en el timeline del pedido; si falla, NO marca como enviada."""
    from app.erp.factusol_pdf import bank_accounts, company_for_serie  # noqa: PLC0415
    from app.erp.invoice_email import send_invoice_email  # noqa: PLC0415
    from app.integrations.gmail.service import (  # noqa: PLC0415
        GmailNotConnectedError,
        GmailScopeMissingError,
    )

    if not payload.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {
            "code": "confirmation_required",
            "detail": "El envío requiere confirmación explícita.",
        })
    to = [t.strip() for t in payload.to if t and t.strip()]
    if not to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {
            "code": "no_recipient", "detail": "Falta el destinatario.",
        })
    # El alias debe estar en las preferencias permitidas del usuario (mismo
    # criterio que el envío normal de la app — no suplantar un alias ajeno).
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.crm import UserEmailAliasPref  # noqa: PLC0415
    pref = session.scalar(
        select(UserEmailAliasPref).where(
            UserEmailAliasPref.user_id == current_user.id,
            UserEmailAliasPref.alias_email == payload.from_alias,
            UserEmailAliasPref.is_allowed.is_(True),
        )
    )
    if pref is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, {
            "code": "alias_not_allowed",
            "detail": "El alias no está en tus preferencias (config. en /account).",
        })

    client, ejercicio = _client_and_ejercicio(session)
    company = company_for_serie(session, serie)
    accounts = bank_accounts(company)
    selected_bank = None
    if payload.bank is not None and accounts:
        selected_bank = accounts[min(payload.bank, len(accounts) - 1)]
    try:
        return send_invoice_email(
            session, client, serie=serie, codigo=codigo, ejercicio=ejercicio,
            current_user=current_user, to=to, subject=payload.subject,
            body_text=payload.body_text, lang=payload.lang,
            from_alias=payload.from_alias,
            reply_to_message_id=payload.reply_to_message_id,
            bank=selected_bank, variant=payload.variant,
            fop_names=_fop_names(client, ejercicio), company=company,
        )
    except (GmailNotConnectedError, GmailScopeMissingError) as exc:
        # Gmail no conectado / sin scope: NO se marca como enviada.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {
            "code": "gmail_unavailable", "detail": str(exc)[:200],
        }) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "invoice_not_found", "detail": str(exc)[:200],
        }) from exc


# --- ERP-F3: marcar la factura como cobrada / pendiente ---------------------


class InvoicePaymentPayload(BaseModel):
    """Marcar el cobro de una factura. `confirm` OBLIGATORIO: marcar una
    factura como cobrada es una afirmación contable, no un clic accidental."""

    confirm: bool = False
    #: True = «cobrada»; False = «pendiente de cobro» (la inversa).
    paid: bool


@router.post("/documents/facturas/{serie}/{codigo}/payment", status_code=202)
def mark_invoice_payment_endpoint(
    serie: int,
    codigo: int,
    payload: InvoicePaymentPayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Encola escribir `ESTFAC` (cobrada/pendiente) por clave COMPUESTA en
    `factusol:writes` (202 + job_id). Pre-chequeo EN VIVO: confirma que la
    factura existe, lee su ESTFAC actual (idempotencia: si ya está en el
    estado destino no encola) y devuelve cliente/importe para el aviso.
    Requiere permiso de EDICIÓN de ERP (marcar cobros es contable)."""
    from app.core.audit import record_event  # noqa: PLC0415
    from app.integrations.factusol.client import (  # noqa: PLC0415
        FactusolClient,
        FactusolError,
    )
    from app.integrations.factusol.jobs import (  # noqa: PLC0415
        enqueue_mark_invoice_paid,
    )
    from app.integrations.factusol.service import (  # noqa: PLC0415
        _estado_str,
        ejercicio_for,
        invoice_payment_value,
        serie_of_row,
    )

    if not payload.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {
            "code": "confirmation_required",
            "detail": "Marcar el cobro requiere confirmación explícita.",
        })
    # Config del estado destino: si está vacía, no marcar (aviso claro).
    target = invoice_payment_value(session, paid=payload.paid)
    if target is None:
        key = "estfac_cobrada" if payload.paid else "estfac_pendiente"
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "payment_config_missing",
            "detail": (
                f"Falta `{key}` en /erp/settings: el marcado de cobro está "
                "desactivado."
            ),
        })
    try:
        client = FactusolClient.from_settings()
        ejercicio = ejercicio_for(session)
        rows = client.load_table(
            "F_FAC", filtro=f"CODFAC={int(codigo)}", ejercicio=ejercicio,
        )
    except FactusolError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "factusol_unreachable", "detail": str(exc)[:200],
        }) from exc
    except Exception as exc:  # noqa: BLE001 — sin credenciales / config
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {
            "code": "factusol_unavailable", "detail": str(exc)[:200],
        }) from exc
    row = next(
        (r for r in rows if serie_of_row(r, "TIPFAC") == serie), None,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "invoice_not_found",
            "detail": f"No existe la factura {serie}-{codigo}.",
        })
    current = _estado_str(row.get("ESTFAC"))
    numero = f"{serie}-{int(codigo):06d}"
    meta = {
        "numero": numero,
        "referencia": str(row.get("REFFAC") or "").strip(),
        "cliente": str(row.get("CNOFAC") or "").strip(),
        "importe": row.get("TOTFAC"),
    }
    # Idempotente: si ya está en el estado destino, no se encola nada.
    if current == _estado_str(target):
        return {
            "status": "already", "estfac": current,
            "paid": payload.paid, **meta,
        }
    job_id = enqueue_mark_invoice_paid(
        serie, int(codigo), payload.paid,
        actor_user_id=current_user.id, current_estado=current, meta=meta,
    )
    record_event(
        session,
        action="erp.invoice_payment_mark_requested",
        target_type="document", target_id=numero,
        actor=current_user,
        metadata={"job_id": job_id, "paid": payload.paid, **meta},
        message=(
            f"Solicitado marcar {numero} como "
            f"{'cobrada' if payload.paid else 'pendiente'}"
        ),
    )
    session.commit()
    return {"status": "queued", "job_id": job_id, "paid": payload.paid, **meta}


# --- ERP-F4-B: registrar un COBRO en FACTUSOL (F_LCO + ESTFAC) --------------
#
# Es lo que F-3 NO hacía (solo ponía el flag ESTFAC): aquí se inserta la línea
# de cobro real en `F_LCO` (importe, fecha, contrapartida, concepto) y después
# se marca ESTFAC=2. Lo conduce en lote `scripts/registrar_cobros_lote.ps1`.


class InvoiceCollectionPayload(BaseModel):
    """Registrar un cobro. `confirm` OBLIGATORIO: escribir en la contabilidad
    no es un clic accidental."""

    confirm: bool = False
    #: Contrapartida (cuenta donde entra el dinero): CÓDIGO («6») o NOMBRE tal
    #: como viene en el Excel («Bomedia (Sabadell)», «Paypal MQ Europe»).
    cuenta: str = Field(min_length=1, max_length=80)
    #: Fecha del cobro (ISO o dd/mm/yyyy).
    fecha: str = Field(min_length=6, max_length=25)
    #: Forma de pago (texto del Excel: Transferencia/TPV/Paypal…) → concepto.
    forma: str | None = Field(default=None, max_length=80)
    observaciones: str | None = Field(default=None, max_length=255)
    #: Importe; por defecto el SALDO pendiente (= total si no había cobros).
    importe: float | None = Field(default=None, gt=0)


@router.post("/documents/facturas/{serie}/{codigo}/collection", status_code=202)
def register_invoice_collection_endpoint(
    serie: int,
    codigo: int,
    payload: InvoiceCollectionPayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Encola registrar el cobro de la factura en `factusol:writes` (202 +
    job_id). Pre-chequeo EN VIVO: resuelve la contrapartida (400 si no está en
    el catálogo), valida la fecha (400), confirma que la factura existe (404) y
    lee su saldo/ESTFAC (idempotencia: ya cobrada → `already`, no encola).
    Devuelve cliente/total/importe previsto para el aviso. Permiso de EDICIÓN."""
    from app.core.audit import record_event  # noqa: PLC0415
    from app.erp.contrapartidas import (  # noqa: PLC0415
        resolve_contrapartida,
        resolve_contrapartida_code,
    )
    from app.integrations.factusol.client import (  # noqa: PLC0415
        FactusolClient,
        FactusolError,
    )
    from app.integrations.factusol.collections_write import (  # noqa: PLC0415
        collection_status,
        factusol_datetime,
    )
    from app.integrations.factusol.jobs import (  # noqa: PLC0415
        enqueue_register_invoice_collection,
    )
    from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

    if not payload.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {
            "code": "confirmation_required",
            "detail": "Registrar un cobro requiere confirmación explícita.",
        })
    contrapartida = resolve_contrapartida_code(session, payload.cuenta)
    if contrapartida is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {
            "code": "unknown_account",
            "detail": (
                f"La cuenta {payload.cuenta!r} no casa con ninguna contrapartida "
                "del catálogo (/erp/settings)."
            ),
        })
    try:
        fecha_iso = factusol_datetime(payload.fecha)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {
            "code": "invalid_date", "detail": str(exc),
        }) from exc
    try:
        client = FactusolClient.from_settings()
        ejercicio = ejercicio_for(session)
        status_info = collection_status(
            client, serie=serie, codigo=codigo, ejercicio=ejercicio,
        )
    except FactusolError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "factusol_unreachable", "detail": str(exc)[:200],
        }) from exc
    except Exception as exc:  # noqa: BLE001 — sin credenciales / config
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {
            "code": "factusol_unavailable", "detail": str(exc)[:200],
        }) from exc
    if status_info is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "invoice_not_found",
            "detail": f"No existe la factura {serie}-{codigo}.",
        })
    cuenta_info = {
        "codigo": contrapartida,
        "nombre": resolve_contrapartida(session, contrapartida) or contrapartida,
    }
    importe = round(
        payload.importe if payload.importe is not None
        else status_info["saldo_pendiente"], 2,
    )
    meta = {
        "numero": status_info["numero"], "cliente": status_info["cliente"],
        "referencia": status_info["referencia"], "total": status_info["total"],
        "saldo_pendiente": status_info["saldo_pendiente"],
        "importe": importe, "contrapartida": cuenta_info,
        "fecha": fecha_iso[:10], "forma": payload.forma,
    }
    # Idempotente: ya cobrada (saldo 0 / ESTFAC=2) → no se encola nada.
    if status_info["ya_cobrada"]:
        return {"status": "already", "estfac": status_info["estfac"], **meta}
    job_id = enqueue_register_invoice_collection(
        serie, int(codigo), contrapartida, fecha_iso,
        importe, payload.forma, payload.observaciones,
        actor_user_id=current_user.id, meta=meta,
    )
    record_event(
        session,
        action="erp.invoice_collection_requested",
        target_type="document", target_id=status_info["numero"],
        actor=current_user,
        metadata={"job_id": job_id, **meta},
        message=(
            f"Solicitado registrar cobro de {importe} € de "
            f"{status_info['numero']} en {cuenta_info['nombre']}"
        ),
    )
    session.commit()
    return {"status": "queued", "job_id": job_id, **meta}


#: ERP-E4 — logos de las empresas emisoras. Los modelos de FACTUSOL apuntan a
#: rutas del PC de Bart, inaccesibles: se suben desde /erp/settings y viven
#: bajo el directorio de assets (bind-mount persistente en producción). El
#: PDF funciona sin logo.
_LOGO_MAX_BYTES = 2 * 1024 * 1024
_LOGO_TYPES = {"image/png": ".png", "image/jpeg": ".jpg"}


@router.post("/companies/{serie}/logo", status_code=201)
async def upload_company_logo(
    serie: int,
    file: UploadFile,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    _ = session, current_user
    from app.erp.factusol_pdf import logos_dir  # noqa: PLC0415

    ext = _LOGO_TYPES.get(str(file.content_type or "").lower())
    if ext is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {
            "code": "logo_type", "detail": "El logo debe ser PNG o JPG.",
        })
    content = await file.read()
    if not content or len(content) > _LOGO_MAX_BYTES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {
            "code": "logo_size", "detail": "El logo debe pesar entre 1 byte y 2 MB.",
        })
    base = logos_dir()
    base.mkdir(parents=True, exist_ok=True)
    # Una sola variante por serie: borrar las otras extensiones evita servir
    # un logo viejo con otra extensión.
    for old_ext in (".png", ".jpg", ".jpeg"):
        old = base / f"serie_{serie}{old_ext}"
        if old.exists():
            old.unlink()
    (base / f"serie_{serie}{ext}").write_bytes(content)
    return {"serie": serie, "logo": True}


@router.get("/companies/{serie}/logo")
def get_company_logo(
    serie: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
):
    """ERP-F3 — sirve el logo actual de la empresa (miniatura en /erp/settings).
    Con auth (se descarga como blob desde el front), no como `<img src>` a
    pelo. 404 si esa serie no tiene logo."""
    _ = session, current_user
    from fastapi.responses import FileResponse  # noqa: PLC0415

    from app.erp.factusol_pdf import logo_path_for_serie  # noqa: PLC0415

    path = logo_path_for_serie(serie)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "no_logo", "detail": f"La serie {serie} no tiene logo.",
        })
    media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(path, media_type=media, filename=path.name)


@router.delete("/companies/{serie}/logo")
def delete_company_logo(
    serie: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    """ERP-F3 — quita el logo de la empresa (botón «Quitar» en /erp/settings).
    Idempotente: si no había logo, responde igual (`logo: False`)."""
    _ = session, current_user
    from app.erp.factusol_pdf import logos_dir  # noqa: PLC0415

    base = logos_dir()
    for ext in (".png", ".jpg", ".jpeg"):
        candidate = base / f"serie_{serie}{ext}"
        if candidate.exists():
            candidate.unlink()
    return {"serie": serie, "logo": False}


class ConvertDocumentPayload(BaseModel):
    """Crear albarán/factura desde el documento abierto (ERP-E3-B).

    `serie`: override de la serie del destino; sin él, hereda la del origen.
    `force`: crear aunque el origen ya tenga un hijo de ese tipo (el operador
    ya vio el aviso del 409)."""

    target: str = Field(pattern="^(albaranes|facturas)$")
    serie: int | None = Field(default=None, ge=1, le=9)
    fecha: str | None = Field(default=None, max_length=10)
    force: bool = False


@router.get("/documents/convert-status/{job_id}")
def convert_document_status(
    job_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Polling del job de conversión — mismo contrato que el de proformas:
    `pending` / `finished` (+result con el nº creado) / `failed` (+error)."""
    _ = current_user, session
    return _rq_quote_status(job_id)


@router.post("/documents/{doc_type}/{serie}/{codigo}/convert", status_code=202)
def convert_factusol_document(
    doc_type: str,
    serie: int,
    codigo: int,
    payload: ConvertDocumentPayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Encola la creación del documento DESTINO (albarán o factura) a partir
    del abierto, enlazado por `DOC/DTP/DCO` en las líneas (ERP-E3-B).

    Va por `factusol:writes` (202 + job_id) como toda escritura: el número
    del destino es un `MAX+1` por serie que exige el worker serializado.

    Anti-duplicado en dos capas: aquí un pre-chequeo EN VIVO que devuelve 409
    con los números existentes («este presupuesto ya tiene albarán 5-500004»)
    — el operador puede reenviar con `force` —, y el mismo chequeo repetido
    DENTRO del worker, que es el que cubre la carrera entre dos 202."""
    from app.integrations.factusol.chain import (  # noqa: PLC0415
        ALLOWED_CONVERSIONS,
        SINGULAR,
        find_existing_children,
    )
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.documents import (  # noqa: PLC0415
        DOC_SPECS,
        get_document,
        visible_number,
    )
    from app.integrations.factusol.jobs import (  # noqa: PLC0415
        enqueue_create_document,
    )

    if doc_type not in DOC_SPECS:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Tipo de documento desconocido: {doc_type!r}",
        )
    if payload.target not in ALLOWED_CONVERSIONS.get(doc_type, ()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {
            "code": "conversion_not_supported",
            "detail": (
                f"No se puede crear {SINGULAR.get(payload.target, payload.target)} "
                f"desde un {SINGULAR.get(doc_type, doc_type)}."
            ),
        })
    client, ejercicio = _client_and_ejercicio(session)
    try:
        doc = get_document(
            client, doc_type, serie=serie, codigo=codigo, ejercicio=ejercicio,
        )
        if doc is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"No existe el documento {serie}-{codigo} en {doc_type}",
            )
        # Fase 2 — guardarraíl web: un pedido de cliente que sea un pedido de
        # WooCommerce NO recibe albarán de BoHub (lo crea WooCommerce). El
        # worker lo re-chequea; aquí se avisa con 409 antes de encolar.
        if doc_type == "pedidos":
            from app.erp.factusol_albaran import web_pedido_reason  # noqa: PLC0415

            web_reason = web_pedido_reason(
                session, {"REFPCL": doc.get("referencia") or ""},
            )
            if web_reason:
                raise HTTPException(status.HTTP_409_CONFLICT, {
                    "code": "web_pedido_no_albaran", "detail": web_reason,
                })
        if not payload.force:
            existing = find_existing_children(
                client, doc_type, payload.target,
                tip=serie, cod=codigo, ejercicio=ejercicio,
            )
            if existing:
                raise HTTPException(status.HTTP_409_CONFLICT, {
                    "code": "already_converted",
                    "detail": (
                        f"El {SINGULAR[doc_type]} "
                        f"{visible_number(serie, codigo)} ya tiene "
                        f"{SINGULAR[payload.target]} {', '.join(existing)} "
                        "en FACTUSOL."
                    ),
                    "existing": existing,
                })
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_convert_failed") from exc

    job_id = enqueue_create_document(
        doc_type, payload.target, serie, codigo,
        {
            "ejercicio": ejercicio,
            "serie": payload.serie,
            "fecha": payload.fecha,
            "force": payload.force,
        },
    )
    _audit_quote(
        session, current_user, "erp.factusol_document_convert",
        f"{doc_type}:{serie}-{codigo}",
        {"job_id": job_id, "target": payload.target,
         "serie_override": payload.serie, "force": payload.force},
    )
    session.commit()
    return {"job_id": job_id, "status": "queued"}


@router.get("/formas-pago")
def formas_pago(
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Catálogo de formas de pago para el desplegable del modal de emisión.
    ERP-F5: se lee de **F_FPA** (`CODFPA` → `DESFPA`, 77 filas); F_FOP, la
    tabla que se leía desde C-2-fix2, está VACÍA en producción. Best-effort:
    si FACTUSOL no responde, lista vacía (el modal permite dejarlo en blanco).
    Cache en proceso de 5 min (`integrations/factusol/catalogs`)."""
    _ = current_user
    from app.integrations.factusol.catalogs import is_cached, payment_methods  # noqa: PLC0415
    from app.integrations.factusol.client import FactusolClient  # noqa: PLC0415
    from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

    ejercicio = ejercicio_for(session)
    cached = is_cached("formas_pago", ejercicio=ejercicio)
    try:
        client = FactusolClient.from_settings()
        items = payment_methods(client, ejercicio=ejercicio)
    except Exception as exc:  # noqa: BLE001 — FACTUSOL caído / sin credenciales
        logger.warning("factusol formas-pago (F_FPA) falló: %s", exc)
        return {"items": [], "ejercicio": ejercicio, "source": "F_FPA",
                "error": "factusol_unreachable"}
    return {"items": items, "ejercicio": ejercicio, "source": "F_FPA", "cached": cached}


# --- clientes: búsqueda / vínculo / alta (Fase C · C-3) ----------------------


def _client_and_ejercicio(session: Session):
    """Cliente FACTUSOL + ejercicio activo. 503 si no hay credenciales."""
    from app.integrations.factusol.client import FactusolClient  # noqa: PLC0415
    from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

    try:
        return FactusolClient.from_settings(), ejercicio_for(session)
    except Exception as exc:  # noqa: BLE001 — sin credenciales / config rota
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {
            "code": "factusol_unavailable", "detail": str(exc)[:200],
        }) from exc


@router.get("/customers/search")
def search_customers_endpoint(
    q: str = Query(..., min_length=1),
    by: str = Query(default="nif", pattern="^(nif|email|name|codcli)$"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Busca clientes en F_CLI (por NIF/email exactos o nombre LIKE) y marca
    cuáles ya están vinculados a una empresa/contacto del CRM."""
    _ = current_user
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.customers import (  # noqa: PLC0415
        crm_links_for,
        search_customers,
    )

    client, ejercicio = _client_and_ejercicio(session)
    try:
        found = search_customers(client, q, by=by, ejercicio=ejercicio)
    except FactusolError as exc:
        logger.warning("factusol customers/search KO: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "factusol_search_failed", "detail": str(exc)[:200],
        }) from exc
    links = crm_links_for(session, [c["codcli"] for c in found if c.get("codcli")])
    for cust in found:
        link = links.get(str(cust.get("codcli")))
        cust["crm_link"] = link
        # Compat con el nombre que pedía el spec de C-3.
        cust["factusol_matches_crm_id"] = link["id"] if link else None
    return {"items": found, "ejercicio": ejercicio}


# --- «Traer datos de FACTUSOL» (ficha de empresa): FACTUSOL → CRM, a demanda ---


class PullIntoCrmIn(BaseModel):
    company_id: str = Field(min_length=1, max_length=36)


def _pull_context(session: Session, company_id: str):
    """Empresa vinculada + su cliente F_CLI (lectura). 404/409 con código."""
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.customers import get_customer  # noqa: PLC0415
    from app.models.crm import Company  # noqa: PLC0415

    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "company_not_found", "detail": "La empresa no existe.",
        })
    codcli = str(company.factusol_company_id or "").strip()
    if not codcli:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "company_unlinked",
            "detail": "La empresa no está vinculada a ningún cliente de FACTUSOL.",
        })
    client, ejercicio = _client_and_ejercicio(session)
    try:
        customer = get_customer(client, codcli, ejercicio=ejercicio)
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_customer_failed") from exc
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "factusol_customer_not_found",
            "detail": f"El cliente FACTUSOL nº {codcli} no existe (ejercicio {ejercicio}).",
        })
    return company, codcli, customer


@router.get("/customers/pull-preview")
def pull_preview_endpoint(
    company_id: str = Query(min_length=1, max_length=36),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Qué cambiaría «Traer datos de FACTUSOL» en la empresa (campo a campo, con
    el valor CRM y el de FACTUSOL). No escribe nada."""
    _ = current_user
    from app.integrations.factusol.customers import pull_changes  # noqa: PLC0415

    company, codcli, customer = _pull_context(session, company_id)
    return {
        "company_id": company.id, "codcli": codcli, "customer": customer,
        "changes": pull_changes(company, customer),
    }


@router.post("/customers/pull-into-crm")
def pull_into_crm_endpoint(
    payload: PullIntoCrmIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """«Traer datos de FACTUSOL»: sobrescribe en la empresa CRM los campos del
    mapping de la diff (nombre, NIF, dirección, ciudad, CP, provincia) y el país
    con los del cliente F_CLI vinculado. FACTUSOL = fuente de verdad (decisión
    de Bart). Solo a demanda, solo escribe en el CRM (nunca en FACTUSOL), con
    auditoría «datos traídos de FACTUSOL cliente nº …»."""
    from datetime import UTC, datetime  # noqa: PLC0415

    from app.integrations.factusol.customers import apply_pull  # noqa: PLC0415
    from app.models.crm import AuditLog  # noqa: PLC0415

    company, codcli, customer = _pull_context(session, payload.company_id)
    changes = apply_pull(company, customer)
    company.factusol_synced_at = datetime.now(UTC)
    company.factusol_sync_source = "factusol_pull"
    session.add(AuditLog(
        actor_user_id=current_user.id,
        action="erp.factusol_customer_pull",
        target_type="company",
        target_id=company.id,
        metadata_json=json.dumps({
            "factusol_codcli": codcli,
            "summary": f"datos traídos de FACTUSOL cliente nº {codcli}",
            "changes": changes,
        }),
    ))
    session.commit()
    logger.info("factusol pull → CRM: empresa %s ← cliente %s (%d cambios)",
                company.id, codcli, len(changes))
    return {"ok": True, "company_id": company.id, "codcli": codcli,
            "changes": changes, "applied": len(changes)}


@router.get("/customers/{codcli}/addresses")
def customer_addresses_endpoint(
    codcli: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Direcciones del cliente F_CLI: la principal (`codigo: 0`) más las
    adicionales configuradas (`ACO1CLI`…`ACO4CLI`) — las que el escritorio
    enseña en el botón «Direcciones».

    Para mandar una proforma a una delegación distinta de la sede. Solo
    lectura: se elige entre las que ya existan, nunca se editan desde el CRM."""
    _ = current_user
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.customers import (  # noqa: PLC0415
        customer_addresses,
    )

    client, ejercicio = _client_and_ejercicio(session)
    try:
        items = customer_addresses(client, codcli, ejercicio=ejercicio)
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_addresses_failed") from exc
    return {"items": items, "ejercicio": ejercicio}


# --- conciliación masiva CRM ↔ FACTUSOL (Fase C · C-5) -----------------------


class BulkMatchDryRunPayload(BaseModel):
    filter: str = Field(default="unlinked_only", pattern="^(unlinked_only|all)$")
    #: `None` = sin tope (el modo por email procesa TODOS los contactos, C-5-fix2).
    #: El modo por empresa cae a `DEFAULT_BATCH_SIZE` porque sí pagina en SQL.
    batch_size: int | None = Field(default=None, ge=1, le=100_000)


@router.post("/bulk-match/dry-run")
def bulk_match_dry_run(
    payload: BulkMatchDryRunPayload | None = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    """Propone parejas empresa CRM ↔ cliente F_CLI. **Solo lectura.**

    Idempotente: no escribe nada, ni en el CRM ni en FACTUSOL. Lee `F_CLI`
    entero de una vez y cruza en Python — con miles de empresas, preguntar por
    cada una serían miles de peticiones contra un token de 3 minutos."""
    _ = current_user
    from app.integrations.factusol.bulk_match import (  # noqa: PLC0415
        DEFAULT_BATCH_SIZE,
        dry_run,
    )
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    payload = payload or BulkMatchDryRunPayload()
    client, ejercicio = _client_and_ejercicio(session)
    try:
        return dry_run(
            session, client, ejercicio=ejercicio,
            unlinked_only=payload.filter == "unlinked_only",
            batch_size=payload.batch_size or DEFAULT_BATCH_SIZE,
        )
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_bulk_match_failed") from exc


class BulkMatchOperation(BaseModel):
    crm_company_id: str = Field(min_length=1, max_length=36)
    factusol_codcli: str = Field(min_length=1, max_length=36)
    fields_to_sync: list[str] = Field(default_factory=list)


class BulkMatchApplyPayload(BaseModel):
    operations: list[BulkMatchOperation] = Field(default_factory=list)


@router.post("/bulk-match/apply")
def bulk_match_apply(
    payload: BulkMatchApplyPayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    """Sobrescribe SOLO las empresas y campos aprobados, guardando antes los
    valores previos en el AuditLog. Una empresa por transacción: que una falle
    no invalida el resto del lote."""
    from app.integrations.factusol.bulk_match import (  # noqa: PLC0415
        apply_operations,
    )
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    if not payload.operations:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {
            "code": "no_operations", "detail": "No hay nada que aplicar.",
        })
    client, ejercicio = _client_and_ejercicio(session)
    try:
        return apply_operations(
            session, client, ejercicio=ejercicio,
            operations=[op.model_dump() for op in payload.operations],
            actor_id=current_user.id,
        )
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_bulk_apply_failed") from exc


@router.post("/bulk-match/by-contact-email/dry-run")
def bulk_match_by_email_dry_run(
    payload: BulkMatchDryRunPayload | None = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    """Propone parejas contacto → cliente F_CLI por **email exacto**.

    C-5-fix1: el modo por NIF/nombre da mucho ruido — la mayoría de las
    empresas del CRM vienen de imports sin NIF, y el nombre difuso produce
    falsos positivos («4d Factory» ↔ «FACTORY»). El email o coincide o no.

    Procesa **todos** los contactos con email: F_CLI se lee una sola vez y el
    resto es comparar strings, así que el coste apenas crece con el volumen."""
    _ = current_user
    from app.integrations.factusol.bulk_match import (  # noqa: PLC0415
        dry_run_by_contact_email,
    )
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    payload = payload or BulkMatchDryRunPayload()
    client, ejercicio = _client_and_ejercicio(session)
    try:
        return dry_run_by_contact_email(
            session, client, ejercicio=ejercicio, batch_size=payload.batch_size,
        )
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_bulk_match_failed") from exc


class BulkMatchByEmailOperation(BaseModel):
    contact_id: str = Field(min_length=1, max_length=36)
    factusol_codcli: str = Field(min_length=1, max_length=36)
    fields_to_sync: list[str] = Field(default_factory=list)


class BulkMatchByEmailApplyPayload(BaseModel):
    operations: list[BulkMatchByEmailOperation] = Field(default_factory=list)


@router.post("/bulk-match/by-contact-email/apply")
def bulk_match_by_email_apply(
    payload: BulkMatchByEmailApplyPayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    """Actualiza la empresa de cada contacto con los datos de su cliente F_CLI.

    Los desenlaces van desglosados aparte de `errors`: crear la empresa de un
    contacto huérfano, reutilizar una ya vinculada a ese CODCLI o reasignar un
    contacto mal agrupado (C-5-fix5) no son fallos, son resultados distintos."""
    from app.integrations.factusol.bulk_match import (  # noqa: PLC0415
        apply_by_contact_email,
    )
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    if not payload.operations:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {
            "code": "no_operations", "detail": "No hay nada que aplicar.",
        })
    client, ejercicio = _client_and_ejercicio(session)
    try:
        return apply_by_contact_email(
            session, client, ejercicio=ejercicio,
            operations=[op.model_dump() for op in payload.operations],
            actor_id=current_user.id,
        )
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_bulk_apply_failed") from exc


class ImportOrphansDryRunPayload(BaseModel):
    filter: str = Field(default="all", pattern="^(all|only_with_email)$")


@router.post("/bulk-match/import-orphans/dry-run")
def import_orphans_dry_run(
    payload: ImportOrphansDryRunPayload | None = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    """Lista los clientes de FACTUSOL que no tiene ninguna empresa del CRM.

    C-6: después de conciliar lo que existía en los dos lados (C-5), quedan
    miles de `F_CLI` que nunca llegaron al CRM — facturación de años que no
    entró por Woo, formularios ni imports antiguos. Solo lee."""
    _ = current_user
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.import_orphans import (  # noqa: PLC0415
        dry_run_orphans,
    )

    payload = payload or ImportOrphansDryRunPayload()
    client, ejercicio = _client_and_ejercicio(session)
    try:
        return dry_run_orphans(
            session, client, ejercicio=ejercicio,
            only_with_email=payload.filter == "only_with_email",
        )
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_import_orphans_failed") from exc


class ImportOrphanOperation(BaseModel):
    codcli: str = Field(min_length=1, max_length=36)
    #: Los datos de F_CLI tal cual los devolvió el dry-run. Con esto el apply
    #: no llama a FACTUSOL: el navegador ya los tiene y volver a pedirlos solo
    #: añadía un punto de fallo (C-6-fix1).
    factusol_data: dict[str, Any] | None = None


class ImportOrphansApplyPayload(BaseModel):
    operations: list[ImportOrphanOperation] = Field(default_factory=list)
    #: Formato viejo, solo códigos. Se mantiene para no romper a un cliente que
    #: no se haya recargado; obliga a releer esos CODCLI de F_CLI.
    codclis: list[str] = Field(default_factory=list)
    create_contacts_if_email: bool = True

    def as_operations(self) -> list[dict[str, Any]]:
        if self.operations:
            return [op.model_dump() for op in self.operations]
        return [{"codcli": c, "factusol_data": None} for c in self.codclis]


@router.post("/bulk-match/import-orphans/apply")
def import_orphans_apply(
    payload: ImportOrphansApplyPayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_admin),
) -> dict[str, Any]:
    """Crea empresa (y contacto, si hay `EMACLI`) para los CODCLI marcados.

    Un CODCLI por transacción, y **siempre 200**: lo que falle va en `errors`
    con su CODCLI, para poder reintentar solo eso. Antes un `KO` de DELSOL en
    la relectura de F_CLI devolvía 502 y se llevaba el lote entero por delante
    (C-6-fix1)."""
    from app.integrations.factusol.import_orphans import (  # noqa: PLC0415
        apply_import_orphans,
    )

    operations = payload.as_operations()
    if not operations:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {
            "code": "no_operations", "detail": "No hay nada que importar.",
        })
    client, ejercicio = _client_and_ejercicio(session)
    # Sin `except FactusolError`: `apply_import_orphans` ya no lo deja escapar.
    # Un fallo de FACTUSOL afecta a las operaciones que lo necesiten, no al
    # lote; propagarlo como 502 tiraría también las que sí se han escrito.
    return apply_import_orphans(
        session, client, ejercicio=ejercicio, operations=operations,
        create_contacts_if_email=payload.create_contacts_if_email,
        actor_id=current_user.id,
    )


class LinkCustomerPayload(BaseModel):
    crm_type: str = Field(pattern="^(company|contact)$")
    #: Los IDs del CRM son UUID (String 36), no enteros.
    crm_id: str = Field(min_length=1, max_length=36)
    factusol_codcli: str = Field(min_length=1, max_length=36)


@router.post("/customers/link")
def link_customer_endpoint(
    payload: LinkCustomerPayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Vincula un CODCLI de FACTUSOL a una empresa/contacto del CRM. 409 si ese
    código ya está vinculado a otro registro."""
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.customers import link_to_crm  # noqa: PLC0415

    try:
        row = link_to_crm(
            session, crm_type=payload.crm_type, crm_id=payload.crm_id,
            codcli=payload.factusol_codcli,
        )
    except FactusolError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "already_linked", "detail": str(exc)[:300],
        }) from exc
    _audit_customer_link(session, current_user, payload.crm_type,
                         payload.crm_id, payload.factusol_codcli)
    session.commit()
    return {"crm_type": payload.crm_type, "crm_id": row.id,
            "factusol_codcli": payload.factusol_codcli, "linked": True}


class CreateCustomerPayload(BaseModel):
    crm_type: str = Field(pattern="^(company|contact)$")
    crm_id: str = Field(min_length=1, max_length=36)
    nombre: str = Field(min_length=1, max_length=255)
    nif: str = Field(default="", max_length=64)
    direccion: str = Field(default="", max_length=255)
    ciudad: str = Field(default="", max_length=120)
    cp: str = Field(default="", max_length=20)
    provincia: str = Field(default="", max_length=120)
    pais: str = Field(default="ES", max_length=10)
    email: str | None = Field(default=None, max_length=255)
    telefono: str | None = Field(default=None, max_length=40)


@router.post("/customers/create", status_code=201)
def create_customer_endpoint(
    payload: CreateCustomerPayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Crea el cliente en F_CLI y lo vincula al CRM.

    Dos guards heredados del bug de C-2-fix1 (`BDEscribirRegistroError`):
    1. **Dedupe**: si ya existe un F_CLI con ese NIF, NO escribe — devuelve el
       CODCLI existente y lo vincula (`created=False`).
    2. **Origen Woo**: si el cliente tiene pedidos de WooCommerce, el cliente lo
       gestiona la app externa Woo→FACTUSOL → 409, solo se vincula.
    """
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.customers import (  # noqa: PLC0415
        create_customer,
        link_to_crm,
    )

    _reject_if_woo_managed(session, payload.crm_type, payload.crm_id)
    client, ejercicio = _client_and_ejercicio(session)
    try:
        codcli, created = create_customer(
            client, payload.model_dump(), ejercicio=ejercicio,
        )
    except FactusolError as exc:
        logger.warning("factusol customers/create KO: %s", exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "factusol_create_failed", "detail": str(exc)[:300],
        }) from exc
    try:
        link_to_crm(session, crm_type=payload.crm_type,
                    crm_id=payload.crm_id, codcli=codcli)
    except FactusolError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "already_linked", "detail": str(exc)[:300],
        }) from exc
    _audit_customer_link(session, current_user, payload.crm_type,
                         payload.crm_id, codcli, created=created)
    session.commit()
    return {"factusol_codcli": codcli, "created": created,
            "crm_type": payload.crm_type, "crm_id": payload.crm_id}


def _reject_if_woo_managed(session: Session, crm_type: str, crm_id: str) -> None:
    """En los pedidos de WooCommerce el cliente lo crea la app externa
    Woo→FACTUSOL; si el CRM lo crease también reproduciría el
    `BDEscribirRegistroError` de C-2-fix1. Ahí solo se vincula."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.erp.models import Order, OrderSource  # noqa: PLC0415

    column = Order.company_id if crm_type == "company" else Order.contact_id
    has_woo = session.scalar(
        select(Order.id).where(
            column == crm_id,
            Order.external_source == OrderSource.WOOCOMMERCE.value,
        ).limit(1)
    )
    if has_woo:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "woo_managed_customer",
            "detail": (
                "Este cliente tiene pedidos de WooCommerce: lo crea la app "
                "Woo→FACTUSOL. Búscalo en FACTUSOL y vincúlalo en vez de crearlo."
            ),
        })


def _audit_customer_link(
    session: Session, actor: User, crm_type: str, crm_id: str,
    codcli: str, *, created: bool | None = None,
) -> None:
    from app.models.crm import AuditLog  # noqa: PLC0415

    session.add(AuditLog(
        actor_user_id=actor.id,
        action="erp.factusol_customer_link",
        target_type=crm_type,
        target_id=crm_id,
        metadata_json=json.dumps({
            "factusol_codcli": codcli,
            **({"created_in_factusol": created} if created is not None else {}),
        }),
    ))


class CreateCrmAndLinkPayload(BaseModel):
    """Datos del cliente F_CLI con los que se crea la empresa CRM."""

    nombre: str = Field(min_length=1, max_length=255)
    nif: str = Field(default="", max_length=64)
    direccion: str = Field(default="", max_length=500)
    ciudad: str = Field(default="", max_length=200)
    cp: str = Field(default="", max_length=20)
    provincia: str = Field(default="", max_length=200)
    telefono: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=255)
    # ERP-F1-fix2: país del cliente en FACTUSOL (PAICLI, ISO numérico o
    # nombre). Se normaliza a ISO2; si no se reconoce, se deja vacío — nunca
    # España por defecto.
    pais: str | None = Field(default=None, max_length=64)


class CreateCrmAndLinkIn(BaseModel):
    factusol_codcli: str = Field(min_length=1, max_length=36)
    factusol_customer_data: CreateCrmAndLinkPayload


@router.post("/customers/create-crm-and-link", status_code=201)
def create_crm_and_link(
    payload: CreateCrmAndLinkIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Crea la empresa CRM a partir de un cliente F_CLI **y la vincula, en una
    sola transacción** (C-3-fix3).

    Antes el frontend hacía dos llamadas (crear empresa → vincular). Si la
    segunda fallaba —p. ej. porque el CODCLI ya estaba vinculado— la empresa
    quedaba creada y **huérfana**: en producción aparecieron 3 empresas
    duplicadas de un mismo cliente por reintentos. Aquí se comprueba el
    vínculo ANTES de crear nada y todo va en una transacción: o hay empresa
    vinculada, o no hay empresa.
    """
    from app.erp.language import normalize_country  # noqa: PLC0415
    from app.integrations.factusol.customers import crm_links_for  # noqa: PLC0415
    from app.models.crm import Company  # noqa: PLC0415

    codcli = payload.factusol_codcli.strip()
    # 1) ¿Ya está vinculado? → 409 SIN crear nada.
    taken = crm_links_for(session, [codcli]).get(codcli)
    if taken:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "already_linked",
            "detail": (
                f"El cliente FACTUSOL {codcli} ya está vinculado a "
                f"{taken['type']} «{taken['name']}» (id: {taken['id']}). "
                "Ve a esa ficha para gestionarlo."
            ),
        })

    data = payload.factusol_customer_data
    company = Company(
        name=data.nombre.strip(),
        tax_id=data.nif.strip() or None,
        address_line=data.direccion.strip() or None,
        city=data.ciudad.strip() or None,
        postal_code=data.cp.strip() or None,
        state=data.provincia.strip() or None,
        # ERP-F1-fix2: país REAL normalizado a ISO2; None si no se reconoce o
        # no viene (nunca España por defecto).
        country=normalize_country(data.pais),
        source="factusol",
        factusol_company_id=codcli,
        factusol_sync_source="erp_link",
    )
    try:
        # 2) Empresa + vínculo en la MISMA transacción: si algo falla, rollback
        # y no queda ninguna empresa huérfana.
        session.add(company)
        session.flush()
        _audit_customer_link(session, current_user, "company", company.id,
                             codcli, created=True)
        session.commit()
    except Exception as exc:  # noqa: BLE001 — integridad, BD caída, etc.
        session.rollback()
        logger.warning("factusol create-crm-and-link KO (rollback): %s", exc)
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "create_and_link_failed",
            "detail": f"No se pudo crear y vincular la empresa: {str(exc)[:200]}",
        }) from exc
    return {"company_id": company.id, "factusol_codcli": codcli, "created": True}


# --- proformas / presupuestos F_PRE (Fase C · C-4) ---------------------------
#
# F_PRE es MONO-LÍNEA (653 presupuestos verificados en la base real): cada fila
# es un presupuesto completo y no hay tabla de líneas. El desglose de las
# proformas que crea el CRM se guarda aparte, en `factusol_quote_lines_cache`.
# Ver `app/integrations/factusol/quotes.py` y `docs/erp/factusol-proformas.md`.


def _factusol_gateway_error(exc: Exception, code: str) -> HTTPException:
    logger.warning("factusol %s KO: %s", code, exc)
    return HTTPException(status.HTTP_502_BAD_GATEWAY, {
        "code": code, "detail": str(exc)[:300],
    })


@router.get("/articles/search")
def search_articles_endpoint(
    q: str = Query(..., min_length=1),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Busca artículos en F_ART por sus 6 identificadores.

    `precio_venta` viene de **F_LTA** (tarifa 1), no de F_ART: en F_ART solo
    está `PCOART`, que es el coste. `precio_venta_source` deja constancia de
    dónde salió cada precio; `null` = ese artículo no tiene tarifa 1
    configurada y el operador teclea el importe."""
    _ = current_user
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.quotes import search_articles  # noqa: PLC0415

    client, ejercicio = _client_and_ejercicio(session)
    try:
        items = search_articles(client, q, ejercicio=ejercicio)
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_article_search_failed") from exc
    return {"items": items, "ejercicio": ejercicio}


@router.get("/quotes")
def list_quotes_endpoint(
    company_id: str | None = Query(default=None),
    days_back: int = Query(default=180, ge=0, le=1825),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Proformas de una empresa del CRM (por su CODCLI vinculado).

    Sin `company_id` lista las de todos los clientes. Si la empresa existe pero
    **no está vinculada** a FACTUSOL devuelve lista vacía con
    `unlinked=True` — no es un error, es que aún no hay nada que enseñar."""
    _ = current_user
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.quotes import list_quotes  # noqa: PLC0415
    from app.models.crm import Company  # noqa: PLC0415

    codcli: str | None = None
    if company_id:
        company = session.get(Company, company_id)
        if company is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, {
                "code": "company_not_found", "detail": "La empresa no existe.",
            })
        if not company.factusol_company_id:
            return {"items": [], "unlinked": True, "ejercicio": None}
        codcli = str(company.factusol_company_id)

    client, ejercicio = _client_and_ejercicio(session)
    try:
        items = list_quotes(client, ejercicio=ejercicio, codcli=codcli,
                            days_back=days_back)
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_quotes_failed") from exc
    return {"items": items, "unlinked": False, "ejercicio": ejercicio}


@router.get("/quotes/search")
def search_quotes_endpoint(
    q: str = Query(default="", max_length=120),
    days_back: int = Query(default=365, ge=0, le=1825),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Busca proformas de **cualquier cliente** para usarlas como plantilla.

    A diferencia de `GET /quotes`, aquí NO se filtra por empresa: el 80 % de las
    plantillas que reutiliza Bomedia son de otro cliente parecido («duplicar la
    de Laboratorios Duaner para Laboratorios Porta»). El texto casa contra
    referencia, nombre del cliente de origen y número de proforma.

    Se declara ANTES de `/quotes/{codpre}`: FastAPI casa por orden y «search»
    encajaría como CODPRE."""
    _ = current_user
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.quotes import list_quotes  # noqa: PLC0415

    client, ejercicio = _client_and_ejercicio(session)
    try:
        items = list_quotes(
            client, ejercicio=ejercicio, codcli=None, days_back=days_back,
            text=q, limit=limit,
        )
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_quotes_search_failed") from exc
    return {"items": items, "ejercicio": ejercicio}


@router.get("/quotes/status/{job_id}")
def quote_job_status(
    job_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Estado del job de proforma para el polling del frontend.

    Va ANTES de `/quotes/{codpre}` a propósito: FastAPI casa las rutas por
    orden de declaración y `status` encajaría en `{codpre}`."""
    _ = current_user, session
    return _rq_quote_status(job_id)


def _rq_quote_status(job_id: str) -> dict[str, Any]:
    """Estado del job RQ (best-effort). Sin Redis (local/tests) → `pending`."""
    try:
        from redis import Redis  # noqa: PLC0415
        from rq.job import Job  # noqa: PLC0415

        from app.core.config import get_settings  # noqa: PLC0415

        conn = Redis.from_url(get_settings().redis_url)
        job = Job.fetch(job_id, connection=conn)
        rq_status = job.get_status(refresh=True)
        if rq_status == "failed":
            return _failed_status(job.exc_info or "")
        if rq_status == "finished":
            return {"status": "finished", "result": job.result}
        return {"status": "pending"}
    except Exception as exc:  # noqa: BLE001 — sin Redis o job caducado
        logger.debug("factusol quote job %s no consultable: %s", job_id, exc)
        return {"status": "pending"}


#: Marcador del rechazo por estado dentro del traceback que guarda RQ.
_NOT_EDITABLE_MARKER = "QuoteNotEditableError"


def _failed_status(exc_info: str) -> dict[str, Any]:
    """Traduce el fallo de un job a algo que el frontend pueda tratar.

    El PATCH responde 202, así que el rechazo por estado («la proforma está
    aceptada») llega por aquí y no como un 409. Se marca con un `code` propio
    para que la UI pueda ofrecer «Editar de todos modos» en vez de enseñar un
    error genérico — y NO lo haga cuando el fallo es otra cosa.
    """
    if _NOT_EDITABLE_MARKER in exc_info:
        # Última línea del traceback: «…QuoteNotEditableError: <mensaje>».
        last = exc_info.strip().splitlines()[-1]
        detail = last.split(": ", 1)[1] if ": " in last else last
        return {"status": "failed", "code": "quote_not_editable",
                "error": detail}
    return {"status": "failed", "error": (exc_info or "la operación falló")[-400:]}


@router.get("/quotes/{codpre}")
def get_quote_endpoint(
    codpre: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_view),
) -> dict[str, Any]:
    """Una proforma con su desglose.

    `line_source` = `cache` (la creó el CRM, hay líneas reales) o `ref_text`
    (se creó en el escritorio FACTUSOL: solo existe el texto de REFPRE)."""
    _ = current_user
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.quotes import get_quote  # noqa: PLC0415

    client, ejercicio = _client_and_ejercicio(session)
    try:
        quote = get_quote(client, session, codpre, ejercicio=ejercicio)
    except FactusolError as exc:
        raise _factusol_gateway_error(exc, "factusol_quote_failed") from exc
    if quote is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "quote_not_found",
            "detail": f"La proforma {codpre} no existe en el ejercicio {ejercicio}.",
        })
    return quote


class QuoteLineIn(BaseModel):
    codart: str = Field(default="", max_length=64)
    description: str = Field(default="", max_length=255)
    quantity: float = Field(default=1, gt=0)
    unit_price: float = Field(default=0, ge=0)
    discount_pct: float = Field(default=0, ge=0, le=100)
    iva_pct: float = Field(default=21, ge=0, le=100)


class QuoteAddressIn(BaseModel):
    """Dirección de envío elegida entre las que el cliente ya tiene en
    FACTUSOL. El CRM no las edita, solo escoge (C-4-fix6)."""

    direccion: str = Field(default="", max_length=255)
    ciudad: str = Field(default="", max_length=255)
    cp: str = Field(default="", max_length=20)
    provincia: str = Field(default="", max_length=255)
    pais: str = Field(default="", max_length=10)


class CreateQuotePayload(BaseModel):
    """Alta de proforma. El cliente sale de la empresa CRM vinculada; el
    operador solo elige líneas (o escribe una referencia libre)."""

    company_id: str = Field(min_length=1, max_length=36)
    #: «Su ref.» del documento: nº de pedido del cliente, obra, proyecto…
    referencia: str = Field(default="", max_length=250)
    lines: list[QuoteLineIn] = Field(default_factory=list)
    fecha: str | None = Field(default=None, max_length=10)
    fopfac: str | None = Field(default=None, max_length=10)
    #: Dirección alternativa; None → la de la empresa CRM.
    address: QuoteAddressIn | None = None


def _apply_address(
    customer: dict[str, Any], address: QuoteAddressIn | None,
) -> dict[str, Any]:
    """Sobrescribe la dirección del cliente con la elegida por el operador.

    Solo pisa los campos que vengan con valor: una dirección alternativa sin
    provincia no debe borrar la de la sede."""
    if address is None:
        return customer
    chosen = {k: v.strip() for k, v in address.model_dump().items() if v.strip()}
    return {**customer, **chosen}


def _customer_from_company(session: Session, company_id: str) -> dict[str, Any]:
    """Datos de cliente para la cabecera de F_PRE, tomados de la empresa CRM.
    409 si la empresa no está vinculada: sin CODCLI la proforma no tiene dueño
    en la contabilidad y acabaría huérfana."""
    from app.models.crm import Company  # noqa: PLC0415

    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "company_not_found", "detail": "La empresa no existe.",
        })
    if not company.factusol_company_id:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "company_not_linked",
            "detail": (
                f"«{company.name}» no está vinculada a un cliente de FACTUSOL. "
                "Vincúlala antes de crear la proforma."
            ),
        })
    return {
        "codcli": str(company.factusol_company_id),
        "nombre": company.name,
        "nif": company.tax_id or "",
        "direccion": company.address_line or "",
        "ciudad": company.city or "",
        "cp": company.postal_code or "",
        "provincia": company.state or "",
    }


@router.post("/quotes", status_code=202)
def create_quote_endpoint(
    payload: CreateQuotePayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Encola la creación de la proforma en `factusol:writes` (202 + job_id).

    Va por cola porque el CODPRE se calcula con `MAX+1` justo antes de escribir
    y el worker serializado (concurrency=1) es lo que impide que dos altas
    simultáneas se pisen la numeración."""
    from app.integrations.factusol.jobs import enqueue_create_quote  # noqa: PLC0415

    if not payload.lines and not payload.referencia.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {
            "code": "empty_quote",
            "detail": "Añade al menos una línea o escribe una referencia.",
        })
    customer = _apply_address(
        _customer_from_company(session, payload.company_id), payload.address,
    )
    job_id = enqueue_create_quote(
        customer,
        [line.model_dump() for line in payload.lines],
        payload.referencia.strip() or None,
        payload.fecha,
        payload.fopfac,
    )
    _audit_quote(session, current_user, "erp.factusol_quote_create",
                 payload.company_id, {"job_id": job_id,
                                      "lines": len(payload.lines)})
    session.commit()
    return {"job_id": job_id, "status": "queued"}


class UpdateQuotePayload(CreateQuotePayload):
    """Edición de proforma. Mismo cuerpo que el alta más `force`, que salta el
    guard de estado (ver `quotes.ESTPRE_PENDING`)."""

    force: bool = False


@router.patch("/quotes/{codpre}", status_code=202)
def update_quote_endpoint(
    codpre: str,
    payload: UpdateQuotePayload,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Encola la reescritura de la proforma (cabecera + líneas).

    El job comprueba `ESTPRE` antes de tocar nada: si la proforma no está
    pendiente, falla y el frontend ofrece reintentar con `force`. La
    comprobación va en el job, no aquí, porque entre el 202 y la escritura
    puede pasar tiempo — validarlo ahora no garantizaría nada."""
    from app.integrations.factusol.jobs import enqueue_update_quote  # noqa: PLC0415

    if not payload.lines and not payload.referencia.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {
            "code": "empty_quote",
            "detail": "Añade al menos una línea o escribe una referencia.",
        })
    customer = _apply_address(
        _customer_from_company(session, payload.company_id), payload.address,
    )
    job_id = enqueue_update_quote(
        codpre, customer, [line.model_dump() for line in payload.lines],
        payload.referencia.strip() or None, payload.force,
    )
    _audit_quote(session, current_user, "erp.factusol_quote_update", codpre,
                 {"job_id": job_id, "lines": len(payload.lines),
                  "force": payload.force})
    session.commit()
    return {"job_id": job_id, "status": "queued", "codpre": codpre}


@router.post("/quotes/{codpre}/duplicate", status_code=202)
def duplicate_quote_endpoint(
    codpre: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Encola la duplicación de una proforma (CODPRE nuevo, fecha de hoy)."""
    from app.integrations.factusol.jobs import enqueue_duplicate_quote  # noqa: PLC0415

    job_id = enqueue_duplicate_quote(codpre)
    _audit_quote(session, current_user, "erp.factusol_quote_duplicate",
                 codpre, {"job_id": job_id})
    session.commit()
    return {"job_id": job_id, "status": "queued", "source_codpre": codpre}


class ConvertQuotePayload(BaseModel):
    """Fase 2 — paso de pago (opción B) + albarán al convertir la proforma."""

    payment: PaymentIn | None = None
    create_albaran: bool = True


@router.post("/quotes/{codpre}/convert-to-order", status_code=202)
def convert_quote_endpoint(
    codpre: str,
    payload: ConvertQuotePayload | None = Body(default=None),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Encola la conversión de la proforma en pedido de BoHub y, Fase 2, en el
    mismo job del worker serial: apunta el pago (opción B: sin emitir
    factura; el cobro F-4-B se registra cuando exista) y crea el ALBARÁN en
    FACTUSOL (idempotente, guard de esquema). El paso de pago se valida aquí
    (400 si la cuenta no está en el catálogo)."""
    from app.erp.factusol_albaran import PaymentError, resolve_payment  # noqa: PLC0415
    from app.integrations.factusol.jobs import (  # noqa: PLC0415
        enqueue_convert_quote_to_order,
    )

    opts = payload or ConvertQuotePayload()
    resolved = None
    if opts.payment is not None:
        try:
            resolved = resolve_payment(session, opts.payment)
        except PaymentError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, {
                "code": exc.code, "detail": str(exc),
            }) from exc
    job_id = enqueue_convert_quote_to_order(
        codpre, current_user.id, payment=resolved,
        create_albaran=opts.create_albaran,
    )
    _audit_quote(session, current_user, "erp.factusol_quote_convert",
                 codpre, {"job_id": job_id, "payment": resolved,
                          "create_albaran": opts.create_albaran})
    session.commit()
    return {"job_id": job_id, "status": "queued", "codpre": codpre}


def _audit_quote(
    session: Session, actor: User, action: str, target_id: str,
    metadata: dict[str, Any],
) -> None:
    from app.models.crm import AuditLog  # noqa: PLC0415

    session.add(AuditLog(
        actor_user_id=actor.id, action=action,
        target_type="factusol_quote", target_id=target_id,
        metadata_json=json.dumps(metadata),
    ))
