# ruff: noqa: F811 — las fixtures de `test_erp_ficha_cobro_banco` se importan por nombre
"""Los pedidos de un contacto, para su ficha (30/10/2026): el listado de
pedidos filtra por `contact_id` igual que por `company_id`. Figurantes."""
from __future__ import annotations

from app.models.crm import Contact
from tests._test_helpers import auth_headers
from tests.test_erp_ficha_cobro_banco import _pedido, http, session_factory  # noqa: F401


def test_el_listado_filtra_por_contacto(http, session_factory) -> None:
    with session_factory() as s:
        s.add(Contact(id="ct-1", first_name="Ana", last_name="Figurante", email="ana@ejemplo.test"))
        s.add(Contact(id="ct-2", first_name="Luis", last_name="Figurante",
                      email="luis@ejemplo.test"))
        s.commit()
        _pedido(s, "o-1", contact_id="ct-1")
        _pedido(s, "o-2", codigo="526082", contact_id="ct-2")
        _pedido(s, "o-3", codigo="526083", contact_id="ct-1")
    r = http.get("/api/erp/orders", params={"contact_id": "ct-1", "show_external": "true"},
                 headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    assert sorted(o["id"] for o in r.json()["items"]) == ["o-1", "o-3"]
