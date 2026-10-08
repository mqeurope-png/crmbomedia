"""El `assignment_mode` inválido que nadie vio, y la red que lo caza.

Los 25 formularios se cargaron con `assignment_mode = 'fixed'`. El valor que
el motor reconoce es `'fixed_owner'`, así que durante días ningún lead se
asignó a nadie, el aviso al propietario no tenía destinatario y los leads no
aparecían en la cartera de ningún comercial. Sin un solo aviso en ninguna
parte. Aquí se comprueba lo que impide que vuelva a pasar:

- la API rechaza un modo que el motor no entiende, y dice los válidos;
- el modelo tampoco lo deja guardar por el ORM;
- si aun así hay uno mal en la BD, el envío se procesa y queda en el log;
- la migración 0128 repara `fixed` → `fixed_owner`;
- el Cuadre lista los leads de formulario que se quedaron sin comercial.
"""

from __future__ import annotations

import logging
import logging.config
import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_session
from app.erp.cuadre import engine as cuadre_engine
from app.erp.models.cuadre import CuadreFinding
from app.main import app
from app.models.crm import Contact, User, UserRole
from app.models.web_forms import ASSIGNMENT_MODES, WebForm, WebFormField
from app.services.web_forms import process_submission
from tests._test_helpers import auth_headers, seed_test_users

BACKEND_ROOT = Path(__file__).resolve().parents[1]
CHECK_ID = "lead_web_sin_comercial"


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with f() as seed:
        seed_test_users(seed)
        seed.commit()
    yield f
    Base.metadata.drop_all(engine)


@pytest.fixture()
def http(factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with factory() as s:
            yield s

    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _payload_form(**over) -> dict:
    base = {
        "slug": "mboprinters-contacto-es", "name": "Contacto", "language": "es",
        "is_active": True, "submit_success_mode": "modal",
        "send_confirmation_email": False, "assignment_mode": "rules",
        "notify_owner_on_new": True, "recaptcha_enabled": False,
        "fields": [{"field_key": "email", "label": "Email", "field_type": "email",
                    "is_required": True, "maps_to_contact_field": "contact.email"}],
    }
    base.update(over)
    return base


# --- 2a · la API y el modelo no dejan guardar un modo que el motor no entiende ----------------


def test_la_api_rechaza_un_modo_invalido_y_dice_los_validos(http):
    cabeceras = auth_headers(http, "admin")
    r = http.post("/api/admin/forms", json=_payload_form(assignment_mode="fixed"),
                  headers=cabeceras)
    assert r.status_code == 400
    detalle = r.json()["detail"]
    assert "fixed" in detalle
    for modo in ASSIGNMENT_MODES:
        assert modo in detalle, detalle


def test_la_api_tampoco_lo_deja_al_actualizar(http):
    cabeceras = auth_headers(http, "admin")
    creado = http.post("/api/admin/forms", json=_payload_form(), headers=cabeceras)
    assert creado.status_code in (200, 201), creado.text
    fid = creado.json()["id"]
    r = http.patch(f"/api/admin/forms/{fid}",
                 json=_payload_form(assignment_mode="fixed"), headers=cabeceras)
    assert r.status_code == 400
    assert "fixed_owner" in r.json()["detail"]


def test_el_modelo_no_lo_deja_guardar_por_el_orm(factory):
    with factory() as s:
        autor = s.scalar(select(User.id).where(User.role == UserRole.ADMIN))
        with pytest.raises(ValueError) as err:
            WebForm(slug="x-contacto-es", name="X", language="es",
                    created_by_user_id=autor, assignment_mode="fixed")
        # El mensaje dice los válidos y, si es un alias conocido, el bueno.
        assert "fixed_owner" in str(err.value)
        for modo in ASSIGNMENT_MODES:
            assert modo in str(err.value)


def test_los_modos_validos_siguen_pasando(factory):
    with factory() as s:
        autor = s.scalar(select(User.id).where(User.role == UserRole.ADMIN))
        for modo in sorted(ASSIGNMENT_MODES):
            form = WebForm(slug=f"w-contacto-{modo}", name="W", language="es",
                           created_by_user_id=autor, assignment_mode=modo)
            s.add(form)
        s.commit()
        assert s.scalar(select(WebForm).where(
            WebForm.assignment_mode == "fixed_owner")) is not None


# --- 2b · si ya hay uno mal en la BD, el motor se queja ---------------------------------------


def _form_con_modo(s: Session, modo: str, *, slug: str = "mboprinters-contacto-es",
                   **over) -> WebForm:
    """Siembra un formulario con un modo CUALQUIERA, saltándose el validador
    del modelo: así se reproduce lo que había en producción."""
    autor = s.scalar(select(User.id).where(User.role == UserRole.ADMIN))
    form = WebForm(slug=slug, name="Contacto", language="es",
                   created_by_user_id=autor, recaptcha_enabled=False, **over)
    s.add(form)
    s.flush()
    campo = WebFormField(form_id=form.id, field_key="email", label="Email",
                         field_type="email", is_required=True, position=0,
                         maps_to_contact_field="contact.email")
    s.add(campo)
    s.commit()
    s.execute(text("UPDATE web_forms SET assignment_mode = :m WHERE id = :i"),
              {"m": modo, "i": form.id})
    s.commit()
    s.expire_all()
    return s.get(WebForm, form.id)


def _enviar(s: Session, form: WebForm, email: str):
    return process_submission(
        s, form=form, payload={"email": email}, meta={"ip": "1.2.3.4"},
        verify_recaptcha_fn=lambda _t, _i: 0.9, rate_limit_fn=lambda _ip, _f: True,
    )


def test_un_modo_desconocido_no_tumba_el_envio_pero_queda_en_el_log(factory, caplog):
    from app.services.web_forms import submit as submit_mod

    with factory() as s:
        form = _form_con_modo(s, "fixed")
        with caplog.at_level(logging.WARNING):
            submit_mod.logger.disabled = False
            out = _enviar(s, form, "lead@muster.de")
        assert out.http_status == 200, out.response
        contacto = s.get(Contact, out.contact_id)
        assert contacto is not None                   # el lead se guarda
        assert contacto.owner_user_id is None         # pero sin comercial
    assert "assignment_mode desconocido" in caplog.text
    assert "'fixed'" in caplog.text
    assert "mboprinters-contacto-es" in caplog.text   # identifica el formulario


def test_propietario_fijo_sin_propietario_tambien_avisa(factory, caplog):
    from app.services.web_forms import submit as submit_mod

    with factory() as s:
        form = _form_con_modo(s, "fixed_owner")        # sin fixed_owner_user_id
        with caplog.at_level(logging.WARNING):
            submit_mod.logger.disabled = False
            out = _enviar(s, form, "otro@muster.de")
        assert out.http_status == 200
        assert s.get(Contact, out.contact_id).owner_user_id is None
    assert "no tiene ninguno puesto" in caplog.text


def test_el_modo_none_no_se_queja(factory, caplog):
    from app.services.web_forms import submit as submit_mod

    with factory() as s:
        form = _form_con_modo(s, "none")
        with caplog.at_level(logging.WARNING):
            submit_mod.logger.disabled = False
            out = _enviar(s, form, "manual@muster.de")
        assert out.http_status == 200
    assert "assignment_mode desconocido" not in caplog.text


# --- 2c · la migración repara lo que haya mal -------------------------------------------------


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


def test_la_migracion_convierte_fixed_y_no_toca_los_buenos(alembic_cfg):
    cfg, db_url = alembic_cfg
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as seed:
        seed_test_users(seed)
        seed.commit()
        autor = seed.scalar(select(User.id).where(User.role == UserRole.ADMIN))
    modos = [("f1", "mboprinters-contacto-es", "fixed", autor),
             ("f2", "mbolasers-contacto-es", "rules", None),
             ("f3", "boprint-contacto", "none", None),
             ("f4", "pimpam-contacto-es", "inventado", None),
             # Mayúsculas: una carga a mano pudo meterlo así.
             ("f5", "fluxlasers-contacto-es", "FIXED", autor),
             # «fixed» sin propietario: arreglar el modo no basta, y el log
             # tiene que decirlo o se da por arreglado algo que sigue roto.
             ("f6", "artisjet-es-contacto-es", "fixed", None)]
    with engine.begin() as c:
        for fid, slug, modo, propietario in modos:
            c.execute(text(
                "INSERT INTO web_forms (id, slug, name, language, is_active, "
                "submit_success_mode, send_confirmation_email, assignment_mode, "
                "fixed_owner_user_id, notify_owner_on_new, recaptcha_enabled, "
                "is_site_default, created_by_user_id, created_at, updated_at) VALUES "
                "(:i, :s, 'F', 'es', 1, 'modal', 0, :m, :p, 1, 1, 0, :u, "
                "'2026-10-08', '2026-10-08')"),
                {"i": fid, "s": slug, "m": modo, "p": propietario, "u": autor})
    command.stamp(cfg, "20261008_0127")
    # `env.py` llama a `fileConfig()`, que apaga los loggers que ya existían
    # y dejaría `caplog` vacío. En el test no hace falta reconfigurar nada.
    # `caplog` no sirve aquí: `env.py` llama a `fileConfig()`, que reconfigura
    # el logging a media ejecución. Se escucha al logger directamente.
    avisos: list[str] = []

    class Recoge(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            avisos.append(record.getMessage())

    log = logging.getLogger("alembic.runtime.migration")
    handler = Recoge(level=logging.WARNING)
    log.addHandler(handler)
    log.disabled = False
    log.setLevel(logging.WARNING)
    # `env.py` llama a `fileConfig()`, que con `disable_existing_loggers`
    # apagaría este logger justo antes de que la migración escriba. En el
    # test no hace falta reconfigurar el logging.
    real = logging.config.fileConfig
    logging.config.fileConfig = lambda *_a, **_k: None
    try:
        command.upgrade(cfg, "20261009_0128")
    finally:
        logging.config.fileConfig = real
        log.removeHandler(handler)
    registro = "\n".join(avisos)
    with engine.connect() as c:
        final = dict(c.execute(text("SELECT id, assignment_mode FROM web_forms")).all())
    assert final["f1"] == "fixed_owner"       # reparado
    assert final["f2"] == "rules"             # intacto
    assert final["f3"] == "none"              # intacto
    # Uno que no se puede deducir se deja como está (y queda en el log):
    # cambiarlo a voleo asignaría leads al comercial equivocado.
    assert final["f4"] == "inventado"
    assert final["f5"] == "fixed_owner"       # «FIXED» también
    assert final["f6"] == "fixed_owner"
    # Y el que se queda sin propietario no se da por arreglado en silencio.
    assert "NO tiene propietario fijo" in registro
    assert "artisjet-es-contacto-es" in registro
    command.upgrade(cfg, "20261009_0128")     # relanzable: no vuelve a tocar nada
    with engine.connect() as c:
        assert dict(c.execute(
            text("SELECT id, assignment_mode FROM web_forms")).all()) == final


# --- 2d · la comprobación del Cuadre ----------------------------------------------------------


def _lead(s: Session, *, email: str, dias: int = 1, owner: str | None = None,
          origen: str = "web_form:mboprinters:es") -> Contact:
    c = Contact(first_name="Hans", last_name="Müller", email=email,
                origin="Formulario web · mboprinters.com (español)",
                origin_account_id=origen, owner_user_id=owner,
                created_at=datetime.now(UTC) - timedelta(days=dias))
    s.add(c)
    s.commit()
    return c


def _hallazgos(s: Session) -> list[CuadreFinding]:
    cuadre_engine.ejecutar(s, fuente="mysql", origen="manual")
    return list(s.scalars(
        select(CuadreFinding).where(CuadreFinding.check_id == CHECK_ID)
    ))


def test_el_cuadre_ve_el_lead_sin_comercial_y_se_cierra_al_asignarlo(factory):
    with factory() as s:
        lead = _lead(s, email="hans@muster.de")
        hallazgos = _hallazgos(s)
        assert [h.entidad_id for h in hallazgos] == [lead.id]
        h = hallazgos[0]
        assert h.estado == "abierto"
        assert h.entidad_tipo == "contacto"
        detalle = h.detalle_json or ""
        assert "mboprinters.com" in detalle
        assert f"/contacts/{lead.id}" in detalle

        # Al asignarle comercial desaparece.
        comercial = s.scalar(select(User).where(User.role == UserRole.USER))
        lead.owner_user_id = comercial.id
        s.commit()
        assert [h.estado for h in _hallazgos(s)] == ["resuelto"]


def test_el_cuadre_no_mira_lo_que_no_es_un_lead_de_formulario(factory):
    with factory() as s:
        comercial = s.scalar(select(User).where(User.role == UserRole.USER))
        _lead(s, email="asignado@muster.de", owner=comercial.id)
        _lead(s, email="woo@muster.de", origen="woocommerce:boprint")
        _lead(s, email="antiguo@muster.de", dias=90)
        s.add(Contact(first_name="Manual", email="manual@muster.de", origin="Manual"))
        s.commit()
        assert _hallazgos(s) == []


def test_el_cuadre_ve_el_lead_cuyo_comercial_esta_de_baja(factory):
    """Dar de baja a alguien no le quita los leads: sin esto, nadie los ve y
    el Cuadre tampoco, porque sí tienen propietario."""
    with factory() as s:
        comercial = s.scalar(select(User).where(User.role == UserRole.USER))
        lead = _lead(s, email="huerfano@muster.de", owner=comercial.id)
        assert _hallazgos(s) == []            # mientras está de alta, nada
        comercial.is_active = False
        s.commit()
        hallazgos = _hallazgos(s)
        assert [h.entidad_id for h in hallazgos] == [lead.id]
        assert "dado de baja" in (hallazgos[0].detalle_json or "")


def test_el_cuadre_no_pide_asignar_un_contacto_dado_de_baja(factory):
    """Un lead que ejerció su objeción RGPD queda inactivo: pedir que se le
    asigne comercial sería justo lo contrario de lo que toca."""
    with factory() as s:
        lead = _lead(s, email="objecion@muster.de")
        lead.is_active = False
        s.commit()
        assert _hallazgos(s) == []


def test_el_cuadre_respeta_los_formularios_de_asignar_a_mano(factory):
    """«Sin asignar» es una opción de la pantalla: ahí no tener comercial es
    lo configurado, no un descuadre. Sin esto, cada lead de un formulario así
    llenaría el Cuadre."""
    with factory() as s:
        form = _form_con_modo(s, "none")
        out = _enviar(s, form, "amano@muster.de")
        assert s.get(Contact, out.contact_id).owner_user_id is None
        assert _hallazgos(s) == []

        # El mismo lead, pero de un formulario por reglas, sí sale.
        otro = _form_con_modo(s, "rules", slug="mbolasers-contacto-es")
        out2 = _enviar(s, otro, "porreglas@muster.de")
        assert [h.entidad_id for h in _hallazgos(s)] == [out2.contact_id]


def test_el_aviso_de_un_comercial_de_baja_cae_al_generico(factory):
    """El agujero que abría mandar el aviso solo al comercial: si está de
    baja, no lo lee nadie."""
    from app.services.web_forms.aviso import destinatarios

    with factory() as s:
        comercial = s.scalar(select(User).where(User.role == UserRole.USER))
        comercial.is_active = False
        s.flush()
        contacto = Contact(first_name="X", email="x@y.z", owner_user_id=comercial.id)
        assert destinatarios(s, contacto) == [("info@streamtec.es", "")]
