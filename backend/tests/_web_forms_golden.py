"""Formulario de muestra para comparar el HTML generado antes y después de
tocar el render (tests/fixtures/web_forms_golden/). Un campo de cada tipo,
textos sin enlaces y la apariencia por defecto."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.crm import User
from app.models.web_forms import WebForm, WebFormField

GOLDEN_FORM_ID = "11111111-2222-3333-4444-555555555555"
GOLDEN_API_BASE = "https://bohub.example"
GOLDEN_DIR = "tests/fixtures/web_forms_golden"


def crear_formulario_golden(session: Session) -> WebForm:
    autor = session.scalar(select(User.id).order_by(User.email))
    form = WebForm(id=GOLDEN_FORM_ID, slug="golden-es", name="Golden <ES>", brand="boprint",
                   language="es", recaptcha_enabled=False, created_by_user_id=autor)
    session.add(form)
    session.flush()
    campos = [
        ("nombre", "Nombre", "text", {"placeholder": "Tu nombre", "is_required": True,
                                       "maps_to_contact_field": "contact.first_name"}),
        ("email", "Email", "email", {"is_required": True, "help_text": "No lo compartimos",
                                      "maps_to_contact_field": "contact.email"}),
        ("telefono", "Teléfono", "tel", {}),
        ("mensaje", "Mensaje & dudas", "textarea", {"placeholder": "Cuéntanos"}),
        ("pais", "País", "select", {"options_json": json.dumps(
            [{"value": "es", "label": "España"}, {"value": "mx", "label": "México"}])}),
        ("productos", "Productos", "tags", {"options_json": json.dumps(
            [{"tag_id": "t-young", "label": "Artisjet Young"},
             {"tag_id": "t-6090", "label": "6090"}])}),
        ("valoracion", "Valoración", "stars", {}),
        ("origen", "Origen", "hidden", {"default_value": "web", "is_hidden": True}),
        ("privacidad", "He leído y acepto la Política de Privacidad", "checkbox",
         {"is_required": True, "help_text": "Consulta la política en la web"}),
    ]
    for pos, (key, label, tipo, extra) in enumerate(campos):
        session.add(WebFormField(form_id=form.id, field_key=key, label=label, field_type=tipo,
                                 position=pos, **extra))
    session.commit()
    session.refresh(form)
    return form
