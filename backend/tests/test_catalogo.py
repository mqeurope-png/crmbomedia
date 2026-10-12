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

#: Las 42 entradas del compositor con la forma de los datos reales del
#: 11/10/2026 («202 filas (42 productos), 10 marcas»): seis marcas con producto
#: (flux 14, mbo_dtf 9, artisjet 7, mbo 7, smartjet 3, pimpam 2; las marcas
#: son los ids del compositor, en minúsculas) y cuatro sin producto. Dos
#: entradas de artisJet son premios (sin precio ni área, solo en castellano):
#: no son máquinas. «MBO UV2513» dice «Consultar» (sin precio público) y «MBO
#: UV1812» «desde 21.500 €».
MAQUINAS: tuple[tuple[str, str], ...] = (
    ("artisjet", "artisJet Young"), ("artisjet", "artisJet Proud"),
    ("artisjet", "artisJet 3000Pro"), ("artisjet", "artisJet 5000U"),
    ("artisjet", "artisJet 6090 Trust"),
    ("artisjet", "Premios ecovadis"), ("artisjet", "Edison Gold Award"),
    ("mbo", "MBO 1015"), ("mbo", "MBO 3050"), ("mbo", "MBO 4060"), ("mbo", "MBO 6090"),
    ("mbo", "MBO UV1612G"), ("mbo", "MBO UV1812"), ("mbo", "MBO UV2513"),
    ("mbo_dtf", "Orion 20"), ("mbo_dtf", "Lyra 35"), ("mbo_dtf", "Astra 35 Pro"),
    ("mbo_dtf", "Vega 60 Pro"), ("mbo_dtf", "Horno Orion"), ("mbo_dtf", "Horno Lyra"),
    ("mbo_dtf", "Horno Vega"), ("mbo_dtf", "Shaker Astra"), ("mbo_dtf", "Secadora Vega"),
    ("flux", "FLUX Beambox II"), ("flux", "FLUX Beambox II Pro"), ("flux", "FLUX Ador"),
    ("flux", "FLUX HEXA"), ("flux", "FLUX Beamo"), ("flux", "FLUX Beamo II"),
    ("flux", "FLUX Promark"), ("flux", "FLUX Lazervida"), ("flux", "FLUX Rotary Beambox"),
    ("flux", "FLUX Rotary HEXA"), ("flux", "FLUX Beam Air Pro"), ("flux", "FLUX Riser HEXA"),
    ("flux", "FLUX Riser Beambox"), ("flux", "FLUX Camera Kit"),
    ("smartjet", "SmartJet FLEX 30"), ("smartjet", "SmartJet FLEX 60"),
    ("smartjet", "SmartJet FLEX Pro"),
    ("pimpam", "PimPam CaseBox"), ("pimpam", "PimPam Custom"),
)
PREMIOS = ("Premios ecovadis", "Edison Gold Award")
CONSULTAR = ("MBO UV2513",)
DESDE = {"MBO UV1812": "desde 21.500 €"}
#: Lo que el cliente de talentosdiversos escribe en sus correos y cómo se llama en
#: el compositor: la Lyra 35 lleva su nombre anterior en la descripción (DM3H).
ALIAS_EN_DESCRIPCION = {"Lyra 35": "DM3H"}
MARCAS_SIN_PRODUCTO = ("bomedia", "muestras_uv", "muestras_laser", "muestras_textil")
IDIOMAS_I18N = ("fr", "de", "en", "nl")
DOMINIO_POR_MARCA = {
    "artisjet": "artisjet-printers.eu", "mbo": "mboprinters.com", "mbo_dtf": "mboprinters.com",
    "flux": "fluxlasers.es", "smartjet": "www.mqeurope.com", "pimpam": "pimpam-vending.com",
    "bomedia": "www.bomedia.net", "muestras_uv": "www.bomedia.net",
    "muestras_laser": "www.bomedia.net", "muestras_textil": "www.bomedia.net",
}
MARCAS_CON_PRODUCTO = ("artisjet", "flux", "mbo", "mbo_dtf", "pimpam", "smartjet")
FILAS_COMPOSITOR = 42 + 40 * 4           # 202: los premios solo en castellano
MAQUINAS_VIVAS = 40                      # 42 entradas menos los dos premios


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
    """El JSON del compositor: 42 entradas, 202 filas (producto × idioma)."""
    productos = []
    for i, (marca, nombre) in enumerate(MAQUINAS):
        slug = nombre.lower().replace(" ", "-")
        dominio = DOMINIO_POR_MARCA[marca]
        premio = nombre in PREMIOS
        alias = ALIAS_EN_DESCRIPCION.get(nombre)

        def _precio(idioma: str, i: int = i, nombre: str = nombre, premio: bool = premio) -> str:
            if premio:
                return ""
            if nombre in CONSULTAR:
                return "Consultar"
            if nombre in DESDE and idioma == "es":
                return DESDE[nombre]
            if (nombre, idioma) in sin_precio:
                return ""
            if idioma == "fr":
                return _precio_fr(i)
            if idioma == "en":
                return _precio_es(i).replace(".", ",")
            return _precio_es(i)

        p: dict[str, Any] = {
            "id": slug, "brand": marca, "name": nombre, "badge": "Nuevo" if i % 5 == 0 else "",
            "img": f"https://{dominio}/img/{slug}.jpg",
            "desc": f"Descripción de {nombre}" + (f" (antes {alias})" if alias else ""),
            "area": "" if premio else ("A2" if "5000" in nombre or "3050" in nombre else "A3"),
            "alt": nombre, "feat1": "" if premio else "Tinta UV LED",
            "feat2": "" if premio else "Cabezal industrial",
            "price": _precio("es"), "link": f"https://{dominio}/{slug}/", "visible": True,
            "i18n": {},
        }
        if not premio:
            for idioma in IDIOMAS_I18N:
                p["i18n"][idioma] = {
                    "desc": f"[{idioma}] {nombre}" + (f" ({alias})" if alias else ""),
                    "feat1": f"[{idioma}] feat1", "feat2": f"[{idioma}] feat2",
                    "price": _precio(idioma), "link": f"https://{dominio}/{idioma}/{slug}/",
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


def test_sincroniza_el_compositor_42_productos_202_filas_y_6_marcas_con_producto(
    session_factory: sessionmaker,
) -> None:
    peticiones: list[httpx.Request] = []
    with session_factory() as s:
        pasada = catalogo_compositor.sincronizar(
            s, http=_supabase_falso(_compositor_data(), peticiones=peticiones), ahora=AHORA)
        s.commit()
        assert pasada.estado == SYNC_OK and pasada.fuente == FUENTE_COMPOSITOR
        assert pasada.productos == FILAS_COMPOSITOR == 202
        assert pasada.nuevos == FILAS_COMPOSITOR and pasada.idos == 0
        assert _naive(pasada.origen_actualizado_en) == _naive(ACTUALIZADO)   # updated_at de la fila
        resumen = json.loads(pasada.resumen_json)["compositor"]
        assert resumen["por_idioma"] == {"es": 42, "fr": 40, "de": 40, "en": 40, "nl": 40}
        # Sin precio: los dos premios (1 fila), «Consultar» (5 filas) y 7 huecos de idioma.
        assert resumen["con_precio"] == 202 - 2 - 5 - 7
        # 42 entradas, 40 máquinas (los premios se descartan) y SEIS marcas con
        # producto: las cuatro sin producto no cuentan como marcas de catálogo.
        assert resumen["filas"] == 202 and resumen["productos"] == MAQUINAS_VIVAS == 40
        assert resumen["marcas"] == 6
        assert resumen["marcas_sin_producto"] == list(MARCAS_SIN_PRODUCTO)
        assert resumen["descartados"] == sorted(PREMIOS)
        assert resumen["sin_precio_publico"] == ["MBO UV2513"]
        assert resumen["saltadas"] == 0 and resumen["errores"] == []
        # La petición: la fila main, con la clave en la cabecera apikey (de .env).
        assert str(peticiones[0].url) == (
            f"{SUPABASE}/rest/v1/composer_data?select=data,updated_at&id=eq.main")
        assert peticiones[0].headers["apikey"] == CLAVE
        filas = list(s.scalars(select(ProductoCatalogo)))
        assert len(filas) == 202 and all(f.tipo == TIPO_MAQUINA for f in filas)
        assert len({f.origen_id for f in filas}) == 42
        assert sum(1 for f in filas if f.enlace) == 202
        assert sum(1 for f in filas if f.precio_texto) == 202 - 2 - 5 - 7
        joven_es = next(f for f in filas if f.origen_id == "artisjet-young" and f.idioma == "es")
        assert joven_es.precio_texto == "1.300 €" and joven_es.precio_num == 1300
        assert joven_es.moneda == "EUR" and joven_es.marca == "artisjet"
        assert joven_es.enlace == "https://artisjet-printers.eu/artisjet-young/"
        assert joven_es.sitio == "artisjet-eu"
        assert _naive(joven_es.actualizado_origen) == _naive(ACTUALIZADO)
        assert json.loads(joven_es.caracteristicas_json) == ["Tinta UV LED", "Cabezal industrial"]
        assert joven_es.precio_publico is True and joven_es.descartado is False
        joven_fr = next(f for f in filas if f.origen_id == "artisjet-young" and f.idioma == "fr")
        assert joven_fr.precio_texto == "1 300 €" and joven_fr.precio_num == 1300
        assert joven_fr.enlace == "https://artisjet-printers.eu/fr/artisjet-young/"
        # Un idioma sin precio: fila con enlace y sin precio (no se inventa).
        mbo_de = next(f for f in filas if f.origen_id == "mbo-3050" and f.idioma == "de")
        assert mbo_de.precio_texto is None and mbo_de.enlace and mbo_de.precio_publico is True
        marcas = {m.nombre: m for m in s.scalars(select(MarcaCatalogo))}
        assert set(marcas) == set(MARCAS_CON_PRODUCTO)
        assert marcas["mbo"].idioma_defecto == "es"
        assert json.loads(marcas["mbo"].urls_json)["en"] == "https://mboprinters.com/en/"
        # Idempotente: la segunda pasada no crea filas; un cambio de precio cuenta.
        data = _compositor_data()
        data["products"][0]["price"] = "1.350 €"
        segunda = catalogo_compositor.sincronizar(s, http=_supabase_falso(data), ahora=AHORA)
        s.commit()
        assert (segunda.productos, segunda.nuevos, segunda.actualizados, segunda.idos) == (
            202, 0, 1, 0)
        # Una máquina desaparece del compositor: se marca ida, no se borra.
        data["products"] = data["products"][1:]
        tercera = catalogo_compositor.sincronizar(s, http=_supabase_falso(data), ahora=AHORA)
        s.commit()
        assert tercera.idos == 5
        assert s.scalar(select(ProductoCatalogo).where(
            ProductoCatalogo.origen_id == "artisjet-young",
            ProductoCatalogo.idioma == "es")).ido_en is not None
        estado = catalogo.estado(s)
        # Lo vigente no cuenta los dos premios descartados.
        assert estado["fuentes"]["compositor"]["productos"] == 202 - 5 - 2
        assert estado["idos"] == 5 and estado["fuentes"]["compositor"]["clave_configurada"]
        assert estado["descartados"] == 2 and estado["sin_precio_publico"] == 1
        assert [m["nombre"] for m in estado["marcas"]] == list(MARCAS_CON_PRODUCTO)


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
        assert urls["https://mboprinters.com/de/mbo-4060/"]["etiqueta"] == "MBO 4060 (mbo), en de"
        assert all(m["eleccion"] == "interes" for m in maquinas)       # nada nombrado en el hilo
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


def test_cnc_sin_maquina_enlaza_la_pagina_del_interes_y_no_inventa(
    session_factory: sessionmaker,
) -> None:
    """El único interés comercial sin máquina en el compositor es el CNC (las
    láser son de FLUX, el packaging de SmartJet, el DTF de MBO DTF)."""
    with session_factory() as s:
        _sincronizar_compositor(s)
        contacto = _contacto(s, idioma="en", intereses=["cnc"])
        hilo = _hilo(s, contacto, [{"de": KLAUS, "texto": "I need a milling machine for aluminium "
                                                           "parts. Which model and what price?"}],
                     delivered_to="sales@mqeurope.com")
        s.commit()
        ctx = contexto.construir(s, thread=hilo, usuario=_admin(s), ahora=AHORA)
        assert ctx.catalogo["maquinas"] == [] and ctx.catalogo["sin_maquina"] == ["cnc"]
        # Si el catálogo no respondiera, el resumen lo dice con None (no «sin productos»).
        with patch.object(catalogo, "para_asistente", side_effect=RuntimeError("tabla caída")):
            sin = contexto.construir(s, thread=hilo, usuario=_admin(s), ahora=AHORA)
        assert sin.catalogo is None and sin.resumen()["catalogo"] is None
        assert "CATÁLOGO: no hay ningún producto" in redactor.bloque_contexto(sin)
        bloque = redactor.bloque_contexto(ctx)
        assert "CATÁLOGO: no hay ningún producto ni precio verificado para este caso" in bloque
        assert "Sin máquina en el catálogo para: cnc" in bloque
        assert not any("catalogo" in (e.get("origen") or "") for e in ctx.enlaces_lista())
        # Y el mapa resuelto enseña el hueco con su motivo (y el hallazgo: solo el CNC).
        resuelto = catalogo.mapa_resuelto(s)
        huecos = {h["interes"]: h["motivo"] for h in resuelto["huecos"]}
        assert set(huecos) == {"cnc"}
        assert huecos["cnc"] == "sin máquina en el compositor: se enlaza la página de producto"
        assert "CNC" in resuelto["hallazgo"] and resuelto["revisado"] is False
        assert resuelto["de_la_base"] is False                 # la propuesta, sin filas en la tabla
        dtf = next(f for f in resuelto["filas"] if f["interes"] == "dtf")
        assert len(dtf["coincide"]) == 9 and {c["marca"] for c in dtf["coincide"]} == {"mbo_dtf"}
        assert dtf["etiqueta"] == "DTF · impresión textil"
        por_interes = {f["interes"]: {c["marca"] for c in f["coincide"]} for f in resuelto["filas"]}
        assert por_interes["corte_laser"] == por_interes["grabado_laser"] == {"flux"}
        assert por_interes["packaging"] == {"smartjet"} and por_interes["vending"] == {"pimpam"}
        # Los dos premios de artisJet no casan con nada: no son máquinas.
        todos = {c["nombre"] for f in resuelto["filas"] for c in f["coincide"]}
        assert not todos & set(PREMIOS)


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
        assert ("Un precio o plazo que aparezca en un pedido, una nota interna o un correo "
                "antiguo es histórico") in sistema
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
        # Hasta seis máquinas: primero las que NOMBRA el cliente (las cuatro
        # Beambox del compositor), luego las de su interés (dos de uv_grande);
        # y hasta ocho consumibles.
        assert len(ctx.catalogo["maquinas"]) == catalogo.MAQUINAS_MAX == 6
        assert [m["eleccion"] for m in ctx.catalogo["maquinas"]] == ["hilo"] * 4 + ["interes"] * 2
        assert all("Beambox" in m["nombre"] for m in ctx.catalogo["maquinas"][:4])
        assert [m["interes"] for m in ctx.catalogo["maquinas"][4:]] == ["uv_grande", "uv_grande"]
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
    assert len(todo["interes_productos"]["filas"]) == len(catalogo.MAPA_DE_PARTIDA) == 17
    # Con la tabla vacía la pantalla ve la propuesta (ids `partida-N`), y un
    # cambio sobre una de esas filas siembra la tabla y se aplica a esa fila.
    assert todo["interes_productos"]["de_la_base"] is False
    partida = todo["interes_productos"]["filas"][2]
    assert partida["id"] == "partida-2" and partida["patron"] == "1015"
    cambio = http.patch("/api/erp/conocimiento/catalogo/intereses/partida-2", headers=headers,
                        json={"activo": False})
    assert cambio.status_code == 200, cambio.text
    sembradas = cambio.json()["interes_productos"]
    assert sembradas["de_la_base"] is True and len(sembradas["filas"]) == 17
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
    assert pasadas["compositor"]["estado"] == "ok" and pasadas["compositor"]["productos"] == 202
    assert pasadas["compositor"]["origen"] == "manual"
    assert pasadas["woocommerce"]["productos"] == 5 and pasadas["woocommerce"]["estado"] == "ok"
    assert r.json()["catalogo"]["fuentes"]["compositor"]["por_idioma"]["de"] == 40
    assert r.json()["catalogo"]["marcas"][0]["nombre"] == "artisjet"
    assert len(r.json()["catalogo"]["marcas"]) == 6                 # las sin producto, fuera
    # La fila dtf casa con las nueve MBO DTF; el CNC es el único hueco.
    resuelto = r.json()["interes_productos"]
    dtf = next(f for f in resuelto["filas"] if f["interes"] == "dtf")
    assert len(dtf["coincide"]) == 9 and resuelto["catalogo_vacio"] is False
    assert {h["interes"] for h in resuelto["huecos"]} == {"cnc"}
    # La tabla, filtrada (los premios descartados solo si se piden).
    lista = http.get("/api/erp/conocimiento/catalogo/productos",
                     headers=headers, params={"fuente": "compositor", "idioma": "fr",
                                              "marca": "mbo"}).json()
    assert lista["total"] == 7 and all(p["idioma"] == "fr" for p in lista["productos"])
    assert lista["productos"][0]["precio_texto"].endswith(" €")
    busca = http.get("/api/erp/conocimiento/catalogo/productos", headers=headers,
                     params={"q": "young"}).json()
    assert busca["total"] == 5 and busca["productos"][0]["nombre"] == "artisJet Young"
    premios = http.get("/api/erp/conocimiento/catalogo/productos", headers=headers,
                       params={"q": "edison"}).json()
    assert premios["total"] == 0
    premios = http.get("/api/erp/conocimiento/catalogo/productos", headers=headers,
                       params={"q": "edison", "con_descartados": "true"}).json()
    assert premios["total"] == 1 and premios["productos"][0]["descartado"] is True
    assert premios["productos"][0]["motivo_descarte"].startswith("sin precio ni área")
    consultar = http.get("/api/erp/conocimiento/catalogo/productos", headers=headers,
                         params={"q": "uv2513", "idioma": "es"}).json()["productos"][0]
    assert consultar["precio_publico"] is False and consultar["precio_texto"] is None
    assert consultar["precio_num"] is None and consultar["datos"]["precio_origen"] == "Consultar"
    tienda = http.get("/api/erp/conocimiento/catalogo/productos", headers=headers,
                      params={"fuente": "woocommerce"}).json()
    assert tienda["total"] == 5 and tienda["productos"][0]["sitio"] == "fluxlasers"
    # Una fila nueva: MBO 6090 también para uv_mediano (sin desplegar).
    alta = http.post("/api/erp/conocimiento/catalogo/intereses", headers=headers, json={
        "interes": "uv_mediano", "marca": "mbo", "patron": "6090", "nota": "prueba"})
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
        "interes": "uv_mediano", "marca": "mbo", "patron": "6090"}).status_code == 400
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
    marca = http.patch("/api/erp/conocimiento/catalogo/marcas/mbo", headers=headers,
                       json={"idioma_defecto": "en"})
    assert marca.status_code == 200
    assert next(m for m in marca.json()["catalogo"]["marcas"]
                if m["nombre"] == "mbo")["idioma_defecto"] == "en"
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
    # Editar una fila que hoy es un hueco hasta que tenga productos (D5: la
    # tabla no se re-siembra; se edita). La fila de packaging con una marca
    # que no existe no casa con nada; con la marca del compositor, con las tres.
    filas = http.get("/api/erp/conocimiento", headers=headers).json()["interes_productos"]["filas"]
    packaging = next(f for f in filas if f["interes"] == "packaging")
    hueco = http.patch(f"/api/erp/conocimiento/catalogo/intereses/{packaging['id']}",
                       headers=headers, json={"marca": "SmartJet FLEX"})
    assert hueco.status_code == 200, hueco.text
    resuelto = hueco.json()["interes_productos"]
    assert next(f for f in resuelto["filas"] if f["id"] == packaging["id"])["coincide"] == []
    assert {h["interes"]: h["motivo"] for h in resuelto["huecos"]}["packaging"] == (
        "las filas no casan con ninguna máquina del catálogo")
    arreglo = http.patch(f"/api/erp/conocimiento/catalogo/intereses/{packaging['id']}",
                         headers=headers, json={"marca": "smartjet", "nota": "revisada"})
    resuelto = arreglo.json()["interes_productos"]
    fila = next(f for f in resuelto["filas"] if f["id"] == packaging["id"])
    assert sorted(c["nombre"] for c in fila["coincide"]) == [
        "SmartJet FLEX 30", "SmartJet FLEX 60", "SmartJet FLEX Pro"]
    assert fila["nota"] == "revisada"
    assert "packaging" not in {h["interes"] for h in resuelto["huecos"]}


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


# --- contra los datos reales (11/10/2026) -----------------------------------------------------


def test_consultar_no_es_un_precio_desde_se_guarda_tal_cual_y_los_premios_se_descartan(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        _sincronizar_compositor(s)
        filas = {(f.origen_id, f.idioma): f for f in s.scalars(select(ProductoCatalogo))}
        # «Consultar» → sin precio público: ni texto, ni número (vacío, no cero).
        consultar = filas[("mbo-uv2513", "es")]
        assert consultar.precio_texto is None and consultar.precio_num is None
        assert consultar.precio_publico is False and consultar.descartado is False
        assert json.loads(consultar.datos_json)["precio_origen"] == "Consultar"
        # «desde 21.500 €» se guarda y se enseña con el «desde»; el número solo ordena.
        desde = filas[("mbo-uv1812", "es")]
        assert desde.precio_texto == "desde 21.500 €" and desde.precio_num == 21500
        assert desde.precio_publico is True
        # Los premios: sin precio ni área, descartados con su motivo, visibles
        # en la pantalla si se piden y fuera del catálogo del asistente.
        premio = filas[("premios-ecovadis", "es")]
        assert premio.descartado is True and "no es una máquina" in premio.motivo_descarte
        assert {f.nombre for f in catalogo.listar(s, con_descartados=True)} >= set(PREMIOS)
        assert not {f.nombre for f in catalogo.listar(s)} & set(PREMIOS)
        grupos = catalogo._maquinas_por_producto(s)
        assert not any(v[0].nombre in PREMIOS for v in grupos.values())
        # El bloque del asistente para uv_grande: «desde» tal cual y, para la
        # que no tiene precio público, la orden de no escribir «Consultar».
        contacto = _contacto(s, idioma="es", intereses=["uv_grande"])
        hilo = _hilo(s, contacto, [{"de": KLAUS, "texto": "¿Precio de vuestras UV grandes?"}])
        s.commit()
        ctx = contexto.construir(s, thread=hilo, usuario=_admin(s), ahora=AHORA)
        por_nombre = {m["nombre"]: m for m in ctx.catalogo["maquinas"]}
        assert por_nombre["MBO UV1812"]["precio_texto"] == "desde 21.500 €"
        assert por_nombre["MBO UV2513"]["precio_texto"] is None
        assert por_nombre["MBO UV2513"]["precio_publico"] is False
        bloque = redactor.bloque_contexto(ctx)
        assert "PRECIO: desde 21.500 € (moneda EUR)" in bloque
        assert "PRECIO: Consultar" not in bloque
        assert ("MBO UV2513 · mbo · área/categoría: A3 · Tinta UV LED; Cabezal industrial · "
                "PRECIO: no tiene precio público — di que el precio se prepara a medida en la "
                "oferta; NO escribas «Consultar»") in bloque
        # Y el borrador que sale (simulado con la orden cumplida) no lleva «Consultar».
        falso = _ClaudeFalso({
            "asunto": "Re: UV", "idioma": "es", "aviso": "", "pendiente": [], "enlaces_usados": [],
            "cuerpo": ("<p>Hola Klaus,</p><ul><li><strong>MBO UV1812</strong> — desde 21.500 €"
                       "</li><li><strong>MBO UV2513</strong> — precio a medida, te lo preparo en "
                       "la oferta</li></ul><p>Un saludo</p>"),
        })
        with patch.object(llm, "_invoke_claude", falso):
            borrador = redactor.generar(s, thread=hilo, usuario=_admin(s), ahora=AHORA)
        assert "Consultar" not in borrador.texto and "desde 21.500 €" in borrador.texto


def test_la_escritura_recorta_a_la_columna_y_salta_la_fila_que_no_entra(
    session_factory: sessionmaker,
) -> None:
    """Lo del 11/10/2026: una categoría de 290 caracteres (nueve categorías de
    una tienda) tiró la pasada entera con un DataError. Ahora la columna lleva
    la categoría principal, cualquier texto se recorta a su columna y la fila
    que la base rechaza se salta, se cuenta y la pasada termina."""
    nueve = [("Consumibles", "consumibles")] + [(f"Categoría larguísima número {i} de la tienda "
                                                 f"con muchas palabras", f"cat-{i}")
                                                for i in range(8)]
    producto = _producto_woo(3)
    producto["categories"] = [{"id": 50 + i, "name": n, "slug": sl, "link": "https://x/"}
                              for i, (n, sl) in enumerate(nueve)]
    sitio = next(s_ for s_ in SITIOS_DE_PARTIDA if s_.clave == "fluxlasers")
    fila = catalogo_tiendas.fila_de(producto, sitio=sitio, idioma="es", ahora=AHORA)
    assert len(" / ".join(n for n, _ in nueve)) > 255               # lo que antes desbordaba
    assert fila["categoria"] == "Consumibles"                        # la principal
    assert json.loads(fila["datos_json"])["categorias_nombres"] == [n for n, _ in nueve]
    assert fila["tipo"] == TIPO_CONSUMIBLE
    # Recorte genérico a la columna, para cualquier campo de texto.
    assert len(catalogo.recortar_a_columna("nombre", "x" * 300)) == 255
    assert len(catalogo.recortar_a_columna("categoria", "y" * 290)) == 255
    assert catalogo.recortar_a_columna("precio_num", 12.5) == 12.5
    with session_factory() as s:
        base = catalogo_tiendas.fila_de(_producto_woo(0), sitio=sitio, idioma="es", ahora=AHORA)
        largo = {**base, "origen_id": "fluxlasers:9001", "nombre": "n" * 300,
                 "categoria": "c" * 290}
        rota = {**base, "origen_id": "fluxlasers:9002", "nombre": None}       # NOT NULL
        otra = {**base, "origen_id": "fluxlasers:9003", "nombre": "Lente 9003"}
        resultado = catalogo.escribir_filas(
            s, [base, largo, rota, otra], fuente=FUENTE_WOOCOMMERCE, ahora=AHORA,
            sitio="fluxlasers", idioma="es")
        s.commit()
        assert resultado["productos"] == 3 and resultado["nuevos"] == 3
        assert resultado["saltadas"] == 1 and len(resultado["errores"]) == 1
        assert "fluxlasers:9002 (es)" in resultado["errores"][0]
        guardadas = {f.origen_id: f for f in s.scalars(select(ProductoCatalogo))}
        assert set(guardadas) == {"fluxlasers:1000", "fluxlasers:9001", "fluxlasers:9003"}
        assert len(guardadas["fluxlasers:9001"].nombre) == 255
        assert len(guardadas["fluxlasers:9001"].categoria) == 255
        # La pasada lo cuenta: parcial, con las filas saltadas en el detalle.
        pasada = catalogo.abrir_pasada(s, fuente=FUENTE_WOOCOMMERCE, origen="manual", ahora=AHORA)
        catalogo.cerrar_pasada(pasada, {"fluxlasers:es": resultado}, [], total=1)
        assert pasada.estado == SYNC_PARCIAL and pasada.productos == 3
        assert "1 fila(s) no entraron y se saltaron" in pasada.detalle
        assert "fluxlasers:9002" in pasada.detalle
        # Un lote con una fila mala no pierde las demás aunque sean muchas
        # (más de un lote): todas menos la mala.
        muchas = [{**base, "origen_id": f"fluxlasers:{8000 + i}", "nombre": f"Lente {i}"}
                  for i in range(catalogo.LOTE_ESCRITURA + 50)]
        muchas[7]["nombre"] = None
        muchas[catalogo.LOTE_ESCRITURA + 3]["nombre"] = None
        resultado2 = catalogo.escribir_filas(
            s, muchas, fuente=FUENTE_WOOCOMMERCE, ahora=AHORA, sitio="fluxlasers", idioma="es")
        s.commit()
        assert resultado2["saltadas"] == 2 and resultado2["productos"] == len(muchas) - 2
        assert resultado2["idos"] == 3          # las tres de antes ya no están en esta pasada


def test_las_maquinas_se_eligen_por_el_hilo_la_instruccion_y_el_interes(
    session_factory: sessionmaker,
) -> None:
    """D6: con el catálogo cargado, la franja decía «sin productos» porque
    solo elegía por el interés clasificado. Ahora, por orden: lo que nombra el
    cliente en el hilo (por el nombre, y más flojo por la descripción), lo que
    pide la instrucción («las máquinas de DTF» manda) y el interés."""
    with session_factory() as s:
        _sincronizar_compositor(s)
        # 1. Un contacto SIN clasificación que nombra modelos en el hilo. «DM3H»
        # no es ningún nombre del compositor, pero está en la descripción de la
        # Lyra 35 (como nombre anterior): entra, más flojo.
        sin_clasificar = _contacto(s, idioma="es", email="marny@talentos.example")
        hilo = _hilo(s, sin_clasificar, [{"de": "marny@talentos.example", "texto": (
            "Hola, me interesa la Orion 20 y el horno Vega. Ahora tenemos una DM3H.")}],
                     asunto="Equipos DTF")
        s.commit()
        ctx = contexto.construir(s, thread=hilo, usuario=_admin(s), ahora=AHORA)
        maquinas = ctx.catalogo["maquinas"]
        assert ctx.intereses == [] and maquinas, maquinas
        nombres = [m["nombre"] for m in maquinas]
        # Por el nombre, lo más completo primero («Orion 20» entero antes que una
        # «Vega 60 Pro» de la que solo se dijo «Vega»); por la descripción, al final.
        assert nombres[:3] == ["Horno Orion", "Horno Vega", "Orion 20"]
        assert set(nombres[3:5]) == {"Secadora Vega", "Vega 60 Pro"}
        assert nombres[-1] == "Lyra 35"                                          # por DM3H
        # Por la descripción es una mención posible, no segura: va detrás y
        # con su propio rótulo en el prompt.
        assert [m["eleccion"] for m in maquinas] == ["hilo"] * 5 + ["hilo_posible"]
        assert ctx.catalogo["menciones"]["hilo"][:3] == ["Horno Orion", "Horno Vega", "Orion 20"]
        assert ctx.catalogo["menciones"]["flojas"] == ["Lyra 35"]
        bloque = redactor.bloque_contexto(ctx)
        assert "Máquinas que el cliente NOMBRA en el hilo (empieza por estas)" in bloque
        assert "Máquinas que PUEDE que nombren el hilo o la instrucción" in bloque
        assert "Máquinas que encajan con su interés" not in bloque
        assert ctx.resumen()["catalogo"]["maquinas"][0]["eleccion"] == "hilo"
        # Lo que el cliente de talentosdiversos escribe de verdad (DM3H, SM3,
        # SwapPress PT5) no está en ningún nombre del compositor: sin un nombre
        # alternativo en la descripción no se encuentra (0 de 3 por el nombre;
        # 1 de 3 gracias a la descripción de este fixture).
        grupos = catalogo._maquinas_por_producto(s)
        aciertos = catalogo.menciones_en("Tenemos una DM3H, una SM3 y una SwapPress PT5", grupos)
        assert [(grupos[o][0].nombre, fuerza) for o, fuerza in aciertos] == [
            ("Lyra 35", "descripcion")]
        # 2. La instrucción manda: sin interés clasificado ni mención, «las
        # máquinas de DTF» trae las de mbo_dtf (hasta seis).
        hilo2 = _hilo(s, sin_clasificar, [{"de": "marny@talentos.example",
                                           "texto": "Gracias por la información."}],
                      asunto="Gracias")
        s.commit()
        ctx2 = contexto.construir(
            s, thread=hilo2, usuario=_admin(s), ahora=AHORA,
            instruccion="prepara un correo con las máquinas de DTF y sus precios")
        assert ctx2.catalogo["intereses_instruccion"] == ["dtf"]
        assert len(ctx2.catalogo["maquinas"]) == catalogo.MAQUINAS_MAX
        assert {m["marca"] for m in ctx2.catalogo["maquinas"]} == {"mbo_dtf"}
        assert all(m["eleccion"] == "instruccion" and m["interes"] == "dtf"
                   for m in ctx2.catalogo["maquinas"])
        assert "Máquinas que pide la INSTRUCCIÓN de quien envía" in redactor.bloque_contexto(ctx2)
        # Una máquina concreta en la instrucción también vale, y va delante.
        ctx3 = contexto.construir(
            s, thread=hilo2, usuario=_admin(s), ahora=AHORA,
            instruccion="ofrécele la Vega 60 Pro y las de DTF")
        assert ctx3.catalogo["maquinas"][0]["nombre"] == "Vega 60 Pro"
        # «Vega» también nombra el horno y la secadora Vega: van detrás, por el nombre completo.
        assert ctx3.catalogo["menciones"]["instruccion"][0] == "Vega 60 Pro"
        assert set(ctx3.catalogo["menciones"]["instruccion"]) == {
            "Vega 60 Pro", "Horno Vega", "Secadora Vega"}
        # 3. Sin mención ni instrucción, el interés clasificado, como siempre.
        clasificado = _contacto(s, idioma="es", intereses=["vending"], email="v@ejemplo.com")
        hilo4 = _hilo(s, clasificado, [{"de": "v@ejemplo.com", "texto": "Hola, más información."}],
                      asunto="Vending")
        s.commit()
        ctx4 = contexto.construir(s, thread=hilo4, usuario=_admin(s), ahora=AHORA)
        assert [m["nombre"] for m in ctx4.catalogo["maquinas"]] == [
            "PimPam CaseBox", "PimPam Custom"]
        assert all(m["eleccion"] == "interes" for m in ctx4.catalogo["maquinas"])
        # Palabras de interés en la instrucción: láser → las dos láser; «UV
        # grande» → solo uv_grande; «UV» a secas → los tres tamaños.
        assert catalogo.intereses_de_texto("una láser para cortar madera") == [
            "corte_laser", "grabado_laser"]
        assert catalogo.intereses_de_texto("una UV grande industrial") == ["uv_grande"]
        assert catalogo.intereses_de_texto("quiero una impresora UV") == [
            "uv_mediano", "uv_pequeno", "uv_grande"]
        assert catalogo.intereses_de_texto("gracias por todo") == []


def test_una_palabra_corriente_no_es_una_maquina_nombrada_y_el_tope_no_inventa_huecos(
    session_factory: sessionmaker,
) -> None:
    """Revisión del PR. (1) Una palabra corriente de un nombre («I trust you» y
    la 6090 Trust) no es una máquina que el cliente nombra: va como posible,
    con su propio rótulo en el prompt; escrita como nombre propio a media
    frase («la Trust»), sí. (2) «Sin máquina para X» solo cuando X no tiene
    máquinas, no porque el tope ya estuviera lleno con lo que nombra el hilo.
    (3) Una marca que ya estaba con otro nombre en mayúsculas se conserva, con
    el idioma que se le puso en la pantalla."""
    with session_factory() as s:
        s.add(MarcaCatalogo(nombre="artisJet", idioma_defecto="en"))
        s.commit()
        _sincronizar_compositor(s)
        marcas = list(s.scalars(select(MarcaCatalogo)))
        artisjet = [m for m in marcas if catalogo._clave(m.nombre) == "artisjet"]
        assert len(artisjet) == 1 and artisjet[0].idioma_defecto == "en"
        assert artisjet[0].ido_en is None
        assert len(marcas) == len(MARCAS_CON_PRODUCTO)
        # (1) Por el texto solo.
        grupos = catalogo._maquinas_por_producto(s)
        nombre = {o: v[0].nombre for o, v in grupos.items()}
        sueltas = catalogo.menciones_en("I trust you can ship fast.", grupos)
        assert [(nombre[o], fuerza) for o, fuerza in sueltas] == [
            ("artisJet 6090 Trust", catalogo.FUERZA_PALABRA)]
        propias = catalogo.menciones_en("Nos interesa la Trust para el taller.", grupos)
        assert [(nombre[o], fuerza) for o, fuerza in propias] == [
            ("artisJet 6090 Trust", catalogo.FUERZA_NOMBRE)]
        # En el borrador: la posible va detrás de lo seguro, con su rótulo, y
        # el interés clasificado sigue entrando.
        contacto = _contacto(s, idioma="es", intereses=["vending"], email="trust@ejemplo.com")
        hilo = _hilo(s, contacto, [{"de": "trust@ejemplo.com",
                                    "texto": "I trust you can ship fast."}], asunto="Trust me")
        s.commit()
        ctx = contexto.construir(s, thread=hilo, usuario=_admin(s), ahora=AHORA)
        assert [(m["nombre"], m["eleccion"]) for m in ctx.catalogo["maquinas"]] == [
            ("artisJet 6090 Trust", "hilo_posible"), ("PimPam CaseBox", "interes"),
            ("PimPam Custom", "interes")]
        bloque = redactor.bloque_contexto(ctx)
        assert "Máquinas que PUEDE que nombren el hilo o la instrucción" in bloque
        assert "Máquinas que el cliente NOMBRA en el hilo" not in bloque
        # (2) Seis máquinas nombradas llenan el tope: el interés (vending) tiene
        # máquinas aunque no quepan, así que no es un hueco del catálogo.
        lleno = _hilo(s, contacto, [{"de": "trust@ejemplo.com", "texto": (
            "Precio de la Orion 20, la Lyra 35, la Vega 60 Pro, el Horno Orion, el Horno Vega "
            "y la Secadora Vega, por favor.")}], asunto="Seis maquinas")
        s.commit()
        ctx2 = contexto.construir(s, thread=lleno, usuario=_admin(s), ahora=AHORA)
        assert len(ctx2.catalogo["maquinas"]) == catalogo.MAQUINAS_MAX
        assert all(m["eleccion"] == "hilo" for m in ctx2.catalogo["maquinas"])
        assert ctx2.catalogo["sin_maquina"] == []
        assert "Sin máquina en el catálogo" not in redactor.bloque_contexto(ctx2)
