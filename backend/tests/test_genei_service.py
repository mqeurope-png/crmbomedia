"""Servicio Genei: mapeo de estados, destino/payload y estado en packing_json."""
from __future__ import annotations

from app.erp.integrations.genei.service import (
    address_missing_fields,
    build_destination,
    build_origin,
    build_shipment_payload,
    clear_genei_state,
    destination_is_complete,
    genei_state_of,
    set_genei_state,
    shipment_code_of_order,
    summarize_shipment,
)
from app.erp.integrations.genei.status import (
    CREATED,
    DELIVERED,
    IN_TRANSIT,
    INCIDENT,
    state_of,
    transport_status_for,
)
from app.erp.models.orders import Order

# --- status -----------------------------------------------------------------


def test_state_of_maps_codes_and_buckets():
    assert state_of(7).bucket == CREATED
    assert state_of(5).bucket == IN_TRANSIT
    assert state_of("3").bucket == DELIVERED
    assert state_of(10).bucket == INCIDENT
    assert state_of(9).bucket == INCIDENT and state_of(14).bucket == INCIDENT
    # Desconocido → OTHER con el código.
    assert state_of(999).code == 999 and state_of(999).bucket == "other"
    assert state_of(None).bucket == "other"


def test_transport_status_for_buckets():
    assert transport_status_for(IN_TRANSIT) == "in_transit"
    assert transport_status_for(DELIVERED) == "delivered"
    assert transport_status_for(INCIDENT) == "incident"
    assert transport_status_for(CREATED) is None  # no mueve el estado


# --- destino / payload ------------------------------------------------------


def test_build_destination_normalizes():
    dest = build_destination({
        "name": "Alexandre", "nif": "B12345678", "email": "a@x.fr",
        "phone": "+33 1 23", "address": "12 Rue", "postal_code": "75001",
        "city": "Paris", "country": "france",
    })
    assert dest["name"] == "Alexandre"
    assert dest["contact"] == "Alexandre"       # contacto cae al nombre
    assert dest["dni"] == "B12345678"
    assert dest["town"] == "Paris"
    assert dest["isoCountry"] == "FR"           # a 2 letras y mayúsculas


def test_destination_is_complete_lists_missing():
    dest = build_destination({"name": "X", "address": "C/ 1", "city": "Madrid"})
    missing = destination_is_complete(dest)
    assert "código postal" in missing and "país" in missing
    assert "nombre" not in missing
    full = build_destination({
        "name": "X", "address": "C/1", "postal_code": "28001",
        "city": "Madrid", "country": "ES",
    })
    assert destination_is_complete(full) == []


def test_build_shipment_payload_shape():
    payload = build_shipment_payload(
        agency_id="10",
        origin={"name": "SAT Bomedia", "isoCountry": "ES"},
        destination={"name": "X"},
        packages=[{"weight": 0.5, "height": 2, "width": 10, "length": 10}],
        external_shipping_code="BOPRIN-99917",
        notification_url="https://bohub/webhooks/genei",
    )
    assert payload["agencyId"] == "10"
    assert payload["externalShippingCode"] == "BOPRIN-99917"
    assert payload["origin"] == {"name": "SAT Bomedia", "isoCountry": "ES"}
    assert payload["destination"] == {"name": "X"}
    # Los DOS campos que faltaban y hacían fallar la creación en vivo
    # («"origin" is mandatory; "paymentMethodShipping" is mandatory»).
    assert payload["paymentMethodShipping"] == 4     # 4 = pago con saldo
    assert payload["shippingFromWarehouse"] == 0     # origen propio, no Genei
    assert payload["notificationUrl"] == "https://bohub/webhooks/genei"
    assert payload["packagesArray"][0]["weight"] == 0.5


def test_build_shipment_payload_omits_empty_optionals():
    payload = build_shipment_payload(
        agency_id="10", origin={}, destination={},
        packages=[], external_shipping_code="X", notification_url=None,
    )
    assert "notificationUrl" not in payload
    # origin y paymentMethodShipping son OBLIGATORIOS: siempre van.
    assert "origin" in payload
    assert payload["paymentMethodShipping"] == 4


def test_build_origin_from_genei_address():
    # Forma real de GET /addresses/{id} (verificada en vivo): el remitente por
    # defecto de la cuenta ya trae todo, incl. el teléfono en E.164.
    origin = build_origin({
        "nombre": "Streamtec SAT", "direccion": "Mossen Antoni Solanas",
        "mail": "sat@example.com", "codigo_postal": "08830",
        "poblacion": "SANT BOI DE LLOBREGAT", "provincia": "Barcelona",
        "nombre_pais": "España", "country_code": "ES",
        "telefono": "609144424", "telefono_e164": "+34609144424",
        "prefijo_telefonico": 34, "vat_number": None, "observaciones": "",
    })
    assert origin["name"] == "Streamtec SAT"
    assert origin["contact"] == "Streamtec SAT"      # cae al nombre
    assert origin["address"] == "Mossen Antoni Solanas"
    assert origin["postalCode"] == "08830"
    assert origin["town"] == "SANT BOI DE LLOBREGAT"
    assert origin["isoCountry"] == "ES"
    assert origin["phone"] == "+34609144424"         # con prefijo internacional
    # Bloque completo → sin campos mínimos que falten.
    assert address_missing_fields(origin) == []


def test_build_origin_phone_falls_back_to_prefix():
    # Sin telefono_e164, se compone «+<prefijo><nacional>».
    origin = build_origin({
        "nombre": "X", "direccion": "C/1", "codigo_postal": "08001",
        "poblacion": "BCN", "country_code": "ES",
        "prefijo_telefonico": 34, "telefono": "600111222",
    })
    assert origin["phone"] == "+34600111222"


# --- summarize --------------------------------------------------------------


def test_summarize_shipment_tolerant():
    summary = summarize_shipment({
        "codigo_envio": "GEN1", "estado": 5, "codigo_seguimiento": "TRK9",
        "nombre_agencia": "GLS Domicilio",
    })
    assert summary["shipment_code"] == "GEN1"
    assert summary["state_code"] == 5
    assert summary["state_bucket"] == IN_TRANSIT
    assert summary["tracking"] == "TRK9"
    assert summary["courier"] == "GLS Domicilio"


def test_summarize_shipment_creation_with_payment_url():
    summary = summarize_shipment({"shipmentCode": "GEN2", "status": 7, "paymentUrl": "https://pay"})
    assert summary["state_bucket"] == CREATED
    assert summary["payment_url"] == "https://pay"


def test_summarize_shipment_creation_reference_key():
    # Forma REAL de la respuesta de creación (verificada en vivo): el código va
    # en `reference` (no `codigo_envio`/`shipmentCode`). Sin esto, shipment_code
    # salía None y el envío quedaba huérfano en Genei.
    summary = summarize_shipment({
        "reference": "DPEHLDPC", "transactionId": 16157377, "paymentUrl": "https://pay/x",
    })
    assert summary["shipment_code"] == "DPEHLDPC"
    assert summary["payment_url"] == "https://pay/x"
    # PR-2: el id de transacción se guarda para pagar por API.
    assert summary["transaction_id"] == "16157377"


# --- estado en packing_json -------------------------------------------------


def test_genei_state_roundtrip_on_order():
    order = Order()
    assert genei_state_of(order) == {}
    assert shipment_code_of_order(order) is None

    set_genei_state(order, {"shipment_code": "GEN1", "state_bucket": CREATED, "empty": None})
    assert shipment_code_of_order(order) == "GEN1"
    state = genei_state_of(order)
    assert state["state_bucket"] == CREATED
    assert "empty" not in state          # None no se guarda

    # Merge, no reemplazo.
    set_genei_state(order, {"tracking": "TRK9"})
    state = genei_state_of(order)
    assert state["shipment_code"] == "GEN1" and state["tracking"] == "TRK9"

    clear_genei_state(order)
    assert genei_state_of(order) == {}


def test_set_genei_state_preserves_other_packing_blocks():
    order = Order()
    order.packing_json = '{"factusol_payment": {"paid": true}}'
    set_genei_state(order, {"shipment_code": "GEN1"})
    import json
    data = json.loads(order.packing_json)
    assert data["factusol_payment"] == {"paid": True}   # no se pisa
    assert data["genei"]["shipment_code"] == "GEN1"
