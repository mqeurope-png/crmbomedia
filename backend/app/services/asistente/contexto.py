"""Lo que se le pone delante al modelo para redactar: el hilo entero, en
orden, con lo que contestamos nosotros; la ficha del contacto entera (empresa,
idioma, propietario, teléfonos, etiquetas, campos personalizados, las notas
internas del equipo, etapa del pipeline, pedidos con fechas y máquina,
facturas pendientes, clasificación de la IA, últimos asuntos tratados); la
máquina que sale de sus pedidos (clave para la KB de FLUX); los enlaces de la
base de conocimiento; el idioma del mensaje (no el de la ficha); y quién
pulsa (su nombre y su remitente). Un remitente desconocido funciona con
menos: solo el hilo.

Sin hilo (un correo nuevo desde Redactar o desde la ficha) el Nivel 1 queda
vacío y los otros tres funcionan igual: el contexto lo dispara el
destinatario (su ficha, si es un contacto), el idioma sale de la ficha o de lo
último que escribió esa persona, y los correos viejos se eligen comparando con
la instrucción de quien envía, que es lo único que dice de qué va el correo.
"""
from __future__ import annotations

import html as html_mod
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.erp.models.orders import Order, PaymentStatus
from app.models.crm import (
    Contact,
    ContactPhone,
    ContactPipelineStage,
    ContactTag,
    EmailDirection,
    EmailMessage,
    EmailSignature,
    EmailThread,
    Note,
    Pipeline,
    PipelineStage,
    Tag,
    User,
    UserEmailAliasPref,
)
from app.models.ia import ALCANCE_CONTACTO, ALCANCE_EMPRESA, ALCANCE_HILO
from app.models.leads import LeadClassification
from app.services.asistente import historico as hist
from app.services.asistente.config import configuracion as config_asistente
from app.services.asistente.texto import recortar, sin_cita, texto_de_mensaje
from app.services.conocimiento import catalogo as kb_catalogo
from app.services.conocimiento import enlaces as kb_enlaces
from app.services.conocimiento import flux
from app.services.contact_sync_protection import custom_value_is_empty, decode_custom_fields
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
#: Las notas internas que entran (las fijadas primero, luego las más
#: recientes) y lo que entra de cada una; el presupuesto quita las más viejas.
NOTAS_MAX = 10
NOTA_MAX_CHARS = 800
#: Campos personalizados y etiquetas: cuántos y cuánto de cada valor.
CAMPOS_MAX = 40
CAMPO_MAX_CHARS = 300
ETIQUETAS_MAX = 40


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
    #: None en un correo nuevo, sin hilo.
    thread: EmailThread | None
    mensajes: list[Mensaje]
    ultimo_del_cliente: Mensaje | None
    idioma: str
    idioma_fuente: str
    alcance: str = ALCANCE_CONTACTO
    #: Lo que la persona escribió en la caja; manda sobre lo que se deduzca del hilo.
    instruccion: str | None = None
    #: El destinatario es una dirección del grupo: un compañero, no un cliente.
    destinatario_interno: bool = False
    contacto: dict[str, Any] | None = None
    #: El resto de la ficha: teléfonos (`{numero, etiqueta}`), etiquetas,
    #: campos personalizados con valor (`{clave, valor}`) y las notas internas
    #: del equipo (`{fecha, autor, fijada, origen, texto}`).
    telefonos: list[dict[str, Any]] = field(default_factory=list)
    etiquetas: list[str] = field(default_factory=list)
    campos: list[dict[str, str]] = field(default_factory=list)
    notas: list[dict[str, Any]] = field(default_factory=list)
    pedidos: list[dict[str, Any]] = field(default_factory=list)
    facturas_pendientes: list[dict[str, Any]] = field(default_factory=list)
    maquina: str | None = None
    modelo_flux: str | None = None
    clasificacion: dict[str, Any] | None = None
    etapa: str | None = None
    asuntos_recientes: list[dict[str, Any]] = field(default_factory=list)
    sitio: str | None = None
    interes: str | None = None
    #: Todos los intereses del lead (el principal primero): deciden las máquinas.
    intereses: list[str] = field(default_factory=list)
    enlaces: dict[str, Any] = field(default_factory=dict)
    #: El catálogo que entra: máquinas de su interés, consumibles de su máquina,
    #: avisos de IVA (`conocimiento.catalogo.para_asistente`). Los precios del
    #: borrador salen de aquí o no salen.
    catalogo: dict[str, Any] | None = None
    usuario: dict[str, Any] = field(default_factory=dict)
    destinatario: str | None = None
    asunto_respuesta: str = ""
    ejemplo_estilo: str | None = None
    #: Nivel 3: correos viejos del contacto que vienen a cuento (con cuerpo).
    historico: list[dict[str, Any]] = field(default_factory=list)
    #: Nivel 4: otras personas de la misma empresa (asunto y fecha, etiquetados;
    #: con cuerpo solo en «toda la empresa»).
    empresa_asuntos: list[dict[str, Any]] = field(default_factory=list)
    empresa: str | None = None
    #: Lo que se dejó fuera por el presupuesto, y los tokens estimados finales.
    recortes: list[str] = field(default_factory=list)
    presupuesto_tokens: int = 0
    tokens_estimados: int = 0
    niveles: list[int] = field(default_factory=lambda: [1])

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
        for p in self.productos_catalogo():
            if p.get("enlace"):
                _añadir({"url": p["enlace"], "origen": f"catalogo_{p.get('fuente') or ''}"},
                        f"{p.get('nombre')} ({p.get('marca')})"
                        + (f", en {p['idioma_enlace']}" if p.get("idioma_enlace") else ""))
        return salida

    def productos_catalogo(self) -> list[dict[str, Any]]:
        """Máquinas y consumibles del catálogo que entran, en orden."""
        if not self.catalogo:
            return []
        return [*(self.catalogo.get("maquinas") or []), *(self.catalogo.get("consumibles") or [])]

    def resumen(self) -> dict[str, Any]:
        """Lo que se guarda con el borrador y se enseña debajo: qué entró."""
        return {
            "alcance": self.alcance,
            "sin_hilo": self.thread is None,
            "instruccion": self.instruccion,
            "destinatario": self.destinatario,
            "destinatario_interno": self.destinatario_interno,
            "idioma": self.idioma, "idioma_fuente": self.idioma_fuente,
            "mensajes_hilo": len(self.mensajes),
            "hilo": [m.como_dict() for m in self.mensajes],
            "contacto": (
                {k: self.contacto.get(k) for k in ("id", "nombre", "email", "empresa", "idioma")}
                if self.contacto else None
            ),
            "telefonos": len(self.telefonos), "etiquetas": len(self.etiquetas),
            "campos_personalizados": len(self.campos), "notas": len(self.notas),
            "pedidos": len(self.pedidos), "facturas_pendientes": len(self.facturas_pendientes),
            "maquina": self.maquina, "modelo_flux": self.modelo_flux,
            "clasificacion": self.clasificacion, "etapa": self.etapa,
            "asuntos_recientes": len(self.asuntos_recientes),
            "sitio": self.sitio, "interes": self.interes, "intereses": list(self.intereses),
            "enlaces": self.enlaces_lista(),
            # None cuando el catálogo no respondió (la franja lo distingue de
            # «sin productos para este caso»).
            "catalogo": None if self.catalogo is None else {
                "maquinas": [
                    {k: p.get(k) for k in ("nombre", "marca", "precio_texto", "moneda",
                                           "precio_idioma", "orientativo", "enlace", "interes",
                                           "eleccion", "precio_publico")}
                    for p in self.catalogo.get("maquinas") or []
                ],
                "consumibles": [
                    {k: p.get(k) for k in ("nombre", "marca", "precio_texto", "moneda",
                                           "precio_idioma", "orientativo", "enlace", "sitio")}
                    for p in self.catalogo.get("consumibles") or []
                ],
                "sin_maquina": list(self.catalogo.get("sin_maquina") or []),
                "intereses_instruccion": list(self.catalogo.get("intereses_instruccion") or []),
                "menciones": dict(self.catalogo.get("menciones") or {}),
                "iva_por_fuente": dict(self.catalogo.get("iva_por_fuente") or {}),
            },
            "niveles": list(self.niveles),
            "historico": [
                {k: v for k, v in h.items() if k != "texto"} | {"con_cuerpo": bool(h.get("texto"))}
                for h in self.historico
            ],
            "empresa": [
                {k: v for k, v in e.items() if k != "texto"} | {"con_cuerpo": bool(e.get("texto"))}
                for e in self.empresa_asuntos
            ],
            "empresa_nombre": self.empresa,
            "recortes": self.recortes,
            "presupuesto_tokens": self.presupuesto_tokens,
            "tokens_estimados": self.tokens_estimados,
            "mensajes_historico": len(self.historico) + sum(
                1 for e in self.empresa_asuntos if e.get("texto")),
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


def idioma_sin_hilo(session: Session, contacto: Contact | None) -> tuple[str, str]:
    """Sin hilo no hay mensaje del cliente del que sacar el idioma: el de la
    ficha; si no lo tiene, el de lo último que escribió esa persona en su
    histórico; y si no, castellano. Sin esto a un lead alemán se le escribiría
    en castellano."""
    if contacto is None:
        return "es", "defecto"
    if contacto.language:
        return contacto.language.lower()[:2], "ficha"
    # Lo último que escribió ESA persona (por su dirección, no por la
    # dirección de captura: una respuesta nuestra desde Gmail entra como
    # entrada y la escribió un compañero).
    direccion = (contacto.email or "").strip().lower()
    if not direccion:
        return "es", "defecto"
    ultimo = session.execute(
        select(EmailMessage.subject, EmailMessage.body_text, EmailMessage.body_html)
        .where(EmailMessage.contact_id == contacto.id,
               func.lower(EmailMessage.from_email) == direccion,
               EmailMessage.sent_at.is_not(None))
        .order_by(EmailMessage.sent_at.desc()).limit(1)
    ).first()
    if ultimo is not None:
        asunto, body_text, body_html = ultimo
        texto = sin_cita(texto_de_mensaje(body_text, body_html))
        idioma, puntos, _ = detectar_idioma(f"{asunto or ''}\n{texto}")
        if idioma and puntos >= 2:
            return idioma, "historico"
    return "es", "defecto"


def contacto_por_email(session: Session, email: str | None) -> Contact | None:
    """La ficha de esa dirección, si la hay (sin distinguir mayúsculas)."""
    direccion = (email or "").strip().lower()
    if not direccion or "@" not in direccion:
        return None
    return session.scalars(
        select(Contact).where(func.lower(Contact.email) == direccion).limit(1)).first()


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


def _clave_telefono(numero: str | None) -> str:
    """Para no repetir un número: sus dígitos y, si tiene nueve o más, los
    nueve últimos (el mismo móvil con y sin prefijo: «600 111 222»,
    «+34600111222», «0034 600 111 222»)."""
    digitos = "".join(c for c in (numero or "") if c.isdigit())
    return digitos[-9:] if len(digitos) >= 9 else digitos


def telefonos_de(session: Session, contacto: Contact) -> list[dict[str, Any]]:
    """Todos sus teléfonos sin repetir número: el de la ficha («principal»,
    con la etiqueta de su fila en `contact_phones` si la tiene) y los demás de
    `contact_phones` con la suya («Móvil», «TELEFONO_2»…). Sin teléfono en la
    ficha, el principal es la fila marcada como tal."""
    salida: list[dict[str, Any]] = []
    vistos: set[str] = set()

    def _añadir(numero: str | None, etiqueta: str | None) -> None:
        clave = _clave_telefono(numero)
        if not clave or clave in vistos:
            return
        vistos.add(clave)
        salida.append({"numero": (numero or "").strip(), "etiqueta": etiqueta or None})

    def _con_principal(etiqueta: str | None) -> str:
        return f"{etiqueta}, principal" if etiqueta else "principal"

    filas = session.scalars(
        select(ContactPhone).where(ContactPhone.contact_id == contacto.id)
        .order_by(ContactPhone.is_primary.desc(), ContactPhone.label)
    ).all()
    if _clave_telefono(contacto.phone):
        suya = next((f for f in filas
                     if _clave_telefono(f.number) == _clave_telefono(contacto.phone)), None)
        _añadir(contacto.phone, _con_principal(suya.label if suya else None))
    else:
        for fila in filas:
            if fila.is_primary:
                _añadir(fila.number, _con_principal(fila.label))
    for fila in filas:
        _añadir(fila.number, fila.label)
    return salida


def etiquetas_de(session: Session, contacto: Contact) -> list[str]:
    """Sus etiquetas, por orden alfabético (las primeras `ETIQUETAS_MAX`)."""
    nombres = session.scalars(
        select(Tag.name).join(ContactTag, ContactTag.tag_id == Tag.id)
        .where(ContactTag.contact_id == contacto.id)
    ).all()
    return sorted({n for n in nombres if n}, key=str.lower)[:ETIQUETAS_MAX]


def _valor_campo(valor: Any) -> str:
    if isinstance(valor, bool):
        return "sí" if valor else "no"
    if isinstance(valor, (dict, list)):
        return json.dumps(valor, ensure_ascii=False)
    return " ".join(str(valor).split())


def campos_de(contacto: Contact) -> list[dict[str, str]]:
    """Los campos personalizados con valor, tal como están guardados (de
    Brevo, Agile, los formularios, los workflows o la ficha)."""
    campos = decode_custom_fields(contacto.custom_fields) or {}
    return [
        {"clave": str(clave), "valor": recortar(_valor_campo(valor), CAMPO_MAX_CHARS)}
        for clave, valor in campos.items()
        if not custom_value_is_empty(valor)
    ][:CAMPOS_MAX]


_SIN_CONTENIDO_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_ETIQUETA_BLOQUE_RE = re.compile(
    r"</?(?:p|div|br|li|ul|ol|tr|td|th|table|h[1-6]|blockquote)\b[^>]*>", re.IGNORECASE)
_ETIQUETA_RE = re.compile(r"</?[a-zA-Z][\w:.-]*\b[^>]*>")
#: Las notas que se miran como mucho para elegir las `NOTAS_MAX` que entran.
_NOTAS_CANDIDATAS = 200


def _texto_de_nota(cuerpo: str | None) -> str:
    """El texto de una nota en una línea: sin HTML (ni el contenido de
    `<style>`/`<script>`), con las entidades resueltas."""
    texto = _SIN_CONTENIDO_RE.sub(" ", cuerpo or "")
    texto = _ETIQUETA_BLOQUE_RE.sub(" ", texto)
    texto = _ETIQUETA_RE.sub("", texto)
    return " ".join(html_mod.unescape(texto).split())


def _es_campo_de_agile(nota: Note) -> bool:
    """Las notas Note1..Note10 de la ficha de Agile: no tienen fecha (la
    suya es la de la importación) y cada cambio de su texto añade otra fila."""
    return (nota.source or "").startswith("agile:Note")


def notas_de(session: Session, contacto: Contact) -> list[dict[str, Any]]:
    """Las notas internas del contacto: las fijadas primero, luego las demás
    de la más reciente a la más antigua y al final las Note1..Note10 de Agile
    (sin fecha; de cada una, solo su última versión). Las vacías no cuentan;
    entran las primeras `NOTAS_MAX`, cada una recortada. Son del equipo, no
    del cliente: el prompt dice que no se citan."""
    filas = session.scalars(
        select(Note).where(Note.contact_id == contacto.id)
        .order_by(func.coalesce(Note.external_created_at, Note.created_at).desc())
        .limit(_NOTAS_CANDIDATAS)
    ).all()
    elegidas: list[tuple[Note, str]] = []
    ranuras_de_agile: set[str] = set()
    for nota in filas:
        if _es_campo_de_agile(nota):
            if nota.source in ranuras_de_agile:
                continue                      # una versión anterior de la misma nota
            ranuras_de_agile.add(nota.source)
        texto = _texto_de_nota(nota.body)
        if texto:
            elegidas.append((nota, texto))
    # Orden estable: fijadas, con fecha (ya de la más reciente) y las de Agile.
    elegidas.sort(key=lambda par: (not par[0].pinned, _es_campo_de_agile(par[0])))
    elegidas = elegidas[:NOTAS_MAX]
    ids = {n.author_user_id or n.created_by_user_id for n, _ in elegidas} - {None}
    nombres = {
        u.id: (u.full_name or u.email)
        for u in session.scalars(select(User).where(User.id.in_(ids)))
    } if ids else {}
    salida: list[dict[str, Any]] = []
    for nota, texto in elegidas:
        fecha = None if _es_campo_de_agile(nota) else (nota.external_created_at or nota.created_at)
        salida.append({
            "fecha": fecha.isoformat() if fecha else None,
            "autor": (nombres.get(nota.author_user_id or nota.created_by_user_id or "")
                      or nota.external_author_name or nota.external_author_email),
            "fijada": bool(nota.pinned),
            "origen": nota.source,
            "texto": recortar(texto, NOTA_MAX_CHARS),
        })
    return salida


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
    session: Session, contacto: Contact, *, excluir_thread_id: str | None,
    limite: int = ASUNTOS_RECIENTES,
) -> list[dict[str, Any]]:
    """Los últimos asuntos tratados con el contacto: fecha, lado y asunto.
    Títulos, no cuerpos (igual que el índice de FLUX). Sin hilo actual no se
    excluye ninguno."""
    consulta = select(EmailThread.id, EmailThread.subject, EmailThread.last_message_at).where(
        EmailThread.contact_id == contacto.id)
    if excluir_thread_id:
        consulta = consulta.where(EmailThread.id != excluir_thread_id)
    hilos = session.execute(
        consulta.order_by(EmailThread.last_message_at.desc()).limit(limite)).all()
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


def sitio_de(contacto: Contact | None, mensajes: list[Mensaje],
             thread: EmailThread | None) -> str | None:
    """La web por la que llegó: la dirección nuestra a la que escribió (su
    dominio) y, si no, el formulario de origen de la ficha."""
    reg = registro_sitios.registro()
    for m in reversed(thread.messages if thread is not None else []):
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
    session: Session, *, thread: EmailThread | None, usuario: User,
    alcance: str = ALCANCE_CONTACTO, destinatario: str | None = None,
    contacto: Contact | None = None, instruccion: str | None = None,
    ahora: datetime | None = None,
) -> Contexto:
    """El contexto de un borrador. Con `thread`, la respuesta a un hilo; sin
    él, un correo nuevo: entonces `destinatario` (la dirección de «Para») es
    lo que dispara el expediente y `instruccion` lo que dice de qué va.
    `ahora` fija la fecha con la que se mide la antigüedad de los precios
    (los tests); por defecto, la de hoy."""
    dominios = registro_sitios.dominios_del_grupo(session)
    mensajes = mensajes_del_hilo(thread, dominios) if thread is not None else []
    if contacto is None and thread is not None:
        contacto = session.get(Contact, thread.contact_id) if thread.contact_id else None
        if contacto is None:
            # Remitente desconocido: lo que la captura casó por dirección.
            for m in thread.messages:
                if m.contact_id:
                    contacto = session.get(Contact, m.contact_id)
                    break
    if contacto is None and destinatario:
        contacto = contacto_por_email(session, destinatario)
    ultimo = next((m for m in reversed(mensajes) if m.lado == "cliente"), None)
    if thread is not None:
        idioma, fuente = idioma_del_mensaje(mensajes, contacto)
    else:
        idioma, fuente = idioma_sin_hilo(session, contacto)
    ctx = Contexto(
        thread=thread, mensajes=mensajes, ultimo_del_cliente=ultimo, idioma=idioma,
        idioma_fuente=fuente, alcance=alcance, instruccion=(instruccion or "").strip() or None,
        niveles=[1] if thread is not None else [],
    )
    ctx.usuario = quien_pulsa(session, usuario)
    ctx.destinatario = (
        (destinatario or "").strip()
        or (destinatario_de(session, thread, usuario, mensajes) if thread is not None else None)
        or (contacto.email if contacto is not None else None)
    )
    ctx.destinatario_interno = es_del_grupo(ctx.destinatario, dominios)
    ctx.asunto_respuesta = asunto_respuesta(thread) if thread is not None else ""
    ctx.sitio = sitio_de(contacto, mensajes, thread)
    categorias = flux.cargar_categorias(session)
    cfg = config_asistente(session)
    ctx.presupuesto_tokens = int(cfg["presupuesto_tokens"])
    texto_cliente = ultimo.texto if ultimo else ""
    # Lo que decide qué correos viejos vienen a cuento: el mensaje del cliente
    # y, sin hilo, la instrucción de quien envía (lo único que dice de qué va).
    texto_comparar = texto_cliente if thread is not None else (ctx.instruccion or "")
    excluir = thread.id if thread is not None else None
    # Nivel 2 (el expediente) solo si hay ficha y el alcance pasa del hilo.
    if contacto is not None and alcance != ALCANCE_HILO:
        ctx.niveles.append(2)
        ctx.contacto = ficha(session, contacto)
        ctx.telefonos = telefonos_de(session, contacto)
        ctx.etiquetas = etiquetas_de(session, contacto)
        ctx.campos = campos_de(contacto)
        ctx.notas = notas_de(session, contacto)
        ctx.pedidos = pedidos_de(session, contacto)
        ctx.facturas_pendientes = facturas_pendientes_de(ctx.pedidos)
        ctx.maquina, ctx.modelo_flux = maquina_de(ctx.pedidos, categorias)
        ctx.clasificacion = clasificacion_de(session, contacto)
        ctx.etapa = etapa_de(session, contacto)
        ctx.asuntos_recientes = asuntos_recientes_de(
            session, contacto, excluir_thread_id=excluir, limite=int(cfg["asuntos_max"]))
        intereses = (ctx.clasificacion or {}).get("intereses") or (
            [contacto.lead_interest] if contacto.lead_interest else [])
        ctx.intereses = [str(i) for i in intereses if i]
        ctx.interes = ctx.intereses[0] if ctx.intereses else None
        ctx.ejemplo_estilo = ejemplo_estilo_de(session, intereses, idioma)
        # Nivel 3: los correos viejos del contacto que vienen a cuento.
        productos = hist.palabras_de_producto(
            (ln["descripcion"] for p in ctx.pedidos for ln in p["lineas"]),
            extra=[ctx.modelo_flux] if ctx.modelo_flux else (),
        )
        asunto_hoy = (ultimo.asunto if ultimo else thread.subject) if thread is not None else None
        elegidos = hist.elegir(
            hist.candidatos_del_contacto(session, contacto, excluir_thread_id=excluir),
            asunto_actual=asunto_hoy, texto_actual=texto_comparar, productos=productos,
            maximo=int(cfg["historico_max"]),
        )
        ctx.niveles.append(3)
        ctx.historico = [c.como_dict(motivo, con_texto=True) for c, motivo in elegidos]
        # Nivel 4: la empresa, por su registro, solo con «toda la empresa».
        if alcance == ALCANCE_EMPRESA:
            empresa = hist.empresa_agrupable(contacto, cfg["dominios_gratuitos"])
            if empresa is not None:
                ctx.niveles.append(4)
                ctx.empresa = empresa.name
                asuntos, candidatos = hist.hilos_de_la_empresa(
                    session, contacto, empresa, excluir_thread_id=excluir,
                    limite=int(cfg["asuntos_max"]))
                con_cuerpo = {
                    c.thread_id: (c, motivo) for c, motivo in hist.elegir(
                        candidatos, asunto_actual=asunto_hoy, texto_actual=texto_comparar,
                        productos=productos, maximo=int(cfg["historico_max"]))
                }
                ctx.empresa_asuntos = []
                for a in asuntos:
                    entrada = dict(a)
                    if a["thread_id"] in con_cuerpo:
                        cand, motivo = con_cuerpo[a["thread_id"]]
                        entrada["texto"] = cand.texto
                        entrada["motivo"] = motivo
                        entrada["lado"] = cand.lado
                    ctx.empresa_asuntos.append(entrada)
    ctx.enlaces = kb_enlaces.enlaces_para(
        session, sitio=ctx.sitio, idioma=idioma, interes=ctx.interes, texto=texto_comparar,
        modelo=ctx.modelo_flux,
    )
    # El catálogo: no entero, sino lo que toca a este correo, por este orden: las
    # máquinas que el cliente nombra en el hilo, las que pide la instrucción de
    # quien envía, los consumibles de la máquina que tiene y las de su interés
    # clasificado. Sin ficha ni mención ni instrucción, el bloque dice que no
    # hay precios que citar.
    texto_hilo = "\n".join(m.texto or "" for m in mensajes if m.lado == "cliente")
    try:
        ctx.catalogo = kb_catalogo.para_asistente(
            session, intereses=ctx.intereses, idioma=idioma, maquina=ctx.maquina,
            modelo_flux=ctx.modelo_flux, ahora=ahora, texto_hilo=texto_hilo,
            instruccion=ctx.instruccion,
        )
    except Exception:  # noqa: BLE001 — sin catálogo el asistente sigue (sin precios)
        logger.warning("asistente: el catálogo no responde; el borrador va sin precios",
                       exc_info=True)
        ctx.catalogo = None
    return ctx
