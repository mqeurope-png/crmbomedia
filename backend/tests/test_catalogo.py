"""Base de conocimiento, Fase 2 (11/10/2026): el catálogo y los precios
verificados. Las máquinas del compositor de correos (Supabase) y los
consumibles de las tiendas (WooCommerce Store API) están simulados: nada sale
a la red. El asistente usa el catálogo como única fuente de precios; Claude
también está simulado."""
from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_session
from app.erp.models.orders import Order, OrderLine, OrderSource, PaymentStatus
from app.main import app
from app.models.conocimiento import (
    FUENTE_COMPOSITOR,
    FUENTE_WOOCOMMERCE,
    SYNC_ERROR,
    SYNC_OK,
    SYNC_PARCIAL,
    TIPO_CONSUMIBLE,
    TIPO_MAQUINA,
    TIPO_REPUESTO,
    CatalogoSincronizacion,
    MarcaCatalogo,
    ProductoCatalogo,
)
from app.models.crm import (
    Contact,
    EmailDirection,
    EmailMessage,
    EmailThread,
    User,
    UserEmailAliasPref,
    UserRole,
)
from app.models.ia import BorradorAsistente
from app.models.leads import LeadClassification
from app.services import llm
from app.services.asistente import contexto, redactor
from app.services.asistente.config import estimar_tokens
from app.services.conocimiento import (
    catalogo,
    catalogo_compositor,
    catalogo_job,
    catalogo_tiendas,
)
from app.services.conocimiento.config import defectos as config_defectos
from app.services.web_forms.sitios import SITIOS_DE_PARTIDA, Registro, fijar_para_tests
from tests._test_helpers import auth_headers, seed_org_google_integration, seed_test_users

ALIAS = "info@bomedia.net"
KLAUS = "torracollons@elbarquito.net"
AHORA = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
ACTUALIZADO = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
SUPABASE = "https://supa.example"
CLAVE = "clave-publicable-de-prueba"

#: Las 18 máquinas del compositor (nombres de la propuesta de interés → máquinas).
MAQUINAS: tuple[tuple[str, str], ...] = (
    ("artisJet", "artisJet Young"), ("artisJet", "artisJet Proud"),
    ("artisJet", "artisJet 3000Pro"), ("artisJet", "artisJet 5000U"),
    ("artisJet", "artisJet 6090 Trust"),
    ("MBO", "MBO 1015"), ("MBO", "MBO 3050"), ("MBO", "MBO 4060"), ("MBO", "MBO 6090"),
    ("MBO", "MBO UV1612G"), ("MBO", "MBO UV1812"), ("MBO", "MBO UV2513"),
    ("PimPam", "PimPam CaseBox"), ("PimPam", "PimPam Custom"),
    ("SmartJet FLEX", "SmartJet FLEX 30"), ("SmartJet FLEX", "SmartJet FLEX 60"),
    ("SmartJet FLEX", "SmartJet FLEX Pro"), ("SmartJet FLEX", "SmartJet FLEX Max"),
)
IDIOMAS_I18N = ("fr", "de", "en", "nl")
DOMINIO_POR_MARCA = {
    "artisJet": "artisjet-printers.eu", "MBO": "mboprinters.com", "PimPam": "pimpam-vending.com",
    "SmartJet FLEX": "www.mqeurope.com",
}


def _precio_es(i: int) -> str:
    return f"{(i + 1) * 1000 + 300:,} €".replace(",", ".")          # «9.300 €»


def _precio_fr(i: int) -> str:
    return f"{(i + 1) * 1000 + 300:,} €".replace(",", " ")     # «9 300 €» (espacio fino)


def _compositor_data(*, sin_precio: tuple[tuple[str, str], ...] = (("artisJet 5000U", "de"),
                                                                    ("MBO 3050", "nl"),
                                                                    ("MBO 3050", "de"),
                                                                    ("PimPam Custom", "en"),
                                                                    ("SmartJet FLEX 30", "fr"),
                                                                    ("SmartJet FLEX 30", "nl"),
                                                                    ("SmartJet FLEX 60", "de"),
                                                                    ("MBO UV2513", "en"))
                     ) -> dict[str, Any]:
    """El JSON del compositor: 18 máquinas × 5 idiomas = 90 enlaces, 82 precios."""
    productos = []
    for i, (marca, nombre) in enumerate(MAQUINAS):
        slug = nombre.lower().replace(" ", "-")
        dominio = DOMINIO_POR_MARCA[marca]
        p: dict[str, Any] = {
            "id": slug, "brand": marca, "name": nombre, "badge": "Nuevo" if i % 5 == 0 else "",
            "img": f"https://{dominio}/img/{slug}.jpg", "desc": f"Descripción de {nombre}",
            "area": "A2" if "5000" in nombre or "3050" in nombre else "A3",
            "alt": nombre, "feat1": "Tinta UV LED", "feat2": "Cabezal industrial",
            "price": _precio_es(i), "link": f"https://{dominio}/{slug}/", "visible": True,
            "i18n": {},
        }
        for idioma in IDIOMAS_I18N:
            precio = _precio_fr(i) if idioma == "fr" else _precio_es(i).replace(".", ",") if (
                idioma == "en") else _precio_es(i)
            if (nombre, idioma) in sin_precio:
                precio = ""
            p["i18n"][idioma] = {
                "desc": f"[{idioma}] {nombre}", "feat1": f"[{idioma}] feat1",
                "feat2": f"[{idioma}] feat2", "price": precio,
                "link": f"https://{dominio}/{idioma}/{slug}/",
            }
        productos.append(p)
    return {
        "products": productos,
        "brands": [
            {"id": marca, "name": marca, "logo": f"https://cdn.example/{marca}.png",
             "color": "#123456", "urls": {"es": f"https://{dominio}/",
                                           "en": f"https://{dominio}/en/"}}
            for marca, dominio in DOMINIO_POR_MARCA.items()
        ],
        "templates": [],
    }


def _supabase_falso(data: dict[str, Any] | None, *, caido: bool = False,
                    updated_at: str = ACTUALIZADO.isoformat(),
                    peticiones: list[httpx.Request] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if peticiones is not None:
            peticiones.append(request)
        if caido:
            raise httpx.ConnectError("Supabase no responde", request=request)
        if request.headers.get("apikey") != CLAVE:
            return httpx.Response(401, json={"message": "Invalid API key"})
        return httpx.Response(200, json=[{"id": "main", "data": data, "updated_at": updated_at}])
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- la tienda de fluxlasers.es (386 productos en 19 categorías) ----------------------------

CATEGORIAS_WOO = (
    ("Máquinas láser", "maquinas-laser"), ("Beambox II", "beambox-ii"), ("Ador", "ador"),
    ("HEXA", "hexa"), ("Beamo", "beamo"), ("Consumibles", "consumibles"),
    ("Lentes", "lentes"), ("Espejos", "espejos"), ("Tubos láser", "tubos-laser"),
    ("Repuestos", "repuestos"), ("Materiales", "materiales"), ("Madera", "madera"),
    ("Metacrilato", "metacrilato"), ("Cuero", "cuero"), ("Accesorios", "accesorios"),
    ("Rotatorios", "rotatorios"), ("Filtros", "filtros"), ("Mesas", "mesas"),
    ("Software", "software"),
)
assert len(CATEGORIAS_WOO) == 19


def _producto_woo(i: int, *, idioma: str = "es", en_oferta: bool = False) -> dict[str, Any]:
    cat_nombre, cat_slug = CATEGORIAS_WOO[i % len(CATEGORIAS_WOO)]
    modelo = ("Beambox II", "Ador", "HEXA", "Beamo")[i % 4]
    if cat_slug == "maquinas-laser":
        nombre = f"FLUX {modelo} láser {i}"
    elif cat_slug in ("repuestos", "tubos-laser"):
        nombre = f"Tubo láser {modelo} {i}"
    else:
        nombre = f"Lente {modelo} {i}" if cat_slug == "lentes" else f"{cat_nombre} {modelo} {i}"
    if idioma != "es":
        nombre = f"[{idioma}] {nombre}"
    prefijo = "/" if idioma == "es" else f"/{idioma}/"
    precio = (i + 1) * 1250                       # en céntimos: 12,50 €, 25,00 €…
    return {
        "id": 1000 + i, "name": nombre, "slug": f"producto-{i}",
        "permalink": f"https://fluxlasers.es{prefijo}producto/producto-{i}/",
        "sku": f"FLX-{i:04d}", "type": "simple", "on_sale": en_oferta,
        "short_description": f"<p>Descripción corta {i}</p>",
        "prices": {
            "price": str(precio), "regular_price": str(precio), "sale_price": str(precio),
            "price_range": None, "currency_code": "EUR", "currency_symbol": "€",
            "currency_minor_unit": 2, "currency_decimal_separator": ",",
            "currency_thousand_separator": ".", "currency_prefix": "", "currency_suffix": "&nbsp;€",
        },
        "images": [{"src": f"https://fluxlasers.es/wp-content/{i}.jpg", "alt": nombre}],
        "categories": [{"id": 50 + (i % 19), "name": cat_nombre, "slug": cat_slug,
                        "link": f"https://fluxlasers.es/categoria/{cat_slug}/"}],
        "tags": [], "attributes": [], "variations": [], "is_purchasable": True,
        "is_in_stock": i % 7 != 0,
    }


def _tienda_falsa(
    *, productos_por_sitio: dict[str, int], caidos: frozenset[str] = frozenset(),
    idiomas_sin_variante: frozenset[str] = frozenset(), peticiones: list[str] | None = None,
) -> httpx.Client:
    """Simula la Store API de cada web: `productos_por_sitio` dice cuántos
    productos tiene cada dominio (las que no están no tienen tienda → 404)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if peticiones is not None:
            peticiones.append(str(request.url))
        host = request.url.host.removeprefix("www.")
        ruta = request.url.path
        clave = next((k for k in productos_por_sitio if k == host), None)
        if host in caidos:
            raise httpx.ConnectError("la tienda no responde", request=request)
        if clave is None or "wp-json/wc/store/products" not in ruta:
            return httpx.Response(404, json={"code": "rest_no_route",
                                             "message": "No route was found"})
        idioma = "es"
        trozos = [t for t in ruta.split("/") if t]
        if trozos and len(trozos[0]) == 2:
            idioma = trozos[0]
        if idioma in idiomas_sin_variante:
            return httpx.Response(404, json={"code": "rest_no_route"})
        total = productos_por_sitio[clave]
        pagina = int(request.url.params.get("page", "1"))
        por_pagina = int(request.url.params.get("per_page", "100"))
        inicio = (pagina - 1) * por_pagina
        trozo = [_producto_woo(i, idioma=idioma) for i in range(inicio, min(inicio + por_pagina,
                                                                              total))]
        paginas = max(1, -(-total // por_pagina))
        return httpx.Response(200, json=trozo, headers={"X-WP-Total": str(total),
                                                        "X-WP-TotalPages": str(paginas)})
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- fixtures ---------------------------------------------------------------------------------


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
        admin = seed.scalar(select(User.id).where(User.role == UserRole.ADMIN))
        seed_org_google_integration(seed, connected_by_user_id=admin)
        seed.add(UserEmailAliasPref(user_id=admin, alias_email=ALIAS, is_allowed=True,
                                    is_default=True))
        seed.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def http(session_factory) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _entorno(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """La IA «configurada» (pero simulada), la clave del compositor en el
    entorno (nunca en el código) y la lista de sitios de partida."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "clave-de-prueba")
    monkeypatch.setenv("SUPABASE_COMPOSER_URL", SUPABASE)
    monkeypatch.setenv("SUPABASE_COMPOSER_KEY", CLAVE)
    get_settings.cache_clear()
    fijar_para_tests(Registro(sitios=SITIOS_DE_PARTIDA))
    yield
    fijar_para_tests(None)
    get_settings.cache_clear()


def _sincronizar_compositor(session: Session, data: dict[str, Any] | None = None, **kw: Any,
                            ) -> CatalogoSincronizacion:
    pasada = catalogo_compositor.sincronizar(
        session, http=_supabase_falso(data if data is not None else _compositor_data(), **kw),
        ahora=kw.pop("ahora", AHORA) if "ahora" in kw else AHORA)
    session.commit()
    return pasada


def _texto_de(prompt: Any) -> str:
    if isinstance(prompt, str):
        return prompt
    return "\n".join(str(b.get("text", "")) for b in prompt if isinstance(b, dict))


class _ClaudeFalso:
    def __init__(self, respuesta: dict[str, Any]) -> None:
        self.respuesta = respuesta
        self.llamadas: list[dict[str, Any]] = []

    def __call__(self, *, api_key: str, model: str, system_prompt: Any, user_prompt: Any,
                 max_tokens: int | None = None, parametros: dict[str, Any] | None = None) -> str:
        self.llamadas.append({"model": model, "system": _texto_de(system_prompt),
                              "user": _texto_de(user_prompt)})
        llm.registrar_uso(llm.Uso(modelo=model, tokens_entrada=1500, tokens_salida=300,
                                  duracion_ms=800))
        return json.dumps(self.respuesta)

    @property
    def ultimo(self) -> dict[str, Any]:
        return self.llamadas[-1]


def _admin(session: Session) -> User:
    return session.scalar(select(User).where(User.role == UserRole.ADMIN))


def _contacto(session: Session, *, idioma: str = "de", intereses: list[str] | None = None,
              email: str = KLAUS) -> Contact:
    c = Contact(first_name="Klaus", last_name="Barquito", email=email, language=idioma,
                origin="web_form:mboprinters-contacto-de",
                lead_interest=(intereses or [None])[0])
    session.add(c)
    session.flush()
    if intereses:
        session.add(LeadClassification(
            contact_id=c.id, source="test", source_ref=f"ref-{c.id[:8]}", provider="test",
            model="test", interest=intereses[0], interests_json=json.dumps(intereses),
            language=idioma, is_spam=False, confidence=0.9,
        ))
        session.flush()
    return c


def _naive(valor: datetime | None) -> datetime | None:
    """SQLite devuelve las fechas sin zona: se comparan sin ella."""
    return valor.replace(tzinfo=None) if valor is not None else None


def _hilo(session: Session, contacto: Contact | None, mensajes: list[dict[str, Any]], *,
          asunto: str = "UV-Drucker", delivered_to: str = "info@mboprinters.com") -> EmailThread:
    admin = _admin(session)
    base = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
    hilo = EmailThread(
        contact_id=contacto.id if contacto else None, initiated_by_user_id=admin.id,
        gmail_thread_id=f"gt-{asunto[:6]}-{len(mensajes)}", gmail_account_user_id=admin.id,
        subject=asunto, first_message_at=base, last_message_at=base, message_count=len(mensajes),
    )
    session.add(hilo)
    session.flush()
    for i, m in enumerate(mensajes):
        nuestro = m.get("lado") == "nosotros"
        session.add(EmailMessage(
            thread_id=hilo.id, gmail_message_id=f"gm-{hilo.id[:6]}-{i}",
            gmail_account_user_id=admin.id,
            direction=EmailDirection.OUTBOUND if nuestro else EmailDirection.INBOUND,
            from_email=m["de"], to_emails_json=json.dumps([delivered_to]), subject=asunto,
            body_text=m["texto"], sent_at=m.get("fecha") or base + timedelta(hours=i),
            contact_id=contacto.id if contacto else None,
            delivered_to=None if nuestro else delivered_to,
            created_by_user_id=admin.id if nuestro else None,
        ))
    session.flush()
    return hilo


def _pedido(session: Session, contacto: Contact, descripcion: str) -> Order:
    o = Order(external_source=OrderSource.MANUAL, order_number="P-1", contact_id=contacto.id,
              total_amount=3990.0, currency="EUR", payment_status=PaymentStatus.PAID,
              placed_at=AHORA - timedelta(days=200))
    session.add(o)
    session.flush()
    session.add(OrderLine(order_id=o.id, position=0, product_sku="FLX", description=descripcion,
                          quantity=1, unit_price=3990.0, line_total=3990.0))
    session.flush()
    return o


# --- el precio, tal cual y como número ---------------------------------------------------------


def test_el_precio_se_guarda_tal_cual_y_el_numero_solo_ordena() -> None:
    assert catalogo.numero_de("9.300 €") == 9300
    assert catalogo.numero_de("9 300 €") == 9300 and catalogo.numero_de("9 300 €") == 9300
    assert catalogo.numero_de("9,300.50 $") == 9300.5 and catalogo.numero_de("9.300,50 €") == 9300.5
    assert catalogo.numero_de("12,50 €") == 12.5 and catalogo.numero_de("ab 2.990 €") == 2990
    assert catalogo.numero_de("1299") == 1299 and catalogo.numero_de("a consultar") is None
    assert catalogo.moneda_de("9.300 €") == "EUR" and catalogo.moneda_de("$ 1,200") == "USD"
    assert catalogo.moneda_de("CHF 950") == "CHF" and catalogo.moneda_de("consultar") is None
    # Orientativo: más de seis meses sin confirmar (o sin fecha).
    assert catalogo.es_orientativo(AHORA - timedelta(days=200), ahora=AHORA, meses=6) is True
    assert catalogo.es_orientativo(AHORA - timedelta(days=100), ahora=AHORA, meses=6) is False
    assert catalogo.es_orientativo(None, ahora=AHORA, meses=6) is True
    # Los avisos existen en los cinco idiomas y otro idioma cae al inglés.
    assert set(catalogo.AVISOS) == {"es", "en", "de", "fr", "nl"}
    for textos in catalogo.AVISOS.values():
        assert set(textos) == {"con", "sin", "sin_declarar", "vies", "no_factura", "orientativo"}
    assert catalogo.avisos_en("pt") == catalogo.AVISOS["en"]
    assert catalogo.avisos_en("es")["orientativo"] == (
        "Precio orientativo, lo confirmamos al preparar la oferta.")


# --- el compositor -----------------------------------------------------------------------------


def test_sincroniza_el_compositor_18_maquinas_en_cinco_idiomas(session_factory: sessionmaker,
                                                                ) -> None:
    peticiones: list[httpx.Request] = []
    with session_factory() as s:
        pasada = catalogo_compositor.sincronizar(
            s, http=_supabase_falso(_compositor_data(), peticiones=peticiones), ahora=AHORA)
        s.commit()
        assert pasada.estado == SYNC_OK and pasada.fuente == FUENTE_COMPOSITOR
        assert pasada.productos == 90 and pasada.nuevos == 90 and pasada.idos == 0
        assert _naive(pasada.origen_actualizado_en) == _naive(ACTUALIZADO)   # updated_at de la fila
        resumen = json.loads(pasada.resumen_json)["compositor"]
        assert resumen["por_idioma"] == {"es": 18, "fr": 18, "de": 18, "en": 18, "nl": 18}
        assert resumen["con_precio"] == 82 and resumen["marcas"] == 4
        # La petición: la fila main, con la clave en la cabecera apikey (de .env).
        assert str(peticiones[0].url) == (
            f"{SUPABASE}/rest/v1/composer_data?select=data,updated_at&id=eq.main")
        assert peticiones[0].headers["apikey"] == CLAVE
        filas = list(s.scalars(select(ProductoCatalogo)))
        assert len(filas) == 90 and all(f.tipo == TIPO_MAQUINA for f in filas)
        assert len({f.origen_id for f in filas}) == 18
        assert sum(1 for f in filas if f.enlace) == 90
        assert sum(1 for f in filas if f.precio_texto) == 82
        joven_es = next(f for f in filas if f.origen_id == "artisjet-young" and f.idioma == "es")
        assert joven_es.precio_texto == "1.300 €" and joven_es.precio_num == 1300
        assert joven_es.moneda == "EUR" and joven_es.marca == "artisJet"
        assert joven_es.enlace == "https://artisjet-printers.eu/artisjet-young/"
        assert joven_es.sitio == "artisjet-eu"
        assert _naive(joven_es.actualizado_origen) == _naive(ACTUALIZADO)
        assert json.loads(joven_es.caracteristicas_json) == ["Tinta UV LED", "Cabezal industrial"]
        joven_fr = next(f for f in filas if f.origen_id == "artisjet-young" and f.idioma == "fr")
        assert joven_fr.precio_texto == "1 300 €" and joven_fr.precio_num == 1300
        assert joven_fr.enlace == "https://artisjet-printers.eu/fr/artisjet-young/"
        # Un idioma sin precio: fila con enlace y sin precio (no se inventa).
        mbo_de = next(f for f in filas if f.origen_id == "mbo-3050" and f.idioma == "de")
        assert mbo_de.precio_texto is None and mbo_de.enlace
        marcas = {m.nombre: m for m in s.scalars(select(MarcaCatalogo))}
        assert set(marcas) == {"artisJet", "MBO", "PimPam", "SmartJet FLEX"}
        assert marcas["MBO"].idioma_defecto == "es"
        assert json.loads(marcas["MBO"].urls_json)["en"] == "https://mboprinters.com/en/"
        # Idempotente: la segunda pasada no crea filas; un cambio de precio cuenta.
        data = _compositor_data()
        data["products"][0]["price"] = "1.350 €"
        segunda = catalogo_compositor.sincronizar(s, http=_supabase_falso(data), ahora=AHORA)
        s.commit()
        assert (segunda.productos, segunda.nuevos, segunda.actualizados, segunda.idos) == (
            90, 0, 1, 0)
        # Una máquina desaparece del compositor: se marca ida, no se borra.
        data["products"] = data["products"][1:]
        tercera = catalogo_compositor.sincronizar(s, http=_supabase_falso(data), ahora=AHORA)
        s.commit()
        assert tercera.idos == 5
        assert s.scalar(select(ProductoCatalogo).where(
            ProductoCatalogo.origen_id == "artisjet-young",
            ProductoCatalogo.idioma == "es")).ido_en is not None
        estado = catalogo.estado(s)
        assert estado["fuentes"]["compositor"]["productos"] == 85
        assert estado["idos"] == 5 and estado["fuentes"]["compositor"]["clave_configurada"]


def test_supabase_caido_o_sin_clave_deja_el_catalogo_anterior(
    session_factory: sessionmaker, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with session_factory() as s:
        _sincronizar_compositor(s)
        antes = {(f.origen_id, f.idioma): (f.precio_texto, f.ido_en)
                 for f in s.scalars(select(ProductoCatalogo))}
        caida = catalogo_compositor.sincronizar(s, http=_supabase_falso(None, caido=True),
                                                ahora=AHORA)
        s.commit()
        assert caida.estado == SYNC_ERROR and "Supabase no responde" in caida.detalle
        assert caida.productos == 0 and caida.idos == 0
        despues = {(f.origen_id, f.idioma): (f.precio_texto, f.ido_en)
                   for f in s.scalars(select(ProductoCatalogo))}
        assert despues == antes
        # Una clave mala tampoco toca nada.
        mala = catalogo_compositor.sincronizar(s, http=_supabase_falso(_compositor_data()),
                                               ahora=AHORA, clave="otra")
        s.commit()
        assert mala.estado == SYNC_ERROR and "HTTP 401" in mala.detalle
        # Una fila sin lista de productos (data a medias) es un error, no «el
        # compositor se ha quedado sin máquinas»: nada se marca ido.
        for data in (None, {}, {"products": None}, {"products": [], "brands": []}):
            rota = catalogo_compositor.sincronizar(s, http=_supabase_falso(data), ahora=AHORA)
            s.commit()
            assert rota.estado == SYNC_ERROR and "products" in (rota.detalle or "")
        assert {(f.origen_id, f.idioma): (f.precio_texto, f.ido_en)
                for f in s.scalars(select(ProductoCatalogo))} == antes
        # Sin clave en el entorno: error con el motivo (y la clave nunca va en el código).
        monkeypatch.delenv("SUPABASE_COMPOSER_KEY")
        get_settings.cache_clear()
        sin_clave = catalogo_compositor.sincronizar(s, ahora=AHORA)
        s.commit()
        assert sin_clave.estado == SYNC_ERROR and "SUPABASE_COMPOSER_KEY" in sin_clave.detalle
        assert catalogo.estado(s)["fuentes"]["compositor"]["clave_configurada"] is False
        # Y el asistente sigue trabajando con el catálogo anterior.
        contacto = _contacto(s, idioma="de", intereses=["uv_mediano"])
        hilo = _hilo(s, contacto, [{"de": KLAUS, "texto": "Welche UV-Drucker haben Sie?"}])
        s.commit()
        falso = _ClaudeFalso({"asunto": "Re: UV", "cuerpo": "Hallo Klaus,\n\nhier die Maschinen.",
                              "idioma": "de", "aviso": "", "pendiente": [], "enlaces_usados": []})
        with patch.object(llm, "_invoke_claude", falso):
            borrador = redactor.generar(s, thread=hilo, usuario=_admin(s), ahora=AHORA)
        s.commit()
        assert borrador.texto.startswith("Hallo Klaus")
        assert "artisJet 5000U" in falso.ultimo["user"] and "MBO 4060" in falso.ultimo["user"]


# --- lo que ve el asistente --------------------------------------------------------------------


def test_lead_aleman_uv_mediano_recibe_maquinas_con_precio_tal_cual_y_enlaces_de(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        _sincronizar_compositor(s)
        contacto = _contacto(s, idioma="de", intereses=["uv_mediano"])
        hilo = _hilo(s, contacto, [{"de": KLAUS, "texto": (
            "Guten Tag, ich suche einen UV-Drucker im mittleren Format (A2). Welche Maschinen "
            "empfehlen Sie und was kosten sie?")}])
        s.commit()
        ctx = contexto.construir(s, thread=hilo, usuario=_admin(s), ahora=AHORA)
        maquinas = ctx.catalogo["maquinas"]
        assert [m["nombre"] for m in maquinas] == [
            "artisJet 3000Pro", "artisJet 5000U", "MBO 3050", "MBO 4060"]
        pro = maquinas[0]
        assert pro["precio_texto"] == "3.300 €" and pro["moneda"] == "EUR"
        assert pro["precio_idioma"] == "de" and pro["idioma_respaldo"] is False
        assert pro["enlace"] == "https://artisjet-printers.eu/de/artisjet-3000pro/"
        assert pro["idioma_enlace"] == "de" and pro["orientativo"] is False
        assert pro["interes"] == "uv_mediano" and pro["iva"] is None
        # 5000U no tiene precio en alemán: el del idioma por defecto de la marca
        # (castellano), dicho, y la moneda, sin convertir.
        u5000 = maquinas[1]
        assert u5000["precio_texto"] == "4.300 €" and u5000["precio_idioma"] == "es"
        assert u5000["idioma_respaldo"] is True and u5000["moneda"] == "EUR"
        assert u5000["enlace"] == "https://artisjet-printers.eu/de/artisjet-5000u/"
        # Los enlaces de las máquinas están en la lista de los que el modelo puede usar.
        urls = {e["url"]: e for e in ctx.enlaces_lista()}
        assert "https://artisjet-printers.eu/de/artisjet-3000pro/" in urls
        assert urls["https://mboprinters.com/de/mbo-4060/"]["origen"] == "catalogo_compositor"
        assert urls["https://mboprinters.com/de/mbo-4060/"]["etiqueta"] == "MBO 4060 (MBO), en de"
        # El bloque que ve el modelo: precio tal cual, enlace, avisos en alemán.
        bloque = redactor.bloque_contexto(ctx)
        assert "CATÁLOGO (los ÚNICOS productos y precios que puedes citar" in bloque
        assert "PRECIO: 3.300 € (moneda EUR)" in bloque
        assert ("PRECIO: 4.300 € (moneda EUR) — es el precio publicado en «es», no hay precio en "
                "«de»") in bloque
        assert "Preise zzgl. MwSt." not in bloque                   # el IVA no se deduce
        assert ("IVA sin declarar («Ob die Preise die MwSt. enthalten, ist noch zu "
                "bestätigen.»)") in bloque
        assert "Für EU-Unternehmen mit einer in VIES validierten USt-IdNr." in bloque
        assert "keine Rechnung und kein verbindliches Angebot" in bloque
        assert "ORIENTATIVO" not in bloque
        resumen = ctx.resumen()
        assert [m["nombre"] for m in resumen["catalogo"]["maquinas"]][:2] == [
            "artisJet 3000Pro", "artisJet 5000U"]
        # Con el IVA declarado por fuente, el bloque lo dice en el idioma del correo.
        from app.services.conocimiento import config as cfg_kb  # noqa: PLC0415

        with patch.object(cfg_kb, "configuracion", return_value={
            **config_defectos(), "iva_por_fuente": {"compositor": "sin", "woocommerce": "con"},
        }):
            ctx2 = contexto.construir(s, thread=hilo, usuario=_admin(s), ahora=AHORA)
        assert "precios sin IVA («Preise zzgl. MwSt.»)" in redactor.bloque_contexto(ctx2)
        assert ctx2.catalogo["maquinas"][0]["iva"] == "sin"


def test_un_precio_viejo_es_orientativo_y_el_borrador_lo_dice(session_factory: sessionmaker,
                                                               ) -> None:
    with session_factory() as s:
        # La fila del compositor lleva 7 meses sin tocarse.
        hace_7_meses = AHORA - timedelta(days=215)
        _sincronizar_compositor(s, updated_at=hace_7_meses.isoformat())
        contacto = _contacto(s, idioma="es", intereses=["uv_pequeno"])
        hilo = _hilo(s, contacto, [{"de": KLAUS, "texto": "¿Qué impresora UV pequeña me "
                                                           "recomendáis y cuánto cuesta?"}])
        s.commit()
        falso = _ClaudeFalso({
            "asunto": "Re: UV", "idioma": "es", "aviso": "", "pendiente": [],
            "cuerpo": ("Hola Klaus,\n\nPara objetos pequeños te encaja la artisJet Young: 1.300 € "
                       "(precio orientativo, lo confirmamos al preparar la oferta). Precios sin "
                       "IVA.\n\nhttps://artisjet-printers.eu/artisjet-young/\n\nUn saludo,\nAdmin"),
            "enlaces_usados": ["https://artisjet-printers.eu/artisjet-young/"],
        })
        with patch.object(llm, "_invoke_claude", falso):
            borrador = redactor.generar(s, thread=hilo, usuario=_admin(s), ahora=AHORA)
        s.commit()
        prompt = falso.ultimo["user"]
        assert "artisJet Young" in prompt and "PRECIO: 1.300 €" in prompt
        assert "ORIENTATIVO: añade «Precio orientativo, lo confirmamos al preparar la oferta.»" in (
            prompt)
        assert "fuente confirmada el " + hace_7_meses.date().isoformat() in prompt
        assert "precio orientativo, lo confirmamos al preparar la oferta" in borrador.texto
        # El enlace de la máquina está en la lista y se convierte en <a> en el editor.
        from app.services.asistente.texto import a_html  # noqa: PLC0415

        html = a_html(borrador.texto, [e["url"] for e in json.loads(borrador.enlaces_json)])
        assert '<a href="https://artisjet-printers.eu/artisjet-young/">' in html
        ctx = json.loads(borrador.contexto_json)
        assert ctx["catalogo"]["maquinas"][0]["orientativo"] is True
        # Con el umbral en 12 meses, el mismo precio ya no es orientativo.
        from app.services.conocimiento import config as cfg_kb  # noqa: PLC0415

        with patch.object(cfg_kb, "configuracion", return_value={
            **config_defectos(), "precio_meses_orientativo": 12,
        }):
            bloque = catalogo.para_asistente(
                s, intereses=["uv_pequeno"], idioma="es", maquina=None, modelo_flux=None,
                ahora=AHORA)
        assert bloque["maquinas"][0]["orientativo"] is False


def test_corte_laser_sin_maquina_enlaza_la_pagina_del_interes_y_no_inventa(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        _sincronizar_compositor(s)
        contacto = _contacto(s, idioma="en", intereses=["corte_laser"])
        hilo = _hilo(s, contacto, [{"de": KLAUS, "texto": "I need a laser cutter for plywood. "
                                                           "Which machine and what price?"}],
                     delivered_to="sales@mqeurope.com")
        s.commit()
        ctx = contexto.construir(s, thread=hilo, usuario=_admin(s), ahora=AHORA)
        assert ctx.catalogo["maquinas"] == [] and ctx.catalogo["sin_maquina"] == ["corte_laser"]
        # Si el catálogo no respondiera, el resumen lo dice con None (no «sin productos»).
        with patch.object(catalogo, "para_asistente", side_effect=RuntimeError("tabla caída")):
            sin = contexto.construir(s, thread=hilo, usuario=_admin(s), ahora=AHORA)
        assert sin.catalogo is None and sin.resumen()["catalogo"] is None
        assert "CATÁLOGO: no hay ningún producto" in redactor.bloque_contexto(sin)
        bloque = redactor.bloque_contexto(ctx)
        assert "CATÁLOGO: no hay ningún producto ni precio verificado para este caso" in bloque
        assert "Sin máquina en el catálogo para: corte_laser" in bloque
        urls = [e["url"] for e in ctx.enlaces_lista()]
        assert "https://www.mqeurope.com/co2-laser-cutting/" in urls      # la página del interés
        assert not any("catalogo" in (e.get("origen") or "") for e in ctx.enlaces_lista())
        # Y el mapa resuelto enseña el hueco con su motivo (y el hallazgo de las láser).
        resuelto = catalogo.mapa_resuelto(s)
        huecos = {h["interes"]: h["motivo"] for h in resuelto["huecos"]}
        assert set(huecos) == {"corte_laser", "grabado_laser", "cnc", "packaging"}
        assert huecos["corte_laser"] == (
            "sin máquina en el compositor: se enlaza la página de producto")
        assert "láser" in resuelto["hallazgo"] and resuelto["revisado"] is False
        assert resuelto["de_la_base"] is False                 # la propuesta, sin filas en la tabla
        dtf = next(f for f in resuelto["filas"] if f["interes"] == "dtf")
        assert sorted(c["nombre"] for c in dtf["coincide"]) == [
            "SmartJet FLEX 30", "SmartJet FLEX 60", "SmartJet FLEX Max", "SmartJet FLEX Pro"]
        assert dtf["etiqueta"] == "DTF · impresión textil"


def test_un_precio_de_un_correo_antiguo_no_es_vigente(session_factory: sessionmaker) -> None:
    """El hilo dice que en 2024 se le ofreció la 5000U a 3.900 €; el catálogo
    dice 4.300 €. El modelo recibe la regla y el bloque: el precio vigente es
    el del catálogo y el viejo es histórico."""
    with session_factory() as s:
        _sincronizar_compositor(s)
        contacto = _contacto(s, idioma="es", intereses=["uv_mediano"])
        viejo = datetime(2024, 3, 5, 10, 0, tzinfo=UTC)
        hilo = _hilo(s, contacto, [
            {"de": ALIAS, "lado": "nosotros", "fecha": viejo,
             "texto": "Hola Klaus, la artisJet 5000U te saldría por 3.900 € + IVA."},
            {"de": KLAUS, "fecha": AHORA - timedelta(days=1),
             "texto": "Hola, retomo lo de la 5000U. ¿Sigue valiendo lo mismo?"},
        ])
        s.commit()
        falso = _ClaudeFalso({
            "asunto": "Re: UV", "idioma": "es", "aviso": "", "pendiente": [],
            "cuerpo": ("Hola Klaus,\n\nEl precio actual de la artisJet 5000U es 4.300 € (el de "
                       "3.900 € era de marzo de 2024).\n\nUn saludo,\nAdmin"),
            "enlaces_usados": [],
        })
        with patch.object(llm, "_invoke_claude", falso):
            borrador = redactor.generar(s, thread=hilo, usuario=_admin(s), ahora=AHORA)
        s.commit()
        sistema, prompt = falso.ultimo["system"], falso.ultimo["user"]
        assert "Los precios de productos salen SOLO del bloque CATÁLOGO" in sistema
        assert "Un precio o plazo que aparezca en un pedido o correo antiguo es histórico" in (
            sistema)
        assert "PRECIO: 4.300 €" in prompt
        catalogo_txt = prompt.split("CATÁLOGO (")[1].split("ENLACES (")[0]
        assert "3.900" not in catalogo_txt                   # el viejo no está en el catálogo
        assert "3.900 € + IVA" in prompt                     # está en el hilo, como historia
        assert "4.300 €" in borrador.texto


# --- las tiendas --------------------------------------------------------------------------------


def test_indexa_los_386_productos_de_fluxlasers_y_una_tienda_caida_no_para_las_demas(
    session_factory: sessionmaker,
) -> None:
    peticiones: list[str] = []
    with session_factory() as s:
        pasada = catalogo_tiendas.sincronizar(
            s, http=_tienda_falsa(productos_por_sitio={"fluxlasers.es": 386, "mboprinters.com": 12},
                                  caidos=frozenset({"mboprinters.com"}),
                                  idiomas_sin_variante=frozenset({"nl"}), peticiones=peticiones),
            ahora=AHORA)
        s.commit()
        assert pasada.fuente == FUENTE_WOOCOMMERCE and pasada.estado == SYNC_PARCIAL
        resumen = json.loads(pasada.resumen_json)
        # fluxlasers: castellano, inglés, alemán y francés (386 cada uno); sin variante neerlandesa.
        assert resumen["fluxlasers:es"]["productos"] == 386
        assert resumen["fluxlasers:de"]["productos"] == 386
        assert resumen["fluxlasers:nl"]["sin_tienda"] is True
        # mboprinters caída: error anotado, los demás siguen.
        assert "la tienda no responde" in resumen["mboprinters:es"]["error"]
        assert "mboprinters:es" in pasada.detalle
        # Las webs sin tienda no son un error.
        assert resumen["boprint:es"]["sin_tienda"] is True and resumen["pimpam:fr"]["sin_tienda"]
        # Paginó de 100 en 100: cuatro páginas por idioma de fluxlasers.
        assert sum("fluxlasers.es/wp-json" in p and "page=4" in p for p in peticiones) == 1
        assert sum("fluxlasers.es/de/wp-json" in p for p in peticiones) == 4
        filas = list(s.scalars(select(ProductoCatalogo).where(
            ProductoCatalogo.fuente == FUENTE_WOOCOMMERCE)))
        assert len(filas) == 386 * 4
        es = [f for f in filas if f.idioma == "es"]
        assert len(es) == 386 and all(f.precio_texto and f.enlace for f in es)
        assert len({f.categoria for f in es}) == 19
        primero = next(f for f in es if f.origen_id == "fluxlasers:1000")
        assert primero.precio_texto == "12,50 €" and primero.precio_num == 12.5
        assert primero.moneda == "EUR" and primero.sitio == "fluxlasers"
        assert primero.enlace == "https://fluxlasers.es/producto/producto-0/"
        assert primero.tipo == TIPO_MAQUINA                    # categoría «Máquinas láser»
        assert primero.marca == "Fluxlasers" and primero.descripcion == "Descripción corta 0"
        grande = next(f for f in es if f.origen_id == "fluxlasers:1385")
        assert grande.precio_texto == "4.825,00 €" and grande.precio_num == 4825.0
        tipos = {f.tipo for f in es}
        assert tipos == {TIPO_MAQUINA, TIPO_CONSUMIBLE, TIPO_REPUESTO}
        tubo = next(f for f in es if f.categoria == "Repuestos")
        assert tubo.tipo == TIPO_REPUESTO
        lente = next(f for f in es if f.categoria == "Lentes")
        assert lente.tipo == TIPO_CONSUMIBLE
        estado = catalogo.estado(s)
        assert estado["fuentes"]["woocommerce"]["por_tienda"] == {"fluxlasers": 386 * 4}
        assert estado["fuentes"]["woocommerce"]["ultima_sincronizacion"]["estado"] == "parcial"
        # Segunda pasada igual: nada nuevo; un producto que desaparece se marca ido.
        segunda = catalogo_tiendas.sincronizar(
            s, http=_tienda_falsa(productos_por_sitio={"fluxlasers.es": 385},
                                  idiomas_sin_variante=frozenset({"nl"})), ahora=AHORA)
        s.commit()
        assert segunda.estado == SYNC_OK and segunda.nuevos == 0 and segunda.idos == 4
        # Una tienda que responde 200 con cero productos no marca ido lo indexado
        # (se anota «sin productos»); una variante de idioma que desaparece
        # (404) sí: ya no está en ninguna tienda.
        tercera = catalogo_tiendas.sincronizar(
            s, http=_tienda_falsa(productos_por_sitio={"fluxlasers.es": 0},
                                  idiomas_sin_variante=frozenset({"nl", "fr"})), ahora=AHORA)
        s.commit()
        resumen3 = json.loads(tercera.resumen_json)
        assert resumen3["fluxlasers:es"]["sin_productos"] is True
        assert resumen3["fluxlasers:es"]["idos"] == 0
        assert resumen3["fluxlasers:fr"]["sin_tienda"] is True
        assert resumen3["fluxlasers:fr"]["idos"] == 385
        vigentes = {f.idioma for f in s.scalars(select(ProductoCatalogo).where(
            ProductoCatalogo.fuente == FUENTE_WOOCOMMERCE, ProductoCatalogo.ido_en.is_(None)))}
        assert vigentes == {"es", "en", "de"}
        # Una web desactivada (o quitada de la tabla) deja de ser vigente en la
        # siguiente pasada completa.
        fijar_para_tests(Registro(sitios=tuple(
            s_ for s_ in SITIOS_DE_PARTIDA if s_.clave != "fluxlasers")))
        with patch("app.services.conocimiento.catalogo_tiendas.registro_sitios.cargar",
                   lambda _s: Registro(sitios=tuple(
                       s_ for s_ in SITIOS_DE_PARTIDA if s_.clave != "fluxlasers"))):
            cuarta = catalogo_tiendas.sincronizar(
                s, http=_tienda_falsa(productos_por_sitio={}), ahora=AHORA)
        s.commit()
        assert json.loads(cuarta.resumen_json)["webs_inactivas"]["idos"] == 385 * 3
        assert s.scalar(select(ProductoCatalogo).where(
            ProductoCatalogo.fuente == FUENTE_WOOCOMMERCE,
            ProductoCatalogo.ido_en.is_(None))) is None
    # El formato del precio sigue a la tienda: separadores, prefijo y sufijo.
    precios = {"currency_minor_unit": 2, "currency_decimal_separator": ".",
               "currency_thousand_separator": ",", "currency_prefix": "$", "currency_suffix": ""}
    assert catalogo_tiendas.formatear_importe("129900", precios) == ("$1,299.00", 1299.0)
    rango = {"prices": {**precios, "price": "1000", "currency_code": "USD",
                        "price_range": {"min_amount": "1000", "max_amount": "250000"}}}
    assert catalogo_tiendas.precio_de(rango) == ("$10.00 – $2,500.00", 10.0, "USD")
    assert catalogo_tiendas.clasificar("Lente para láser 50 mm", ["Lentes"]) == TIPO_CONSUMIBLE
    assert catalogo_tiendas.clasificar("Beambox II", ["Máquinas láser"]) == TIPO_MAQUINA
    assert catalogo_tiendas.clasificar("Ersatzteil Spiegel", ["Zubehör"]) == TIPO_REPUESTO


def test_los_consumibles_de_su_maquina_entran_por_sus_pedidos(session_factory: sessionmaker,
                                                               ) -> None:
    with session_factory() as s:
        catalogo_tiendas.sincronizar(
            s, http=_tienda_falsa(productos_por_sitio={"fluxlasers.es": 40},
                                  idiomas_sin_variante=frozenset({"nl", "fr"})), ahora=AHORA)
        s.commit()
        contacto = _contacto(s, idioma="de", intereses=["soporte_postventa"])
        _pedido(s, contacto, "FLUX Beambox II 40W")
        hilo = _hilo(s, contacto, [{"de": KLAUS, "texto": "Ich brauche eine neue Linse für meine "
                                                           "Beambox II."}],
                     delivered_to="info@fluxlasers.es")
        s.commit()
        ctx = contexto.construir(s, thread=hilo, usuario=_admin(s), ahora=AHORA)
        consumibles = ctx.catalogo["consumibles"]
        assert 0 < len(consumibles) <= catalogo.CONSUMIBLES_MAX
        assert all("Beambox II" in c["nombre"] for c in consumibles)
        assert all(c["tipo"] != TIPO_MAQUINA for c in consumibles)
        # En alemán, porque la tienda lo publica; precio tal cual, moneda, enlace alemán.
        assert all(c["precio_idioma"] == "de" and not c["idioma_respaldo"] for c in consumibles)
        assert consumibles[0]["enlace"].startswith("https://fluxlasers.es/de/producto/")
        assert consumibles[0]["precio_texto"].endswith(" €") and consumibles[0]["moneda"] == "EUR"
        bloque = redactor.bloque_contexto(ctx)
        assert "Consumibles y repuestos de la tienda para su máquina (FLUX Beambox II 40W)" in (
            bloque)
        # Sin máquina en sus pedidos, nada de la tienda.
        otro = _contacto(s, idioma="es", email="otro@ejemplo.com")
        hilo2 = _hilo(s, otro, [{"de": "otro@ejemplo.com", "texto": "Hola"}], asunto="Otro")
        s.commit()
        ctx2 = contexto.construir(s, thread=hilo2, usuario=_admin(s), ahora=AHORA)
        assert ctx2.catalogo["consumibles"] == []


def test_el_presupuesto_de_tokens_se_respeta_con_el_catalogo_cargado(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        _sincronizar_compositor(s)
        catalogo_tiendas.sincronizar(
            s, http=_tienda_falsa(productos_por_sitio={"fluxlasers.es": 40},
                                  idiomas_sin_variante=frozenset({"nl", "fr", "de", "en"})),
            ahora=AHORA)
        s.commit()
        contacto = _contacto(s, idioma="es", intereses=["uv_grande", "dtf"])
        _pedido(s, contacto, "FLUX Beambox II")
        hilo = _hilo(s, contacto, [{"de": KLAUS, "texto": "Quiero una UV grande y también DTF, y "
                                                           "lentes para la Beambox."}])
        s.commit()
        ctx = contexto.construir(s, thread=hilo, usuario=_admin(s), ahora=AHORA)
        # Hasta seis máquinas (cinco de uv_grande y una de dtf) y hasta ocho consumibles.
        assert len(ctx.catalogo["maquinas"]) == catalogo.MAQUINAS_MAX == 6
        assert [m["interes"] for m in ctx.catalogo["maquinas"]].count("uv_grande") == 5
        assert len(ctx.catalogo["consumibles"]) <= catalogo.CONSUMIBLES_MAX
        completo = estimar_tokens(redactor.bloque_contexto(ctx))
        # Un presupuesto más pequeño que el bloque recorta el catálogo (consumibles
        # primero, luego máquinas hasta dejar dos) y lo anota.
        from app.services.asistente.historico import ajustar_al_presupuesto  # noqa: PLC0415

        presupuesto = completo - 250
        tokens = ajustar_al_presupuesto(
            ctx, presupuesto=presupuesto,
            medir=lambda c: estimar_tokens(redactor.bloque_contexto(c)))
        assert tokens <= presupuesto
        assert ctx.catalogo["consumibles"] == [] or len(ctx.catalogo["maquinas"]) >= 2
        assert any(r.startswith("catálogo: consumible") for r in ctx.recortes)
        assert "se dejaron fuera" in redactor.bloque_contexto(ctx)
        # El hilo y las dos primeras máquinas no se tocan nunca.
        assert len(ctx.catalogo["maquinas"]) >= 2 and len(ctx.mensajes) == 1


# --- el job diario -----------------------------------------------------------------------------


def test_el_job_diario_corre_las_dos_fuentes_y_respeta_el_interruptor(
    session_factory: sessionmaker,
) -> None:
    llamadas: list[str] = []

    def _falsa(fuente: str) -> Any:
        def _sinc(session: Session, *, origen: str, ahora: datetime, **_: Any
                  ) -> CatalogoSincronizacion:
            llamadas.append(fuente)
            fila = CatalogoSincronizacion(fuente=fuente, origen=origen, inicio=ahora, fin=ahora,
                                          estado=SYNC_OK)
            session.add(fila)
            session.flush()
            return fila
        return _sinc

    ahora = datetime(2026, 10, 12, 4, 45, tzinfo=UTC)
    with session_factory() as s, \
            patch.object(catalogo_compositor, "sincronizar", _falsa("compositor")), \
            patch.object(catalogo_tiendas, "sincronizar", _falsa("woocommerce")):
        assert catalogo_job.run_daily(s, ahora=ahora) is True
        assert llamadas == ["compositor", "woocommerce"]
        assert catalogo_job.run_daily(s, ahora=ahora + timedelta(hours=1)) is False   # ya corrió
        assert catalogo_job.run_daily(s, ahora=ahora + timedelta(hours=13)) is True
        with patch.object(catalogo_job, "configuracion",
                          return_value={**config_defectos(), "catalogo_sync_activa": False}):
            assert catalogo_job.run_daily(s, ahora=ahora + timedelta(days=2)) is False
            assert catalogo_job.run_daily(s, ahora=ahora + timedelta(days=2), force=True) is True
    assert len(llamadas) == 6
    assert catalogo_job.CATALOGO_QUEUE == "kb:catalogo_sync"


# --- la pantalla (API) y la configuración ---------------------------------------------------------


def test_api_catalogo_sincronizar_listar_y_la_tabla_de_interes_a_maquinas(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    headers = auth_headers(http, "admin")
    todo = http.get("/api/erp/conocimiento", headers=headers).json()
    assert todo["catalogo"]["fuentes"]["compositor"]["productos"] == 0
    assert todo["catalogo"]["fuentes"]["compositor"]["clave_configurada"] is True
    assert todo["interes_productos"]["catalogo_vacio"] is True
    assert len(todo["interes_productos"]["filas"]) == 15
    # Con la tabla vacía la pantalla ve la propuesta (ids `partida-N`), y un
    # cambio sobre una de esas filas siembra la tabla y se aplica a esa fila.
    assert todo["interes_productos"]["de_la_base"] is False
    partida = todo["interes_productos"]["filas"][2]
    assert partida["id"] == "partida-2" and partida["patron"] == "1015"
    cambio = http.patch("/api/erp/conocimiento/catalogo/intereses/partida-2", headers=headers,
                        json={"activo": False})
    assert cambio.status_code == 200, cambio.text
    sembradas = cambio.json()["interes_productos"]
    assert sembradas["de_la_base"] is True and len(sembradas["filas"]) == 15
    assert next(f for f in sembradas["filas"] if f["patron"] == "1015")["activo"] is False
    http.patch(f"/api/erp/conocimiento/catalogo/intereses/"
               f"{next(f for f in sembradas['filas'] if f['patron'] == '1015')['id']}",
               headers=headers, json={"activo": True})
    with patch.object(catalogo_compositor, "_cliente",
                      lambda: _supabase_falso(_compositor_data())), \
            patch.object(catalogo_tiendas, "_cliente", lambda: _tienda_falsa(
                productos_por_sitio={"fluxlasers.es": 5},
                idiomas_sin_variante=frozenset({"en", "de", "fr", "nl"}))):
        r = http.post("/api/erp/conocimiento/catalogo/sincronizar", headers=headers, json={})
    assert r.status_code == 200, r.text
    pasadas = {p["fuente"]: p for p in r.json()["sincronizaciones"]}
    assert pasadas["compositor"]["estado"] == "ok" and pasadas["compositor"]["productos"] == 90
    assert pasadas["compositor"]["origen"] == "manual"
    assert pasadas["woocommerce"]["productos"] == 5 and pasadas["woocommerce"]["estado"] == "ok"
    assert r.json()["catalogo"]["fuentes"]["compositor"]["por_idioma"]["de"] == 18
    assert r.json()["catalogo"]["marcas"][0]["nombre"] == "MBO"
    # La fila dtf casa con las cuatro SmartJet FLEX; corte_laser es un hueco.
    resuelto = r.json()["interes_productos"]
    dtf = next(f for f in resuelto["filas"] if f["interes"] == "dtf")
    assert len(dtf["coincide"]) == 4 and resuelto["catalogo_vacio"] is False
    assert {h["interes"] for h in resuelto["huecos"]} == {"corte_laser", "grabado_laser", "cnc",
                                                          "packaging"}
    # La tabla, filtrada.
    lista = http.get("/api/erp/conocimiento/catalogo/productos",
                     headers=headers, params={"fuente": "compositor", "idioma": "fr",
                                              "marca": "mbo"}).json()
    assert lista["total"] == 7 and all(p["idioma"] == "fr" for p in lista["productos"])
    assert lista["productos"][0]["precio_texto"].endswith(" €")
    busca = http.get("/api/erp/conocimiento/catalogo/productos", headers=headers,
                     params={"q": "young"}).json()
    assert busca["total"] == 5 and busca["productos"][0]["nombre"] == "artisJet Young"
    tienda = http.get("/api/erp/conocimiento/catalogo/productos", headers=headers,
                      params={"fuente": "woocommerce"}).json()
    assert tienda["total"] == 5 and tienda["productos"][0]["sitio"] == "fluxlasers"
    # Una fila nueva: MBO 6090 también para uv_mediano (sin desplegar).
    alta = http.post("/api/erp/conocimiento/catalogo/intereses", headers=headers, json={
        "interes": "uv_mediano", "marca": "MBO", "patron": "6090", "nota": "prueba"})
    assert alta.status_code == 201, alta.text
    fila = next(f for f in alta.json()["interes_productos"]["filas"]
                if f["interes"] == "uv_mediano" and f["patron"] == "6090")
    assert [c["nombre"] for c in fila["coincide"]] == ["MBO 6090"]
    assert alta.json()["interes_productos"]["de_la_base"] is True
    with session_factory() as s:
        nombres = [m["nombre"] for m in catalogo.maquinas_para(
            s, ["uv_mediano"], idioma="es", ahora=AHORA, config=config_defectos())]
        assert nombres == ["artisJet 3000Pro", "artisJet 5000U", "MBO 3050", "MBO 4060", "MBO 6090"]
    # Repetida → 400 (al crear y al editar); sin marca ni patrón → 400;
    # desactivar → deja de casar; borrar.
    assert http.post("/api/erp/conocimiento/catalogo/intereses", headers=headers, json={
        "interes": "uv_mediano", "marca": "MBO", "patron": "6090"}).status_code == 400
    assert http.post("/api/erp/conocimiento/catalogo/intereses", headers=headers, json={
        "interes": "uv_mediano", "marca": "", "patron": ""}).status_code == 400
    otra = next(f for f in alta.json()["interes_productos"]["filas"]
                if f["interes"] == "uv_mediano" and f["patron"] == "4060")
    choque = http.patch(f"/api/erp/conocimiento/catalogo/intereses/{otra['id']}", headers=headers,
                        json={"patron": "6090"})
    assert choque.status_code == 400 and "otra fila igual" in choque.json()["detail"]
    baja = http.patch(f"/api/erp/conocimiento/catalogo/intereses/{fila['id']}", headers=headers,
                      json={"activo": False})
    assert baja.status_code == 200
    with session_factory() as s:
        assert "MBO 6090" not in [m["nombre"] for m in catalogo.maquinas_para(
            s, ["uv_mediano"], idioma="es", ahora=AHORA, config=config_defectos())]
    assert http.delete(f"/api/erp/conocimiento/catalogo/intereses/{fila['id']}",
                       headers=headers).status_code == 200
    assert http.delete("/api/erp/conocimiento/catalogo/intereses/nadie",
                       headers=headers).status_code == 404
    # El idioma por defecto de una marca (el precio de respaldo).
    marca = http.patch("/api/erp/conocimiento/catalogo/marcas/MBO", headers=headers,
                       json={"idioma_defecto": "en"})
    assert marca.status_code == 200
    assert next(m for m in marca.json()["catalogo"]["marcas"]
                if m["nombre"] == "MBO")["idioma_defecto"] == "en"
    with session_factory() as s:
        mbo_3050 = next(m for m in catalogo.maquinas_para(
            s, ["uv_mediano"], idioma="nl", ahora=AHORA, config=config_defectos())
            if m["nombre"] == "MBO 3050")
        assert mbo_3050["precio_idioma"] == "en" and mbo_3050["idioma_respaldo"] is True
    assert http.patch("/api/erp/conocimiento/catalogo/marcas/Nadie", headers=headers,
                      json={"idioma_defecto": "en"}).status_code == 404
    # «Probar» devuelve también el catálogo que saldría (y el idioma de los
    # avisos normalizado: «de-DE» son los textos alemanes).
    probar = http.post("/api/erp/conocimiento/resolver", headers=headers, json={
        "sitio": "mboprinters", "idioma": "de", "interes": "uv_pequeno"}).json()
    assert [m["nombre"] for m in probar["catalogo"]["maquinas"]] == [
        "artisJet Young", "artisJet Proud", "MBO 1015"]
    assert probar["catalogo"]["avisos"]["sin"] == "Preise zzgl. MwSt."
    with session_factory() as s:
        bloque = catalogo.para_asistente(s, intereses=[], idioma="de-DE", maquina=None,
                                         modelo_flux=None, ahora=AHORA)
        assert bloque["idioma_avisos"] == "de" and bloque["avisos"]["sin"] == "Preise zzgl. MwSt."
    # Fuente desconocida → 400; solo quien configura el ERP.
    assert http.post("/api/erp/conocimiento/catalogo/sincronizar", headers=headers,
                     json={"fuente": "otra"}).status_code == 400
    assert http.get("/api/erp/conocimiento/catalogo/productos",
                    headers=auth_headers(http, "user")).status_code == 403


def test_configuracion_del_catalogo_en_ajustes(session_factory: sessionmaker, http: TestClient,
                                               ) -> None:
    headers = auth_headers(http, "admin")
    antes = http.get("/api/erp/settings", headers=headers).json()["conocimiento"]
    assert antes["catalogo_sync_activa"] is True and antes["catalogo_sync_hora"] == "04:45"
    assert antes["precio_meses_orientativo"] == 6
    assert antes["iva_por_fuente"] == {"compositor": None, "woocommerce": None}
    assert antes["interes_productos_revisado"] is False
    cambio = http.patch("/api/erp/settings", headers=headers, json={"conocimiento": {
        "catalogo_sync_hora": "5:10", "precio_meses_orientativo": 9,
        "iva_por_fuente": {"compositor": "sin"}, "interes_productos_revisado": True,
    }})
    assert cambio.status_code == 200, cambio.text
    guardado = cambio.json()["conocimiento"]
    assert guardado["catalogo_sync_hora"] == "05:10" and guardado["precio_meses_orientativo"] == 9
    assert guardado["iva_por_fuente"] == {"compositor": "sin", "woocommerce": None}
    assert guardado["interes_productos_revisado"] is True
    assert guardado["flux_sync_hora"] == "04:30"                     # lo demás no se tocó
    assert http.patch("/api/erp/settings", headers=headers, json={"conocimiento": {
        "precio_meses_orientativo": 0}}).status_code == 400
    assert http.patch("/api/erp/settings", headers=headers, json={"conocimiento": {
        "iva_por_fuente": {"compositor": "quizás"}}}).status_code == 400
    with session_factory() as s:
        from app.services.conocimiento.config import configuracion  # noqa: PLC0415

        cfg = configuracion(s)
        assert cfg["iva_por_fuente"]["compositor"] == "sin" and cfg["precio_meses_orientativo"] == 9
        assert catalogo.mapa_resuelto(s)["revisado"] is True


def test_ningun_camino_del_catalogo_escribe_en_el_compositor_ni_envia_correo() -> None:
    """BoHub solo LEE la fila de Supabase (GET) y las tiendas (GET); nada de
    POST/PATCH/DELETE hacia fuera, nada de envío de correo."""
    import pathlib  # noqa: PLC0415

    base = pathlib.Path(catalogo.__file__).parent
    for nombre in ("catalogo.py", "catalogo_compositor.py", "catalogo_tiendas.py",
                   "catalogo_job.py"):
        fuente = (base / nombre).read_text(encoding="utf-8")
        for prohibido in ("http.post(", "http.patch(", "http.put(", "http.delete(", "httpx.post(",
                           "requests.", "send_message", "send_email", "gmail", "smtplib"):
            assert prohibido not in fuente, f"{nombre} contiene {prohibido!r}"
    # Y la clave publicable nunca está escrita en el código: sale del entorno.
    compositor = (base / "catalogo_compositor.py").read_text(encoding="utf-8")
    assert "eyJ" not in compositor and "sb_publishable" not in compositor
    assert "supabase_composer_key" in compositor
    # El test de «ningún camino envía» del asistente sigue cubriendo el redactor.
    assert BorradorAsistente.__tablename__ == "ia_borradores"
