"""Corregir lo emitido desde la ficha del pedido (30/10/2026): la mención legal
de una factura y el cliente de un pedido o de su factura.

Esto ESCRIBE EN FACTUSOL sobre documentos ya emitidos, con la autorización
expresa de Bart. Hasta hoy, de una factura emitida BoHub solo tocaba `ESTFAC`.
Todo va con vista previa (lo que se va a escribir, tal cual), confirmación
explícita y Auditoría (quién, cuándo, de qué a qué).

**Mención legal** (Ceuta y Melilla, Canarias, Fuera de la UE… y las que Bart
añada, en Configuración ERP): se escribe en las OBSERVACIONES de la factura
(`OB1FAC`/`OB2FAC`), no como una línea. FactuSOL deja editar las
observaciones aunque haya VERI*FACTU (no forman parte del registro), no cambia
importes, numeración, IVA ni asientos, y el PDF que genera BoHub las imprime
desde ese momento. Nunca se pisa una observación que ya tenga texto. Si la
factura ya se envió al cliente, el PDF que tiene no la lleva: se avisa. Si a
la factura le faltaba del todo la mención de exención, la norma (RD 1619/2012,
arts. 6.1.j y 15) apunta a una rectificativa: eso lo decide la gestoría. La
mención también se puede elegir al emitir (`FacturaOptions.mencion`: la
primera observación vacía de la factura nueva). Una factura abonada no se toca.

**Cliente**: buscar en FACTUSOL por nombre o CIF y revincular, o crear el
cliente (comprobando ANTES que no exista ya con ese CIF). Si el pedido ya
tiene factura emitida, Bart decidió (30/10) que se cambie también la factura:
`CLIFAC` y el bloque de cliente (`CNO/CDO/CPO/CCP/CPR/CPA/CNIFAC`) con
`ActualizarRegistro`, con estas cautelas:

- se avisa de lo que implica: cambiar el destinatario de una factura emitida
  es, en la norma, una rectificativa; el PDF ya enviado lleva el cliente
  anterior; si estaba contabilizada, el asiento sigue con el anterior;
- NO se hace si el régimen de IVA del cliente nuevo no casa con la factura (la
  factura llevaría el IVA mal): eso es abono y factura nueva. El régimen es el
  EFECTIVO para la empresa que emite la serie, como al emitir
  (`regimen_efectivo`), y tiene que ser el mismo que el del cliente anterior;
- NO se hace con VERI*FACTU activo (interruptor en Configuración ERP): ahí el
  cliente es parte del registro y solo cabe la rectificativa;
- NO se hace si la factura está abonada.

En cuanto FACTUSOL acepta el cambio queda en Auditoría (con commit), antes de
releer y de tocar BoHub: si algo falla después, consta y se dice.

Los cobros (F_LCO) van por factura, no por cliente: no se tocan.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any

from sqlalchemy.orm import Session

from app.integrations.factusol.mapper import OBSERVACIONES_FAC

logger = logging.getLogger(__name__)

# --- Configuración ERP: menciones legales y VERI*FACTU ---------------------------

#: Clave en `ErpSettings.factusol_series_json`.
MENCIONES_KEY = "menciones_legales"
VERIFACTU_KEY = "verifactu_activo"
#: Largo máximo de una mención (lo que cabe en una observación de FACTUSOL).
MAX_MENCION = 255

#: Las de partida (decisión de Bart, 30/10): redacciones de REFERENCIA, sin
#: revisar. Hasta que alguien las revisa en Configuración ERP, no se aplican.
DEFAULT_MENCIONES: tuple[dict[str, Any], ...] = (
    {
        "id": "ceuta-melilla", "nombre": "Ceuta y Melilla", "revisado": False,
        "texto": ("Operación exenta de IVA – exportación (art. 21 Ley 37/1992). "
                  "Destino: Ceuta y Melilla."),
    },
    {
        "id": "canarias", "nombre": "Canarias", "revisado": False,
        "texto": ("Operación exenta de IVA – exportación (art. 21 Ley 37/1992). "
                  "Destino: Canarias."),
    },
    {
        "id": "fuera-ue", "nombre": "Fuera de la UE", "revisado": False,
        "texto": "Operación exenta de IVA por exportación, artículo 21.1º de la Ley 37/1992.",
    },
)


def _slug(nombre: str) -> str:
    plano = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plano.lower()).strip("-")[:40] or "mencion"


def menciones_config(raw: Any) -> list[dict[str, Any]]:
    """Lista normalizada `[{id, nombre, texto, revisado}]`; si nunca se
    guardó, las de partida."""
    if not isinstance(raw, list):
        return [dict(m) for m in DEFAULT_MENCIONES]
    out: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        nombre = str(entry.get("nombre") or "").strip()
        texto = str(entry.get("texto") or "").strip()
        if not nombre:
            continue
        ident = str(entry.get("id") or "").strip() or _slug(nombre)
        if ident in vistos:
            continue
        vistos.add(ident)
        out.append({"id": ident, "nombre": nombre, "texto": texto[:MAX_MENCION],
                    "revisado": bool(entry.get("revisado"))})
    return out


def validate_menciones(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lo que llega de Configuración ERP: nombre único, texto hasta 255.
    ValueError con el motivo. Una mención sin texto se guarda pero no se puede
    aplicar."""
    out: list[dict[str, Any]] = []
    nombres: set[str] = set()
    for entry in raw:
        nombre = str((entry or {}).get("nombre") or "").strip()
        texto = str((entry or {}).get("texto") or "").strip()
        if not nombre and not texto:
            continue  # fila vacía del formulario
        if not nombre:
            raise ValueError("una mención legal no tiene nombre")
        if nombre.lower() in nombres:
            raise ValueError(f"mención legal repetida: {nombre}")
        if len(texto) > MAX_MENCION:
            raise ValueError(
                f"la mención «{nombre}» pasa de {MAX_MENCION} caracteres (lo que cabe en las "
                "observaciones de la factura)"
            )
        nombres.add(nombre.lower())
        out.append({
            "id": str((entry or {}).get("id") or "").strip() or _slug(nombre),
            "nombre": nombre, "texto": texto,
            "revisado": bool((entry or {}).get("revisado")) and bool(texto),
        })
    return out


def _series(session: Session) -> dict[str, Any]:
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    return series_config(session)


def menciones(session: Session) -> list[dict[str, Any]]:
    return menciones_config(_series(session).get(MENCIONES_KEY))


def verifactu_activo(session: Session) -> bool:
    return bool(_series(session).get(VERIFACTU_KEY))


class FacturaClienteError(ValueError):
    """No se puede hacer lo pedido (con un código para la API)."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def factura_abonada(order: Any) -> bool:
    """¿La factura del pedido tiene un abono? Entonces no se toca."""
    from app.erp.models import InvoiceStatus  # noqa: PLC0415

    estado = getattr(order.invoice_status, "value", order.invoice_status)
    return str(estado or "") == InvoiceStatus.CREDIT_NOTE.value


def motivo_abonada(numero: str) -> str:
    return (f"La factura {numero} está abonada (tiene una rectificativa): BoHub no la toca. "
            "Lo que haga falta, en la factura nueva desde FACTUSOL.")


def anotar_auditoria(log: Any, **campos: Any) -> None:
    """Añade datos al `metadata` de un registro de Auditoría ya guardado (lo
    que se supo al releer FACTUSOL). El commit, de quien llama."""
    try:
        meta = json.loads(log.metadata_json) if log.metadata_json else {}
    except (TypeError, ValueError):
        meta = {}
    meta.update(campos)
    log.metadata_json = json.dumps(meta, default=str)


def mencion_aplicable(session: Session, *, mencion_id: str | None,
                      texto_libre: str | None) -> tuple[str, str | None]:
    """El texto a escribir y de qué mención sale (None: texto libre). Una
    mención de la lista tiene que estar revisada y tener texto."""
    if mencion_id:
        m = next((x for x in menciones(session) if x["id"] == mencion_id), None)
        if m is None:
            raise FacturaClienteError("mencion_no_existe", "Esa mención ya no está en la lista.")
        if not m["texto"]:
            raise FacturaClienteError(
                "mencion_sin_texto",
                f"La mención «{m['nombre']}» no tiene texto: escríbelo en Configuración ERP.",
            )
        if not m["revisado"]:
            raise FacturaClienteError(
                "mencion_sin_revisar",
                f"La mención «{m['nombre']}» es un texto de referencia sin revisar: revísalo "
                "(o que lo revise la gestoría) y márcalo como revisado en Configuración ERP.",
            )
        return m["texto"], m["id"]
    texto = (texto_libre or "").strip()
    if not texto:
        raise FacturaClienteError("mencion_vacia", "Elige una mención o escribe el texto.")
    if len(texto) > MAX_MENCION:
        raise FacturaClienteError(
            "mencion_larga", f"El texto pasa de {MAX_MENCION} caracteres.",
        )
    return texto, None


# --- la factura emitida en FACTUSOL -----------------------------------------------

#: Las dos observaciones de la factura que imprime el PDF de BoHub.
OBSERVACIONES = OBSERVACIONES_FAC


def _texto(value: Any) -> str:
    return str(value or "").strip()


def leer_factura(client: Any, *, serie: int, codigo: int, ejercicio: str) -> dict[str, Any]:
    """La fila de F_FAC de la factura, en vivo. FacturaClienteError si ya no
    está."""
    from app.integrations.factusol.collections_write import invoice_row  # noqa: PLC0415

    row = invoice_row(client, serie=serie, codigo=codigo, ejercicio=ejercicio)
    if row is None:
        raise FacturaClienteError(
            "factura_no_existe", f"No existe la factura {serie}-{codigo:06d} en FACTUSOL.",
        )
    return row


def plan_mencion(row: dict[str, Any], texto: str) -> dict[str, Any]:
    """Dónde iría la mención: la primera observación vacía. `ya_esta` si
    alguna ya la lleva tal cual; `campo` None si las dos tienen texto (no se
    pisa nada: se escribe desde FACTUSOL)."""
    actuales = {c: _texto(row.get(c)) for c in OBSERVACIONES}
    if any(v == texto.strip() for v in actuales.values()):
        return {"campo": None, "ya_esta": True, "actuales": actuales}
    campo = next((c for c in OBSERVACIONES if not actuales[c]), None)
    return {"campo": campo, "ya_esta": False, "actuales": actuales}


def escribir_mencion(
    client: Any, *, row: dict[str, Any], campo: str, texto: str, ejercicio: str,
) -> None:
    """`ActualizarRegistro` de F_FAC con la clave compuesta (tal cual la
    devolvió FACTUSOL, mismos tipos) y SOLO esa observación."""
    if campo not in OBSERVACIONES:
        raise FacturaClienteError("campo_no_valido", f"Campo no permitido: {campo}")
    client.update_record(
        "F_FAC", {"TIPFAC": row["TIPFAC"], "CODFAC": row["CODFAC"], campo: texto},
        ejercicio=ejercicio,
    )


# --- el cliente de una factura emitida ------------------------------------------------

#: F_FAC del bloque de cliente que se cambia, y de dónde sale (cliente de F_CLI
#: normalizado por `albaran_manual.customer_for_albaran`).
_BLOQUE_CLIENTE = (
    ("CNOFAC", "nombre"), ("CDOFAC", "direccion"), ("CPOFAC", "ciudad"),
    ("CCPFAC", "cp"), ("CPRFAC", "provincia"), ("CNIFAC", "nif"), ("TELFAC", "telefono"),
)
CAMPO_LABEL = {
    "CLIFAC": "Cliente (código)", "CNOFAC": "Nombre", "CDOFAC": "Dirección",
    "CPOFAC": "Población", "CCPFAC": "CP", "CPRFAC": "Provincia",
    "CNIFAC": "NIF", "CPAFAC": "País", "TELFAC": "Teléfono",
}


def _mismo_tipo(actual: Any, nuevo: Any) -> Any:
    """El valor nuevo con el tipo JSON del que ya hay (DELSOL los quiere
    iguales: gotcha nº 13)."""
    if isinstance(actual, bool):
        return nuevo
    if isinstance(actual, int):
        try:
            return int(str(nuevo).strip())
        except ValueError:
            return nuevo
    if isinstance(actual, float):
        try:
            return float(str(nuevo).strip())
        except ValueError:
            return nuevo
    return str(nuevo if nuevo is not None else "")


def factura_lleva_iva(row: dict[str, Any]) -> bool:
    """¿El documento cobra IVA? (alguna cuota `IIVA<n>FAC`, `…PCL`, `…ALB`
    distinta de 0)."""
    total = 0.0
    for col, val in row.items():
        if re.fullmatch(r"IIVA\d+[A-Z]{3}", str(col)):
            try:
                total += abs(float(val or 0))
            except (TypeError, ValueError):
                continue
    return total > 0.005


def cambios_cliente_factura(
    row: dict[str, Any], cliente: dict[str, Any], *, completo: bool = False,
) -> list[dict[str, Any]]:
    """Lo que cambiaría en la factura, campo a campo (`antes` → `despues`),
    con el bloque de cliente de F_CLI. Solo lo que cambia, o todo el bloque
    con `completo`."""
    from app.integrations.factusol.albaran_manual import _cpapre  # noqa: PLC0415

    nuevos: dict[str, Any] = {"CLIFAC": cliente["codcli"]}
    for col, key in _BLOQUE_CLIENTE:
        nuevos[col] = str(cliente.get(key) or "")[:255]
    nuevos["CPAFAC"] = _cpapre(cliente.get("pais"))
    out = []
    for col, valor in nuevos.items():
        if col not in row:
            continue  # columna que esta F_FAC no tiene: no se manda (gotcha 13)
        antes = row.get(col)
        if _texto(antes) == _texto(valor) and not completo:
            continue
        out.append({"campo": col, "label": CAMPO_LABEL.get(col, col),
                    "antes": _texto(antes), "despues": _texto(valor),
                    "valor": _mismo_tipo(antes, valor)})
    return out


def regimen_efectivo(session: Session, cliente: dict[str, Any], *, serie: Any) -> str | None:
    """Régimen de IVA con el que la empresa que emite la `serie` factura a este
    cliente (de `customer_for_albaran`), como al emitir
    (`albaran_manual.apply_regime`): manda el cálculo por la pareja de países
    y la ficha F_CLI solo donde el régimen es una decisión (los dos en la UE y
    distintos, con el país del emisor configurado). None si no se puede saber
    (sin país y sin ficha, o la ficha decide y no se entiende)."""
    from app.erp.factusol_pdf import issuer_iso2_for_serie  # noqa: PLC0415
    from app.erp.language import normalize_country  # noqa: PLC0415
    from app.integrations.factusol.vat_regime import (  # noqa: PLC0415
        REGIME_EXPORTACION,
        REGIMES,
        _emisor,
        ficha_manda,
        pareja_elegible,
        regime_for,
    )

    ficha = cliente.get("regime")
    pais = normalize_country(cliente.get("pais_iso2") or cliente.get("pais"))
    if pais is None:
        # Sin país no hay cálculo: la ficha (como al emitir).
        return ficha if ficha in REGIMES else None
    emisor = issuer_iso2_for_serie(session, serie)
    if pais == _emisor(emisor) and ficha == REGIME_EXPORTACION:
        # Canarias, Ceuta y Melilla: mismo país que el emisor pero exentos como
        # una exportación. BoHub no los distingue de la Península por el país:
        # manda la ficha (la mención de esas facturas es justo esta).
        return ficha
    nif = cliente.get("nif")
    calculado = regime_for(pais, issuer_iso2=emisor, vat=nif, nif=nif)
    if emisor is not None and pareja_elegible(pais, issuer_iso2=emisor):
        if ficha not in REGIMES:
            return None
        return ficha_manda(ficha) or calculado
    return calculado


def _regimen_txt(regimen: str) -> str:
    from app.integrations.factusol.vat_regime import REGIME_LABELS  # noqa: PLC0415

    label = REGIME_LABELS.get(regimen, regimen)
    return label[:1].lower() + label[1:]


def cliente_o_none(client: Any, codcli: Any, *, ejercicio: str) -> dict[str, Any] | None:
    """El cliente de F_CLI (como `customer_for_albaran`), o None si no hay
    código o no se puede leer."""
    from app.integrations.factusol.albaran_manual import customer_for_albaran  # noqa: PLC0415
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    code = _texto(codcli)
    if not code:
        return None
    try:
        return customer_for_albaran(client, codcli=code, ejercicio=ejercicio)
    except FactusolError:
        return None


def bloqueo_regimen(
    session: Session, row: dict[str, Any], cliente: dict[str, Any], *, documento: str,
    serie: Any, anterior: dict[str, Any] | None,
) -> str | None:
    """Por qué el cliente nuevo no casa con el IVA del documento, o None.

    Con el régimen EFECTIVO de cada cliente para quien emite la `serie`: el
    del nuevo tiene que casar con el IVA del documento y ser el mismo que el
    del cliente que lleva ahora (`anterior`; si no se pudo leer, solo lo
    primero). Ante la duda, se bloquea."""
    from app.integrations.factusol.vat_regime import REGIME_NACIONAL  # noqa: PLC0415

    nuevo = regimen_efectivo(session, cliente, serie=serie)
    if nuevo is None:
        return ("El cliente nuevo no tiene en FACTUSOL un régimen de IVA reconocible "
                "(nacional, intracomunitario o exportación): revisa su ficha antes.")
    quien = f"para la empresa que emite la serie {_texto(serie) or '—'}"
    con_iva = factura_lleva_iva(row)
    if con_iva != (nuevo == REGIME_NACIONAL):
        return (
            f"{documento} {'lleva' if con_iva else 'no lleva'} IVA y el cliente nuevo, {quien}, "
            f"es {_regimen_txt(nuevo)}: con él llevaría el IVA mal."
        )
    viejo = regimen_efectivo(session, anterior, serie=serie) if anterior else None
    if viejo is not None and viejo != nuevo:
        return (
            f"{documento} es de un cliente {_regimen_txt(viejo)} y el nuevo, {quien}, es "
            f"{_regimen_txt(nuevo)}: el IVA y la mención de la factura no serían los suyos."
        )
    return None


def bloqueo_cliente_factura(
    session: Session, row: dict[str, Any], cliente: dict[str, Any], *,
    client: Any, ejercicio: str,
) -> str | None:
    """Por qué NO se puede cambiar el cliente de esta factura en FACTUSOL, o
    None si se puede."""
    if verifactu_activo(session):
        return ("VERI*FACTU está activo: el cliente forma parte del registro de la factura y "
                "no se puede cambiar en el sitio. Hace falta una rectificativa (abono y factura "
                "nueva) desde FACTUSOL.")
    motivo = bloqueo_regimen(
        session, row, cliente, documento="La factura", serie=row.get("TIPFAC"),
        anterior=cliente_o_none(client, row.get("CLIFAC"), ejercicio=ejercicio),
    )
    if motivo:
        return motivo + " Hace falta abono y factura nueva desde FACTUSOL."
    return None


AVISOS_CLIENTE_FACTURA = (
    "Cambiar el destinatario de una factura ya emitida es, en la norma (RD 1619/2012), "
    "una factura rectificativa; esto la corrige en el sitio, como decidió Bart.",
    "Si la factura ya se envió, el PDF que tiene el cliente lleva el cliente anterior.",
    "Si la factura ya está contabilizada, el asiento sigue con el cliente anterior: "
    "revísalo en la contabilidad.",
    "Los cobros ya registrados van por factura: no cambian.",
)


def escribir_cliente_factura(
    client: Any, *, row: dict[str, Any], cambios: list[dict[str, Any]], ejercicio: str,
) -> None:
    """`ActualizarRegistro` de F_FAC: clave compuesta + SOLO lo que cambia."""
    if not cambios:
        return
    registro = {"TIPFAC": row["TIPFAC"], "CODFAC": row["CODFAC"]}
    for c in cambios:
        registro[c["campo"]] = c["valor"]
    client.update_record("F_FAC", registro, ejercicio=ejercicio)


# --- revincular el pedido a otro cliente ------------------------------------------

#: Acciones de Auditoría (target "order": salen en el historial del pedido).
INVOICE_MENTION_EVENT = "erp.invoice_mention_added"
INVOICE_CLIENT_CHANGED_EVENT = "erp.invoice_client_changed"
ORDER_CLIENT_RELINKED_EVENT = "erp.order_client_relinked"
CUSTOMER_CREATED_EVENT = "erp.order_customer_created"


def _cliente_resumen(cliente: dict[str, Any]) -> dict[str, Any]:
    return {k: cliente.get(k) or None for k in (
        "codcli", "nombre", "nif", "direccion", "cp", "ciudad", "provincia", "pais", "regime",
    )}


def cliente_revinculado(session: Session, order: Any) -> str | None:
    """El CODCLI al que se revinculó el pedido desde su ficha (el último
    `erp.order_client_relinked`), si el pedido sigue con la empresa de ese
    cambio. Al emitir, la factura sale a ESE cliente aunque el documento de
    origen (el F_PCL de la web o el albarán) lleve otro, y aunque la empresa
    esté vinculada a otro código con el mismo NIF (hay clientes duplicados)."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.crm import AuditLog  # noqa: PLC0415

    if not order.company_id:
        return None
    meta = session.scalar(
        select(AuditLog.metadata_json).where(
            AuditLog.action == ORDER_CLIENT_RELINKED_EVENT,
            AuditLog.target_type == "order", AuditLog.target_id == order.id,
        ).order_by(AuditLog.created_at.desc()).limit(1)
    )
    try:
        datos = json.loads(meta) if meta else {}
    except (TypeError, ValueError):
        return None
    if not isinstance(datos, dict) or datos.get("to_company") != order.company_id:
        return None
    return _texto(datos.get("to_codcli")) or None


def _documento_origen(client: Any, order: Any, *, session: Session,
                      ejercicio: str) -> dict[str, Any] | None:
    """El documento de FACTUSOL del que saldrá la factura (aún sin emitir):
    el albarán de BoHub o el pedido de cliente F_PCL de la web. `{tipo,
    numero, codcli, serie, row}` (`serie`: la de la factura que saldrá) o None
    si no hay ninguno."""
    from app.erp.factusol_cobro import parse_invoice_number  # noqa: PLC0415
    from app.erp.workflow import is_web_order  # noqa: PLC0415
    from app.integrations.factusol.documents import visible_number  # noqa: PLC0415
    from app.integrations.factusol.service import (  # noqa: PLC0415
        _store_ref_prefix,
        find_pcl_by_order,
        resolve_serie,
        serie_of_row,
    )

    serie, codigo = parse_invoice_number(order.factusol_albaran_number)
    if serie is not None and codigo is not None:
        rows = client.load_table("F_ALB", filtro=f"CODALB={int(codigo)}", ejercicio=ejercicio)
        row = next((r for r in rows if serie_of_row(r, "TIPALB") == serie), None)
        if row is None:
            return None
        return {"tipo": "albarán", "numero": visible_number(serie, codigo),
                "codcli": _texto(row.get("CLIALB")), "serie": serie, "row": row}
    if is_web_order(order):
        pcl = find_pcl_by_order(client, order, ejercicio,
                                ref_prefix=_store_ref_prefix(session, order))
        if pcl is None:
            return None
        return {"tipo": "pedido de cliente", "numero": _texto(pcl.get("CODPCL")),
                "codcli": _texto(pcl.get("CLIPCL")),
                "serie": resolve_serie(session, order, None, pcl_row=pcl), "row": pcl}
    return None


def plan_revincular(
    session: Session, order: Any, *, client: Any, ejercicio: str, codcli: str,
) -> dict[str, Any]:
    """Lo que pasaría al revincular el pedido al cliente `codcli` de FACTUSOL,
    sin escribir nada: el cliente de ahora y el nuevo, la ficha del CRM que
    queda vinculada (o que se crea), y —si hay factura emitida— los campos que
    cambian en ella o por qué no se puede. `sin_cambios`: no cambiaría nada
    (ni el pedido, ni la factura, ni a quién saldrá al emitir).
    FacturaClienteError si no se puede de ninguna forma."""
    return _plan_revincular(session, order, client=client, ejercicio=ejercicio,
                            codcli=codcli)[0]


def _plan_revincular(
    session: Session, order: Any, *, client: Any, ejercicio: str, codcli: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """`plan_revincular` y la fila de F_FAC leída (None sin factura)."""
    from app.erp.linked_invoice import get_linked_invoice  # noqa: PLC0415
    from app.erp.web_order_company import find_company_for_customer  # noqa: PLC0415
    from app.erp.workflow import is_invoiced  # noqa: PLC0415
    from app.integrations.factusol.albaran_manual import customer_for_albaran  # noqa: PLC0415
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.models.crm import Company  # noqa: PLC0415

    code = str(codcli or "").strip()
    try:
        cliente = customer_for_albaran(client, codcli=code, ejercicio=ejercicio)
    except FactusolError as exc:
        raise FacturaClienteError(
            "cliente_no_existe", f"No hay cliente {code} en FACTUSOL.",
        ) from exc
    actual = session.get(Company, order.company_id) if order.company_id else None
    empresa = find_company_for_customer(session, code, cliente.get("nif"))
    if empresa is not None and empresa.is_archived:
        raise FacturaClienteError(
            "empresa_archivada",
            f"La ficha del cliente {code} en BoHub ({empresa.name}) está archivada: desarchívala "
            "antes.",
        )
    if empresa is None and not _texto(cliente.get("nif")):
        raise FacturaClienteError(
            "cliente_sin_nif",
            f"El cliente {code} de FACTUSOL no tiene NIF: no se crea su ficha a ciegas. "
            "Complétalo en FACTUSOL o busca su empresa.",
        )
    plan: dict[str, Any] = {
        "cliente": _cliente_resumen(cliente),
        "actual": ({"company_id": actual.id, "nombre": actual.name,
                    "codcli": (actual.factusol_company_id or "").strip() or None}
                   if actual else None),
        "empresa": ({"company_id": empresa.id, "nombre": empresa.name, "crear": False}
                    if empresa else {"company_id": None, "nombre": cliente.get("nombre"),
                                     "crear": True}),
        "factura": None, "documento": None, "avisos": [],
    }
    plan["sin_cambios_crm"] = bool(empresa is not None and actual is not None
                                   and empresa.id == actual.id)
    vinculo = _texto(empresa.factusol_company_id) if empresa is not None else ""
    if vinculo and vinculo != code:
        # Mismo NIF con dos códigos en FACTUSOL: la ficha de BoHub no cambia de
        # vínculo; el pedido recuerda el código elegido (`cliente_revinculado`).
        plan["avisos"].append(
            f"La empresa de BoHub «{empresa.name}» está vinculada al cliente {vinculo} de "
            f"FACTUSOL (mismo NIF): el pedido queda con esa empresa y la factura, con el "
            f"cliente {code}.",
        )
    row: dict[str, Any] | None = None
    linked = get_linked_invoice(order)
    if linked is not None:
        row = leer_factura(client, serie=linked.serie, codigo=linked.codigo, ejercicio=ejercicio)
        cambios = cambios_cliente_factura(row, cliente)
        abonada = factura_abonada(order)
        if abonada:
            bloqueo = motivo_abonada(linked.numero)
        elif cambios:
            bloqueo = bloqueo_cliente_factura(session, row, cliente, client=client,
                                              ejercicio=ejercicio)
        else:
            bloqueo = None
        plan["factura"] = {
            "numero": linked.numero, "cambios": cambios, "bloqueo": bloqueo, "abonada": abonada,
            "avisos": list(AVISOS_CLIENTE_FACTURA) if cambios and not abonada else [],
        }
    elif is_invoiced(order):
        plan["avisos"].append(
            "El pedido está marcado como facturado fuera de BoHub: su factura no se toca. "
            "Cámbiala en FACTUSOL si hace falta.",
        )
    else:
        doc = _documento_origen(client, order, session=session, ejercicio=ejercicio)
        if doc is not None and doc["codcli"] and doc["codcli"] != code:
            motivo = bloqueo_regimen(
                session, doc["row"], cliente, documento=f"El {doc['tipo']}", serie=doc["serie"],
                anterior=cliente_o_none(client, doc["codcli"], ejercicio=ejercicio),
            )
            plan["documento"] = {
                "tipo": doc["tipo"], "numero": doc["numero"], "codcli": doc["codcli"],
                "bloqueo": (motivo + " Corrige el documento en FACTUSOL antes de facturar."
                            if motivo else None),
            }
            if not motivo:
                plan["avisos"].append(
                    f"El {doc['tipo']} {doc['numero']} de FACTUSOL sigue con el cliente "
                    f"{doc['codcli']}: no se toca, pero la factura saldrá a nombre del cliente "
                    "nuevo.",
                )
    # A quién saldría hoy la factura al emitir: el último revinculado o, si no
    # lo hay, el vínculo de la empresa del pedido.
    vigente = cliente_revinculado(session, order) or (
        _texto(actual.factusol_company_id) if actual is not None else "")
    plan["sin_cambios"] = bool(
        plan["sin_cambios_crm"] and not (plan["factura"] and plan["factura"]["cambios"])
        and plan["documento"] is None and vigente == code
    )
    return plan, row


def factura_cambiada_pedido_no(numero: str) -> str:
    """Lo que se dice si algo falla DESPUÉS de cambiar la factura en FACTUSOL."""
    return (f"La factura {numero} ya se cambió en FACTUSOL; el pedido no: vuelve a pulsar "
            "«Cambiar el cliente» para completarlo.")


def aplicar_revincular(
    session: Session, order: Any, *, client: Any, ejercicio: str, codcli: str,
    campos_esperados: dict[str, str] | None, actor: Any = None,
) -> dict[str, Any]:
    """Revincula: primero la factura en FACTUSOL (si cambia y se puede) y,
    solo si eso fue bien, el pedido en BoHub (empresa del CRM, creada si hace
    falta).

    `campos_esperados` es lo que enseñó la vista previa (campo → valor nuevo):
    si FACTUSOL ya no da eso, no se escribe nada. En cuanto FACTUSOL acepta el
    cambio queda la Auditoría `erp.invoice_client_changed` con COMMIT, antes
    de releer y de tocar BoHub: si algo falla después, consta y el error lo
    dice. Un reintento con la misma vista previa encuentra la factura ya
    cambiada, no la escribe otra vez y completa el pedido. Lo de BoHub no hace
    commit (lo hace quien llama)."""
    from app.core.audit import record_event  # noqa: PLC0415
    from app.erp.web_order_company import (  # noqa: PLC0415
        WEB_ORDER_SYNC_SOURCE,
        _codcli_free_for,
        _company_from_customer,
    )
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.customers import get_customer  # noqa: PLC0415
    from app.models.crm import Company, Contact  # noqa: PLC0415

    code = str(codcli).strip()
    plan, row = _plan_revincular(session, order, client=client, ejercicio=ejercicio,
                                 codcli=code)
    factura = plan["factura"]
    if factura and factura["abonada"]:
        raise FacturaClienteError("factura_abonada", factura["bloqueo"])
    cambios = factura["cambios"] if factura else []
    esperados = {str(k): _texto(v) for k, v in (campos_esperados or {}).items()}
    ya_cambiada = False
    if {c["campo"]: c["despues"] for c in cambios} != esperados:
        # Reintento tras un fallo DESPUÉS de escribir: la factura ya tiene lo
        # que enseñó la vista previa. No se escribe otra vez.
        ya_cambiada = bool(esperados and row is not None and not cambios and all(
            _texto(row.get(campo)) == valor for campo, valor in esperados.items()))
        if not ya_cambiada:
            raise FacturaClienteError(
                "cambio_en_factusol",
                "La factura ha cambiado en FACTUSOL desde la vista previa: vuelve a revisarla. "
                "No se ha escrito nada.",
            )
    if factura and cambios and factura["bloqueo"]:
        raise FacturaClienteError("factura_bloqueada", factura["bloqueo"])
    if plan["documento"] and plan["documento"]["bloqueo"]:
        raise FacturaClienteError("documento_bloqueado", plan["documento"]["bloqueo"])
    if plan["sin_cambios"]:
        raise FacturaClienteError("sin_cambios", "El pedido ya está con ese cliente.")

    # La ficha del CRM se busca (no se crea) ANTES de escribir en FACTUSOL.
    empresa = (session.get(Company, plan["empresa"]["company_id"])
               if plan["empresa"]["company_id"] else None)
    cust = (get_customer(client, code, ejercicio=ejercicio) or {}) if empresa is None else {}

    escrita = False
    if cambios and factura and row is not None:
        numero = factura["numero"]
        serie, _, cod = numero.partition("-")
        respuesta_perdida = False
        try:
            escribir_cliente_factura(client, row=row, cambios=cambios, ejercicio=ejercicio)
        except FactusolError:
            # Pudo llegar y perderse la respuesta (las escrituras no se
            # reintentan): se relee y, si ya tiene lo nuevo, cuenta como escrita.
            try:
                ahora = leer_factura(client, serie=int(serie), codigo=int(cod),
                                     ejercicio=ejercicio)
            except (FacturaClienteError, FactusolError) as exc:
                # Puede haberse escrito: consta en Auditoría (sin comprobar)
                # con el antes y el después, por si lo estuviera.
                record_event(
                    session, action=INVOICE_CLIENT_CHANGED_EVENT, target_type="order",
                    target_id=order.id, actor=actor,
                    message=(f"Cambio del cliente de la factura {numero} mandado a FACTUSOL "
                             "sin respuesta: puede que se haya hecho"),
                    metadata={
                        "order_number": order.order_number, "factura": numero,
                        "from_codcli": _texto(row.get("CLIFAC")) or None, "to_codcli": code,
                        "cambios": [{k: c[k] for k in ("campo", "antes", "despues")}
                                    for c in cambios],
                        "comprobada": None, "incierta": True,
                        "reason": f"Cliente de la factura {numero}: a {code} (sin confirmar)",
                    },
                )
                session.commit()
                raise FacturaClienteError(
                    "factusol_write_incierto",
                    f"FACTUSOL no ha respondido al cambiar la {numero} ni al releerla: puede "
                    "que se haya cambiado. Compruébalo en FACTUSOL antes de volver a intentarlo.",
                ) from exc
            if any(_texto(ahora.get(c["campo"])) != _texto(c["despues"]) for c in cambios):
                raise
            respuesta_perdida = True
        escrita = True
        log = record_event(
            session, action=INVOICE_CLIENT_CHANGED_EVENT, target_type="order",
            target_id=order.id, actor=actor,
            message=(f"Cliente de la factura {numero} cambiado en FACTUSOL: de "
                     f"{_texto(row.get('CLIFAC')) or '—'} a {code}"),
            metadata={
                "order_number": order.order_number, "factura": numero,
                "from_codcli": _texto(row.get("CLIFAC")) or None, "to_codcli": code,
                "cambios": [{k: c[k] for k in ("campo", "antes", "despues")} for c in cambios],
                "comprobada": None,
                **({"respuesta_perdida": True} if respuesta_perdida else {}),
                "reason": f"Cliente de la factura {numero}: a {code}",
            },
        )
        session.commit()
        try:
            despues = leer_factura(client, serie=int(serie), codigo=int(cod),
                                   ejercicio=ejercicio)
        except (FacturaClienteError, FactusolError) as exc:
            raise FacturaClienteError(
                "relectura_fallida",
                f"FACTUSOL no responde al releer la factura. {factura_cambiada_pedido_no(numero)}",
            ) from exc
        comprobada = (_texto(despues.get("CLIFAC")) == code
                      or not any(c["campo"] == "CLIFAC" for c in cambios))
        anotar_auditoria(log, comprobada=comprobada)
        session.commit()
        if not comprobada:
            raise FacturaClienteError(
                "factusol_no_cambio",
                f"FACTUSOL respondió bien pero al releer la {numero} no tiene el cliente nuevo: "
                "revísala allí. El pedido sigue con su cliente.",
            )

    try:
        creada = False
        if empresa is None:
            empresa = _company_from_customer(session, code, cust)
            creada = True
        elif not (empresa.factusol_company_id or "").strip() and _codcli_free_for(
            session, code, empresa,
        ):
            empresa.factusol_company_id = code
            empresa.factusol_sync_source = WEB_ORDER_SYNC_SOURCE
        anterior = order.company_id
        order.company_id = empresa.id
        if order.contact_id:
            contacto = session.get(Contact, order.contact_id)
            if contacto is not None and not contacto.company_id:
                contacto.company_id = empresa.id
        session.flush()
    except Exception as exc:
        if not (escrita or ya_cambiada):
            raise
        session.rollback()
        raise FacturaClienteError(
            "pedido_sin_cambiar", factura_cambiada_pedido_no(factura["numero"]),
        ) from exc
    return {**plan, "company_id": empresa.id, "company_name": empresa.name,
            "anterior": anterior, "creada": creada, "escrito_en_factura": escrita,
            "factura_ya_cambiada": ya_cambiada}


def cliente_para_emitir(
    session: Session, client: Any, *, cabecera: dict[str, Any], codcli: str, ejercicio: str,
    origen: dict[str, Any], codcli_origen: Any, documento: str, serie: Any,
) -> dict[str, Any]:
    """Columnas del bloque de cliente que pisan las copiadas del documento de
    origen cuando el pedido se revinculó a otro cliente (`cliente_revinculado`).
    FactusolError si el cliente nuevo no casa con el IVA del documento o, para
    la empresa que emite la `serie`, no es del mismo régimen que el cliente del
    documento (`codcli_origen`): antes no facturar que facturar con el IVA mal."""
    from app.integrations.factusol.albaran_manual import customer_for_albaran  # noqa: PLC0415
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    cliente = customer_for_albaran(client, codcli=codcli, ejercicio=ejercicio)
    motivo = bloqueo_regimen(
        session, origen, cliente, documento=documento, serie=serie,
        anterior=cliente_o_none(client, codcli_origen, ejercicio=ejercicio),
    )
    if motivo:
        raise FactusolError(
            f"El pedido se revinculó al cliente {codcli}, pero {motivo[0].lower()}{motivo[1:]} "
            "No se emite: corrige el documento en FACTUSOL o vuelve a revincular.",
        )
    # El bloque entero: en un albarán con destinatario de envío lo que no se
    # mandara lo pondría la factura con el cliente fiscal ANTERIOR.
    return {c["campo"]: c["valor"]
            for c in cambios_cliente_factura(cabecera, cliente, completo=True)}
