"""El acuse de recibo del formulario: remitente por web y trazable.

Los 25 formularios llevaban desde el 08/10 recibiendo leads sin que quien los
rellena recibiera nada. Lo que se comprueba aquí:

- sale del remitente de SU web, en SU idioma, con `Reply-To` del comercial;
- sin comercial, el `Reply-To` cae al propio remitente y el correo sale igual;
- lo que está vacío no deja hueco: ni la línea de productos ni el bloque de la
  consulta;
- queda en la ficha del contacto, en Enviados y con seguimiento de apertura,
  porque va por el mismo camino que la Bandeja;
- un formulario sin remitente configurado usa el de su web;
- y si el envío falla, el lead se guarda igual y el fallo queda registrado.

Sin Gmail de verdad: se sustituye el cliente, como en el resto de los tests de
envío.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Generator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db.base import Base
from app.models.crm import (
    ActivityEvent,
    Contact,
    EmailMessage,
    EmailMessageToken,
    User,
    UserRole,
)
from app.models.web_forms import WebForm, WebFormField
from app.services.web_forms import process_submission
from app.services.web_forms.acuse import aplicar_variables, construir_acuse
from tests._test_helpers import seed_test_users

BACKEND_ROOT = Path(__file__).resolve().parents[1]


# --- andamios ---------------------------------------------------------------


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with f() as seed:
        seed_test_users(seed)
        seed.commit()
    yield f
    Base.metadata.drop_all(engine)


class GmailFalso:
    """Guarda lo que se le manda y declara de qué alias puede firmar, que es
    lo que el acuse le pregunta antes de enviar."""

    def __init__(self, revienta: bool = False) -> None:
        self.enviados: list[dict] = []
        self.alias: list[str] = []
        self.revienta = revienta

    def list_send_as_aliases(self) -> list[dict]:
        return [{"send_as_email": a} for a in self.alias]

    def send_message(self, **kwargs) -> dict:
        if self.revienta:
            raise RuntimeError("Gmail dijo no")
        self.enviados.append(kwargs)
        n = len(self.enviados)
        return {"id": f"gmail-{n}", "threadId": f"hilo-{n}", "labelIds": ["SENT"]}


@pytest.fixture()
def gmail(monkeypatch) -> GmailFalso:
    from app.integrations.gmail import service as gmail_service

    falso = GmailFalso()
    monkeypatch.setattr(gmail_service, "_client_for", lambda *_a, **_k: falso)
    return falso


def _admin(s: Session) -> User:
    return s.scalar(select(User).where(User.role == UserRole.ADMIN))


def _comercial(s: Session, email: str = "manel@bomedia.net") -> User:
    user = User(email=email, full_name="Manel Pons", role=UserRole.USER,
                password_hash="x", is_active=True)
    s.add(user)
    s.flush()
    return user


def _plantilla_de(s: Session, idioma: str) -> str:
    """Las plantillas tal y como las deja la migración: una por idioma, con
    las dobles llaves y los bloques condicionales."""
    from app.email_templates.models import EmailTemplate
    from app.services.web_forms.plantillas_acuse import PLANTILLAS

    datos = PLANTILLAS[idioma]
    existente = s.scalar(
        select(EmailTemplate).where(EmailTemplate.name == datos["nombre"])
    )
    if existente is not None:
        return existente.id
    tpl = EmailTemplate(name=datos["nombre"], subject=datos["asunto"],
                        body_html=datos["html"], body_text=datos["texto"],
                        is_global=True)
    s.add(tpl)
    s.flush()
    return tpl.id


def _form(s: Session, *, slug: str, idioma: str, con_productos: bool = True,
          con_plantilla: bool = True, **over) -> WebForm:
    over.setdefault(
        "confirmation_email_template_id",
        _plantilla_de(s, idioma) if con_plantilla else None,
    )
    form = WebForm(slug=slug, name=f"Contacto {slug}", language=idioma,
                   created_by_user_id=_admin(s).id, recaptcha_enabled=False,
                   send_confirmation_email=True, **over)
    s.add(form)
    s.flush()
    campos = [
        WebFormField(field_key="nombre", label="Nombre", field_type="text",
                     maps_to_contact_field="contact.first_name"),
        WebFormField(field_key="email", label="Email", field_type="email",
                     is_required=True, maps_to_contact_field="contact.email"),
        WebFormField(field_key="consulta", label="Consulta",
                     field_type="textarea"),
    ]
    if con_productos:
        campos.append(WebFormField(field_key="productos", label="Productos",
                                   field_type="tags",
                                   options_json='["artisJet Passion", "artisJet 5000U"]'))
    for i, campo in enumerate(campos):
        campo.form_id = form.id
        campo.position = i
        s.add(campo)
    s.commit()
    s.refresh(form)
    return form


def _enviar(s: Session, form: WebForm, payload: dict):
    return process_submission(s, form=form, payload=payload,
                              meta={"ip": "1.2.3.4"},
                              verify_recaptcha_fn=lambda _t, _i: 0.9,
                              rate_limit_fn=lambda _ip, _f: True)


def _cabecera(enviado: dict, nombre: str) -> str | None:
    return (enviado.get("extra_headers") or {}).get(nombre)


@pytest.fixture()
def log_acuse(monkeypatch):
    """Deja hablar al logger del `submit`.

    `env.py` de Alembic llama a `fileConfig()`, que con
    `disable_existing_loggers` APAGA los loggers que ya existían. Basta con que
    otro test de la suite corra una migración antes de este para que `caplog`
    salga vacío — pasó en el CI con los tres tests de abajo, verdes en local.
    Es el tropiezo recurrente de esta base de código."""
    from app.services.web_forms import submit as submit_mod

    monkeypatch.setattr(submit_mod.logger, "disabled", False)
    monkeypatch.setattr(submit_mod.logger, "level", logging.WARNING)
    return submit_mod.logger


# --- 1 · remitente por web, idioma e `Reply-To` -----------------------------


def test_el_acuse_sale_de_la_web_del_lead_en_su_idioma_y_contesta_al_comercial(
    factory, gmail
):
    with factory() as s:
        comercial = _comercial(s)
        gmail.alias.append("info@mboprinters.com")
        form = _form(s, slug="mboprinters-contacto-de", idioma="de",
                     assignment_mode="fixed_owner",
                     fixed_owner_user_id=comercial.id)
        s.commit()
        _enviar(s, form, {"nombre": "Hans", "email": "hans@muster.de",
                          "consulta": "Was kostet der Drucker?"})

    assert len(gmail.enviados) == 1
    enviado = gmail.enviados[0]
    assert enviado["from_alias"] == "info@mboprinters.com"
    assert enviado["from_name"] == "MBO Printers"
    assert enviado["to"] == ["hans@muster.de"]
    # El cliente ve la marca; si contesta, le llega a una persona.
    assert _cabecera(enviado, "Reply-To") == "manel@bomedia.net"
    assert "Hallo Hans" in enviado["body_text"]


def test_mqeurope_sale_de_sales_porque_no_tiene_info(factory, gmail):
    """`sales@mqeurope.com`: en ese dominio no hay `info@`. Y la web se sirve
    con `www.`, que en un remitente no pinta nada."""
    with factory() as s:
        gmail.alias.append("sales@mqeurope.com")
        form = _form(s, slug="mqeurope-contacto-nl", idioma="nl")
        s.commit()
        _enviar(s, form, {"nombre": "Daan", "email": "daan@voorbeeld.nl",
                          "consulta": "Hoeveel kost het?"})

    enviado = gmail.enviados[0]
    assert enviado["from_alias"] == "sales@mqeurope.com"
    assert enviado["from_name"] == "MQ Europe"
    assert "Hallo Daan" in enviado["body_text"]
    assert "Bedankt voor uw bericht" in enviado["body_text"]


def test_sin_comercial_el_reply_to_es_el_propio_remitente_y_el_correo_sale(
    factory, gmail
):
    with factory() as s:
        gmail.alias.append("info@boprint.net")
        form = _form(s, slug="boprint-contacto-es", idioma="es",
                     assignment_mode="none")
        s.commit()
        salida = _enviar(s, form, {"nombre": "Toni", "email": "toni@ejemplo.es",
                                   "consulta": "¿Precio?"})
        assert s.get(Contact, salida.contact_id).owner_user_id is None

    enviado = gmail.enviados[0]
    assert enviado["from_alias"] == "info@boprint.net"
    assert _cabecera(enviado, "Reply-To") == "info@boprint.net"


# --- 2 · lo vacío no deja hueco ---------------------------------------------


def test_sin_productos_marcados_la_linea_de_productos_no_aparece(factory, gmail):
    with factory() as s:
        gmail.alias.append("info@mbolasers.com")
        form = _form(s, slug="mbolasers-contacto-es", idioma="es")
        s.commit()
        _enviar(s, form, {"nombre": "Toni", "email": "toni@ejemplo.es",
                          "consulta": "Quiero información."})

    cuerpo = gmail.enviados[0]["body_text"]
    html = gmail.enviados[0]["body_html"]
    assert "Productos que te interesan" not in cuerpo
    assert "Productos que te interesan" not in (html or "")
    assert "Quiero información." in cuerpo       # la consulta sí


def test_con_la_consulta_en_blanco_el_bloque_entero_desaparece(factory, gmail):
    """Pasa de verdad: uno de los leads de prueba del 08/10 la mandó vacía."""
    with factory() as s:
        gmail.alias.append("info@fluxlasers.es")
        form = _form(s, slug="fluxlasers-contacto-es", idioma="es")
        s.commit()
        _enviar(s, form, {"nombre": "Toni", "email": "toni@ejemplo.es",
                          "consulta": "   "})

    cuerpo = gmail.enviados[0]["body_text"]
    assert "Lo que nos cuentas" not in cuerpo
    assert "Hemos recibido tu consulta" in cuerpo   # el resto del correo, sí


def test_la_consulta_no_puede_colar_html_en_el_acuse(factory, gmail):
    """Es texto escrito por un desconocido en un formulario público."""
    with factory() as s:
        gmail.alias.append("info@boprint.net")
        form = _form(s, slug="boprint-contacto-es", idioma="es")
        s.commit()
        _enviar(s, form, {"nombre": "Toni", "email": "toni@ejemplo.es",
                          "consulta": "<script>alert(1)</script>"})

    html = gmail.enviados[0]["body_html"]
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# --- 3 · trazable como cualquier otro correo de BoHub -----------------------


def test_el_acuse_queda_en_la_ficha_en_enviados_y_con_seguimiento(factory, gmail):
    with factory() as s:
        gmail.alias.append("info@mboprinters.com")
        form = _form(s, slug="mboprinters-contacto-es", idioma="es")
        s.commit()
        salida = _enviar(s, form, {"nombre": "Toni", "email": "toni@ejemplo.es",
                                   "consulta": "¿Precio?"})
        contacto_id = salida.contact_id

    with factory() as s:
        # Enviados: el mensaje existe, con su remitente y atado al contacto.
        mensaje = s.scalar(
            select(EmailMessage).where(EmailMessage.contact_id == contacto_id)
        )
        assert mensaje is not None
        assert mensaje.from_email == "info@mboprinters.com"
        assert mensaje.direction.value == "outbound"
        # Apertura: el token de seguimiento está, así que se puede consultar.
        assert s.scalar(
            select(EmailMessageToken).where(
                EmailMessageToken.message_id == mensaje.id)
        ) is not None
        # Ficha: el mismo evento de timeline que un correo de la Bandeja.
        evento = s.scalar(
            select(ActivityEvent).where(
                ActivityEvent.contact_id == contacto_id,
                ActivityEvent.event_type == "email.sent_from_crm",
            )
        )
        assert evento is not None
        assert "acuse_formulario_web" in (evento.metadata_json or "")


# --- 4 · defectos y fallos --------------------------------------------------


def test_un_formulario_sin_remitente_usa_el_de_su_web(factory, gmail):
    with factory() as s:
        gmail.alias.append("info@pimpam-vending.com")
        form = _form(s, slug="pimpam-contacto-es", idioma="es")
        assert form.confirmation_from_email is None
        s.commit()
        _enviar(s, form, {"nombre": "Toni", "email": "toni@ejemplo.es"})

    assert gmail.enviados[0]["from_alias"] == "info@pimpam-vending.com"


def test_el_remitente_del_formulario_manda_sobre_el_de_la_web(factory, gmail):
    with factory() as s:
        gmail.alias.append("ventas@boprint.net")
        form = _form(s, slug="boprint-contacto-es", idioma="es",
                     confirmation_from_email="ventas@boprint.net")
        s.commit()
        _enviar(s, form, {"nombre": "Toni", "email": "toni@ejemplo.es"})

    assert gmail.enviados[0]["from_alias"] == "ventas@boprint.net"


def test_si_el_acuse_falla_el_lead_se_guarda_igual_y_queda_registrado(
    factory, gmail, caplog, log_acuse
):
    gmail.revienta = True
    with factory() as s:
        gmail.alias.append("info@boprint.net")
        form = _form(s, slug="boprint-contacto-es", idioma="es")
        s.commit()
        with caplog.at_level(logging.WARNING):
            salida = _enviar(s, form, {"nombre": "Toni",
                                       "email": "toni@ejemplo.es"})
        # El lead está, con su contacto.
        assert salida.http_status == 200
        assert s.get(Contact, salida.contact_id) is not None
    assert "no se pudo enviar el acuse" in caplog.text


def test_sin_alias_sincronizado_no_se_manda_desde_otra_direccion(
    factory, gmail, caplog, log_acuse
):
    """Gmail reescribe un `From:` que no sea alias verificado de la cuenta que
    autentica: el acuse saldría desde el buzón de la organización, que es el
    problema que esto arregla. Mejor no mandarlo y decirlo.

    Y la prueba se le pide a Gmail, no a `user_email_alias_prefs`: esa tabla
    CONSERVA la fila de un alias revocado y solo le baja `is_allowed`, que es
    la misma marca que llevan los alias de las marcas a propósito."""
    with factory() as s:
        form = _form(s, slug="mbolasers-contacto-es", idioma="es")
        s.commit()
        with caplog.at_level(logging.WARNING):
            salida = _enviar(s, form, {"nombre": "Toni",
                                       "email": "toni@ejemplo.es"})
        assert s.get(Contact, salida.contact_id) is not None

    assert gmail.enviados == []
    assert "no es un alias de envío" in caplog.text


def test_una_web_que_no_esta_en_la_lista_no_se_inventa_un_remitente(
    factory, gmail, caplog, log_acuse
):
    with factory() as s:
        form = _form(s, slug="webnueva-contacto-es", idioma="es")
        s.commit()
        with caplog.at_level(logging.WARNING):
            _enviar(s, form, {"nombre": "Toni", "email": "toni@ejemplo.es"})

    assert gmail.enviados == []
    assert "no tiene remitente" in caplog.text


# --- 5 · las variables y los bloques ----------------------------------------


def test_los_bloques_se_resuelven_y_una_variable_desconocida_no_viaja():
    valores = {"nombre": "Toni", "productos": "", "consulta": "Hola"}
    salida = aplicar_variables(
        "Hola {{nombre}}.{{#productos}} Productos: {{productos}}."
        "{{/productos}}{{#consulta}} Dice: {{consulta}}.{{/consulta}}"
        " [{{inventada}}]",
        valores,
    )
    assert salida == "Hola Toni. Dice: Hola. []"


def test_el_asunto_y_el_cuerpo_salen_de_la_plantilla_del_formulario(factory):
    """Y las dobles llaves NO pasan por `replace_merge_vars`, que con una sola
    llave dejaría `{Toni}` dentro de `{{nombre}}`."""
    from app.email_templates.models import EmailTemplate

    with factory() as s:
        form = _form(s, slug="artisjet-eu-contacto-en", idioma="en")
        tpl = EmailTemplate(
            name="Acuse EN", subject="Hi {{nombre}} · {{marca}}",
            body_html="<p>{{nombre}} — {{web}}</p>", body_text="{{nombre}} — {{web}}",
            is_global=True,
        )
        s.add(tpl)
        s.flush()
        form.confirmation_email_template_id = tpl.id
        s.commit()
        salida = _enviar(s, form, {"nombre": "Toni", "email": "toni@ejemplo.es"})
        contacto = s.get(Contact, salida.contact_id)
        acuse = construir_acuse(s, form, contacto, {})

    assert acuse["asunto"] == "Hi Toni · Artisjet Europe"
    assert acuse["texto"] == "Toni — artisjet-printers.eu"
    assert "{Toni}" not in acuse["asunto"]


# --- 6 · la migración que lo activa -----------------------------------------


@pytest.fixture()
def alembic_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    get_settings.cache_clear()
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    old_cwd = os.getcwd()
    os.chdir(BACKEND_ROOT)
    # `fileConfig()` de `env.py` apaga los loggers que ya existían: se
    # restauran para no estropear el `caplog` de otros tests.
    estados = {n: lg.disabled for n, lg in logging.root.manager.loggerDict.items()
               if isinstance(lg, logging.Logger)}
    try:
        yield cfg, db_url
    finally:
        for n, lg in logging.root.manager.loggerDict.items():
            if isinstance(lg, logging.Logger):
                lg.disabled = estados.get(n, False)
        os.chdir(old_cwd)
        get_settings.cache_clear()


def test_la_migracion_crea_las_plantillas_pone_remitente_y_enciende(alembic_cfg):
    cfg, db_url = alembic_cfg
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as seed:
        seed_test_users(seed)
        seed.commit()
        autor = seed.scalar(select(User.id).where(User.role == UserRole.ADMIN))
    formularios = [
        ("f1", "mboprinters-contacto-de", "de"),
        ("f2", "mqeurope-contacto-nl", "nl"),
        # Una web que no está en la lista: su acuse NO se enciende.
        ("f3", "webnueva-contacto-es", "es"),
    ]
    with engine.begin() as c:
        for fid, slug, idioma in formularios:
            c.execute(text(
                "INSERT INTO web_forms (id, slug, name, language, is_active, "
                "submit_success_mode, send_confirmation_email, assignment_mode, "
                "notify_owner_on_new, recaptcha_enabled, is_site_default, "
                "created_by_user_id, created_at, updated_at) VALUES "
                "(:i, :s, 'F', :l, 1, 'modal', 0, 'rules', 1, 1, 0, :u, "
                "'2026-10-08', '2026-10-08')"),
                {"i": fid, "s": slug, "l": idioma, "u": autor})
    # La columna la crea 0130; `create_all` ya la ha puesto, así que se parte
    # de ahí y se corre la migración de DATOS.
    command.stamp(cfg, "20261010_0130")
    real = logging.config.fileConfig
    logging.config.fileConfig = lambda *_a, **_k: None
    try:
        command.upgrade(cfg, "20261010_0131")
    finally:
        logging.config.fileConfig = real

    with engine.connect() as c:
        filas = {
            fila[0]: fila[1:]
            for fila in c.execute(text(
                "SELECT id, confirmation_from_email, send_confirmation_email,"
                " confirmation_email_template_id FROM web_forms")).all()
        }
        plantillas = dict(c.execute(text(
            "SELECT name, subject FROM email_templates")).all())

    assert len(plantillas) == 6                      # una por idioma
    assert filas["f1"][0] == "info@mboprinters.com"
    assert filas["f1"][1] == 1
    assert filas["f2"][0] == "sales@mqeurope.com"
    assert filas["f2"][1] == 1
    # Cada uno con la plantilla de SU idioma, y distinta entre sí.
    assert filas["f1"][2] and filas["f2"][2]
    assert filas["f1"][2] != filas["f2"][2]
    # La web desconocida se queda apagada: inventar un `info@` de un dominio
    # que no firma acabaría en spam.
    assert filas["f3"][0] is None and filas["f3"][1] == 0

    # Relanzable y sin pisar nada puesto a mano: ni el remitente que alguien
    # haya cambiado, ni un acuse apagado a propósito.
    with engine.begin() as c:
        c.execute(text("UPDATE web_forms SET confirmation_from_email = "
                       "'ventas@mboprinters.com' WHERE id = 'f1'"))
        c.execute(text("UPDATE web_forms SET send_confirmation_email = 0 "
                       "WHERE id = 'f2'"))
    logging.config.fileConfig = lambda *_a, **_k: None
    try:
        command.upgrade(cfg, "20261010_0131")
    finally:
        logging.config.fileConfig = real
    with engine.connect() as c:
        assert c.execute(text(
            "SELECT confirmation_from_email FROM web_forms WHERE id = 'f1'"
        )).scalar() == "ventas@mboprinters.com"
        assert c.execute(text(
            "SELECT COUNT(*) FROM email_templates")).scalar() == 6
        # El que alguien apagó sigue apagado: tener plantilla es la señal de
        # que ya estaba configurado, así que no se vuelve a encender.
        assert c.execute(text(
            "SELECT send_confirmation_email FROM web_forms WHERE id = 'f2'"
        )).scalar() == 0


def test_una_plantilla_sin_asunto_no_manda_el_acuse_con_el_asunto_vacio(factory):
    from app.email_templates.models import EmailTemplate

    with factory() as s:
        form = _form(s, slug="boprint-contacto-es", idioma="es",
                     con_plantilla=False)
        tpl = EmailTemplate(name="Sin asunto", subject=None,
                            body_html="<p>Hola {{nombre}}</p>", is_global=True)
        s.add(tpl)
        s.flush()
        form.confirmation_email_template_id = tpl.id
        s.commit()
        salida = _enviar(s, form, {"nombre": "Toni", "email": "toni@ejemplo.es"})
        contacto = s.get(Contact, salida.contact_id)
        acuse = construir_acuse(s, form, contacto, {})

    # Cae al asunto por idioma, no se manda un `Subject` en blanco.
    assert acuse["asunto"] == "Gracias por contactar con Boprint"
    assert "Hola Toni" in acuse["html"]


def test_una_web_sin_nombre_comercial_usa_la_marca_del_formulario(factory, gmail):
    """«webnueva» es una clave interna: no se le puede enseñar a un cliente
    como nombre de la marca ni como firma."""
    with factory() as s:
        gmail.alias.append("hola@webnueva.com")
        form = _form(s, slug="webnueva-contacto-es", idioma="es",
                     brand="Web Nueva",
                     confirmation_from_email="hola@webnueva.com")
        s.commit()
        _enviar(s, form, {"nombre": "Toni", "email": "toni@ejemplo.es"})

    enviado = gmail.enviados[0]
    assert enviado["from_name"] == "Web Nueva"
    assert "webnueva-contacto-es" not in enviado["body_text"]
