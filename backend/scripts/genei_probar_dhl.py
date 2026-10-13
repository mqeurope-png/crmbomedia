"""Prueba el arreglo de DHL SIN GASTAR UN EURO. Lo ejecuta Bart.

El problema (verificado en vivo el 13/10/2026): los envíos de DHL creados por
BoHub caían 7→6→1→**11** («pendiente de abono», anulación con devolución) ellos
solos, mientras CTT y UPS iban bien. La única diferencia con el envío bueno
creado en el panel era el NIF del remitente (`dni_salida`): la dirección
registrada en Genei lo devuelve vacío por API y DHL anula el envío sin él. El
arreglo manda ese NIF (config `origin.vat`, Ajustes → Envíos).

Cómo se prueba sin pagar: un envío de Genei nace en estado **7 (pendiente de
pago)** y solo se cobra al pagarlo. Así que se crea el envío de DHL con el
arreglo y se mira su estado:

  - Se queda en **7** → los campos que faltaban eran esos. DESPUÉS, y solo
    entonces, Bart paga a mano UNO para confirmar que llega a tramitado (1).
  - Cae a **11** → no era el NIF; hay que probar el siguiente campo
    (ver «Pendientes» del PR). No se ha gastado nada.

Este script NO paga, NO borra y NO vincula nada. Por defecto solo ENSEÑA el
payload (no llama a Genei); con `--crear` crea el envío (nace en 7, sin coste) y
enseña su estado.

Uso (VPS), con el mismo destino que falló (ARTISJ-9700):

    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.genei_probar_dhl --pedido ARTISJ-9700            # solo enseña
    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \\
        python -m scripts.genei_probar_dhl --pedido ARTISJ-9700 --crear    # crea (nace en 7)

Opciones: `--agencia` (id de la agencia de Genei; 102 = DHL Parcel B2C Dom-Dom,
la variante que se usó), `--nif` (fuerza el NIF del remitente; por defecto el de
Ajustes).
"""
from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

# DHL Parcel B2C Dom-Dom: la variante «domicilio-personalizable» que se usó en
# ARTISJ-9700 (grupo «DHL Recogida Domicilio»). 103 = Dom-Ofi, 226 = Ofi-Dom.
DHL_DOM_DOM = "102"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pedido", required=True, metavar="NUM",
                        help="nº de pedido de BoHub (destino y bultos salen de aquí)")
    parser.add_argument("--agencia", default=DHL_DOM_DOM, metavar="ID",
                        help=f"id de la agencia de Genei (por defecto {DHL_DOM_DOM} = DHL Dom-Dom)")
    parser.add_argument("--nif", default=None, metavar="NIF",
                        help="NIF del remitente (por defecto, el de Ajustes → Envíos)")
    parser.add_argument("--crear", action="store_true",
                        help="crea el envío de verdad (NACE EN 7, PENDIENTE DE PAGO: sin coste). "
                             "Sin esto, solo enseña el payload.")
    args = parser.parse_args(argv)

    from app.db.session import get_engine
    from app.erp.api.genei import build_client
    from app.erp.integrations.genei.client import GeneiClient
    from app.erp.integrations.genei.config import GeneiConfig
    from app.erp.integrations.genei.service import (
        build_destination,
        build_origin,
        build_shipment_payload,
        destination_is_complete,
        state_of,
        summarize_shipment,
    )
    from app.erp.models import Order
    from app.erp.models.carriers import Carrier
    from app.erp.models.shipping import ShipmentPackage
    from app.erp.shipping_destination import resolve_shipping_destination

    with Session(get_engine()) as session:
        carrier = session.scalar(select(Carrier).where(Carrier.code == "genei"))
        if carrier is None or not carrier.api_credentials_encrypted:
            print("Genei no está configurado (Ajustes → Envíos).")
            return 2
        cfg = GeneiConfig.of(carrier)
        order = session.scalar(select(Order).where(Order.order_number == args.pedido))
        if order is None:
            print(f"No encuentro el pedido {args.pedido}.")
            return 2

        campos, _ = resolve_shipping_destination(session, order, completar=True)
        dest = build_destination(campos)
        falta = destination_is_complete(dest)
        if falta:
            print(f"El destino del pedido está incompleto: {', '.join(falta)}. Complétalo "
                  "en la ficha antes de probar.")
            return 2
        packages = [
            {"weight": float(p.weight_kg), "height": float(p.height_cm),
             "width": float(p.width_cm), "length": float(p.depth_cm)}
            for p in session.scalars(
                select(ShipmentPackage).where(ShipmentPackage.order_id == order.id)
                .order_by(ShipmentPackage.position.asc()))
        ] or [cfg.default_package.as_package()]

        client = build_client(carrier)
        nif = args.nif if args.nif is not None else cfg.origin.vat
        origin = build_origin(client.get_address(carrier.default_address_id), vat_override=nif)
        if not origin.get("dni"):
            print("⚠️  El remitente sale SIN NIF (ni en la dirección de Genei ni en Ajustes). "
                  "Pon el NIF de la empresa en Ajustes → Envíos (o pásalo con --nif) antes de "
                  "probar: es justo lo que DHL exige.")
            return 2
        payload = build_shipment_payload(
            agency_id=str(args.agencia), origin=origin, destination=dest, packages=packages,
            external_shipping_code=order.order_number or order.id,
            notification_url=cfg.webhook_url(
                GeneiClient.webhook_secret_of(carrier.api_credentials_encrypted)),
        )
        print("Payload que BoHub mandaría (fíjate en origin.dni = NIF del remitente):")
        print(json.dumps({**payload, "origin": {**origin, "dni": origin["dni"]}},
                         ensure_ascii=False, indent=1))

        if not args.crear:
            print("\n(Solo enseñado. Añade --crear para crearlo de verdad; nace en 7, sin coste.)")
            return 0

        print("\nCreando el envío (nace en 7, pendiente de pago: SIN COSTE)…")
        created = client.create_shipment(payload)
        summary = summarize_shipment(created)
        code = summary["shipment_code"]
        print(f"Creado: {code}  (referencia externa {payload['externalShippingCode']})")
        # Leer el estado real (la creación no lo trae).
        import time  # noqa: PLC0415
        time.sleep(5)
        raw = client.get_shipment(code)
        st = state_of(summarize_shipment(raw)["state_code"])
        print(f"Estado ahora: {st.code} «{st.label}» (bucket {st.bucket})")
        if st.bucket == "cancelled":
            print("\n❌ Ha caído a ANULADO (11/12): el NIF del remitente NO era (todo) lo que "
                  "faltaba. No se ha pagado nada. Prueba el siguiente campo (Pendientes del PR). "
                  f"Elimínalo cuando quieras: DELETE /shipments/{code} (o desde el panel).")
        elif st.code in (7, 6, 1):
            print("\n✅ Se mantiene vivo (7/6/1): el NIF del remitente era lo que faltaba. "
                  "Para CONFIRMAR, paga este UNO a mano (web/panel de Genei) y mira que llega a "
                  "tramitado (1). Si no lo quieres, elimínalo (nace sin coste).")
        else:
            print(f"\n⚠️  Estado inesperado ({st.code}). Míralo en el panel de Genei.")
        print("\nEste script NO ha pagado, NO ha borrado y NO ha vinculado nada.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
