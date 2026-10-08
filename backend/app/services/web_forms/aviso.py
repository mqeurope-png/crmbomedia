"""Aviso por correo de cada lead de formulario.

Antes se mandaba al propietario del contacto una línea con el nombre y el
correo. Con 25 formularios en ocho webs hace falta poder contestar sin abrir
BoHub, así que el aviso lleva de qué web y en qué idioma viene, los datos
del contacto, las etiquetas que marcó (por su nombre), su consulta entera,
si aceptó comunicaciones comerciales y un enlace a la ficha.

Destinatarios: la dirección fija de `WEB_FORMS_NOTIFY_TO`
(`info@streamtec.es`) y, además, el comercial al que se asignó el lead —una
sola vez si coinciden—. Lo gobierna el interruptor que ya existía,
`notify_owner_on_new`.

La plantilla admite una variante por web sin tocar código: si existe
`app/templates/email/lead_<sitio>.html` (y/o `.txt`) se usa esa en lugar de
`lead_notification.html`, así una web puede llevar su logotipo y su firma.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from jinja2 import TemplateNotFound
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.crm import ConsentStatus, Contact, User
from app.models.web_forms import WebForm
from app.services.web_forms.sitios import web_de_formulario
from app.services.web_forms.textos import nombre_idioma, normalizar_idioma

logger = logging.getLogger(__name__)

PLANTILLA_BASE = "lead_notification"


@dataclass
class Lead:
    """Lo que se cuenta en el aviso de un lead."""
    web: str
    idioma: str
    formulario: str
    slug: str
    nombre: str
    email: str
    telefono: str = ""
    etiquetas: list[str] = field(default_factory=list)
    consulta: str = ""
    consentimiento: bool = False
    campos: list[tuple[str, str]] = field(default_factory=list)
    enlace: str = ""
    nuevo: bool = True

    def asunto(self) -> str:
        return f"Nuevo lead desde {self.web} ({self.idioma})"

    def contexto(self) -> dict[str, Any]:
        return {
            "web": self.web, "idioma": self.idioma, "formulario": self.formulario,
            "slug": self.slug, "nombre": self.nombre, "email": self.email,
            "telefono": self.telefono, "etiquetas": self.etiquetas,
            "consulta": self.consulta, "consentimiento": self.consentimiento,
            "campos": self.campos, "enlace": self.enlace, "nuevo": self.nuevo,
            "asunto": self.asunto(),
        }


def _nombre_completo(contact: Contact) -> str:
    partes = [contact.first_name, contact.last_name]
    return " ".join(p for p in partes if p).strip() or (contact.email or "")


def _valor(raw: Any) -> str:
    if isinstance(raw, (list, tuple)):
        return ", ".join(str(v).strip() for v in raw if str(v).strip())
    return str(raw).strip() if raw is not None else ""


def construir_lead(
    form: WebForm, contact: Contact, payload: dict[str, Any], *,
    etiquetas: list[str] | None = None, nuevo: bool = True,
) -> Lead:
    """Junta lo que hay que contar. No consulta la base de datos: todo sale
    del formulario, del contacto y de lo que se envió."""
    from app.services.web_forms.submit import CHECKBOX_TRUE  # noqa: PLC0415

    settings = get_settings()
    idioma = normalizar_idioma(contact.language or form.language)
    consulta: list[str] = []
    campos: list[tuple[str, str]] = []
    consentimiento = contact.marketing_consent == ConsentStatus.GRANTED
    for f in sorted(form.fields, key=lambda x: x.position):
        valor = _valor(payload.get(f.field_key) or payload.get(f"{f.field_key}[]"))
        destino = (f.maps_to_contact_field or "").strip()
        if destino == "contact.marketing_consent":
            consentimiento = consentimiento or valor.lower() in CHECKBOX_TRUE
            continue
        if not valor or f.is_hidden or f.field_type in {"hidden", "tags"}:
            continue
        if f.field_type == "textarea" or destino == "contact.notes":
            consulta.append(valor)
            continue
        if destino in {"contact.first_name", "contact.last_name", "contact.email",
                       "contact.phone"}:
            continue      # ya van arriba, con los datos del contacto
        campos.append((f.label or f.field_key, valor))
    base = (settings.frontend_base_url or "").rstrip("/")
    return Lead(
        web=web_de_formulario(form.slug),
        idioma=nombre_idioma(idioma),
        formulario=form.name,
        slug=form.slug,
        nombre=_nombre_completo(contact),
        email=contact.email or "",
        telefono=contact.phone or "",
        etiquetas=sorted(etiquetas or []),
        consulta="\n\n".join(consulta),
        consentimiento=consentimiento,
        campos=campos,
        enlace=f"{base}/contacts/{contact.id}" if base else "",
        nuevo=nuevo,
    )


def destinatarios(session: Session, contact: Contact) -> list[tuple[str, str]]:
    """`(correo, nombre)` sin repetir: la dirección fija de los avisos y el
    comercial del lead (el de la regla de asignación o el propietario fijo,
    que es quien acaba en `owner_user_id`)."""
    fijas = [
        d.strip() for d in (get_settings().web_forms_notify_to or "").split(",")
        if d.strip()
    ]
    salida: list[tuple[str, str]] = [(d, "") for d in fijas]
    if contact.owner_user_id:
        owner = session.get(User, contact.owner_user_id)
        if owner is not None and owner.email:
            salida.append((owner.email, owner.full_name or owner.email))
    vistos: set[str] = set()
    unicos: list[tuple[str, str]] = []
    for correo, nombre in salida:
        clave = correo.lower()
        if clave in vistos:
            continue
        vistos.add(clave)
        unicos.append((correo, nombre))
    return unicos


def _plantilla(sitio: str | None, extension: str) -> str:
    """El nombre de la plantilla a usar: la de la web si existe."""
    from app.services.email import _jinja_env  # noqa: PLC0415

    sitio = (sitio or "").strip().lower()
    if sitio:
        propia = f"lead_{sitio}.{extension}"
        try:
            _jinja_env.get_template(propia)
        except TemplateNotFound:
            pass
        else:
            return propia
    return f"{PLANTILLA_BASE}.{extension}"


def render_aviso(lead: Lead, sitio: str | None = None) -> tuple[str, str, str]:
    """`(asunto, texto, html)` del aviso."""
    from app.services.email import _jinja_env  # noqa: PLC0415

    contexto = lead.contexto()
    texto = _jinja_env.get_template(_plantilla(sitio, "txt")).render(**contexto)
    html = _jinja_env.get_template(_plantilla(sitio, "html")).render(**contexto)
    return lead.asunto(), texto, html


def enviar_aviso_lead(
    session: Session, form: WebForm, contact: Contact, payload: dict[str, Any], *,
    etiquetas: list[str] | None = None, nuevo: bool = True,
) -> list[str]:
    """Manda el aviso y devuelve a quién se le envió. Un fallo de correo NO
    tumba la captura del lead: se registra y se sigue."""
    from app.services.email import get_email_service  # noqa: PLC0415

    enviados: list[str] = []
    try:
        lead = construir_lead(form, contact, payload, etiquetas=etiquetas, nuevo=nuevo)
        from app.services.web_forms.sitios import clave_de_sitio  # noqa: PLC0415

        asunto, texto, html = render_aviso(lead, clave_de_sitio(form.slug))
        quienes = destinatarios(session, contact)
    except Exception:  # noqa: BLE001 — el lead ya está guardado
        logger.warning("web_forms.aviso_lead: no se pudo preparar el aviso", exc_info=True)
        return enviados
    servicio = get_email_service()
    for correo, nombre in quienes:
        try:
            servicio.send_notification(
                to_email=correo, to_name=nombre or correo,
                subject=asunto, text_body=texto, html_body=html,
            )
            enviados.append(correo)
        except Exception:  # noqa: BLE001 — un destinatario no corta los demás
            logger.warning("web_forms.aviso_lead: fallo al avisar a %s (formulario %s)",
                           correo, form.slug, exc_info=True)
    return enviados
