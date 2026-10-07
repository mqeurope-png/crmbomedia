"""ERP · fotos del embalaje (07/10/2026).

Las fotos se guardaban dentro del contenedor `api` y cada despliegue las
borraba. Ahora son `shipment_files` (`kind = foto`) en el almacén de expedición
(el bind mount que ya guarda albaranes y etiquetas):

- la foto subida queda en `shipment_files` y en el almacén, y se lee desde otra
  instancia (como tras reiniciar el contenedor);
- la migración de `packing_json.documents`: la que tiene archivo pasa a
  `shipment_files`, la perdida no deja referencia rota;
- HEIC (iPhone) y WebP se aceptan y se guardan en JPEG; las miniaturas;
- errores claros: vacío 400, más de 15 MB 413, formato 415;
- varias fotos conviven y salen todas en la Cola SAT;
- Cuadre: `cuadre_runs.fuente` admite `woocommerce` en un entorno creado por
  las migraciones, y la comprobación de Woo usa la ventana del repaso.
"""
from __future__ import annotations

import io
import json
import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.erp.api.shipping as shipping_api
import app.main  # noqa: F401
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_session
from app.erp.fotos import migrar_documentos
from app.erp.models import ErpSettings, Order, ShipmentFile
from app.erp.models.settings import ERP_SETTINGS_SINGLETON_ID
from app.main import app
from app.storage.local import LocalShippingStorage
from tests._test_helpers import auth_headers, seed_test_users

BACKEND_ROOT = Path(__file__).resolve().parents[1]


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
def almacen(tmp_path, monkeypatch) -> Path:
    """El bind mount de expedición (`/opt/crmbo/uploads/erp-shipping`)."""
    base = tmp_path / "erp-shipping"
    monkeypatch.setattr(shipping_api, "get_shipping_storage",
                        lambda: LocalShippingStorage(base_dir=str(base)))
    return base


@pytest.fixture()
def http(factory, almacen) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _pedido(factory, *, transport: str = "not_shipped", prep: str = "packed",
            packing: dict | None = None, numero: str = "BOPRIN-99977") -> str:
    with factory() as s:
        o = Order(order_number=numero, external_source="manual", preparation_status=prep,
                  transport_status=transport, payment_status="paid",
                  packing_json=json.dumps(packing) if packing else None)
        s.add(o)
        s.commit()
        return o.id


def _jpeg(size=(40, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (120, 80, 40)).save(buf, format="JPEG")
    return buf.getvalue()


def _heic() -> bytes:
    import pillow_heif

    buf = io.BytesIO()
    pillow_heif.from_pillow(Image.new("RGB", (40, 30), (10, 160, 90))).save(buf, quality=80)
    return buf.getvalue()


def _subir(http, oid, nombre, data, mime):
    return http.post(f"/api/erp/orders/{oid}/attach-document",
                     files={"file": (nombre, data, mime)}, headers=auth_headers(http, "sat"))


# --- 1 · la foto queda donde persiste ----------------------------------------------------


def test_la_foto_queda_en_shipment_files_y_en_el_almacen_persistente(http, factory, almacen):
    oid = _pedido(factory)
    r = _subir(http, oid, "embalaje.jpg", _jpeg(), "image/jpeg")
    assert r.status_code == 201, r.text
    with factory() as s:
        row = s.scalar(select(ShipmentFile).where(ShipmentFile.order_id == oid))
        assert (row.kind, row.source, row.mime_type) == ("foto", "manual_upload", "image/jpeg")
        assert row.storage_path.startswith(f"{oid}/foto/")
        o = s.get(Order, oid)
        assert "documents" not in json.loads(o.packing_json or "{}")
    # «Tras reiniciar el contenedor»: otra instancia del almacén, misma carpeta
    # (el bind mount), lee el mismo fichero.
    assert (almacen / row.storage_path).is_file()
    assert LocalShippingStorage(base_dir=str(almacen)).read(row.storage_path)[:2] == b"\xff\xd8"
    # Y se descarga por el endpoint de siempre.
    d = http.get(r.json()["file"]["download_url"], headers=auth_headers(http, "sat"))
    assert d.status_code == 200 and d.content[:2] == b"\xff\xd8"


def test_varias_fotos_conviven_y_salen_todas_en_la_cola_sat(http, factory):
    oid = _pedido(factory)
    for n in ("a.jpg", "b.jpg"):
        assert _subir(http, oid, n, _jpeg(), "image/jpeg").status_code == 201
    r = http.get(f"/api/erp/orders/{oid}/shipping-files?kind=foto",
                 headers=auth_headers(http, "sat"))
    assert [f["filename"] for f in r.json()["items"]] == ["b.jpg", "a.jpg"]   # las dos vigentes
    cola = http.get("/api/erp/sat/queue", headers=auth_headers(http, "sat")).json()
    item = next(i for tab in ("embalados", "pendiente_recogida", "por_embalar",
                              "en_preparacion") for i in cola[tab] if i["id"] == oid)
    assert [f["filename"] for f in item["fotos"]] == ["a.jpg", "b.jpg"]
    assert item["has_etiqueta"] is False                   # una foto no es una etiqueta


def test_tambien_por_el_endpoint_de_ficheros_de_envio(http, factory):
    oid = _pedido(factory)
    r = http.post(f"/api/erp/orders/{oid}/shipping-files", data={"kind": "foto"},
                  files={"file": ("x.jpg", _jpeg(), "image/jpeg")},
                  headers=auth_headers(http, "sat"))
    assert r.status_code == 201, r.text
    assert r.json()["file"]["kind"] == "foto" and r.json()["transition_applied"] is False


# --- 2 · formatos del móvil y errores --------------------------------------------------------


def test_heic_del_iphone_se_acepta_y_se_guarda_en_jpeg(http, factory):
    oid = _pedido(factory)
    r = _subir(http, oid, "IMG_0042.HEIC", _heic(), "image/heic")
    assert r.status_code == 201, r.text
    f = r.json()["file"]
    assert (f["filename"], f["mime_type"]) == ("IMG_0042.jpg", "image/jpeg")
    d = http.get(f["download_url"], headers=auth_headers(http, "sat"))
    assert d.content[:2] == b"\xff\xd8"                   # se ve en cualquier navegador
    # Miniatura pequeña para la card / ficha.
    t = http.get(f"{f['download_url']}?thumb=1", headers=auth_headers(http, "sat"))
    assert t.status_code == 200 and t.headers["content-type"] == "image/jpeg"


def test_webp_y_heic_sin_tipo_tambien(http, factory):
    oid = _pedido(factory)
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), (1, 2, 3)).save(buf, format="WEBP")
    r = _subir(http, oid, "foto.webp", buf.getvalue(), "image/webp")
    assert r.status_code == 201 and r.json()["file"]["mime_type"] == "image/jpeg"
    r = _subir(http, oid, "foto.heic", _heic(), "")      # Android a veces no manda tipo
    assert r.status_code == 201 and r.json()["file"]["mime_type"] == "image/jpeg"


def test_pdf_tal_cual(http, factory):
    oid = _pedido(factory)
    r = _subir(http, oid, "albaran firmado.pdf", b"%PDF-1.4 algo", "application/pdf")
    assert r.status_code == 201 and r.json()["file"]["mime_type"] == "application/pdf"


@pytest.mark.parametrize(("data", "mime", "status", "texto"), [
    (b"", "image/jpeg", 400, "Archivo vacío."),
    (b"no soy una imagen", "text/plain", 415, "No se reconoce el formato"),
])
def test_rechazos_con_mensaje_claro(http, factory, data, mime, status, texto):
    oid = _pedido(factory)
    r = _subir(http, oid, "x", data, mime)
    assert r.status_code == status and texto in r.json()["detail"]
    with factory() as s:
        assert s.scalar(select(ShipmentFile).where(ShipmentFile.order_id == oid)) is None


def test_imagen_descomunal_se_rechaza_sin_tumbar_el_api(http, factory, monkeypatch):
    """Una «bomba de descompresión» (millones de píxeles en pocos KB) es un 415
    con mensaje, no un 500."""
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)   # 40×30 ya pasa del doble
    oid = _pedido(factory)
    r = _subir(http, oid, "bomba.png", _jpeg(), "image/png")
    assert r.status_code == 415 and "demasiado grande" in r.json()["detail"]


def test_nombre_con_acentos_en_la_descarga(http, factory):
    oid = _pedido(factory)
    f = _subir(http, oid, "Caja «frágil».pdf", b"%PDF-1.4 x", "application/pdf").json()["file"]
    d = http.get(f["download_url"], headers=auth_headers(http, "sat"))
    cd = d.headers["content-disposition"]
    assert d.status_code == 200 and cd.startswith("inline; filename=")
    assert "filename*=UTF-8''Caja%20%C2%ABfr%C3%A1gil%C2%BB.pdf" in cd


def test_mas_de_15_mb_413(http, factory):
    oid = _pedido(factory)
    r = _subir(http, oid, "enorme.jpg", b"\xff\xd8" + b"0" * (15 * 1024 * 1024 + 1),
               "image/jpeg")
    assert r.status_code == 413 and "15 MB" in r.json()["detail"]


# --- 3 · migración de `packing_json.documents` -----------------------------------------------


def test_migracion_mueve_la_que_existe_y_no_deja_rota_la_perdida(factory, tmp_path):
    uploads = tmp_path / "uploads-erp"                 # el antiguo `erp_uploads_dir`
    shipping = tmp_path / "erp-shipping"
    oid = _pedido(factory, packing={"weight_kg": 3, "documents": [
        {"storage_key": "ORDER/abc_embalaje.png", "url": None, "backend": "local",
         "filename": "embalaje.png", "content_type": "image/png", "size_bytes": 10,
         "uploaded_at": "2026-10-07T10:00:00+00:00", "uploaded_by_user_id": "no-existe"},
        {"storage_key": "ORDER/def_movil.jpg", "url": None, "backend": "local",
         "filename": "movil.jpg", "content_type": "image/jpeg", "size_bytes": 999,
         "uploaded_at": "2026-10-07T08:00:00+00:00"},
    ]})
    # Solo la primera sigue en disco (la otra se fue con un despliegue).
    png = io.BytesIO()
    Image.new("RGB", (4, 4)).save(png, format="PNG")
    destino = uploads / "ORDER" / "abc_embalaje.png"
    destino.parent.mkdir(parents=True)
    destino.write_bytes(png.getvalue())

    with factory() as s:
        r = migrar_documentos(s.connection(), uploads_dir=str(uploads),
                              shipping_dir=str(shipping))
        s.commit()
    assert r == {"pedidos": 1, "movidas": 1, "perdidas": 1}
    with factory() as s:
        rows = s.scalars(select(ShipmentFile).where(ShipmentFile.order_id == oid)).all()
        assert [(f.kind, f.filename, f.mime_type) for f in rows] == [
            ("foto", "embalaje.png", "image/png")]
        assert rows[0].uploaded_by_user_id is None          # el autor ya no existe
        assert (shipping / rows[0].storage_path).is_file()
        packing = json.loads(s.get(Order, oid).packing_json)
        assert "documents" not in packing and packing["weight_kg"] == 3
        perdida = packing["fotos_perdidas"][0]
        assert perdida["filename"] == "movil.jpg" and "storage_key" not in perdida
    # Idempotente: otra pasada no hace nada.
    with factory() as s:
        assert migrar_documentos(s.connection(), uploads_dir=str(uploads),
                                 shipping_dir=str(shipping))["pedidos"] == 0


def test_migracion_recupera_de_la_carpeta_de_rescate(factory, tmp_path):
    """La que se copió al volumen antes de desplegar (`_rescate/`) se recupera."""
    shipping = tmp_path / "erp-shipping"
    oid = _pedido(factory, packing={"documents": [
        {"storage_key": "ORDER/x_foto.jpg", "backend": "local", "filename": "foto.jpg",
         "content_type": "image/jpeg"}]})
    rescate = shipping / "_rescate" / "ORDER" / "x_foto.jpg"
    rescate.parent.mkdir(parents=True)
    rescate.write_bytes(_jpeg())
    with factory() as s:
        r = migrar_documentos(s.connection(), uploads_dir=str(tmp_path / "vacio"),
                              shipping_dir=str(shipping))
        s.commit()
    assert r["movidas"] == 1 and r["perdidas"] == 0
    with factory() as s:
        assert s.scalar(select(ShipmentFile).where(ShipmentFile.order_id == oid)).kind == "foto"


def test_migracion_un_documento_que_falla_no_tumba_el_resto(factory, tmp_path):
    """Si copiar uno falla (disco, permisos…), ese queda como perdido con su
    motivo y los demás se migran: la migración no frena el arranque del api."""
    uploads = tmp_path / "uploads-erp"
    shipping = tmp_path / "erp-shipping"
    oid = _pedido(factory, packing={"documents": [
        {"storage_key": "ORDER/a_rota.jpg", "backend": "local", "filename": "rota.jpg",
         "content_type": "image/jpeg"},
        {"storage_key": "ORDER/b_buena.jpg", "backend": "local", "filename": "buena.jpg",
         "content_type": "image/jpeg"},
    ]})
    for k in ("a_rota.jpg", "b_buena.jpg"):
        (uploads / "ORDER").mkdir(parents=True, exist_ok=True)
        (uploads / "ORDER" / k).write_bytes(_jpeg())

    class AlmacenQueFalla(LocalShippingStorage):
        def save(self, order_id, kind, filename, content):
            if filename.startswith("rota"):
                raise OSError("disco lleno")
            return super().save(order_id, kind, filename, content)

    with factory() as s:
        r = migrar_documentos(s.connection(), uploads_dir=str(uploads),
                              shipping_dir=str(shipping),
                              storage=AlmacenQueFalla(base_dir=str(shipping)))
        s.commit()
    assert r == {"pedidos": 1, "movidas": 1, "perdidas": 1}
    with factory() as s:
        rows = s.scalars(select(ShipmentFile).where(ShipmentFile.order_id == oid)).all()
        assert [f.filename for f in rows] == ["buena.jpg"]
        perdida = json.loads(s.get(Order, oid).packing_json)["fotos_perdidas"][0]
        assert perdida["filename"] == "rota.jpg"
        assert "No se pudo recuperar" in perdida["motivo"]


def test_la_ficha_avisa_de_las_fotos_perdidas(http, factory):
    oid = _pedido(factory, packing={"fotos_perdidas": [
        {"filename": "movil.jpg", "uploaded_at": "2026-10-07T08:00:00+00:00"}]})
    r = http.get(f"/api/erp/orders/{oid}/shipping-files?kind=foto",
                 headers=auth_headers(http, "sat"))
    assert r.json()["items"] == []
    assert r.json()["fotos_perdidas"][0]["filename"] == "movil.jpg"


# --- 4 · Cuadre ------------------------------------------------------------------------------


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
    try:
        yield cfg, db_url
    finally:
        os.chdir(old_cwd)
        get_settings.cache_clear()


def test_cuadre_runs_admite_woocommerce_creado_por_las_migraciones(alembic_cfg):
    cfg, db_url = alembic_cfg
    command.stamp(cfg, "20261001_0123")
    command.upgrade(cfg, "20261007_0125")
    engine = create_engine(db_url)
    cols = {c["name"]: c["type"] for c in inspect(engine).get_columns("cuadre_runs")}
    assert cols["fuente"].length == 32
    cols = {c["name"]: c["type"] for c in inspect(engine).get_columns("cuadre_findings")}
    assert cols["check_id"].length == 64
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO cuadre_runs (id, fuente, origen, estado, resumen_json, created_at, "
            "updated_at) VALUES ('r1', 'woocommerce', 'manual', 'ok', '{}', "
            "'2026-10-07 00:00:00', '2026-10-07 00:00:00')"))
    command.downgrade(cfg, "20261003_0124")
    cols = {c["name"]: c["type"] for c in inspect(engine).get_columns("cuadre_runs")}
    assert cols["fuente"].length == 10


def test_la_comprobacion_de_woo_usa_la_ventana_del_repaso(factory, monkeypatch):
    """Un solo ajuste: cambiar los días del repaso cambia también el Cuadre."""
    from app.erp.cuadre import engine as cuadre_engine
    from app.erp.cuadre.registry import registro
    from app.integrations.woocommerce.missing import missing_config

    assert registro()["pedido_woo_pagado_sin_bohub"].dias_defecto is None   # sin ajuste propio
    vistos: list[int] = []

    def falso(session, store, client, *, days, now, **kw):
        vistos.append(days)
        return {"faltan": [], "listados": 0, "llamadas": 1, "con_tope": False}

    monkeypatch.setattr("app.integrations.woocommerce.missing.pagados_que_faltan", falso)
    monkeypatch.setattr("app.integrations.woocommerce.client.WooHTTPClient", lambda st: None)
    with factory() as s:
        from app.core.crypto import encrypt
        from app.models.crm import ExternalSystem
        from app.models.integration_settings import (
            IntegrationAccount,
            IntegrationMode,
            IntegrationStatus,
        )

        s.add(IntegrationAccount(
            system=ExternalSystem.WOOCOMMERCE, account_id="boprint", display_name="Boprint",
            enabled=True, mode=IntegrationMode.LIVE, status=IntegrationStatus.CONFIGURED,
            base_url="https://boprint.example", consumer_key_encrypted=encrypt("ck"),
            consumer_secret_encrypted=encrypt("cs"), credential_status="configured"))
        s.add(ErpSettings(id=ERP_SETTINGS_SINGLETON_ID,
                          factusol_series_json=json.dumps({"woo_missing_days": 14})))
        s.commit()
        assert missing_config(s)["days"] == 14                       # el repaso
        cuadre_engine.ejecutar(s, fuente="woocommerce", origen="manual",
                               ahora=datetime.now(UTC) - timedelta(seconds=1))
    assert vistos == [14]                                            # y el Cuadre
