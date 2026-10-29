"""Corregir lo emitido desde la ficha del pedido (30/10/2026): añadir una
mención legal a la factura y cambiar el cliente del pedido (y de su factura).

Escriben en FACTUSOL con la autorización expresa de Bart, y solo esto: la
observación de la factura, el bloque de cliente de la factura y, si hace
falta, el cliente nuevo de F_CLI. Siempre en dos pasos —vista previa con lo
que se va a escribir tal cual, y confirmación—, releyendo FACTUSOL en vivo
antes de escribir (si cambió desde la vista previa, no se escribe) y con
Auditoría (quién, cuándo, de qué a qué), guardada en cuanto FACTUSOL acepta
la escritura: si después falla algo, consta y el error dice qué hacer. Una
factura abonada no se toca. La lógica vive en `app/erp/factura_cliente.py`.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.erp.api.deps import require_erp_edit, require_invoice_emit
from app.erp.factura_cliente import (
    CUSTOMER_CREATED_EVENT,
    INVOICE_MENTION_EVENT,
    ORDER_CLIENT_RELINKED_EVENT,
    FacturaClienteError,
    factura_abonada,
    factura_cambiada_pedido_no,
    motivo_abonada,
)
from app.models.crm import User

router = APIRouter(prefix="/api/erp/orders", tags=["erp-order-correcciones"])

#: Códigos de FacturaClienteError → HTTP.
_HTTP = {
    "factura_no_existe": status.HTTP_404_NOT_FOUND,
    "cliente_no_existe": status.HTTP_404_NOT_FOUND,
    "mencion_no_existe": status.HTTP_404_NOT_FOUND,
    "mencion_vacia": status.HTTP_400_BAD_REQUEST,
    "mencion_larga": status.HTTP_400_BAD_REQUEST,
    "campo_no_valido": status.HTTP_400_BAD_REQUEST,
    # Ya escrito en FACTUSOL y algo falló después: el detalle dice qué hacer.
    "relectura_fallida": status.HTTP_502_BAD_GATEWAY,
    "factusol_no_cambio": status.HTTP_502_BAD_GATEWAY,
    "pedido_sin_cambiar": status.HTTP_500_INTERNAL_SERVER_ERROR,
}


def _error(exc: FacturaClienteError) -> HTTPException:
    return HTTPException(_HTTP.get(exc.code, status.HTTP_409_CONFLICT),
                         {"code": exc.code, "detail": str(exc)})


def _factusol(session: Session) -> tuple[Any, str]:
    from app.integrations.factusol.client import FactusolClient  # noqa: PLC0415
    from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

    try:
        return FactusolClient.from_settings(), ejercicio_for(session)
    except Exception as exc:  # noqa: BLE001 — sin credenciales / config
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, {
            "code": "factusol_unavailable",
            "detail": "No se ha podido consultar FACTUSOL: prueba en un momento.",
        }) from exc


def _caido(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_502_BAD_GATEWAY, {
        "code": "factusol_unreachable",
        "detail": f"FACTUSOL no responde: {str(exc)[:160]}. No se ha escrito nada.",
    })


def _pedido(session: Session, order_id: str, user: User):  # noqa: ANN202
    from app.erp.api.orders import _get_order, _require_manage  # noqa: PLC0415

    order = _get_order(session, order_id, user)
    _require_manage(user, order)
    return order


def _factura(order) -> Any:  # noqa: ANN001
    from app.erp.linked_invoice import get_linked_invoice  # noqa: PLC0415

    linked = get_linked_invoice(order)
    if linked is None:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "sin_factura", "detail": "El pedido no tiene una factura emitida en FACTUSOL.",
        })
    return linked


def _enviada(session: Session, order) -> dict[str, Any] | None:  # noqa: ANN001
    from app.erp.api.orders import _invoice_sent  # noqa: PLC0415

    return _invoice_sent(session, order)


# --- mención legal en la factura emitida --------------------------------------------


class MencionIn(BaseModel):
    """Una mención de la lista (`mencion_id`) o un texto libre (`texto`)."""

    mencion_id: str | None = Field(default=None, max_length=60)
    texto: str | None = Field(default=None, max_length=255)


class MencionAplicarIn(MencionIn):
    confirm: bool = False
    #: La observación que enseñó la vista previa (`OB1FAC`/`OB2FAC`): si en
    #: FACTUSOL ya no es la libre, no se escribe.
    campo: str = Field(pattern="^OB[12]FAC$")


def _plan_mencion(session: Session, order, payload: MencionIn) -> dict[str, Any]:  # noqa: ANN001
    from app.erp.factura_cliente import (  # noqa: PLC0415
        leer_factura,
        mencion_aplicable,
        plan_mencion,
    )
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    linked = _factura(order)
    if factura_abonada(order):
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "factura_abonada", "detail": motivo_abonada(linked.numero),
        })
    try:
        texto, mencion_id = mencion_aplicable(
            session, mencion_id=payload.mencion_id, texto_libre=payload.texto,
        )
    except FacturaClienteError as exc:
        raise _error(exc) from exc
    client, ejercicio = _factusol(session)
    try:
        row = leer_factura(client, serie=linked.serie, codigo=linked.codigo, ejercicio=ejercicio)
    except FacturaClienteError as exc:
        raise _error(exc) from exc
    except FactusolError as exc:
        raise _caido(exc) from exc
    plan = plan_mencion(row, texto)
    enviada = _enviada(session, order)
    avisos = []
    if enviada:
        avisos.append(
            "La factura ya se envió al cliente: el PDF que tiene NO lleva la mención. Si la "
            "necesita, reenvíasela desde «Enviar factura» cuando esté puesta.",
        )
    if plan["campo"] is None and not plan["ya_esta"]:
        avisos.append(
            "Las dos observaciones de la factura ya tienen texto y BoHub no pisa ninguna: "
            "añade la mención desde FACTUSOL.",
        )
    return {
        "factura": linked.numero, "texto": texto, "mencion_id": mencion_id,
        **plan, "enviada": enviada, "avisos": avisos,
        "_row": row, "_client": client, "_ejercicio": ejercicio,
    }


def _publico(plan: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in plan.items() if not k.startswith("_")}


@router.post("/{order_id}/factura/mencion/vista-previa")
def mencion_vista_previa(
    order_id: str,
    payload: MencionIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_invoice_emit),
) -> dict[str, Any]:
    """Qué se escribiría en la factura: el texto exacto, en qué observación
    (la primera vacía) y lo que hay ahora. Avisa si la factura ya se envió.
    Solo lee FACTUSOL."""
    order = _pedido(session, order_id, current_user)
    return _publico(_plan_mencion(session, order, payload))


@router.post("/{order_id}/factura/mencion")
def mencion_aplicar(
    order_id: str,
    payload: MencionAplicarIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_invoice_emit),
) -> dict[str, Any]:
    """Escribe la mención en la observación que enseñó la vista previa
    (`ActualizarRegistro` de F_FAC con SOLO esa columna), lo deja en
    Auditoría en cuanto FACTUSOL lo acepta y la relee para comprobarlo (el
    resultado se anota en ese mismo registro). Nada más cambia en la factura."""
    from app.core.audit import record_event  # noqa: PLC0415
    from app.erp.factura_cliente import (  # noqa: PLC0415
        anotar_auditoria,
        escribir_mencion,
        leer_factura,
    )
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    order = _pedido(session, order_id, current_user)
    if not payload.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {
            "code": "confirmation_required",
            "detail": "Escribir en una factura emitida requiere confirmación explícita.",
        })
    plan = _plan_mencion(session, order, payload)
    if plan["ya_esta"]:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "ya_esta", "detail": f"La factura {plan['factura']} ya lleva esa mención.",
        })
    if plan["campo"] != payload.campo:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "cambio_en_factusol",
            "detail": ("Las observaciones de la factura han cambiado en FACTUSOL desde la vista "
                       "previa: vuelve a revisarla. No se ha escrito nada."),
        })
    client, ejercicio, row = plan["_client"], plan["_ejercicio"], plan["_row"]
    linked = _factura(order)
    respuesta_perdida = False
    try:
        escribir_mencion(client, row=row, campo=payload.campo, texto=plan["texto"],
                         ejercicio=ejercicio)
    except FacturaClienteError as exc:
        raise _error(exc) from exc
    except FactusolError as exc:
        # Pudo llegar y perderse la respuesta (las escrituras no se reintentan):
        # se relee, y si la mención está, cuenta como escrita.
        try:
            ahora = leer_factura(client, serie=linked.serie, codigo=linked.codigo,
                                 ejercicio=ejercicio)
        except (FacturaClienteError, FactusolError):
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
                "code": "factusol_write_incierto",
                "detail": (f"FACTUSOL no ha respondido al escribir la mención ni al releer la "
                           f"factura {plan['factura']}: puede que se haya escrito. Compruébalo "
                           "en FACTUSOL antes de volver a intentarlo."),
            }) from exc
        if str(ahora.get(payload.campo) or "").strip() != plan["texto"]:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
                "code": "factusol_write_failed",
                "detail": f"FACTUSOL no ha aceptado el cambio: {str(exc)[:160]}",
            }) from exc
        respuesta_perdida = True
    # Escrita: a Auditoría YA, antes de releer (si la relectura falla, consta).
    log = record_event(
        session, action=INVOICE_MENTION_EVENT, target_type="order", target_id=order.id,
        actor=current_user,
        message=f"Mención legal añadida a la factura {plan['factura']} ({payload.campo})",
        metadata={
            "order_number": order.order_number, "factura": plan["factura"],
            "campo": payload.campo, "texto": plan["texto"], "mencion_id": plan["mencion_id"],
            "antes": plan["actuales"].get(payload.campo) or "", "comprobada": None,
            "ya_enviada": bool(plan["enviada"]),
            **({"respuesta_perdida": True} if respuesta_perdida else {}),
            "reason": f"«{plan['texto']}» en {payload.campo}",
        },
    )
    session.commit()
    try:
        despues = leer_factura(client, serie=linked.serie, codigo=linked.codigo,
                               ejercicio=ejercicio)
    except (FacturaClienteError, FactusolError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "relectura_fallida",
            "detail": (f"La mención se escribió en la factura {plan['factura']} "
                       f"({payload.campo}), pero FACTUSOL no responde al releerla: compruébala "
                       "allí. No la vuelvas a añadir."),
        }) from exc
    escrita = str(despues.get(payload.campo) or "").strip() == plan["texto"]
    anotar_auditoria(log, comprobada=escrita)
    session.commit()
    if not escrita:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "factusol_no_cambio",
            "detail": (f"FACTUSOL respondió bien pero al releer la {plan['factura']} la mención no "
                       "está: revísala allí."),
        })
    return {"ok": True, "factura": plan["factura"], "campo": payload.campo,
            "texto": plan["texto"], "ya_enviada": bool(plan["enviada"])}


# --- cambiar el cliente del pedido (y de su factura) --------------------------------


class ClienteIn(BaseModel):
    codcli: str = Field(min_length=1, max_length=20)


class ClienteAplicarIn(ClienteIn):
    confirm: bool = False
    #: Lo que enseñó la vista previa de la factura: campo → valor nuevo
    #: (`despues`). Vacío si no tenía factura o no cambiaba. Si FACTUSOL ya
    #: no da eso (campos o valores), no se escribe.
    campos_factura: dict[str, str] = Field(default_factory=dict, max_length=20)


def _puede_tocar_factura(user: User) -> bool:
    from app.erp.capabilities import Cap, has_capability  # noqa: PLC0415

    return has_capability(user, Cap.INVOICE_EMIT)


@router.post("/{order_id}/cliente/vista-previa")
def cliente_vista_previa(
    order_id: str,
    payload: ClienteIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Qué pasaría al revincular el pedido al cliente `codcli` de FACTUSOL: el
    cliente de ahora y el nuevo, la ficha del CRM que queda (o que se crea) y,
    si hay factura emitida, los campos exactos que cambian en ella, sus avisos
    o por qué no se puede. Solo lee."""
    from app.erp.factura_cliente import plan_revincular  # noqa: PLC0415
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    order = _pedido(session, order_id, current_user)
    client, ejercicio = _factusol(session)
    try:
        plan = plan_revincular(session, order, client=client, ejercicio=ejercicio,
                               codcli=payload.codcli)
    except FacturaClienteError as exc:
        raise _error(exc) from exc
    except FactusolError as exc:
        raise _caido(exc) from exc
    if plan["factura"] and plan["factura"]["cambios"]:
        plan["factura"]["enviada"] = _enviada(session, order)
        plan["factura"]["puedes"] = _puede_tocar_factura(current_user)
    return plan


@router.post("/{order_id}/cliente")
def cliente_aplicar(
    order_id: str,
    payload: ClienteAplicarIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Revincula el pedido: primero, si tiene factura emitida y cambia, la
    factura en FACTUSOL (`CLIFAC` y el bloque de cliente; queda en Auditoría
    `erp.invoice_client_changed` en cuanto FACTUSOL lo acepta y se relee para
    comprobarlo); después el pedido en BoHub. Auditoría con de qué cliente a
    cuál y los campos de la factura antes y después."""
    from app.core.audit import record_event  # noqa: PLC0415
    from app.erp.factura_cliente import aplicar_revincular  # noqa: PLC0415
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415

    order = _pedido(session, order_id, current_user)
    if not payload.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {
            "code": "confirmation_required",
            "detail": "Cambiar el cliente requiere confirmación explícita.",
        })
    if payload.campos_factura and not _puede_tocar_factura(current_user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, {
            "code": "sin_permiso_factura",
            "detail": "Cambiar el cliente de una factura emitida es de quien emite facturas.",
        })
    client, ejercicio = _factusol(session)
    try:
        res = aplicar_revincular(
            session, order, client=client, ejercicio=ejercicio, codcli=payload.codcli,
            campos_esperados=payload.campos_factura, actor=current_user,
        )
    except FacturaClienteError as exc:
        session.rollback()
        raise _error(exc) from exc
    except FactusolError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "factusol_write_failed",
            "detail": (f"FACTUSOL no ha aceptado el cambio: {str(exc)[:160]}. El pedido sigue "
                       "con su cliente."),
        }) from exc
    actual = res["actual"] or {}
    cliente = res["cliente"]
    factura = res["factura"]
    en_factura = res["escrito_en_factura"] or res["factura_ya_cambiada"]
    try:
        record_event(
            session, action=ORDER_CLIENT_RELINKED_EVENT, target_type="order",
            target_id=order.id, actor=current_user,
            message=(f"Cliente del pedido {order.order_number}: de "
                     f"«{actual.get('nombre') or '—'}» a «{cliente['nombre']}» "
                     f"({cliente['codcli']})"
                     + (f"; también en la factura {factura['numero']}" if en_factura else "")),
            metadata={
                "order_number": order.order_number,
                "from_company": actual.get("company_id"), "from_name": actual.get("nombre"),
                "from_codcli": actual.get("codcli"),
                "to_company": res["company_id"], "to_name": res["company_name"],
                "to_codcli": cliente["codcli"], "empresa_creada": res["creada"],
                "factura": factura["numero"] if factura else None,
                "cambios_factura": ([{k: c[k] for k in ("campo", "antes", "despues")}
                                     for c in factura["cambios"]]
                                    if res["escrito_en_factura"] else []),
                # Reintento: la factura se cambió en un intento anterior (ver
                # su `erp.invoice_client_changed`).
                "factura_ya_cambiada": res["factura_ya_cambiada"],
                "documento": res["documento"],
                "reason": (f"De «{actual.get('nombre') or '—'}» a «{cliente['nombre']}»"
                           + (" (factura incluida)" if en_factura else "")),
            },
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        if not en_factura:
            raise
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, {
            "code": "pedido_sin_cambiar", "detail": factura_cambiada_pedido_no(factura["numero"]),
        }) from exc
    return {
        "ok": True, "company_id": res["company_id"], "company_name": res["company_name"],
        "codcli": cliente["codcli"], "empresa_creada": res["creada"],
        "factura": factura["numero"] if factura else None,
        "escrito_en_factura": en_factura,
    }


# --- crear el cliente en FACTUSOL ---------------------------------------------------


class ClienteNuevoIn(BaseModel):
    """Lo mínimo para el alta (la razón social y el CIF son obligatorios: sin
    CIF no se puede comprobar que no exista ya)."""

    nombre: str = Field(min_length=2, max_length=255)
    nif: str = Field(min_length=3, max_length=40)
    direccion: str = Field(default="", max_length=255)
    cp: str = Field(default="", max_length=20)
    ciudad: str = Field(default="", max_length=120)
    provincia: str = Field(default="", max_length=120)
    pais: str = Field(default="ES", max_length=64)
    email: str | None = Field(default=None, max_length=255)
    telefono: str | None = Field(default=None, max_length=40)


class ClienteNuevoAplicarIn(ClienteNuevoIn):
    confirm: bool = False


def _datos_alta(payload: ClienteNuevoIn) -> dict[str, Any]:
    from app.erp.company_discovery import nif_key, nif_looks_malformed  # noqa: PLC0415

    data = {k: (v.strip() if isinstance(v, str) else v)
            for k, v in payload.model_dump(exclude={"confirm"}).items()}
    # Con un CIF de relleno («...», espacios) la búsqueda no encuentra a nadie
    # y se crearía un duplicado: no se sigue.
    clave = nif_key(data["nif"])
    if clave is None or nif_looks_malformed(clave):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {
            "code": "nif_no_valido",
            "detail": ("El CIF no parece válido (al menos 5 letras o números, alguno de ellos "
                       "un número): sin él no se puede comprobar que el cliente no exista ya."),
        })
    # El NIF-IVA es el CIF tal cual: con prefijo de país (FR…, BE…) decide el
    # régimen intracomunitario.
    data["vat"] = data["nif"]
    return data


def _existentes(session: Session, client: Any, ejercicio: str, nif: str) -> dict[str, Any]:
    """Quién tiene ya ese CIF: en FACTUSOL (todos los CODCLI) y en el CRM."""
    from app.api.companies import find_companies_by_nif  # noqa: PLC0415
    from app.integrations.factusol.customers import search_customers  # noqa: PLC0415

    factusol = [
        {"codcli": c.get("codcli"), "nombre": c.get("nombre") or c.get("nofcli"),
         "nif": c.get("nif")}
        for c in search_customers(client, nif, by="nif", ejercicio=ejercicio)
    ]
    crm = [{"company_id": c.id, "nombre": c.name,
            "codcli": (c.factusol_company_id or "").strip() or None}
           for c in find_companies_by_nif(session, nif) if not c.is_archived]
    return {"factusol": factusol, "crm": crm}


@router.post("/{order_id}/cliente/nuevo/vista-previa")
def cliente_nuevo_vista_previa(
    order_id: str,
    payload: ClienteNuevoIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """ANTES de ofrecer crear: ¿existe ya un cliente con ese CIF en FACTUSOL
    (o una empresa en el CRM)? Si existe, se dice cuál y no se ofrece crear.
    Si no, los campos exactos de F_CLI que se crearían. Solo lee."""
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.customers import (  # noqa: PLC0415
        build_customer_payload,
        customer_regime,
    )
    from app.integrations.factusol.vat_regime import REGIME_LABELS  # noqa: PLC0415

    _pedido(session, order_id, current_user)
    data = _datos_alta(payload)
    client, ejercicio = _factusol(session)
    try:
        existentes = _existentes(session, client, ejercicio, data["nif"])
    except FactusolError as exc:
        raise _caido(exc) from exc
    if existentes["factusol"]:
        return {"existe": True, **existentes, "campos": []}
    registro = build_customer_payload(data, "(el siguiente libre)")
    regime = customer_regime(data)
    return {
        "existe": False, **existentes,
        "campos": [{"campo": k, "valor": str(v)} for k, v in registro.items()],
        "regime": regime, "regime_label": REGIME_LABELS.get(regime, regime),
    }


@router.post("/{order_id}/cliente/nuevo", status_code=201)
def cliente_nuevo_crear(
    order_id: str,
    payload: ClienteNuevoAplicarIn,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_erp_edit),
) -> dict[str, Any]:
    """Crea el cliente en F_CLI (solo si sigue sin existir ese CIF: se
    comprueba otra vez justo antes) y lo deja en Auditoría. No vincula nada:
    después se revincula el pedido con su vista previa."""
    from app.core.audit import record_event  # noqa: PLC0415
    from app.integrations.factusol.client import FactusolError  # noqa: PLC0415
    from app.integrations.factusol.customers import create_customer  # noqa: PLC0415

    order = _pedido(session, order_id, current_user)
    if not payload.confirm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, {
            "code": "confirmation_required",
            "detail": "Crear un cliente en FACTUSOL requiere confirmación explícita.",
        })
    data = _datos_alta(payload)
    client, ejercicio = _factusol(session)
    try:
        existentes = _existentes(session, client, ejercicio, data["nif"])
        if existentes["factusol"]:
            primero = existentes["factusol"][0]
            raise HTTPException(status.HTTP_409_CONFLICT, {
                "code": "ya_existe", "codcli": primero["codcli"],
                "detail": (f"Ya hay un cliente con ese CIF en FACTUSOL: {primero['nombre']} "
                           f"({primero['codcli']}). Usa ese; no se crea otro."),
            })
        codcli, creado = create_customer(client, data, ejercicio=ejercicio)
    except FactusolError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, {
            "code": "factusol_create_failed",
            "detail": f"FACTUSOL no ha creado el cliente: {str(exc)[:200]}",
        }) from exc
    if creado:
        record_event(
            session, action=CUSTOMER_CREATED_EVENT, target_type="order", target_id=order.id,
            actor=current_user,
            message=f"Cliente {codcli} creado en FACTUSOL desde el pedido {order.order_number}",
            metadata={"order_number": order.order_number, "codcli": codcli,
                      "nombre": data["nombre"], "nif": data["nif"],
                      "reason": f"Alta en FACTUSOL de «{data['nombre']}» ({codcli})"},
        )
        session.commit()
    return {"codcli": codcli, "creado": creado}
