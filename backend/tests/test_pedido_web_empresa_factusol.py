"""Pedidos web: la empresa CRM se resuelve por el CLIENTE de FACTUSOL.

El bug: el mapper solo resolvía la empresa cuando el *billing* de Woo traía el
NIF; sin él, el pedido web quedaba con `company_id` vacío (~26 % en producción).
Aquí se cubre el camino fiable (REFPCL → F_PCL.CLIPCL → F_CLI), el cruce por
NIF-IVA con prefijo de país (`FR91523447399` ≡ `91523447399`), que no se
duplican empresas y que nada de esto rompe la ingesta si FACTUSOL no está.

FACTUSOL va simulado en memoria (sin red) y SIEMPRE en solo lectura.
"""
from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401 — registra los modelos
from app.db.base import Base
from app.erp.models import Order
from app.erp.web_order_company import (
    REASON_NO_FACTUSOL,
    REASON_NO_NIF,
    REASON_NO_PCL,
    REASON_NOT_WEB,
    ensure_web_order_company,
)
from app.models.crm import Company

#: El caso real del VPS: pedido ARTISJ-9572 → F_CLI 3854 «EURL Y'A PAS PHOTO»
#: con el NIF-IVA prefijado que guarda FACTUSOL.
ORDER_NUMBER = "ARTISJ-9572"
REFPCL = "ART-009572"          # prefijo derivado (ART) + nº Woo con padding
CODCLI = "3854"
NIF_FR = "FR91523447399"
NIF_BARE = "91523447399"


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.drop_all(engine)


class FakeFactusol:
    """F_PCL + F_CLI simulados. Registra las tablas leídas y NUNCA escribe."""

    def __init__(self, *, pcl: dict | None = None, cli: dict | None = None):
        self.default_ejercicio = "2026"
        self._pcl = pcl
        self._cli = cli
        self.reads: list[tuple[str, str]] = []

    def load_table(self, tabla, *, filtro="1=1", ejercicio=None):
        self.reads.append((tabla, filtro))
        if tabla == "F_PCL":
            if self._pcl and f"REFPCL='{REFPCL}'" == filtro:
                return [self._pcl]
            return []
        if tabla == "F_CLI":
            if not self._cli:
                return []
            # `by="codcli"` → CODCLI=n; `by="nif"` → IN (...) con las variantes.
            if f"CODCLI={int(CODCLI)}" == filtro:
                return [self._cli]
            if "NIFCLI" in filtro and str(self._cli.get("NIFCLI") or "") in filtro:
                return [self._cli]
            return []
        return []


def _cli_row(nif: str = NIF_FR, nombre: str = "EURL Y'A PAS PHOTO") -> dict:
    return {
        "CODCLI": CODCLI, "NIFCLI": nif, "NOFCLI": nombre, "NOCCLI": nombre,
        "DOMCLI": "12 RUE DE LA PAIX", "POBCLI": "PARIS", "CPOCLI": "75002",
        "PROCLI": "", "PAICLI": "", "EMACLI": "hola@yapasphoto.fr", "TELCLI": "",
    }


def _fake(*, nif: str = NIF_FR, with_pcl: bool = True) -> FakeFactusol:
    return FakeFactusol(
        pcl={"CLIPCL": CODCLI, "REFPCL": REFPCL} if with_pcl else None,
        cli=_cli_row(nif),
    )


def _web_order(s: Session, *, number: str = ORDER_NUMBER, company_id=None) -> str:
    o = Order(
        order_number=number, external_source="woocommerce", external_id="9572",
        preparation_status="pending_review", payment_status="paid",
        transport_status="not_shipped", company_id=company_id,
    )
    s.add(o)
    s.commit()
    return o.id


# --- cruce por NIF-IVA con prefijo de país ------------------------------------


def test_nif_canonico_casa_desnudo_y_prefijado(session_factory):
    """`FR91523447399` y `91523447399` son el MISMO identificador, en los dos
    sentidos y con separadores. Es lo que rompía el buscador por CIF."""
    from app.api.companies import find_companies_by_nif

    with session_factory() as s:
        s.add(Company(name="EURL Y'A PAS PHOTO", tax_id=NIF_FR))
        s.commit()
        for query in (NIF_FR, NIF_BARE, "fr 91.523.447-399"):
            hits = find_companies_by_nif(s, query)
            assert [c.name for c in hits] == ["EURL Y'A PAS PHOTO"], query


def test_nif_canonico_encuentra_el_prefijado_desde_el_desnudo(session_factory):
    """Y al revés: guardado DESNUDO, se encuentra buscando con prefijo."""
    from app.api.companies import find_companies_by_nif

    with session_factory() as s:
        s.add(Company(name="Bare SL", tax_id=NIF_BARE))
        s.commit()
        assert [c.name for c in find_companies_by_nif(s, NIF_FR)] == ["Bare SL"]


@pytest.mark.parametrize(("prefijado", "desnudo"), [
    ("FR91523447399", "91523447399"),
    ("BE0812240188", "0812240188"),
    ("NL123456789B01", "123456789B01"),
    ("DE455128445", "455128445"),
    ("ESB64113590", "B64113590"),
])
def test_nif_canonico_por_pais(session_factory, prefijado, desnudo):
    from app.api.companies import find_companies_by_nif

    with session_factory() as s:
        s.add(Company(name="X", tax_id=prefijado))
        s.commit()
        assert find_companies_by_nif(s, desnudo), prefijado
        assert find_companies_by_nif(s, prefijado), prefijado


# --- resolución por el cliente de FACTUSOL ------------------------------------


def test_vincula_la_empresa_existente_y_le_rellena_el_codcli(session_factory):
    """La empresa buena YA existe en el CRM (con el NIF-IVA francés) pero el
    pedido no la tenía: se vincula y además se le enlaza su CODCLI."""
    with session_factory() as s:
        s.add(Company(name="EURL Y'A PAS PHOTO", tax_id=NIF_FR))
        s.commit()
        oid = _web_order(s)
        order = s.get(Order, oid)

        r = ensure_web_order_company(s, order, client=_fake(), ejercicio="2026")
        s.commit()

        assert r.resolved and not r.created
        assert r.codcli == CODCLI and r.linked_order and r.linked_company
        assert s.get(Order, oid).company_id == r.company.id
        assert r.company.factusol_company_id == CODCLI
        # No se ha creado ninguna empresa de más.
        assert s.query(Company).count() == 1


def test_crea_la_empresa_desde_f_cli_si_no_existe(session_factory):
    with session_factory() as s:
        oid = _web_order(s)
        order = s.get(Order, oid)

        r = ensure_web_order_company(s, order, client=_fake(), ejercicio="2026")
        s.commit()

        assert r.resolved and r.created and r.codcli == CODCLI
        company = s.get(Company, r.company.id)
        assert company.name == "EURL Y'A PAS PHOTO"
        assert company.tax_id == NIF_FR
        assert company.factusol_company_id == CODCLI
        assert company.city == "PARIS"
        assert s.get(Order, oid).company_id == company.id


def test_es_idempotente_no_duplica_al_reprocesar(session_factory):
    """Reprocesar el pedido no crea una segunda empresa ni cambia el vínculo."""
    with session_factory() as s:
        oid = _web_order(s)
        order = s.get(Order, oid)
        first = ensure_web_order_company(s, order, client=_fake(), ejercicio="2026")
        s.commit()

        again = ensure_web_order_company(s, order, client=_fake(), ejercicio="2026")
        s.commit()

        assert not again.created
        assert s.query(Company).count() == 1
        assert s.get(Order, oid).company_id == first.company.id


def test_empresa_desnuda_en_el_crm_casa_con_el_nif_prefijado_de_factusol(session_factory):
    """FACTUSOL guarda `FR91523447399` y el CRM el número desnudo: es la misma
    empresa, así que se vincula en vez de crear una duplicada."""
    with session_factory() as s:
        s.add(Company(name="Y A PAS PHOTO", tax_id=NIF_BARE))
        s.commit()
        oid = _web_order(s)

        r = ensure_web_order_company(
            s, s.get(Order, oid), client=_fake(), ejercicio="2026")
        s.commit()

        assert r.resolved and not r.created
        assert s.query(Company).count() == 1


def test_prefiere_la_empresa_ya_vinculada_a_factusol(session_factory):
    """Con varias candidatas por el mismo NIF gana la vinculada a FACTUSOL
    (nunca se coge una al azar)."""
    with session_factory() as s:
        s.add(Company(name="Duplicada Brevo", tax_id=NIF_FR))
        s.add(Company(name="La buena", tax_id=NIF_FR, factusol_company_id=CODCLI))
        s.commit()
        oid = _web_order(s)

        r = ensure_web_order_company(
            s, s.get(Order, oid), client=_fake(), ejercicio="2026")
        s.commit()

        assert r.company.name == "La buena"


# --- lo que NO se puede resolver ----------------------------------------------


def test_sin_pedido_en_factusol_no_inventa_nada(session_factory):
    """Carrera típica: BoHub importa antes de que FesteWeb escriba el F_PCL."""
    with session_factory() as s:
        oid = _web_order(s)

        r = ensure_web_order_company(
            s, s.get(Order, oid), client=_fake(with_pcl=False), ejercicio="2026")
        s.commit()

        assert not r.resolved and r.reason == REASON_NO_PCL
        assert s.query(Company).count() == 0
        assert s.get(Order, oid).company_id is None


def test_cliente_sin_nif_no_crea_empresa_a_ciegas(session_factory):
    with session_factory() as s:
        oid = _web_order(s)

        r = ensure_web_order_company(
            s, s.get(Order, oid), client=_fake(nif=""), ejercicio="2026")
        s.commit()

        assert not r.resolved and r.reason == REASON_NO_NIF
        assert s.query(Company).count() == 0


def test_pedido_no_web_no_se_toca(session_factory):
    """Los pedidos manuales ya traen empresa por su propio flujo."""
    with session_factory() as s:
        o = Order(order_number="MAN-0001", external_source="manual",
                  preparation_status="pending_review", payment_status="paid",
                  transport_status="not_shipped")
        s.add(o)
        s.commit()

        r = ensure_web_order_company(s, o, client=_fake(), ejercicio="2026")

        assert not r.resolved and r.reason == REASON_NOT_WEB


def test_sin_factusol_no_rompe_y_deja_el_pedido_igual(session_factory, monkeypatch):
    """FACTUSOL caído / sin credenciales: la ingesta sigue, sin empresa."""
    monkeypatch.setattr(
        "app.erp.web_order_company.factusol_client_and_ejercicio", lambda s: None)
    with session_factory() as s:
        oid = _web_order(s)

        r = ensure_web_order_company(s, s.get(Order, oid))

        assert not r.resolved and r.reason == REASON_NO_FACTUSOL
        assert s.get(Order, oid).company_id is None


def test_solo_lee_de_factusol(session_factory):
    """Guard explícito: este camino NUNCA escribe en FACTUSOL."""
    fake = _fake()
    with session_factory() as s:
        oid = _web_order(s)
        ensure_web_order_company(s, s.get(Order, oid), client=fake, ejercicio="2026")
        s.commit()

    assert not hasattr(fake, "writes") or not getattr(fake, "writes", [])
    assert {t for t, _ in fake.reads} <= {"F_PCL", "F_CLI"}


# --- backfill (Parte B) -------------------------------------------------------


def test_backfill_selecciona_solo_los_web_sin_empresa(session_factory):
    """La consulta del backfill coge los pedidos WEB con `company_id` vacío y
    deja fuera los manuales y los que ya tienen empresa."""
    from scripts.backfill_empresa_pedidos_web import _rows_without_company

    with session_factory() as s:
        empresa = Company(name="Ya vinculada", tax_id=NIF_FR)
        s.add(empresa)
        s.flush()
        _web_order(s, number="WEB-SIN")
        _web_order(s, number="WEB-CON", company_id=empresa.id)
        s.add(Order(order_number="MAN-1", external_source="manual",
                    preparation_status="pending_review", payment_status="paid",
                    transport_status="not_shipped"))
        s.commit()

        rows = _rows_without_company(s, order_number=None, limit=None)

        assert [o.order_number for o in rows] == ["WEB-SIN"]


def test_backfill_no_toca_un_pedido_ya_vinculado(session_factory):
    """Idempotencia del backfill: un pedido con empresa YA vinculada a FACTUSOL
    se reporta como tal y no se le cambia nada."""
    with session_factory() as s:
        empresa = Company(name="La buena", tax_id=NIF_FR, factusol_company_id=CODCLI)
        s.add(empresa)
        s.flush()
        oid = _web_order(s, company_id=empresa.id)
        s.commit()

        r = ensure_web_order_company(
            s, s.get(Order, oid), client=_fake(), ejercicio="2026")

        assert not r.created and not r.linked_order
        assert s.get(Order, oid).company_id == empresa.id
        assert s.query(Company).count() == 1


# --- fusión de duplicados por NOMBRE, sin NIF (Parte D) -----------------------


def test_fusion_por_nombre_propone_la_canonica_con_nif(session_factory):
    """El caso «y'a pas photo»: varias fichas de Brevo SIN NIF junto a la buena
    (con NIF). Se propone fusionar en la que tiene NIF."""
    from app.services.company_dedupe import plan_name_only_merges

    with session_factory() as s:
        s.add(Company(name="EURL Y'A PAS PHOTO", tax_id=NIF_FR,
                      factusol_company_id=CODCLI))
        for _ in range(3):
            s.add(Company(name="EURL Y'A PAS PHOTO"))
        s.commit()

        plan = plan_name_only_merges(s)

        assert len(plan.to_merge) == 1 and not plan.review
        accion = plan.to_merge[0]
        assert accion.keep_name == "EURL Y'A PAS PHOTO"
        assert accion.keep_codcli == CODCLI
        assert len(accion.merge_ids) == 3


def test_fusion_por_nombre_manda_a_revisar_si_no_hay_canonica(session_factory):
    """Ninguna con NIF → no hay superviviente clara: se revisa a mano."""
    from app.services.company_dedupe import plan_name_only_merges

    with session_factory() as s:
        s.add(Company(name="Y A PAS PHOTO"))
        s.add(Company(name="Y A PAS PHOTO"))
        s.commit()

        plan = plan_name_only_merges(s)

        assert not plan.to_merge and len(plan.review) == 1
        assert "ninguna ficha del grupo tiene NIF" in plan.review[0].reason


def test_fusion_por_nombre_no_toca_dos_nif_distintos(session_factory):
    """Mismo nombre pero NIF distintos: son empresas distintas, no se fusionan."""
    from app.services.company_dedupe import plan_name_only_merges

    with session_factory() as s:
        s.add(Company(name="Talleres Gomez", tax_id="B64113590"))
        s.add(Company(name="Talleres Gomez", tax_id="B12345674"))
        s.commit()

        plan = plan_name_only_merges(s)

        assert not plan.to_merge and len(plan.review) == 1
        assert "NIF DISTINTO" in plan.review[0].reason


def test_no_cuelga_el_codcli_de_una_empresa_con_otro_nif(session_factory):
    """Si el pedido ya traía una empresa cuyo NIF NO es el del cliente de
    FACTUSOL, no se le cuelga ese CODCLI: sería un vínculo falso."""
    with session_factory() as s:
        otra = Company(name="Otra empresa", tax_id="B64113590")
        s.add(otra)
        s.flush()
        oid = _web_order(s, company_id=otra.id)
        s.commit()

        r = ensure_web_order_company(
            s, s.get(Order, oid), client=_fake(), ejercicio="2026")
        s.commit()

        assert not r.linked_company
        assert s.get(Company, otra.id).factusol_company_id is None
