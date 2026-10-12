"""Ofertas: presupuestar a un lead sin tocar FACTUSOL.

Un presupuesto de FACTUSOL (`F_PRE`) necesita un cliente con datos fiscales;
un lead no los tiene. La OFERTA es un presupuesto que vive en BoHub:

1. Una PLANTILLA se guarda desde un presupuesto existente (sus líneas con los
   artículos de FACTUSOL, descripción, cantidades y precios) — solo LECTURA de
   `F_PRE`/`F_LPS`.
2. La oferta se crea desde una plantilla para un contacto (empresa de BoHub si
   la tiene, serie emisora, idioma, líneas, validez) sin escribir nada en
   FACTUSOL. Numeración propia `OF-2026-0001` con contador por año.
3. Se edita aunque esté enviada: al editar, la fecha pasa a hoy y la validez se
   recalcula desde ahí, salvo que se cambie la fecha a mano. Cada envío queda
   en el historial (`ofertas_envios`) con la versión que se mandó.
4. El PDF sale del maquetador de los presupuestos (variante `oferta`): idioma
   del contacto, con o sin IVA a elección, aviso fijo de VIES y de que no es
   una factura; el régimen definitivo se fija al convertir.
5. Se envía por el camino de siempre (`gmail.service.send_email`, PDF adjunto);
   nada se envía solo. Al enviar: estado `enviada`, fecha, contacto a
   «Propuesta enviada» y el hecho `oferta.enviada` para los workflows.
6. «Convertir en presupuesto» es el ÚNICO sitio de este módulo que escribe en
   FACTUSOL (`convertir`, que corre en el worker serializado `factusol:writes`
   y solo lo lanza una persona). Hace falta empresa vinculada (`company_not_
   linked` → el flujo de #502) y todas las líneas con artículo.
7. Caducidad: una enviada con la validez pasada es `caducada`; el job diario
   lo deja escrito y emite `oferta.caducada`. Mover a «Cerrado perdido» es
   cosa del motor de pipeline (Fase 3), que consume ese hecho.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.erp.language import SUPPORTED_LANGS, language_for_country, normalize_language
from app.erp.models.ofertas import (
    ESTADO_BORRADOR,
    ESTADO_CADUCADA,
    ESTADO_CONVERTIDA,
    ESTADO_ENVIADA,
    Oferta,
    OfertaContador,
    OfertaEnvio,
    OfertaLinea,
    OfertaPlantilla,
    OfertaPlantillaLinea,
)
from app.models.crm import Company, Contact, User

logger = logging.getLogger(__name__)

#: Clave del blob `factusol_series_json` con la configuración de las ofertas.
CONFIG_KEY = "ofertas"
PREFIJO_DEFECTO = "OF"
VALIDEZ_DEFECTO = 30
VALIDEZ_MIN = 1
VALIDEZ_MAX = 365
#: Series emisoras que admite una oferta (las mismas que una proforma a mano).
SERIES_OFERTA: tuple[int, ...] = (1, 2, 4, 5)
ESTADOS: tuple[str, ...] = (ESTADO_BORRADOR, ESTADO_ENVIADA, ESTADO_CADUCADA, ESTADO_CONVERTIDA)
#: Tope de BoHub para la descripción de una línea (el de las proformas).
DESCRIPCION_MAX = 2000
#: Variante del maquetador de PDF.
VARIANTE_PDF = "oferta"
#: Eventos de auditoría (timeline del contacto / de la oferta).
EVENTO_CREADA = "erp.oferta_created"
EVENTO_EDITADA = "erp.oferta_updated"
EVENTO_ENVIADA = "erp.oferta_emailed"
EVENTO_CONVERTIDA = "erp.oferta_converted"
EVENTO_CADUCADA = "erp.oferta_expired"
TARGET_OFERTA = "oferta"
#: Hechos para los workflows (`dispatch_event`).
TRIGGER_ENVIADA = "oferta.enviada"
TRIGGER_CADUCADA = "oferta.caducada"
#: Etapas del pipeline a las que pasa el contacto al enviar (por nombre, sin
#: distinguir mayúsculas): la de la plantilla «Ventas B2B» y su equivalente.
ETAPAS_PROPUESTA: tuple[str, ...] = ("propuesta enviada", "oferta enviada")

_LANG_NAMES = {"es": "español", "en": "inglés", "de": "alemán",
               "fr": "francés", "nl": "neerlandés"}

#: Plantillas del correo (asunto y cuerpo) por idioma, cuando la IA no propone
#: el cuerpo. Marcadores: {numero}, {empresa}, {contacto}, {total}, {fecha},
#: {validez} (la frase del PDF) y {firma} (la empresa emisora).
OFERTA_EMAIL_DEFAULTS: dict[str, dict[str, str]] = {
    "es": {
        "subject": "Oferta {numero}",
        "body": "Hola {contacto}:\n\nTe adjunto la oferta {numero} de fecha {fecha} "
                "por un importe de {total}.\n{validez}\n\n"
                "Quedo a tu disposición para cualquier duda.\n\nUn saludo,\n{firma}",
    },
    "en": {
        "subject": "Offer {numero}",
        "body": "Dear {contacto},\n\nPlease find attached our offer {numero} dated "
                "{fecha} for a total of {total}.\n{validez}\n\n"
                "We remain at your disposal for any questions.\n\nKind regards,\n{firma}",
    },
    "de": {
        "subject": "Angebot {numero}",
        "body": "Sehr geehrte/r {contacto},\n\nanbei unser Angebot {numero} vom {fecha} "
                "über {total}.\n{validez}\n\n"
                "Bei Fragen stehen wir Ihnen gerne zur Verfügung.\n\n"
                "Mit freundlichen Grüßen,\n{firma}",
    },
    "fr": {
        "subject": "Offre {numero}",
        "body": "Bonjour {contacto},\n\nVeuillez trouver ci-joint notre offre {numero} "
                "du {fecha} d'un montant de {total}.\n{validez}\n\n"
                "Nous restons à votre disposition pour toute question.\n\n"
                "Cordialement,\n{firma}",
    },
    "nl": {
        "subject": "Offerte {numero}",
        "body": "Beste {contacto},\n\nBijgevoegd vindt u onze offerte {numero} van "
                "{fecha} voor een bedrag van {total}.\n{validez}\n\n"
                "Wij staan tot uw beschikking voor eventuele vragen.\n\n"
                "Met vriendelijke groet,\n{firma}",
    },
}


class OfertaError(ValueError):
    """Un rechazo con código (`code`) y, si aplica, datos (`extra`) para que
    la API lo devuelva como 409/422 y la pantalla sepa qué ofrecer."""

    def __init__(self, code: str, mensaje: str, *, extra: dict[str, Any] | None = None,
                 status: int = 422) -> None:
        super().__init__(mensaje)
        self.code = code
        self.extra = extra or {}
        self.status = status


# --- configuración ------------------------------------------------------------


def _prefijo(raw: Any) -> str | None:
    texto = str(raw or "").strip().upper()
    if not texto or len(texto) > 6 or not texto.replace("-", "").isalnum():
        return None
    return texto.rstrip("-")


def _validez(raw: Any) -> int | None:
    try:
        dias = int(raw)
    except (TypeError, ValueError):
        return None
    return dias if VALIDEZ_MIN <= dias <= VALIDEZ_MAX else None


def normalizar_config(raw: Any) -> dict[str, Any]:
    """`{"prefijo": "OF", "validez_dias": 30}` con defaults; tolera basura."""
    data = raw if isinstance(raw, dict) else {}
    return {
        "prefijo": _prefijo(data.get("prefijo")) or PREFIJO_DEFECTO,
        "validez_dias": _validez(data.get("validez_dias")) or VALIDEZ_DEFECTO,
    }


def validar_config(payload: Any, actual: Any = None) -> dict[str, Any]:
    """Lo que llega del PATCH de Configuración ERP, fundido con lo guardado.
    `ValueError` legible si algo no vale."""
    if not isinstance(payload, dict):
        raise ValueError("La configuración de ofertas no es válida.")
    base = normalizar_config(actual)
    if "prefijo" in payload:
        prefijo = _prefijo(payload["prefijo"])
        if prefijo is None:
            raise ValueError("Prefijo de las ofertas no válido: letras o números, 6 como mucho.")
        base["prefijo"] = prefijo
    if "validez_dias" in payload:
        dias = _validez(payload["validez_dias"])
        if dias is None:
            raise ValueError(f"Validez por defecto: entre {VALIDEZ_MIN} y {VALIDEZ_MAX} días.")
        base["validez_dias"] = dias
    return base


def configuracion(session: Session) -> dict[str, Any]:
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    return normalizar_config(series_config(session).get(CONFIG_KEY))


def nombre_emisora(session: Session, serie: int) -> str:
    from app.erp.factusol_albaran import MANUAL_SERIE_NAMES  # noqa: PLC0415
    from app.integrations.factusol.service import series_names  # noqa: PLC0415

    return str(series_names(session).get(int(serie)) or MANUAL_SERIE_NAMES.get(int(serie))
               or f"Serie {serie}")


# --- numeración ---------------------------------------------------------------


def siguiente_numero(session: Session, *, fecha: date, prefijo: str) -> tuple[str, int, int]:
    """`("OF-2026-0001", 2026, 1)`: el contador del año, bloqueado hasta el
    commit (en MySQL; SQLite lo ignora) para que dos altas a la vez no se
    pisen. Si Bart prefiere otro formato, es un ajuste de aquí."""
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

    anio = fecha.year
    fila = session.get(OfertaContador, anio, with_for_update=True)
    if fila is None:
        # La primera del año: dos altas a la vez insertan las dos el contador
        # y una pierde en la clave primaria. En un SAVEPOINT, para que la que
        # pierde vuelva a leer (ya bloqueado) en vez de morir con un 500.
        try:
            with session.begin_nested():
                session.add(OfertaContador(anio=anio, ultimo=0))
        except IntegrityError:
            pass
        fila = session.get(OfertaContador, anio, with_for_update=True)
        if fila is None:  # pragma: no cover — solo si la BD miente
            raise OfertaError("contador", "No se pudo abrir el contador de ofertas del año.")
    fila.ultimo = int(fila.ultimo or 0) + 1
    session.flush()
    return f"{prefijo}-{anio}-{fila.ultimo:04d}", anio, fila.ultimo


# --- líneas y totales -----------------------------------------------------------

_DOS = Decimal("0.01")


def _dec(value: Any, defecto: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(defecto)
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError, TypeError):
        return Decimal(defecto)


def _redondear(value: Decimal) -> Decimal:
    return value.quantize(_DOS, rounding=ROUND_HALF_UP)


def _f(value: Any) -> float:
    return float(_dec(value))


def normalizar_lineas(raw: list[Any] | None) -> list[dict[str, Any]]:
    """Las líneas tal como llegan (de la API, de una plantilla o de un
    presupuesto) a su forma limpia: `codart`, `sku`, `descripcion`, `cantidad`,
    `precio`, `descuento_pct`, `iva_pct`, `total` (sin IVA) y `text_only`.
    `OfertaError('lineas_invalidas')` por un valor fuera de rango."""
    salida: list[dict[str, Any]] = []
    for i, item in enumerate(raw or [], start=1):
        if not isinstance(item, dict):
            raise OfertaError("lineas_invalidas", f"La línea {i} no es válida.")
        descripcion = str(item.get("descripcion") or item.get("description") or "")
        descripcion = descripcion.replace("\r\n", "\n").strip()
        if len(descripcion) > DESCRIPCION_MAX:
            raise OfertaError(
                "lineas_invalidas",
                f"La descripción de la línea {i} pasa de {DESCRIPCION_MAX} caracteres.")
        codart = str(item.get("codart") or "").strip()[:64] or None
        sku = str(item.get("sku") or "").strip()[:64] or None
        cantidad = _dec(item.get("cantidad", item.get("quantity", 1)), "1")
        precio = _dec(item.get("precio", item.get("unit_price", 0)))
        descuento = _dec(item.get("descuento_pct", item.get("discount_pct", 0)))
        iva = _dec(item.get("iva_pct", 21), "21")
        if cantidad < 0 or precio < 0:
            raise OfertaError("lineas_invalidas",
                              f"La línea {i} lleva cantidad o precio negativos.")
        if not (0 <= descuento <= 100) or not (0 <= iva <= 100):
            raise OfertaError("lineas_invalidas",
                              f"La línea {i} lleva un porcentaje fuera de 0-100.")
        text_only = codart is None and cantidad == 0 and precio == 0
        if not descripcion and not codart:
            raise OfertaError("lineas_invalidas", f"La línea {i} no tiene ni artículo ni texto.")
        total = _redondear(cantidad * precio * (1 - descuento / 100))
        salida.append({
            "codart": codart, "sku": sku, "descripcion": descripcion,
            "cantidad": cantidad, "precio": precio, "descuento_pct": descuento,
            "iva_pct": iva, "total": total, "text_only": text_only,
        })
    return salida


def totales(lineas: list[dict[str, Any]], *, con_iva: bool) -> dict[str, Decimal]:
    """Base (suma de líneas sin IVA), tipo, IVA y total. Como en F_PRE (una
    sola banda), el tipo es el de la primera línea con importe; si hay varios
    se aplica ese y queda en el log. Sin IVA: tipo 0 e IVA 0."""
    base = _redondear(sum((ln["total"] for ln in lineas), Decimal("0")))
    con_importe = [ln for ln in lineas if not ln.get("text_only")]
    tipos = {ln["iva_pct"] for ln in con_importe}
    iva_pct = con_importe[0]["iva_pct"] if con_importe else Decimal("21")
    if len(tipos) > 1:
        logger.warning("ofertas: varios tipos de IVA %s; se aplica %s a toda la base",
                       sorted(tipos), iva_pct)
    if not con_iva:
        return {"base": base, "iva_pct": Decimal("0"), "iva": Decimal("0"), "total": base}
    iva = _redondear(base * iva_pct / 100)
    return {"base": base, "iva_pct": iva_pct, "iva": iva, "total": _redondear(base + iva)}


def _volcar_lineas(oferta: Oferta, lineas: list[dict[str, Any]]) -> None:
    oferta.lineas.clear()
    for pos, ln in enumerate(lineas, start=1):
        oferta.lineas.append(OfertaLinea(
            posicion=pos, codart=ln["codart"], sku=ln["sku"], descripcion=ln["descripcion"],
            cantidad=ln["cantidad"], precio=ln["precio"], descuento_pct=ln["descuento_pct"],
            iva_pct=ln["iva_pct"], total=ln["total"],
        ))


def _aplicar_totales(oferta: Oferta, lineas: list[dict[str, Any]]) -> None:
    t = totales(lineas, con_iva=bool(oferta.con_iva))
    oferta.base, oferta.iva_pct, oferta.iva, oferta.total = (
        t["base"], t["iva_pct"], t["iva"], t["total"])


def _lineas_de(filas: list[Any]) -> list[dict[str, Any]]:
    """Las líneas guardadas (de una plantilla o de una oferta) como dicts."""
    return normalizar_lineas([{
        "codart": ln.codart, "sku": ln.sku, "descripcion": ln.descripcion,
        "cantidad": ln.cantidad, "precio": ln.precio, "descuento_pct": ln.descuento_pct,
        "iva_pct": ln.iva_pct,
    } for ln in filas])


# --- plantillas -----------------------------------------------------------------


def lineas_de_presupuesto(
    client: Any, session: Session, *, serie: int, codpre: int, ejercicio: str,
) -> list[dict[str, Any]]:
    """Las líneas REALES de un presupuesto de FACTUSOL (`F_LPS`, solo lectura)
    con su `codart` (interno) y su `sku` (comercial). `OfertaError('quote_not_
    found')` si no existe."""
    from app.integrations.factusol.quotes import get_quote  # noqa: PLC0415

    quote = get_quote(client, session, str(codpre), ejercicio=ejercicio, serie=serie)
    if quote is None:
        raise OfertaError("quote_not_found", f"No existe el presupuesto {serie}-{codpre}.",
                          status=404)
    lineas = []
    for ln in quote.get("lines") or []:
        lineas.append({
            "codart": ln.get("codart"), "sku": ln.get("sku"),
            "descripcion": ln.get("description") or "",
            "cantidad": ln.get("quantity"), "precio": ln.get("unit_price"),
            "descuento_pct": ln.get("discount_pct"), "iva_pct": ln.get("iva_pct"),
        })
    return normalizar_lineas(lineas)


def crear_plantilla(
    session: Session, *, nombre: str, lineas: list[dict[str, Any]], usuario: User | None,
    descripcion: str | None = None, origen_serie: int | None = None,
    origen_codpre: int | None = None, activa: bool = True,
) -> OfertaPlantilla:
    nombre = str(nombre or "").strip()
    if not nombre:
        raise OfertaError("nombre_requerido", "La plantilla necesita un nombre.")
    if not lineas:
        raise OfertaError("sin_lineas", "La plantilla necesita al menos una línea.")
    plantilla = OfertaPlantilla(
        nombre=nombre[:160], descripcion=(descripcion or "").strip() or None, activa=activa,
        origen_serie=origen_serie, origen_codpre=origen_codpre,
        created_by_user_id=usuario.id if usuario is not None else None,
    )
    for pos, ln in enumerate(lineas, start=1):
        plantilla.lineas.append(OfertaPlantillaLinea(
            posicion=pos, codart=ln["codart"], sku=ln["sku"], descripcion=ln["descripcion"],
            cantidad=ln["cantidad"], precio=ln["precio"], descuento_pct=ln["descuento_pct"],
            iva_pct=ln["iva_pct"],
        ))
    session.add(plantilla)
    session.flush()
    return plantilla


def plantilla_desde_presupuesto(
    session: Session, client: Any, *, serie: int, codpre: int, ejercicio: str,
    nombre: str, usuario: User | None, descripcion: str | None = None,
) -> OfertaPlantilla:
    """«Guardar como plantilla de oferta» sobre un presupuesto existente: copia
    sus líneas con sus artículos. Solo LEE de FACTUSOL."""
    lineas = lineas_de_presupuesto(client, session, serie=serie, codpre=codpre,
                                   ejercicio=ejercicio)
    return crear_plantilla(
        session, nombre=nombre, lineas=lineas, usuario=usuario, descripcion=descripcion,
        origen_serie=int(serie), origen_codpre=int(codpre),
    )


def actualizar_plantilla(
    session: Session, plantilla: OfertaPlantilla, *, nombre: str | None = None,
    descripcion: str | None = None, activa: bool | None = None,
    lineas: list[dict[str, Any]] | None = None,
) -> OfertaPlantilla:
    if nombre is not None:
        if not nombre.strip():
            raise OfertaError("nombre_requerido", "La plantilla necesita un nombre.")
        plantilla.nombre = nombre.strip()[:160]
    if descripcion is not None:
        plantilla.descripcion = descripcion.strip() or None
    if activa is not None:
        plantilla.activa = bool(activa)
    if lineas is not None:
        if not lineas:
            raise OfertaError("sin_lineas", "La plantilla necesita al menos una línea.")
        plantilla.lineas.clear()
        for pos, ln in enumerate(lineas, start=1):
            plantilla.lineas.append(OfertaPlantillaLinea(
                posicion=pos, codart=ln["codart"], sku=ln["sku"],
                descripcion=ln["descripcion"], cantidad=ln["cantidad"], precio=ln["precio"],
                descuento_pct=ln["descuento_pct"], iva_pct=ln["iva_pct"],
            ))
    session.flush()
    return plantilla


def _linea_dict(ln: Any, *, con_total: bool = False) -> dict[str, Any]:
    out = {
        "id": ln.id, "posicion": ln.posicion, "codart": ln.codart, "sku": ln.sku,
        "descripcion": ln.descripcion, "cantidad": _f(ln.cantidad), "precio": _f(ln.precio),
        "descuento_pct": _f(ln.descuento_pct), "iva_pct": _f(ln.iva_pct),
        "text_only": ln.codart is None and _f(ln.cantidad) == 0 and _f(ln.precio) == 0,
    }
    if con_total:
        out["total"] = _f(ln.total)
    return out


def plantilla_como_dict(p: OfertaPlantilla, *, con_lineas: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": p.id, "nombre": p.nombre, "descripcion": p.descripcion, "activa": bool(p.activa),
        "origen_serie": p.origen_serie, "origen_codpre": p.origen_codpre,
        "origen_numero": (f"{p.origen_serie}-{int(p.origen_codpre):06d}"
                          if p.origen_serie and p.origen_codpre else None),
        "num_lineas": len(p.lineas),
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }
    if con_lineas:
        out["lineas"] = [_linea_dict(ln) for ln in p.lineas]
    return out


def listar_plantillas(session: Session, *, solo_activas: bool = False) -> list[OfertaPlantilla]:
    stmt = select(OfertaPlantilla).order_by(OfertaPlantilla.activa.desc(),
                                           OfertaPlantilla.nombre.asc())
    if solo_activas:
        stmt = stmt.where(OfertaPlantilla.activa.is_(True))
    return list(session.scalars(stmt))


# --- crear y editar --------------------------------------------------------------


def _nombre_contacto(contacto: Contact | None) -> str:
    if contacto is None:
        return ""
    return " ".join(p for p in (contacto.first_name, contacto.last_name) if p).strip()


def idioma_para(session: Session, contacto: Contact | None, company: Company | None,
                serie: int) -> tuple[str, str]:
    """`(idioma, fuente)`: el del contacto, el de su empresa, el del país de
    cualquiera de los dos, el de la emisora, español. Mismo espíritu que la
    cascada de los PDF (#531)."""
    from app.erp.factusol_pdf import company_for_serie  # noqa: PLC0415

    if contacto is not None:
        idioma = normalize_language(contacto.language)
        if idioma:
            return idioma, "contacto"
    if company is not None:
        idioma = normalize_language(company.language)
        if idioma:
            return idioma, "empresa"
    for pais, fuente in ((getattr(contacto, "address_country", None), "pais_contacto"),
                         (getattr(company, "country", None), "pais_empresa")):
        derivado = language_for_country(pais) if pais else None
        if derivado in SUPPORTED_LANGS:
            return derivado, fuente
    emisora = str(company_for_serie(session, int(serie)).get("idioma_defecto") or "").lower()
    if emisora in SUPPORTED_LANGS:
        return emisora, "emisora"
    return "es", "defecto"


def _comprobar_serie(serie: Any) -> int:
    try:
        valor = int(serie)
    except (TypeError, ValueError):
        valor = 0
    if valor not in SERIES_OFERTA:
        raise OfertaError("serie_invalida",
                          f"La serie {serie!r} no es una empresa emisora válida "
                          f"({', '.join(str(s) for s in SERIES_OFERTA)}).")
    return valor


def _comprobar_idioma(idioma: Any) -> str:
    valor = normalize_language(idioma)
    if not valor:
        raise OfertaError("idioma_invalido", f"Idioma no soportado: {idioma!r}.")
    return valor


def _comprobar_validez(dias: Any) -> int:
    valor = _validez(dias)
    if valor is None:
        raise OfertaError("validez_invalida",
                          f"La validez tiene que estar entre {VALIDEZ_MIN} y {VALIDEZ_MAX} días.")
    return valor


def _hoy() -> date:
    return datetime.now(UTC).date()


def crear_oferta(
    session: Session, *, contacto: Contact, serie: int, usuario: User | None,
    plantilla: OfertaPlantilla | None = None, lineas: list[dict[str, Any]] | None = None,
    idioma: str | None = None, con_iva: bool = True, validez_dias: int | None = None,
    fecha: date | None = None, referencia: str | None = None, notas: str | None = None,
    hoy: date | None = None,
) -> Oferta:
    """La oferta nueva, sin tocar FACTUSOL. Las líneas salen de `lineas` (ya
    retocadas en pantalla) o, si no vienen, de la plantilla. Empresa: la del
    contacto, si la tiene."""
    serie = _comprobar_serie(serie)
    cfg = configuracion(session)
    hoy = hoy or _hoy()
    fecha = fecha or hoy
    validez = _comprobar_validez(validez_dias if validez_dias is not None else cfg["validez_dias"])
    if lineas is None:
        if plantilla is None:
            raise OfertaError("sin_lineas", "Elige una plantilla o añade al menos una línea.")
        lineas = _lineas_de(plantilla.lineas)
    if not lineas:
        raise OfertaError("sin_lineas", "La oferta necesita al menos una línea.")
    company = contacto.company if contacto.company_id else None
    if idioma:
        idioma_final = _comprobar_idioma(idioma)
    else:
        idioma_final, _ = idioma_para(session, contacto, company, serie)
    numero, anio, secuencia = siguiente_numero(session, fecha=fecha, prefijo=cfg["prefijo"])
    oferta = Oferta(
        numero=numero, anio=anio, secuencia=secuencia, contact_id=contacto.id,
        company_id=company.id if company is not None else None,
        plantilla_id=plantilla.id if plantilla is not None else None,
        serie=serie, idioma=idioma_final, con_iva=bool(con_iva), fecha=fecha,
        validez_dias=validez, valida_hasta=fecha + timedelta(days=validez),
        estado=ESTADO_BORRADOR, referencia=(referencia or "").strip()[:255] or None,
        notas=(notas or "").strip() or None, version=1,
        created_by_user_id=usuario.id if usuario is not None else None,
        updated_by_user_id=usuario.id if usuario is not None else None,
    )
    _volcar_lineas(oferta, lineas)
    _aplicar_totales(oferta, lineas)
    session.add(oferta)
    session.flush()
    _evento(session, oferta, EVENTO_CREADA, usuario,
            f"Oferta {oferta.numero} creada para {_nombre_contacto(contacto) or contacto.email}")
    return oferta


def actualizar_oferta(
    session: Session, oferta: Oferta, *, usuario: User | None,
    lineas: list[dict[str, Any]] | None = None, serie: int | None = None,
    idioma: str | None = None, con_iva: bool | None = None, validez_dias: int | None = None,
    fecha: date | None = None, referencia: str | None = None, notas: str | None = None,
    hoy: date | None = None,
) -> Oferta:
    """Editar (también una enviada). La fecha pasa a HOY y la validez se
    recalcula desde ahí, salvo que venga una fecha a mano. Una convertida no
    se toca. Una caducada que se edita vuelve a `borrador` (hay que volver a
    mandarla); una enviada sigue `enviada` y sube de versión."""
    if oferta.estado == ESTADO_CONVERTIDA:
        raise OfertaError("oferta_convertida",
                          f"La oferta {oferta.numero} ya está convertida en presupuesto y no "
                          "se edita.", status=409)
    hoy = hoy or _hoy()
    # Antes de mover fechas: ¿estaba caducada (marcada o por la validez)?
    era_caducada = (oferta.estado == ESTADO_CADUCADA
                    or estado_efectivo(oferta, hoy) == ESTADO_CADUCADA)
    if serie is not None:
        oferta.serie = _comprobar_serie(serie)
    if idioma is not None:
        oferta.idioma = _comprobar_idioma(idioma)
    if con_iva is not None:
        oferta.con_iva = bool(con_iva)
    if validez_dias is not None:
        oferta.validez_dias = _comprobar_validez(validez_dias)
    if referencia is not None:
        oferta.referencia = referencia.strip()[:255] or None
    if notas is not None:
        oferta.notas = notas.strip() or None
    # La fecha: la que se escribe a mano manda; si no, hoy.
    oferta.fecha = fecha or hoy
    oferta.valida_hasta = oferta.fecha + timedelta(days=int(oferta.validez_dias))
    if lineas is not None:
        if not lineas:
            raise OfertaError("sin_lineas", "La oferta necesita al menos una línea.")
        _volcar_lineas(oferta, lineas)
    _aplicar_totales(oferta, _lineas_de(oferta.lineas))
    if era_caducada:
        oferta.estado = ESTADO_BORRADOR
        oferta.caducada_en = None
    oferta.version = int(oferta.version or 1) + 1
    oferta.updated_by_user_id = usuario.id if usuario is not None else None
    session.flush()
    _evento(session, oferta, EVENTO_EDITADA, usuario,
            f"Oferta {oferta.numero} editada (versión {oferta.version})")
    return oferta


# --- estado y lectura -----------------------------------------------------------


def estado_efectivo(oferta: Oferta, hoy: date | None = None) -> str:
    """El estado que se enseña: una enviada con la validez pasada es
    `caducada` aunque el job diario aún no la haya marcado."""
    hoy = hoy or _hoy()
    if oferta.estado == ESTADO_ENVIADA and oferta.valida_hasta and oferta.valida_hasta < hoy:
        return ESTADO_CADUCADA
    return str(oferta.estado)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def _envio_dict(e: OfertaEnvio) -> dict[str, Any]:
    return {
        "id": e.id, "enviado_en": _iso(e.enviado_en), "usuario_id": e.usuario_id,
        "version": e.version, "fecha_oferta": _iso(e.fecha_oferta),
        "total": _f(e.total) if e.total is not None else None, "idioma": e.idioma,
        "para": json.loads(e.para_json) if e.para_json else [],
        "cc": json.loads(e.cc_json) if e.cc_json else [],
        "asunto": e.asunto, "from_alias": e.from_alias, "adjunto": e.adjunto,
        "message_id": e.message_id, "thread_id": e.thread_id,
    }


def nombres_emisoras(session: Session) -> dict[int, str]:
    """`{serie: nombre}` de las series de oferta, una sola lectura de la
    configuración (el listado lo pasa a `oferta_como_dict` fila a fila)."""
    return {s: nombre_emisora(session, s) for s in SERIES_OFERTA}


def oferta_como_dict(
    session: Session, oferta: Oferta, *, con_lineas: bool = True, hoy: date | None = None,
    emisoras: dict[int, str] | None = None,
) -> dict[str, Any]:
    hoy = hoy or _hoy()
    contacto = oferta.contact
    company = oferta.company
    ultimo = oferta.envios[-1] if oferta.envios else None
    emisora = ((emisoras or {}).get(int(oferta.serie))
               or nombre_emisora(session, oferta.serie))
    out: dict[str, Any] = {
        "id": oferta.id, "numero": oferta.numero, "anio": oferta.anio,
        "secuencia": oferta.secuencia, "estado": estado_efectivo(oferta, hoy),
        "estado_guardado": oferta.estado,
        "vencida": bool(oferta.valida_hasta and oferta.valida_hasta < hoy),
        "contact_id": oferta.contact_id,
        "contacto": ({"id": contacto.id, "nombre": _nombre_contacto(contacto),
                      "email": contacto.email, "company_id": contacto.company_id}
                     if contacto is not None else None),
        "company_id": oferta.company_id,
        "empresa": ({"id": company.id, "nombre": company.name,
                     "factusol_company_id": company.factusol_company_id}
                    if company is not None else None),
        "plantilla_id": oferta.plantilla_id,
        "serie": oferta.serie, "emisora": emisora,
        "idioma": oferta.idioma, "con_iva": bool(oferta.con_iva),
        "fecha": _iso(oferta.fecha), "validez_dias": oferta.validez_dias,
        "valida_hasta": _iso(oferta.valida_hasta),
        "referencia": oferta.referencia, "notas": oferta.notas,
        "base": _f(oferta.base), "iva_pct": _f(oferta.iva_pct), "iva": _f(oferta.iva),
        "total": _f(oferta.total), "version": oferta.version,
        "factusol": ({"serie": oferta.factusol_serie, "codpre": oferta.factusol_codpre,
                      "numero": f"{oferta.factusol_serie}-{int(oferta.factusol_codpre):06d}",
                      "ejercicio": oferta.factusol_ejercicio}
                     if oferta.factusol_codpre else None),
        "convertida_en": _iso(oferta.convertida_en), "enviada_en": _iso(oferta.enviada_en),
        "caducada_en": _iso(oferta.caducada_en),
        "num_envios": len(oferta.envios),
        "ultimo_envio": _envio_dict(ultimo) if ultimo is not None else None,
        # Editada después del último envío: la versión de ahora no se ha mandado.
        "pendiente_de_envio": bool(ultimo is not None and ultimo.version < (oferta.version or 1)),
        "created_by_user_id": oferta.created_by_user_id,
        "created_at": _iso(oferta.created_at), "updated_at": _iso(oferta.updated_at),
    }
    if con_lineas:
        out["lineas"] = [_linea_dict(ln, con_total=True) for ln in oferta.lineas]
        out["envios"] = [_envio_dict(e) for e in oferta.envios]
    return out


def listar_ofertas(
    session: Session, *, contact_id: str | None = None, company_id: str | None = None,
    estado: str | None = None, q: str | None = None, limit: int = 200,
    hoy: date | None = None,
) -> list[Oferta]:
    """El listado. `estado` es el EFECTIVO (una enviada vencida es caducada)
    y se filtra en SQL, antes del `limit`; las relaciones que pinta la
    lista (contacto, empresa, envíos) se cargan de una vez."""
    from sqlalchemy.orm import selectinload  # noqa: PLC0415

    hoy = hoy or _hoy()
    stmt = (
        select(Oferta)
        .options(selectinload(Oferta.contact), selectinload(Oferta.company),
                 selectinload(Oferta.envios))
        .order_by(Oferta.fecha.desc(), Oferta.secuencia.desc())
    )
    if contact_id:
        stmt = stmt.where(Oferta.contact_id == contact_id)
    if company_id:
        stmt = stmt.where(Oferta.company_id == company_id)
    if estado == ESTADO_ENVIADA:
        stmt = stmt.where(Oferta.estado == ESTADO_ENVIADA, Oferta.valida_hasta >= hoy)
    elif estado == ESTADO_CADUCADA:
        stmt = stmt.where(
            (Oferta.estado == ESTADO_CADUCADA)
            | ((Oferta.estado == ESTADO_ENVIADA) & (Oferta.valida_hasta < hoy))
        )
    elif estado:
        stmt = stmt.where(Oferta.estado == estado)
    if q:
        patron = f"%{q.strip().lower()}%"
        stmt = stmt.outerjoin(Contact, Contact.id == Oferta.contact_id).outerjoin(
            Company, Company.id == Oferta.company_id).where(
            func.lower(Oferta.numero).like(patron)
            | func.lower(func.coalesce(Oferta.referencia, "")).like(patron)
            | func.lower(func.coalesce(Contact.first_name, "")).like(patron)
            | func.lower(func.coalesce(Contact.last_name, "")).like(patron)
            | func.lower(func.coalesce(Contact.email, "")).like(patron)
            | func.lower(func.coalesce(Company.name, "")).like(patron)
        )
    return list(session.scalars(stmt.limit(limit)))


# --- PDF --------------------------------------------------------------------------


def _fecha_pdf(value: date | None) -> str:
    return value.strftime("%d-%m-%Y") if value else ""


def datos_pdf(session: Session, oferta: Oferta) -> dict[str, Any]:
    """La oferta en la forma neutra del maquetador de los documentos de
    FACTUSOL (`extract_document_data`): una banda de IVA (o exenta si va sin
    IVA), las líneas y la fecha de validez. El bloque de cliente sale de la
    empresa de BoHub si la hay y, si no, del contacto."""
    from app.erp.language import normalize_country  # noqa: PLC0415

    contacto = oferta.contact
    company = oferta.company
    nombre = (company.name if company is not None else "") or _nombre_contacto(contacto)
    pais_raw = ((company.country if company is not None else None)
                or getattr(contacto, "address_country", None) or "")
    cliente = {
        "codigo": "",
        "nif": ((company.tax_id or company.vat) if company is not None else None) or "",
        "nombre": nombre or (contacto.email if contacto is not None else "") or "",
        "domicilio": ((company.address_line if company is not None else None)
                      or getattr(contacto, "address_line", None) or ""),
        "cp": ((company.postal_code if company is not None else None)
               or getattr(contacto, "address_postal_code", None) or ""),
        "poblacion": ((company.city if company is not None else None)
                      or getattr(contacto, "address_city", None) or ""),
        "provincia": ((company.state if company is not None else None)
                      or getattr(contacto, "address_state", None) or ""),
        "pais": normalize_country(pais_raw) or str(pais_raw or ""),
        "telefono": (contacto.phone if contacto is not None else None) or "",
    }
    base, iva_pct = _f(oferta.base), _f(oferta.iva_pct)
    iva, total = _f(oferta.iva), _f(oferta.total)
    con_iva = bool(oferta.con_iva)
    band = {
        "exenta": not con_iva, "neto": base, "dto": 0.0, "portes": 0.0, "fin": 0.0,
        "base": base, "piva": iva_pct if con_iva else 0.0, "iva": iva if con_iva else 0.0,
        "prec": 0.0, "rec": 0.0,
    }
    lines = []
    for ln in oferta.lineas:
        cantidad, precio, dto = _f(ln.cantidad), _f(ln.precio), _f(ln.descuento_pct)
        lines.append({
            "codart": ln.sku or ln.codart or "",
            "descripcion": ln.descripcion,
            "cantidad": cantidad, "precio": precio,
            "dto": (f"{dto:g}" if dto else ""),
            "subtotal": cantidad * precio, "total": _f(ln.total), "albaran": None,
        })
    return {
        "doc_type": "presupuestos", "serie": int(oferta.serie), "codigo": int(oferta.secuencia),
        "numero": oferta.numero, "fecha": _fecha_pdf(oferta.fecha),
        "cliente": cliente, "cliente_codigo": None,
        "forma_pago": "", "referencia": oferta.referencia or "",
        "pedido_cliente": "", "fecha_pedido_cliente": "",
        "observaciones": [oferta.notas] if oferta.notas else [],
        "vencimiento": "", "bands": [band], "charges": [], "total": total, "cambio": 0.0,
        "lines": lines, "valida_hasta": _fecha_pdf(oferta.valida_hasta), "con_iva": con_iva,
        "email": contacto.email if contacto is not None else "",
    }


def pdf(session: Session, oferta: Oferta, *, lang: str | None = None,
        bank: dict[str, Any] | None = None) -> bytes:
    from app.erp.factusol_pdf import (  # noqa: PLC0415
        company_for_serie,
        generate_document_pdf,
        logo_path_for_serie,
    )

    idioma = lang if lang in SUPPORTED_LANGS else (oferta.idioma or "es")
    return generate_document_pdf(
        datos_pdf(session, oferta), company=company_for_serie(session, int(oferta.serie)),
        lang=idioma, logo=logo_path_for_serie(int(oferta.serie)), variant=VARIANTE_PDF,
        bank=bank,
    )


def nombre_fichero(session: Session, oferta: Oferta, lang: str | None = None) -> str:
    """`Oferta ACME SL OF-2026-0001.pdf`: el mismo criterio que los adjuntos
    de pedido, factura y presupuesto (#531-#533)."""
    from app.erp.factusol_pdf import pdf_filename  # noqa: PLC0415

    idioma = lang if lang in SUPPORTED_LANGS else (oferta.idioma or "es")
    return pdf_filename("presupuestos", datos_pdf(session, oferta), idioma, VARIANTE_PDF)


# --- correo ------------------------------------------------------------------------


def _validez_texto(lang: str, oferta: Oferta) -> str:
    from app.erp.factusol_pdf import labels_for  # noqa: PLC0415

    return str(labels_for(lang).get("validez_oferta") or "").format(
        fecha=_fecha_pdf(oferta.valida_hasta))


def campos_correo(session: Session, oferta: Oferta, *, lang: str, contacto_nombre: str,
                  ) -> dict[str, str]:
    from app.erp.factusol_pdf import _fmt_money  # noqa: PLC0415

    company = oferta.company
    empresa = company.name if company is not None else _nombre_contacto(oferta.contact)
    return {
        "numero": oferta.numero, "empresa": empresa,
        "contacto": contacto_nombre or empresa,
        "total": f"{_fmt_money(_f(oferta.total), lang, 'EUR')} €",
        "fecha": _fecha_pdf(oferta.fecha), "validez": _validez_texto(lang, oferta),
        "firma": nombre_emisora(session, oferta.serie),
    }


def _destinatarios(session: Session, oferta: Oferta) -> list[dict[str, Any]]:
    """El contacto de la oferta (premarcado) y los demás de su empresa."""
    contacto = oferta.contact
    salida: list[dict[str, Any]] = []
    vistos: set[str] = set()

    def _add(c: Contact, *, primary: bool) -> None:
        if c.id in vistos:
            return
        vistos.add(c.id)
        email = (c.email or "").strip() if c.is_email_valid else ""
        salida.append({"id": c.id, "name": _nombre_contacto(c) or email or "—",
                       "email": email or None, "has_email": bool(email),
                       "is_primary": primary and bool(email)})

    if contacto is not None:
        _add(contacto, primary=True)
    if oferta.company_id:
        for c in session.scalars(select(Contact).where(
                Contact.company_id == oferta.company_id, Contact.is_active.is_(True),
        ).order_by(Contact.last_name.asc(), Contact.first_name.asc())):
            _add(c, primary=False)
    return salida


def preview_envio(
    session: Session, oferta: Oferta, *, current_user: User, lang_override: str | None = None,
) -> dict[str, Any]:
    """Lo que el modal necesita antes de enviar: destinatarios candidatos,
    idioma, asunto y cuerpo (plantilla sobria; la IA propone el suyo),
    remitente por serie y nombre del adjunto. No envía ni genera el PDF."""
    from app.erp.invoice_email import (  # noqa: PLC0415
        check_sender_alias,
        default_from_alias,
        series_from_alias,
    )
    from app.erp.quote_email import fill_template  # noqa: PLC0415

    if oferta.estado == ESTADO_CONVERTIDA:
        raise OfertaError("oferta_convertida",
                          f"La oferta {oferta.numero} ya está convertida; envía el presupuesto "
                          "de FACTUSOL.", status=409)
    lang = lang_override if lang_override in SUPPORTED_LANGS else (oferta.idioma or "es")
    contactos = _destinatarios(session, oferta)
    saludo = next((c for c in contactos if c["is_primary"]), None)
    fields = campos_correo(session, oferta, lang=lang,
                           contacto_nombre=saludo["name"] if saludo else "")
    tpl = OFERTA_EMAIL_DEFAULTS.get(lang) or OFERTA_EMAIL_DEFAULTS["es"]
    subject, body_text = fill_template(tpl["subject"], tpl["body"], fields)
    serie_alias = series_from_alias(session, int(oferta.serie))
    from_alias = serie_alias or default_from_alias(session, current_user)
    alias_check = (check_sender_alias(session, current_user, from_alias)
                   if from_alias else {"ok": False, "reason": "sin_alias"})
    # La dirección precargada: la del contacto solo si es utilizable (una que
    # rebotó, `is_email_valid=False`, no se ofrece).
    contacto = oferta.contact
    to = ((contacto.email or "").strip()
          if contacto is not None and contacto.is_email_valid else "")
    return {
        "oferta_id": oferta.id, "numero": oferta.numero,
        "to": to,
        "lang": lang, "lang_source": "selector" if lang_override in SUPPORTED_LANGS else "oferta",
        "subject": subject, "body_text": body_text,
        "from_alias": from_alias, "from_alias_source": "serie" if serie_alias else "usuario",
        "from_alias_ok": alias_check.get("ok"), "from_alias_problem": alias_check.get("reason"),
        "attachment_filename": nombre_fichero(session, oferta, lang),
        "company_id": oferta.company_id,
        "company_name": oferta.company.name if oferta.company is not None else None,
        "contact_id": oferta.contact_id,
        "company_contacts": contactos, "contacto_id": saludo["id"] if saludo else None,
        "markers": fields, "version": oferta.version,
        "pendiente_de_envio": bool(oferta.envios and oferta.envios[-1].version < oferta.version),
    }


def enviar(
    session: Session, oferta: Oferta, *, current_user: User, to: list[str],
    cc: list[str] | None = None, bcc: list[str] | None = None, subject: str, body_text: str,
    body_html: str | None = None, lang: str, from_alias: str, bank: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Genera el PDF y lo manda por Gmail desde `from_alias`, ligado al
    contacto de la oferta. Deja el envío en el historial, la oferta en
    `enviada` (con fecha), mueve el contacto a «Propuesta enviada» si está en
    un pipeline con esa etapa y emite `oferta.enviada`. Si el envío falla,
    propaga y no marca nada. NO toca FACTUSOL."""
    from app.erp.invoice_email import _text_to_html  # noqa: PLC0415
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    if oferta.estado == ESTADO_CONVERTIDA:
        raise OfertaError("oferta_convertida",
                          f"La oferta {oferta.numero} ya está convertida; envía el presupuesto "
                          "de FACTUSOL.", status=409)
    lang = lang if lang in SUPPORTED_LANGS else (oferta.idioma or "es")
    contenido = pdf(session, oferta, lang=lang, bank=bank)
    filename = nombre_fichero(session, oferta, lang)
    message = gmail_service.send_email(
        session, sender_user_id=current_user.id, from_alias=from_alias, from_name=None,
        to=list(to), cc=cc or None, bcc=bcc or None, subject=subject,
        body_html=body_html or _text_to_html(body_text), body_text=body_text,
        contact_id=oferta.contact_id,
        attachments=[{"filename": filename, "content_type": "application/pdf",
                      "data": contenido}],
    )
    ahora = datetime.now(UTC)
    envio = OfertaEnvio(
        enviado_en=ahora, usuario_id=current_user.id, version=oferta.version,
        fecha_oferta=oferta.fecha, total=oferta.total, idioma=lang,
        para_json=json.dumps(list(to)), cc_json=json.dumps(list(cc or [])),
        asunto=subject[:500], from_alias=from_alias[:255], adjunto=filename[:255],
        message_id=getattr(message, "id", None), thread_id=getattr(message, "thread_id", None),
    )
    oferta.envios.append(envio)
    oferta.estado = ESTADO_ENVIADA
    oferta.enviada_en = ahora
    oferta.caducada_en = None
    session.flush()
    destinatarios = list(to) + list(cc or [])
    texto = (f"Oferta {oferta.numero} enviada a {', '.join(destinatarios)} "
             f"en {_LANG_NAMES.get(lang, lang)}")
    metadata = {
        "numero": oferta.numero, "oferta_id": oferta.id, "version": oferta.version,
        "to": list(to), "cc": list(cc or []), "bcc": list(bcc or []), "lang": lang,
        "from_alias": from_alias, "message_id": envio.message_id,
        "thread_id": envio.thread_id, "attachment": filename, "total": _f(oferta.total),
        "user": getattr(current_user, "email", None),
    }
    _evento(session, oferta, EVENTO_ENVIADA, current_user, texto, metadata)
    etapas = []
    if oferta.contact is not None:
        etapas = mover_a_propuesta_enviada(session, oferta.contact, numero=oferta.numero,
                                           usuario_id=current_user.id)
    session.commit()
    _emitir_hecho(session, TRIGGER_ENVIADA, oferta, {
        "to": list(to), "lang": lang, "version": oferta.version, "total": _f(oferta.total),
        "valida_hasta": _iso(oferta.valida_hasta), "etapas_movidas": etapas,
    })
    return {
        "sent": True, "message_id": envio.message_id, "thread_id": envio.thread_id,
        "to": list(to), "cc": list(cc or []), "bcc": list(bcc or []), "lang": lang,
        "numero": oferta.numero, "attachment_filename": filename, "estado": oferta.estado,
        "enviada_en": _iso(oferta.enviada_en), "etapas_movidas": etapas,
    }


# --- pipeline y hechos ------------------------------------------------------------


def mover_a_propuesta_enviada(
    session: Session, contacto: Contact, *, numero: str, usuario_id: str | None,
) -> list[str]:
    """El contacto pasa a «Propuesta enviada» en cada pipeline (no archivado)
    donde esté y que tenga una etapa con ese nombre. Devuelve los nombres de
    los pipelines movidos. No añade a ningún pipeline: si no está en ninguno,
    no hay nada que mover."""
    from app.models.crm import ContactPipelineStage, Pipeline, PipelineStage  # noqa: PLC0415
    from app.repositories.pipelines import move_contact_to_stage  # noqa: PLC0415

    movidos: list[str] = []
    asignaciones = list(session.scalars(select(ContactPipelineStage).where(
        ContactPipelineStage.contact_id == contacto.id,
        ContactPipelineStage.is_archived.is_(False),
    )))
    for asignacion in asignaciones:
        etapa = session.scalar(select(PipelineStage).where(
            PipelineStage.pipeline_id == asignacion.pipeline_id,
            func.lower(PipelineStage.name).in_(ETAPAS_PROPUESTA),
        ).order_by(PipelineStage.position.asc()))
        if etapa is None or asignacion.stage_id == etapa.id:
            continue
        move_contact_to_stage(session, assignment=asignacion, new_stage_id=etapa.id,
                              note=f"Oferta {numero} enviada", moved_by_user_id=usuario_id)
        pipeline = session.get(Pipeline, asignacion.pipeline_id)
        movidos.append(pipeline.name if pipeline is not None else asignacion.pipeline_id)
    return movidos


def _emitir_hecho(session: Session, tipo: str, oferta: Oferta, extra: dict[str, Any]) -> None:
    """El hecho para los workflows (`oferta.enviada` / `oferta.caducada`). Sin
    contacto no hay a quién atarlo; un fallo del bus no tumba la operación."""
    if not oferta.contact_id:
        return
    from app.workflows.dispatcher import dispatch_event  # noqa: PLC0415

    payload = {"oferta_id": oferta.id, "numero": oferta.numero, "serie": oferta.serie,
               "company_id": oferta.company_id, **extra}
    try:
        dispatch_event(session, tipo, oferta.contact_id, payload)
    except Exception:  # noqa: BLE001 — el hecho es secundario
        logger.warning("ofertas: no se pudo emitir %s para %s", tipo, oferta.numero,
                       exc_info=True)


def caducar_pendientes(session: Session, *, hoy: date | None = None) -> list[Oferta]:
    """Las enviadas con la validez pasada quedan `caducada` (con fecha) y emiten
    `oferta.caducada`: el candidato a «Cerrado perdido» lo decide el motor del
    pipeline que consuma ese hecho (Fase 3), no este módulo."""
    hoy = hoy or _hoy()
    filas = list(session.scalars(select(Oferta).where(
        Oferta.estado == ESTADO_ENVIADA, Oferta.valida_hasta < hoy,
    )))
    ahora = datetime.now(UTC)
    for oferta in filas:
        oferta.estado = ESTADO_CADUCADA
        oferta.caducada_en = ahora
        _evento(session, oferta, EVENTO_CADUCADA, None,
                f"Oferta {oferta.numero} caducada sin respuesta "
                f"(válida hasta {_fecha_pdf(oferta.valida_hasta)})")
    if filas:
        session.commit()
    for oferta in filas:
        _emitir_hecho(session, TRIGGER_CADUCADA, oferta, {
            "valida_hasta": _iso(oferta.valida_hasta), "total": _f(oferta.total),
            "enviada_en": _iso(oferta.enviada_en),
        })
    return filas


# --- convertir en presupuesto de FACTUSOL -------------------------------------------


def prechequeo_conversion(session: Session, oferta: Oferta) -> dict[str, Any]:
    """Lo que tiene que cumplirse ANTES de encolar la conversión, sin tocar
    FACTUSOL: no convertida, contacto con empresa VINCULADA a un cliente de
    FACTUSOL (si no, `company_not_linked` con la empresa/contacto para que la
    pantalla abra el flujo de #502) y todas las líneas con artículo
    (`lineas_sin_articulo`). Devuelve el cliente de la cabecera y las líneas
    en la forma de `create_quote`."""
    from app.erp.quote_customer import customer_from_company  # noqa: PLC0415

    if oferta.estado == ESTADO_CONVERTIDA:
        raise OfertaError("oferta_convertida",
                          f"La oferta {oferta.numero} ya es el presupuesto "
                          f"{oferta.factusol_serie}-{int(oferta.factusol_codpre or 0):06d}.",
                          status=409, extra={"factusol_codpre": oferta.factusol_codpre,
                                             "factusol_serie": oferta.factusol_serie})
    contacto = oferta.contact
    company = oferta.company or (contacto.company if contacto is not None else None)
    if company is None:
        raise OfertaError(
            "company_not_linked",
            "El contacto no tiene empresa en BoHub: crea o vincula la empresa (y su cliente "
            "de FACTUSOL) antes de convertir.",
            status=409, extra={"company_id": None, "contact_id": oferta.contact_id,
                               "company_name": None},
        )
    if not company.factusol_company_id:
        raise OfertaError(
            "company_not_linked",
            f"«{company.name}» no está vinculada a un cliente de FACTUSOL. Vincúlala (o "
            "créalo) antes de convertir la oferta.",
            status=409, extra={"company_id": company.id, "contact_id": oferta.contact_id,
                               "company_name": company.name},
        )
    sin_articulo = [ln.posicion for ln in oferta.lineas
                    if not ln.codart and not (_f(ln.cantidad) == 0 and _f(ln.precio) == 0)]
    if sin_articulo:
        raise OfertaError(
            "lineas_sin_articulo",
            "Hay líneas sin artículo de FACTUSOL (" + ", ".join(str(p) for p in sin_articulo)
            + "): elige el artículo de cada una antes de convertir.",
            extra={"posiciones": sin_articulo},
        )
    if not oferta.lineas:
        raise OfertaError("sin_lineas", "La oferta no tiene líneas.")
    customer = customer_from_company(session, company, serie=int(oferta.serie))
    if contacto is not None and contacto.email:
        customer["email"] = contacto.email
    lines = [{
        "codart": ln.codart or "", "description": ln.descripcion,
        "quantity": _f(ln.cantidad), "unit_price": _f(ln.precio),
        "discount_pct": _f(ln.descuento_pct), "iva_pct": _f(ln.iva_pct),
    } for ln in oferta.lineas]
    return {"company": company, "customer": customer, "lines": lines}


def convertir(
    session: Session, client: Any, oferta: Oferta, *, ejercicio: str, actor: User | None,
) -> dict[str, Any]:
    """EL ÚNICO camino de las ofertas que escribe en FACTUSOL: crea el
    presupuesto `F_PRE` (+ `F_LPS`) en la serie de la emisora con las líneas y
    sus artículos, como uno hecho a mano (`create_quote`: régimen de IVA por
    pareja emisor → cliente, numeración global). Corre en el worker
    serializado `factusol:writes` y solo lo lanza el botón «Convertir en
    presupuesto». La oferta queda `convertida` y enlazada."""
    from app.integrations.factusol.quotes import create_quote  # noqa: PLC0415

    datos = prechequeo_conversion(session, oferta)
    result = create_quote(
        client, session, ejercicio=ejercicio, customer=datos["customer"], lines=datos["lines"],
        referencia=oferta.referencia, serie=int(oferta.serie),
    )
    oferta.factusol_serie = int(result.get("serie") or oferta.serie)
    oferta.factusol_codpre = int(result["codpre"])
    oferta.factusol_ejercicio = str(result.get("ejercicio") or ejercicio)
    oferta.estado = ESTADO_CONVERTIDA
    oferta.convertida_en = datetime.now(UTC)
    oferta.updated_by_user_id = actor.id if actor is not None else oferta.updated_by_user_id
    numero_pre = f"{oferta.factusol_serie}-{oferta.factusol_codpre:06d}"
    _evento(session, oferta, EVENTO_CONVERTIDA, actor,
            f"Oferta {oferta.numero} convertida en el presupuesto {numero_pre} de FACTUSOL",
            {"numero": oferta.numero, "codpre": oferta.factusol_codpre,
             "serie": oferta.factusol_serie, "ejercicio": oferta.factusol_ejercicio,
             "lines": result.get("lines"), "total": result.get("total"),
             "warning": result.get("warning")})
    session.commit()
    return {
        "oferta_id": oferta.id, "numero": oferta.numero, "codpre": oferta.factusol_codpre,
        "serie": oferta.factusol_serie, "ejercicio": oferta.factusol_ejercicio,
        "factusol_numero": numero_pre, "lines": result.get("lines"),
        "total": result.get("total"), "warning": result.get("warning"),
    }


# --- asistente ------------------------------------------------------------------------


def oferta_para_asistente(session: Session, oferta_id: str | None) -> dict[str, Any] | None:
    """La oferta como la ve el asistente «Ayúdame a contestar»: número, fechas,
    importes, con/sin IVA y sus líneas. None si no existe."""
    if not oferta_id:
        return None
    oferta = session.get(Oferta, oferta_id)
    if oferta is None:
        return None
    return {
        "id": oferta.id, "numero": oferta.numero, "estado": estado_efectivo(oferta),
        "fecha": _fecha_pdf(oferta.fecha), "valida_hasta": _fecha_pdf(oferta.valida_hasta),
        "idioma": oferta.idioma, "con_iva": bool(oferta.con_iva), "serie": oferta.serie,
        "emisora": nombre_emisora(session, oferta.serie), "moneda": "EUR",
        "base": _f(oferta.base), "iva_pct": _f(oferta.iva_pct), "iva": _f(oferta.iva),
        "total": _f(oferta.total), "referencia": oferta.referencia,
        "contact_id": oferta.contact_id,
        "lineas": [{
            "descripcion": ln.descripcion, "sku": ln.sku or ln.codart,
            "cantidad": _f(ln.cantidad), "precio": _f(ln.precio),
            "descuento_pct": _f(ln.descuento_pct), "total": _f(ln.total),
            "text_only": ln.codart is None and _f(ln.cantidad) == 0 and _f(ln.precio) == 0,
        } for ln in oferta.lineas],
    }


# --- auditoría -------------------------------------------------------------------------


def _evento(session: Session, oferta: Oferta, action: str, actor: User | None, message: str,
            metadata: dict[str, Any] | None = None) -> None:
    """Un evento sobre la oferta y, si tiene contacto, otro en su timeline."""
    from app.core.audit import record_event  # noqa: PLC0415

    # `serie` va siempre: el estimador de los disparadores de los workflows
    # filtra por ella (`trigger_definitions._est_oferta`).
    meta = {"numero": oferta.numero, "oferta_id": oferta.id, "estado": oferta.estado,
            "serie": int(oferta.serie), "total": _f(oferta.total), **(metadata or {})}
    record_event(session, action=action, target_type=TARGET_OFERTA, target_id=oferta.id,
                 actor=actor, message=message, metadata=meta)
    if oferta.contact_id:
        record_event(session, action=action, target_type="contact", target_id=oferta.contact_id,
                     actor=actor, message=message, metadata=meta)
