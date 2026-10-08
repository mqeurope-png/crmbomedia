"""El acuse de recibo del formulario: el correo que recibe quien lo rellena.

Hasta ahora no lo recibía nadie (`send_confirmation_email` estaba a `0` en los
25 formularios). Activar la casilla no bastaba por tres cosas, y las tres se
arreglan aquí:

1. **El remitente sale de la web por la que entró el lead**, no de la
   aplicación. `SMTP_FROM` es `info@streamtec.es`, que a un cliente de
   mboprinters.com no le dice nada. El remitente es un dato de cada
   formulario (`web_forms.confirmation_from_email`), editable desde la
   pantalla, y si está vacío se usa el de su web (`sitios.REMITENTES`).

2. **`Reply-To` es el comercial del lead.** El cliente ve la marca, pero si
   contesta le llega a una persona y no a un buzón genérico que nadie vacía.
   Sin comercial, el `Reply-To` cae al propio remitente.

3. **Va por el mismo camino que la Bandeja**, `gmail.service.send_email`, que
   es lo que hace que el acuse NO sea un correo fantasma: queda en la ficha
   del contacto, en Enviados y con el seguimiento de apertura, como cualquier
   otro correo de BoHub. Es también lo que hace que lo firme el dominio
   correcto: los ocho remitentes existen como alias de envío en el Gmail de la
   organización. Mandarlo por SMTP desde `info@mboprinters.com` autenticando
   contra otro buzón traería de vuelta el aviso «via streamtec.es» y la
   carpeta de spam.

   Mismo patrón que el aviso de envío al cliente (`erp/shipment_email.py`) y
   que el email de factura, que ya van por ahí en producción.

**Si el alias no está en ningún usuario con Gmail conectado, no se manda.**
Gmail reescribe un `From:` que no sea un alias verificado de la cuenta que
autentica, así que el acuse saldría desde la cuenta de la organización: justo
el problema que esto viene a resolver. Se registra el fallo con el alias que
falta y el lead queda guardado igual.

**Las variables** de las plantillas son las del encargo, con dobles llaves
(`{{nombre}}`), y además bloques condicionales `{{#productos}}…{{/productos}}`
para lo que desaparece cuando está vacío: si no marcó ningún producto no se
enseña «Productos que te interesan:» seguido de un hueco, y si dejó la
consulta en blanco el bloque entero no aparece. Pasa de verdad.
"""

from __future__ import annotations

import html as html_mod
import logging
import re
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from app.models.crm import Contact, User, UserEmailAliasPref, UserRole
from app.services.web_forms.sitios import (
    marca_de_formulario,
    remitente_de_formulario,
    web_de_formulario,
)

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import Session

    from app.models.web_forms import WebForm

logger = logging.getLogger(__name__)

#: `{{#productos}}…{{/productos}}` — el bloque entero se va si la variable
#: está vacía. Es lo que evita el «Productos que te interesan:» con un hueco.
PATRON_BLOQUE = re.compile(r"\{\{#\s*(\w+)\s*\}\}(.*?)\{\{/\s*\1\s*\}\}", re.DOTALL)
#: `{{nombre}}`. Una variable que la plantilla no conozca se queda en blanco,
#: igual que en `replace_merge_vars`: nadie debe recibir un `{{nombre}}`.
PATRON_VARIABLE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def aplicar_variables(texto: str | None, valores: dict[str, str]) -> str | None:
    """Sustituye las variables y resuelve los bloques condicionales."""
    if not texto:
        return texto

    def _bloque(m: re.Match[str]) -> str:
        clave, dentro = m.group(1), m.group(2)
        return dentro if (valores.get(clave) or "").strip() else ""

    # Primero los bloques: si no, las variables de dentro de un bloque que se
    # va se sustituirían para nada.
    texto = PATRON_BLOQUE.sub(_bloque, texto)
    return PATRON_VARIABLE.sub(lambda m: valores.get(m.group(1), ""), texto)


def _para_html(valores: dict[str, str]) -> dict[str, str]:
    """Los mismos valores, listos para meter en HTML: escapados (son texto
    que ha escrito un desconocido en un formulario público) y con los saltos
    de línea de la consulta convertidos en `<br>`."""
    return {
        clave: html_mod.escape(valor or "").replace("\n", "<br>\n")
        for clave, valor in valores.items()
    }


def variables_de(
    form: WebForm, contact: Contact, payload: dict[str, Any], *,
    etiquetas: list[str] | None = None,
) -> dict[str, str]:
    """Las variables del acuse. `productos` y `consulta` salen de lo mismo que
    el aviso interno del lead (`aviso.construir_lead`), para que el cliente y
    el comercial lean exactamente lo mismo."""
    from app.services.web_forms.aviso import construir_lead  # noqa: PLC0415

    lead = construir_lead(form, contact, payload, etiquetas=etiquetas)
    empresa = getattr(contact, "company", None)
    return {
        "nombre": (contact.first_name or "").strip() or lead.nombre,
        # Una web que no está en `MARCAS` cae en el `brand` del formulario
        # antes que en la clave del slug: «webnueva» no es un nombre
        # comercial, y el cliente lo leería en el asunto y en la firma.
        "marca": marca_de_formulario(form.slug, respaldo=form.brand),
        "web": web_de_formulario(form.slug),
        "productos": ", ".join(lead.etiquetas),
        "consulta": lead.consulta,
        # De propina, para que una plantilla pueda usarlas como el resto del
        # sistema de plantillas.
        "email": contact.email or "",
        "empresa": getattr(empresa, "name", "") or "",
    }


def remitente(form: WebForm) -> str | None:
    """El remitente del acuse: el del formulario y, si está vacío, el de su
    web. `None` cuando no se puede saber (una web que no está en la lista):
    inventarse una dirección de un dominio que no firma es peor que no
    mandar."""
    propio = (form.confirmation_from_email or "").strip()
    return propio or remitente_de_formulario(form.slug)


def reply_to(session: Session, contact: Contact, *, por_defecto: str) -> str:
    """El correo del comercial asignado. Si el lead no tiene comercial —o lo
    tiene dado de baja, que no lee su buzón—, el propio remitente."""
    if contact.owner_user_id:
        owner = session.get(User, contact.owner_user_id)
        if owner is not None and owner.email and owner.is_active:
            return owner.email
    return por_defecto


def usuario_remitente(session: Session, alias: str) -> User | None:
    """A nombre de qué usuario queda el envío.

    La cuenta de Google es de la organización (`_client_for` no distingue por
    usuario), así que esto no decide con qué buzón se manda: decide de quién
    es el hilo en BoHub. Se prefiere un administrador, que es la cuenta que no
    desaparece cuando a un comercial se le da de baja; si no hay, el usuario
    que tenga el alias entre sus preferencias de envío.
    """
    admin = session.scalar(
        select(User)
        .where(User.role == UserRole.ADMIN, User.is_active.is_(True))
        .order_by(User.created_at)
        .limit(1)
    )
    if admin is not None:
        return admin
    ids = list(
        session.scalars(
            select(UserEmailAliasPref.user_id).where(
                func.lower(UserEmailAliasPref.alias_email) == alias.strip().lower()
            )
        )
    )
    if not ids:
        return None
    return session.scalar(
        select(User).where(User.id.in_(ids), User.is_active.is_(True)).limit(1)
    )


def alias_verificado(cliente: Any, alias: str) -> bool:
    """¿Es `alias` un alias de envío de la cuenta de Gmail que autentica?

    Se le pregunta a Gmail, no a `user_email_alias_prefs`. Esa tabla no sirve
    de prueba: el sync CONSERVA la fila de un alias que Gmail ya no ofrece y
    solo le baja `is_allowed`, que es la misma marca que llevan a propósito
    los alias de las marcas para que un comercial no vea los de los demás. O
    sea, un alias revocado y uno oculto son indistinguibles ahí.

    Y la pregunta importa: Gmail reescribe un `From:` que no sea un alias
    verificado, así que el acuse saldría desde la cuenta de la organización,
    que es exactamente el problema que este módulo viene a resolver.
    """
    objetivo = alias.strip().lower()
    for entrada in cliente.list_send_as_aliases() or []:
        if str(entrada.get("send_as_email") or "").strip().lower() == objetivo:
            return True
    return False


def _plantilla(session: Session, template_id: str) -> tuple[str, str, str] | None:
    """`(asunto, html, texto)` de la plantilla, SIN sustituir nada.

    A propósito no se pasa por `replace_merge_vars`: ese sustituye `{nombre}`
    con una sola llave, así que a un `{{nombre}}` de estas plantillas le
    comería el interior y dejaría `{Toni}`. Las variables de aquí son las de
    dobles llaves y las resuelve `aplicar_variables`, que además entiende los
    bloques condicionales.
    """
    try:
        from app.email_templates.models import EmailTemplate  # noqa: PLC0415
        from app.email_templates.services import (  # noqa: PLC0415
            extract_text_from_html,
        )

        tpl = session.get(EmailTemplate, template_id)
        if tpl is None:
            return None
        html = tpl.body_html or ""
        return (
            tpl.subject or "",
            html,
            tpl.body_text or extract_text_from_html(html),
        )
    except Exception:  # noqa: BLE001 — sin plantilla quedan los textos base
        logger.warning("web_forms.acuse: no se pudo leer la plantilla %s",
                       template_id, exc_info=True)
        return None


def construir_acuse(
    session: Session, form: WebForm, contact: Contact, payload: dict[str, Any], *,
    etiquetas: list[str] | None = None,
) -> dict[str, Any]:
    """`{asunto, html, texto, remitente, reply_to}` del acuse, sin mandar nada.

    La plantilla es la del formulario (`confirmation_email_template_id`); si
    no tiene o no se puede leer, los textos por idioma de siempre."""
    from app.services.web_forms.textos import textos  # noqa: PLC0415

    valores = variables_de(form, contact, payload, etiquetas=etiquetas)
    tx = textos(form.language)
    asunto = tx["confirmacion_asunto"].format(quien=valores["marca"])
    texto: str | None = tx["confirmacion_cuerpo"]
    html: str | None = None
    if form.confirmation_email_template_id:
        plantilla = _plantilla(session, form.confirmation_email_template_id)
        if plantilla is not None and (plantilla[1] or "").strip():
            # El asunto solo si la plantilla trae uno: guardada sin asunto,
            # el acuse habría salido con el `Subject` vacío.
            asunto = (plantilla[0] or "").strip() or asunto
            html, texto = plantilla[1], plantilla[2]
    valores_html = _para_html(valores)
    de = remitente(form)
    return {
        "asunto": aplicar_variables(asunto, valores) or "",
        "html": aplicar_variables(html, valores_html),
        "texto": aplicar_variables(texto, valores),
        "remitente": de,
        "reply_to": reply_to(session, contact, por_defecto=de or "") if de else None,
        "variables": valores,
    }


def enviar_acuse(
    session: Session, form: WebForm, contact: Contact, payload: dict[str, Any], *,
    etiquetas: list[str] | None = None,
) -> str:
    """Manda el acuse por el camino de la Bandeja y devuelve el id del mensaje.

    Levanta `ValueError` cuando no se puede mandar bien (sin remitente, o con
    un alias que ninguna cuenta de Gmail puede firmar). El llamador lo captura:
    el lead ya está guardado y un fallo de correo no lo tumba.
    """
    from app.integrations.gmail import service as gmail_service  # noqa: PLC0415

    acuse = construir_acuse(session, form, contact, payload, etiquetas=etiquetas)
    de = acuse["remitente"]
    if not de:
        raise ValueError(
            f"El formulario {form.slug!r} no tiene remitente para el acuse y su "
            "web no está en la lista de remitentes."
        )
    if not contact.email:
        raise ValueError(f"El lead del formulario {form.slug!r} no tiene correo.")
    emisor = usuario_remitente(session, de)
    if emisor is None:
        raise ValueError(
            "No hay ningún usuario activo a cuyo nombre registrar el acuse."
        )
    # Se le pregunta a Gmail si el alias sigue siendo suyo ANTES de mandar: si
    # no lo es, reescribiría el `From:` y el acuse saldría desde la cuenta de
    # la organización, que es el problema que esto arregla.
    if not alias_verificado(gmail_service._client_for(session, emisor.id), de):
        raise ValueError(
            f"{de!r} no es un alias de envío de la cuenta de Gmail de la "
            "organización, así que Gmail reescribiría el remitente. Añádelo "
            "como «Enviar como» en esa cuenta."
        )
    mensaje = gmail_service.send_email(
        session,
        sender_user_id=emisor.id,
        from_alias=de,
        from_name=acuse["variables"]["marca"],
        to=[contact.email],
        cc=None, bcc=None,
        subject=acuse["asunto"],
        body_html=acuse["html"],
        body_text=acuse["texto"],
        contact_id=contact.id,
        reply_to=acuse["reply_to"],
    )
    # Lo mismo que apunta la Bandeja al enviar: sin esto el acuse estaría en
    # Enviados pero no en el histórico de la ficha del contacto.
    _apuntar_en_la_ficha(session, contact, acuse, mensaje)
    # El `commit` va AQUÍ, no en el llamador: a partir de este punto el correo
    # ya ha salido, así que un `rollback` del llamador borraría el rastro de un
    # acuse que el cliente tiene en su buzón. Si el guardado falla, se dice
    # exactamente eso.
    try:
        session.commit()
    except Exception:  # noqa: BLE001
        session.rollback()
        logger.error(
            "web_forms.acuse ENVIADO al lead %s desde %s pero NO se pudo "
            "registrar en BoHub (mensaje de Gmail %s)", contact.id, de,
            mensaje.gmail_message_id, exc_info=True,
        )
    logger.info(
        "web_forms.acuse enviado formulario=%s contacto=%s desde=%s responder_a=%s "
        "mensaje=%s", form.slug, contact.id, de, acuse["reply_to"], mensaje.id,
    )
    return mensaje.id


def _apuntar_en_la_ficha(
    session: Session, contact: Contact, acuse: dict[str, Any], mensaje: Any
) -> None:
    """El evento del timeline, igual que `_emit_activity` de la Bandeja: mismo
    `event_type` y misma forma del `external_id`, para que la ficha lo enseñe
    exactamente como un correo enviado a mano."""
    import json  # noqa: PLC0415
    from datetime import UTC, datetime  # noqa: PLC0415

    from app.models.crm import ActivityEvent  # noqa: PLC0415

    session.add(
        ActivityEvent(
            contact_id=contact.id,
            system="crm",
            account_id="emails",
            external_id=f"email:{mensaje.id}:email.sent_from_crm",
            event_type="email.sent_from_crm",
            subject=(acuse["asunto"] or "")[:200],
            body=(acuse["texto"] or "")[:200] or None,
            metadata_json=json.dumps(
                {
                    "message_id": mensaje.id,
                    "thread_id": mensaje.thread_id,
                    "direction": "outbound",
                    "from_email": mensaje.from_email,
                    "to": contact.email or "",
                    "reply_to": acuse["reply_to"],
                    "origen": "acuse_formulario_web",
                },
                default=str,
            ),
            occurred_at=mensaje.sent_at or datetime.now(UTC),
            synced_at=datetime.now(UTC),
        )
    )
