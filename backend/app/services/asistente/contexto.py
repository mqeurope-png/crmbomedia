"""Lo que se le pone delante al modelo para redactar: el hilo entero, en
orden, con lo que contestamos nosotros; la ficha del contacto (empresa,
idioma, propietario, etapa del pipeline, pedidos con fechas y máquina,
facturas pendientes, clasificación de la IA, últimos asuntos tratados); la
máquina que sale de sus pedidos (clave para la KB de FLUX); los enlaces de la
base de conocimiento; el idioma del mensaje (no el de la ficha); y quién
pulsa (su nombre y su remitente). Un remitente desconocido funciona con
menos: solo el hilo.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.erp.models.orders import Order, PaymentStatus
from app.models.crm import (
    Contact,
    ContactPipelineStage,
    EmailDirection,
    EmailMessage,
    EmailSignature,
    EmailThread,
    Pipeline,
    PipelineStage,
    User,
    UserEmailAliasPref,
)
from app.models.ia import ALCANCE_CONTACTO
from app.models.leads import LeadClassification
from app.services.asistente.texto import recortar, sin_cita, texto_de_mensaje
from app.services.conocimiento import enlaces as kb_enlaces
from app.services.conocimiento import flux
from app.services.leads.clasificador import detectar_idioma
from app.services.web_forms import sitios as registro_sitios
from app.services.web_forms.sitios import ORIGEN_PREFIJO, clave_de_sitio, es_del_grupo

logger = logging.getLogger(__name__)

#: Un mensaje del hilo entra entero salvo que sea enorme (boletines, HTML
#: con todo citado): a partir de aquí se recorta por el final.
MAX_CHARS_MENSAJE = 6000
ASUNTOS_RECIENTES = 15
PEDIDOS_MAX = 10
PAGO_PENDIENTE = (PaymentStatus.PENDING, PaymentStatus.PARTIAL_PAID, PaymentStatus.FAILED)


@dataclass
class Mensaje:
    id: str
    fecha: datetime | None
    #: `cliente` (la otra parte) o `nosotros` (una dirección del grupo).
    lado: str
    de: str
    para: list[str]
    asunto: str
    texto: str

    def como_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "fecha": self.fecha.isoformat() if self.fecha else None,
            "lado": self.lado, "de": self.de, "asunto": self.asunto, "caracteres": len(self.texto),
        }


@dataclass
class Contexto:
    thread: EmailThread
    mensajes: list[Mensaje]
    ultimo_del_cliente: Mensaje | None
    idioma: str
    idioma_fuente: str
    alcance: str = ALCANCE_CONTACTO
    contacto: dict[str, Any] | None = None
    pedidos: list[dict[str, Any]] = field(default_factory=list)
    facturas_pendientes: list[dict[str, Any]] = field(default_factory=list)
    maquina: str | None = None
    modelo_flux: str | None = None
    clasificacion: dict[str, Any] | None = None
    etapa: str | None = None
    asuntos_recientes: list[dict[str, Any]] = field(default_factory=list)
    sitio: str | None = None
    interes: str | None = None
    enlaces: dict[str, Any] = field(default_factory=dict)
    usuario: dict[str, Any] = field(default_factory=dict)
    destinatario: str | None = None
    asunto_respuesta: str = ""
    ejemplo_estilo: str | None = None
    #: C bis: correos viejos del contacto que vienen a cuento (con cuerpo) y
    #: los de la empresa (asunto y fecha, etiquetados).
    historico: list[dict[str, Any]] = field(default_factory=list)
    empresa_asuntos: list[dict[str, Any]] = field(default_factory=list)
    recortes: list[str] = field(default_factory=list)

    def enlaces_lista(self) -> list[dict[str, Any]]:
        """Todos los enlaces que el modelo puede usar, con su etiqueta."""
        salida: list[dict[str, Any]] = []
        vistos: set[str] = set()

        def _añadir(e: dict[str, Any] | None, etiqueta: str) -> None:
            if not e or not e.get("url") or e["url"] in vistos:
                return
            vistos.add(e["url"])
            salida.append({"url": e["url"], "etiqueta": etiqueta, "origen": e.get("origen")})

        _añadir(self.enlaces.get("soporte"), "página de soporte")
        _añadir(self.enlaces.get("producto"),
                f"página de producto ({self.interes})" if self.interes else "página de producto")
        bloque = self.enlaces.get("flux") or {}
        if bloque:
            elegido = bloque.get("enlace")
            etiqueta = (
                f"KB de FLUX · {bloque.get('modelo') or 'portada'}"
                if bloque.get("intencion") == flux.INTENCION_KB else "soporte FLUX (nuestro)"
            )
            _añadir(elegido, etiqueta)
            for a in bloque.get("articulos") or []:
                _añadir({"url": a["url"], "origen": "flux_articulo"},
                        f"artículo FLUX: {a['titulo']}")
        return salida

    def resumen(self) -> dict[str, Any]:
        """Lo que se guarda con el borrador y se enseña debajo: qué entró."""
        return {
            "alcance": self.alcance,
            "idioma": self.idioma, "idioma_fuente": self.idioma_fuente,
            "mensajes_hilo": len(self.mensajes),
            "hilo": [m.como_dict() for m in self.mensajes],
            "contacto": (
                {k: self.contacto.get(k) for k in ("id", "nombre", "email", "empresa", "idioma")}
                if self.contacto else None
            ),
            "pedidos": len(self.pedidos), "facturas_pendientes": len(self.facturas_pendientes),
            "maquina": self.maquina, "modelo_flux": self.modelo_flux,
            "clasificacion": self.clasificacion, "etapa": self.etapa,
            "asuntos_recientes": len(self.asuntos_recientes),
            "sitio": self.sitio, "interes": self.interes,
            "enlaces": self.enlaces_lista(),
            "historico": self.historico, "empresa": self.empresa_asuntos,
            "recortes": self.recortes,
            "ejemplo_estilo": bool(self.ejemplo_estilo),
        }


# --- piezas ------------------------------------------------------------------------


def _direcciones(raw: str | None) -> list[str]:
    try:
        datos = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(d) for d in datos if str(d or "").strip()] if isinstance(datos, list) else []


def mensajes_del_hilo(thread: EmailThread, dominios: set[str]) -> list[Mensaje]:
    """Todos los mensajes, en orden, con su lado. Nuestro lado es cualquier
    dirección del grupo (la captura universal guarda también lo que
    contestamos desde Gmail, que llega marcado como entrada)."""
    salida: list[Mensaje] = []
    for m in thread.messages:
        if m.sent_at is None and getattr(m, "scheduled_for", None) is not None:
            continue                                 # programado: aún no ha salido
        nuestro = (m.direction == EmailDirection.OUTBOUND) or es_del_grupo(m.from_email, dominios)
        texto = sin_cita(texto_de_mensaje(m.body_text, m.body_html))
        salida.append(Mensaje(
            id=m.id, fecha=m.sent_at or m.created_at, lado="nosotros" if nuestro else "cliente",
            de=m.from_email or "", para=_direcciones(m.to_emails_json),
            asunto=m.subject or thread.subject or "", texto=recortar(texto, MAX_CHARS_MENSAJE),
        ))
    return salida


def idioma_del_mensaje(mensajes: list[Mensaje], contacto: Contact | None) -> tuple[str, str]:
    """El idioma del último mensaje del cliente, no el de la ficha. Si el
    texto no delata ninguno, el de la ficha; y si no, castellano."""
    del_cliente = [m for m in mensajes if m.lado == "cliente"]
    for m in reversed(del_cliente):
        idioma, puntos, _ = detectar_idioma(f"{m.asunto}\n{m.texto}")
        if idioma and puntos >= 2:
            return idioma, "mensaje"
    if contacto is not None and contacto.language:
        return contacto.language.lower()[:2], "ficha"
    return "es", "defecto"


def _usuario_nombre(session: Session, user_id: str | None) -> str | None:
    if not user_id:
        return None
    u = session.get(User, user_id)
    return (u.full_name or u.email) if u is not None else None


def ficha(session: Session, contacto: Contact) -> dict[str, Any]:
    empresa = contacto.company.name if contacto.company is not None else None
    return {
        "id": contacto.id,
        "nombre": " ".join(p for p in (contacto.first_name, contacto.last_name) if p).strip(),
        "email": contacto.email, "telefono": contacto.phone, "empresa": empresa,
        "cargo": contacto.job_title,
        "pais": contacto.address_country_name or contacto.address_country,
        "idioma": (contacto.language or "").lower()[:2] or None,
        "propietario": _usuario_nombre(session, contacto.owner_user_id),
        "estado_comercial": contacto.commercial_status,
    }


def pedidos_de(session: Session, contacto: Contact) -> list[dict[str, Any]]:
    filas = session.scalars(
        select(Order).where(Order.contact_id == contacto.id)
        .options(selectinload(Order.lines))
        .order_by(func.coalesce(Order.placed_at, Order.created_at).desc()).limit(PEDIDOS_MAX)
    ).all()
    salida: list[dict[str, Any]] = []
    for o in filas:
        lineas = [
            {"descripcion": ln.description, "cantidad": float(ln.quantity or 0),
             "precio": float(ln.unit_price or 0)}
            for ln in sorted(o.lines, key=lambda x: x.position)
            if not ln.is_shipping and (ln.description or "").strip()
        ][:6]
        fecha = o.placed_at or o.created_at
        salida.append({
            "numero": o.order_number, "fecha": fecha.date().isoformat() if fecha else None,
            "total": float(o.total_amount or 0), "moneda": o.currency,
            "pago": o.payment_status.value if o.payment_status else None,
            "preparacion": o.preparation_status.value if o.preparation_status else None,
            "transporte": o.transport_status.value if o.transport_status else None,
            "origen": o.external_source.value if o.external_source else None,
            "cancelado": o.cancelled_at is not None, "lineas": lineas,
            "serie": o.serial_number,
        })
    return salida


def facturas_pendientes_de(pedidos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """BoHub no tiene tabla de facturas: lo pendiente de cobro son los
    pedidos no cancelados con el pago sin completar."""
    pendientes = {p.value for p in PAGO_PENDIENTE}
    return [
        {"numero": p["numero"], "fecha": p["fecha"], "total": p["total"], "moneda": p["moneda"],
         "pago": p["pago"]}
        for p in pedidos if p["pago"] in pendientes and not p["cancelado"]
    ]


def maquina_de(pedidos: list[dict[str, Any]], categorias: list[flux.Categoria]) -> tuple[str | None,
                                                                                        str | None]:
    """`(máquina, modelo FLUX)`: la primera línea de producto del pedido más
    reciente, y el modelo de FLUX si alguna línea de algún pedido lo nombra."""
    maquina = None
    modelo = None
    for p in pedidos:
        for ln in p["lineas"]:
            if maquina is None and not p["cancelado"]:
                maquina = ln["descripcion"]
            cat = flux.modelo_en_texto(ln["descripcion"], categorias)
            if cat is not None and modelo is None:
                modelo = cat.modelo
    return maquina, modelo


def clasificacion_de(session: Session, contacto: Contact) -> dict[str, Any] | None:
    fila = session.scalars(
        select(LeadClassification).where(LeadClassification.contact_id == contacto.id)
        .order_by(LeadClassification.created_at.desc()).limit(1)
    ).first()
    if fila is None:
        return None
    return {
        "idioma": fila.idioma_efectivo, "interes": fila.interes_efectivo,
        "intereses": fila.intereses_efectivos, "es_spam": fila.es_spam_efectivo,
        "confianza": round(float(fila.confidence or 0), 2),
        "cuando": fila.created_at.isoformat() if fila.created_at else None,
    }


def etapa_de(session: Session, contacto: Contact) -> str | None:
    filas = session.execute(
        select(Pipeline.name, PipelineStage.name, ContactPipelineStage.entered_stage_at)
        .join(PipelineStage, PipelineStage.id == ContactPipelineStage.stage_id)
        .join(Pipeline, Pipeline.id == ContactPipelineStage.pipeline_id)
        .where(ContactPipelineStage.contact_id == contacto.id)
        .order_by(ContactPipelineStage.entered_stage_at.desc())
    ).all()
    if not filas:
        return None
    partes = []
    for pipeline, etapa, desde in filas:
        fecha = f" (desde {desde.date().isoformat()})" if desde else ""
        partes.append(f"{pipeline} · {etapa}{fecha}")
    return "; ".join(partes)


def asuntos_recientes_de(
    session: Session, contacto: Contact, *, excluir_thread_id: str, limite: int = ASUNTOS_RECIENTES,
) -> list[dict[str, Any]]:
    """Los últimos asuntos tratados con el contacto: fecha, lado y asunto.
    Títulos, no cuerpos (igual que el índice de FLUX)."""
    hilos = session.execute(
        select(EmailThread.id, EmailThread.subject, EmailThread.last_message_at)
        .where(EmailThread.contact_id == contacto.id, EmailThread.id != excluir_thread_id)
        .order_by(EmailThread.last_message_at.desc()).limit(limite)
    ).all()
    if not hilos:
        return []
    ids = [h[0] for h in hilos]
    ultimos: dict[str, tuple[datetime | None, str]] = {}
    for tid, direccion, sent_at in session.execute(
        select(EmailMessage.thread_id, EmailMessage.direction, EmailMessage.sent_at)
        .where(EmailMessage.thread_id.in_(ids))
    ).all():
        anterior = ultimos.get(tid)
        if anterior is None or (sent_at and (anterior[0] is None or sent_at > anterior[0])):
            lado = "nosotros" if direccion == EmailDirection.OUTBOUND else "cliente"
            ultimos[tid] = (sent_at, lado)
    return [
        {"thread_id": tid, "fecha": fecha.date().isoformat() if fecha else None,
         "asunto": asunto or "(sin asunto)", "lado": ultimos.get(tid, (None, "cliente"))[1]}
        for tid, asunto, fecha in hilos
    ]


def sitio_de(contacto: Contact | None, mensajes: list[Mensaje], thread: EmailThread) -> str | None:
    """La web por la que llegó: la dirección nuestra a la que escribió (su
    dominio) y, si no, el formulario de origen de la ficha."""
    reg = registro_sitios.registro()
    for m in reversed(thread.messages):
        if m.delivered_to:
            sitio = reg.por_dominio(m.delivered_to)
            if sitio is not None and sitio.tipo == registro_sitios.TIPO_WEB:
                return sitio.clave
    for m in mensajes:
        if m.lado == "nosotros":
            sitio = reg.por_dominio(m.de)
            if sitio is not None and sitio.tipo == registro_sitios.TIPO_WEB:
                return sitio.clave
    origen = (contacto.origin or "") if contacto is not None else ""
    if origen.startswith(f"{ORIGEN_PREFIJO}:"):
        return clave_de_sitio(origen.split(":", 1)[1])
    return None


def quien_pulsa(session: Session, usuario: User) -> dict[str, Any]:
    remitente = session.scalar(
        select(UserEmailAliasPref.alias_email).where(
            UserEmailAliasPref.user_id == usuario.id, UserEmailAliasPref.is_allowed.is_(True),
        ).order_by(UserEmailAliasPref.is_default.desc()).limit(1)
    )
    firma = session.scalar(
        select(EmailSignature).where(
            EmailSignature.user_id == usuario.id, EmailSignature.is_default.is_(True)).limit(1)
    )
    nombre = (usuario.full_name or usuario.email or "").strip()
    return {
        "id": usuario.id, "nombre": nombre, "nombre_pila": nombre.split(" ")[0] if nombre else "",
        "email": usuario.email, "remitente": remitente or usuario.email,
        "firma": bool(firma),
    }


def destinatario_de(session: Session, thread: EmailThread, usuario: User,
                    mensajes: list[Mensaje]) -> str | None:
    try:
        from app.api.emails import _reply_to_suggestion  # noqa: PLC0415

        sugerido = _reply_to_suggestion(session, thread, usuario)
    except Exception:  # noqa: BLE001 — nunca sin destinatario por esto
        sugerido = None
    if sugerido:
        return sugerido
    for m in reversed(mensajes):
        if m.lado == "cliente" and m.de:
            return m.de
    return None


def asunto_respuesta(thread: EmailThread) -> str:
    asunto = (thread.subject or "").strip()
    if not asunto:
        return ""
    return asunto if asunto.lower().startswith(("re:", "aw:", "antw:")) else f"Re: {asunto}"


def ejemplo_estilo_de(session: Session, intereses: list[str], idioma: str) -> str | None:
    """Una de las 30 plantillas del equipo, la del interés y el idioma del
    lead si la hay: le marca al modelo el tono y la longitud."""
    try:
        from app.services.html_sanitizer import html_to_text  # noqa: PLC0415
        from app.services.leads import intereses as cat_intereses  # noqa: PLC0415
        from app.services.leads import plantillas  # noqa: PLC0415
        from app.services.leads.config import configuracion  # noqa: PLC0415

        if not intereses:
            return None
        cfg = configuracion(session)
        tpl = plantillas.plantilla_para(
            session, intereses, idioma, mapa=cfg.get("mapa"),
            catalogo=cat_intereses.cargar(session))
        if tpl is None or not getattr(tpl, "body_html", None):
            return None
        return recortar(html_to_text(tpl.body_html), 900)
    except Exception:  # noqa: BLE001 — el ejemplo es opcional
        logger.debug("asistente: sin ejemplo de estilo", exc_info=True)
        return None


# --- el contexto entero -------------------------------------------------------------


def construir(
    session: Session, *, thread: EmailThread, usuario: User, alcance: str = ALCANCE_CONTACTO,
) -> Contexto:
    dominios = registro_sitios.dominios_del_grupo(session)
    mensajes = mensajes_del_hilo(thread, dominios)
    contacto = session.get(Contact, thread.contact_id) if thread.contact_id else None
    if contacto is None:
        # Remitente desconocido: lo que la captura casó por dirección.
        for m in thread.messages:
            if m.contact_id:
                contacto = session.get(Contact, m.contact_id)
                break
    idioma, fuente = idioma_del_mensaje(mensajes, contacto)
    ultimo = next((m for m in reversed(mensajes) if m.lado == "cliente"), None)
    ctx = Contexto(
        thread=thread, mensajes=mensajes, ultimo_del_cliente=ultimo, idioma=idioma,
        idioma_fuente=fuente, alcance=alcance,
    )
    ctx.usuario = quien_pulsa(session, usuario)
    ctx.destinatario = destinatario_de(session, thread, usuario, mensajes)
    ctx.asunto_respuesta = asunto_respuesta(thread)
    ctx.sitio = sitio_de(contacto, mensajes, thread)
    categorias = flux.cargar_categorias(session)
    if contacto is not None:
        ctx.contacto = ficha(session, contacto)
        ctx.pedidos = pedidos_de(session, contacto)
        ctx.facturas_pendientes = facturas_pendientes_de(ctx.pedidos)
        ctx.maquina, ctx.modelo_flux = maquina_de(ctx.pedidos, categorias)
        ctx.clasificacion = clasificacion_de(session, contacto)
        ctx.etapa = etapa_de(session, contacto)
        ctx.asuntos_recientes = asuntos_recientes_de(session, contacto, excluir_thread_id=thread.id)
        intereses = (ctx.clasificacion or {}).get("intereses") or (
            [contacto.lead_interest] if contacto.lead_interest else [])
        ctx.interes = intereses[0] if intereses else None
        ctx.ejemplo_estilo = ejemplo_estilo_de(session, intereses, idioma)
    texto_cliente = ultimo.texto if ultimo else ""
    ctx.enlaces = kb_enlaces.enlaces_para(
        session, sitio=ctx.sitio, idioma=idioma, interes=ctx.interes, texto=texto_cliente,
        modelo=ctx.modelo_flux,
    )
    return ctx
