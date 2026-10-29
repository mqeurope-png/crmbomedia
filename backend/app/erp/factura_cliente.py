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
mención también se puede elegir al emitir (`FacturaOptions.ob1fac`).

**Cliente**: buscar en FACTUSOL por nombre o CIF y revincular, o crear el
cliente (comprobando ANTES que no exista ya con ese CIF). Si el pedido ya
tiene factura emitida, Bart decidió (30/10) que se cambie también la factura:
`CLIFAC` y el bloque de cliente (`CNO/CDO/CPO/CCP/CPR/CPA/CNIFAC`) con
`ActualizarRegistro`, con estas cautelas:

- se avisa de lo que implica: cambiar el destinatario de una factura emitida
  es, en la norma, una rectificativa; el PDF ya enviado lleva el cliente
  anterior; si estaba contabilizada, el asiento sigue con el anterior;
- NO se hace si el régimen de IVA del cliente nuevo no casa con la factura (la
  factura llevaría el IVA mal): eso es abono y factura nueva;
- NO se hace con VERI*FACTU activo (interruptor en Configuración ERP): ahí el
  cliente es parte del registro y solo cabe la rectificativa.

Los cobros (F_LCO) van por factura, no por cliente: no se tocan.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from sqlalchemy.orm import Session

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
OBSERVACIONES = ("OB1FAC", "OB2FAC")


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
    ("CCPFAC", "cp"), ("CPRFAC", "provincia"), ("CNIFAC", "nif"),
)
CAMPO_LABEL = {
    "CLIFAC": "Cliente (código)", "CNOFAC": "Nombre", "CDOFAC": "Dirección",
    "CPOFAC": "Población", "CCPFAC": "CP", "CPRFAC": "Provincia",
    "CNIFAC": "NIF", "CPAFAC": "País",
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


def bloqueo_regimen(row: dict[str, Any], cliente: dict[str, Any], *, documento: str) -> str | None:
    """Por qué el cliente nuevo no casa con el IVA del documento, o None."""
    from app.integrations.factusol.vat_regime import (  # noqa: PLC0415
        REGIME_EXPORTACION,
        REGIME_INTRACOMUNITARIO,
        REGIME_NACIONAL,
    )

    regimen = cliente.get("regime")
    if regimen not in (REGIME_NACIONAL, REGIME_INTRACOMUNITARIO, REGIME_EXPORTACION):
        return ("El cliente nuevo no tiene en FACTUSOL un régimen de IVA reconocible "
                "(nacional, intracomunitario o exportación): revisa su ficha antes.")
    con_iva = factura_lleva_iva(row)
    if con_iva != (regimen == REGIME_NACIONAL):
        return (
            f"{documento} {'lleva' if con_iva else 'no lleva'} IVA y el cliente nuevo es de "
            f"régimen {regimen}: con él llevaría el IVA mal."
        )
    return None


def bloqueo_cliente_factura(
    session: Session, row: dict[str, Any], cliente: dict[str, Any],
) -> str | None:
    """Por qué NO se puede cambiar el cliente de esta factura en FACTUSOL, o
    None si se puede."""
    if verifactu_activo(session):
        return ("VERI*FACTU está activo: el cliente forma parte del registro de la factura y "
                "no se puede cambiar en el sitio. Hace falta una rectificativa (abono y factura "
                "nueva) desde FACTUSOL.")
    motivo = bloqueo_regimen(row, cliente, documento="La factura")
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
ORDER_CLIENT_RELINKED_EVENT = "erp.order_client_relinked"
CUSTOMER_CREATED_EVENT = "erp.order_customer_created"


def _cliente_resumen(cliente: dict[str, Any]) -> dict[str, Any]:
    return {k: cliente.get(k) or None for k in (
        "codcli", "nombre", "nif", "direccion", "cp", "ciudad", "provincia", "pais", "regime",
    )}


def cliente_revinculado(session: Session, order: Any) -> str | None:
    """El CODCLI al que se revinculó el pedido desde su ficha, si su empresa
    lo sigue teniendo. Al emitir, la factura sale a ESE cliente aunque el
    documento de origen (el F_PCL de la web o el albarán) lleve otro."""
    import json  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415

    from app.models.crm import AuditLog, Company  # noqa: PLC0415

    if not order.company_id:
        return None
    meta = session.scalar(
        select(AuditLog.metadata_json).where(
            AuditLog.action == ORDER_CLIENT_RELINKED_EVENT,
            AuditLog.target_type == "order", AuditLog.target_id == order.id,
        ).order_by(AuditLog.created_at.desc()).limit(1)
    )
    try:
        codcli = str((json.loads(meta) if meta else {}).get("to_codcli") or "").strip()
    except (TypeError, ValueError):
        return None
    company = session.get(Company, order.company_id)
    if not codcli or company is None or (company.factusol_company_id or "").strip() != codcli:
        return None
    return codcli


def _documento_origen(client: Any, order: Any, *, session: Session,
                      ejercicio: str) -> dict[str, Any] | None:
    """El documento de FACTUSOL del que saldrá la factura (aún sin emitir):
    el albarán de BoHub o el pedido de cliente F_PCL de la web. `{tipo,
    numero, codcli, row}` o None si no hay ninguno."""
    from app.erp.factusol_cobro import parse_invoice_number  # noqa: PLC0415
    from app.erp.workflow import is_web_order  # noqa: PLC0415
    from app.integrations.factusol.documents import visible_number  # noqa: PLC0415
    from app.integrations.factusol.service import (  # noqa: PLC0415
        _store_ref_prefix,
        find_pcl_by_order,
        serie_of_row,
    )

    serie, codigo = parse_invoice_number(order.factusol_albaran_number)
    if serie is not None and codigo is not None:
        rows = client.load_table("F_ALB", filtro=f"CODALB={int(codigo)}", ejercicio=ejercicio)
        row = next((r for r in rows if serie_of_row(r, "TIPALB") == serie), None)
        if row is None:
            return None
        return {"tipo": "albarán", "numero": visible_number(serie, codigo),
                "codcli": _texto(row.get("CLIALB")), "row": row}
    if is_web_order(order):
        pcl = find_pcl_by_order(client, order, ejercicio,
                                ref_prefix=_store_ref_prefix(session, order))
        if pcl is None:
            return None
        return {"tipo": "pedido de cliente", "numero": _texto(pcl.get("CODPCL")),
                "codcli": _texto(pcl.get("CLIPCL")), "row": pcl}
    return None


def plan_revincular(
    session: Session, order: Any, *, client: Any, ejercicio: str, codcli: str,
) -> dict[str, Any]:
    """Lo que pasaría al revincular el pedido al cliente `codcli` de FACTUSOL,
    sin escribir nada: el cliente de ahora y el nuevo, la ficha del CRM que
    queda vinculada (o que se crea), y —si hay factura emitida— los campos que
    cambian en ella o por qué no se puede. FacturaClienteError si no se puede
    de ninguna forma."""
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
    linked = get_linked_invoice(order)
    if linked is not None:
        row = leer_factura(client, serie=linked.serie, codigo=linked.codigo, ejercicio=ejercicio)
        cambios = cambios_cliente_factura(row, cliente)
        plan["factura"] = {
            "numero": linked.numero, "cambios": cambios,
            "bloqueo": bloqueo_cliente_factura(session, row, cliente) if cambios else None,
            "avisos": list(AVISOS_CLIENTE_FACTURA) if cambios else [],
        }
    elif is_invoiced(order):
        plan["avisos"].append(
            "El pedido está marcado como facturado fuera de BoHub: su factura no se toca. "
            "Cámbiala en FACTUSOL si hace falta.",
        )
    else:
        doc = _documento_origen(client, order, session=session, ejercicio=ejercicio)
        if doc is not None and doc["codcli"] and doc["codcli"] != code:
            motivo = bloqueo_regimen(doc["row"], cliente, documento=f"El {doc['tipo']}")
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
    return plan


def aplicar_revincular(
    session: Session, order: Any, *, client: Any, ejercicio: str, codcli: str,
    campos_esperados: list[str] | None,
) -> dict[str, Any]:
    """Revincula: primero la factura en FACTUSOL (si cambia y se puede; se
    relee después para comprobarlo) y, solo si eso fue bien, el pedido en
    BoHub (empresa del CRM, creada si hace falta). Si lo que hay en FACTUSOL
    ya no es lo que se previsualizó, no escribe nada. No hace commit."""
    from app.erp.web_order_company import (  # noqa: PLC0415
        WEB_ORDER_SYNC_SOURCE,
        _codcli_free_for,
        _company_from_customer,
    )
    from app.integrations.factusol.customers import get_customer  # noqa: PLC0415
    from app.models.crm import Company, Contact  # noqa: PLC0415

    code = str(codcli).strip()
    plan = plan_revincular(session, order, client=client, ejercicio=ejercicio, codcli=code)
    factura = plan["factura"]
    cambios = factura["cambios"] if factura else []
    if sorted(c["campo"] for c in cambios) != sorted(campos_esperados or []):
        raise FacturaClienteError(
            "cambio_en_factusol",
            "La factura ha cambiado en FACTUSOL desde la vista previa: vuelve a revisarla.",
        )
    if factura and cambios and factura["bloqueo"]:
        raise FacturaClienteError("factura_bloqueada", factura["bloqueo"])
    if plan["documento"] and plan["documento"]["bloqueo"]:
        raise FacturaClienteError("documento_bloqueado", plan["documento"]["bloqueo"])
    if plan["sin_cambios_crm"] and not cambios:
        raise FacturaClienteError("sin_cambios", "El pedido ya está con ese cliente.")

    if cambios:
        linked = factura["numero"]
        serie, _, cod = linked.partition("-")
        row = leer_factura(client, serie=int(serie), codigo=int(cod), ejercicio=ejercicio)
        escribir_cliente_factura(client, row=row, cambios=cambios, ejercicio=ejercicio)
        despues = leer_factura(client, serie=int(serie), codigo=int(cod), ejercicio=ejercicio)
        if _texto(despues.get("CLIFAC")) != code and any(c["campo"] == "CLIFAC" for c in cambios):
            raise FacturaClienteError(
                "factusol_no_cambio",
                f"FACTUSOL no ha guardado el cliente nuevo en la {linked}: revísala allí.",
            )

    creada = False
    empresa = (session.get(Company, plan["empresa"]["company_id"])
               if plan["empresa"]["company_id"] else None)
    if empresa is None:
        cust = get_customer(client, code, ejercicio=ejercicio) or {}
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
    return {**plan, "company_id": empresa.id, "company_name": empresa.name,
            "anterior": anterior, "creada": creada, "escrito_en_factura": bool(cambios)}


def cliente_para_emitir(
    client: Any, *, cabecera: dict[str, Any], codcli: str, ejercicio: str,
    origen: dict[str, Any], documento: str,
) -> dict[str, Any]:
    """Columnas del bloque de cliente que pisan las copiadas del documento de
    origen cuando el pedido se revinculó a otro cliente (`cliente_revinculado`).
    `{}` si el documento ya es de ese cliente. FactusolError si el cliente
    nuevo no casa con el IVA del documento: antes no facturar que facturar
    con el IVA mal."""
    from app.integrations.factusol.albaran_manual import customer_for_albaran  # noqa: PLC0415
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    cliente = customer_for_albaran(client, codcli=codcli, ejercicio=ejercicio)
    motivo = bloqueo_regimen(origen, cliente, documento=documento)
    if motivo:
        raise FactusolError(
            f"El pedido se revinculó al cliente {codcli}, pero {motivo[0].lower()}{motivo[1:]} "
            "No se emite: corrige el documento en FACTUSOL o vuelve a revincular.",
        )
    # El bloque entero: en un albarán con destinatario de envío lo que no se
    # mandara lo pondría la factura con el cliente fiscal ANTERIOR.
    return {c["campo"]: c["valor"]
            for c in cambios_cliente_factura(cabecera, cliente, completo=True)}
