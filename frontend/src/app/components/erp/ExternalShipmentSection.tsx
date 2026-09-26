"use client";

import { useEffect, useState } from "react";
import { OTHER_COURIER_LABEL } from "../../lib/couriers";
import { getOrderShipment, type OrderShipmentInfo } from "../../lib/erpApi";
import { carrierDate } from "../../lib/geneiApi";
import { CourierTrackingEditor } from "./CourierFields";
import { CustomerEmailBlock, GeneiShipmentSection } from "./GeneiShipmentSection";

/** Transporte con el paquete ya fuera (marcado recogido o posterior). */
const SHIPPED = ["in_transit", "delivered", "already_shipped_externally"];

/** «Enviado · UPS» / «Entregado · UPS» / «Enviado · otro courier». */
export function externalShipmentLabel(info: Pick<OrderShipmentInfo, "courier" | "transport_status">): string {
  const courier = info.courier || OTHER_COURIER_LABEL;
  return info.transport_status === "delivered" ? `Entregado · ${courier}` : `Enviado · ${courier}`;
}

/** Envío de la ficha: el de Genei (`GeneiShipmentSection`) o, si el paquete
 *  salió con OTRO courier (UPS, MRW… apuntado a mano), su bloque propio — y
 *  entonces sin «Envío con Genei», que no lo hay. */
export function OrderShipmentSections({
  orderId, canManage, onChanged,
}: {
  orderId: string;
  canManage: boolean;
  onChanged?: () => void;
}) {
  const [info, setInfo] = useState<OrderShipmentInfo | null>(null);
  // Si no se puede saber, se enseña lo de siempre (la sección de Genei).
  const [failed, setFailed] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let alive = true;
    // Cualquier fallo (incluso síncrono) acaba en «se enseña lo de siempre».
    Promise.resolve()
      .then(() => getOrderShipment(orderId))
      .then((i) => { if (alive) setInfo(i); })
      .catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
  }, [orderId, reloadKey]);

  const reload = () => { setReloadKey((n) => n + 1); onChanged?.(); };

  if (!info && !failed) return null;
  const external = info?.kind === "externo";
  const shippedExternally = external && SHIPPED.includes(info?.transport_status ?? "");
  return (
    <>
      {info && external ? (
        <ExternalShipmentSection
          orderId={orderId} info={info} canManage={canManage}
          onChanged={reload}
        />
      ) : null}
      {/* Genei, salvo que el paquete ya saliera con otro courier. */}
      {shippedExternally ? null : (
        <GeneiShipmentSection orderId={orderId} canManage={canManage} onChanged={reload} />
      )}
    </>
  );
}

/** Envío con OTRO courier (no Genei): courier, nº de seguimiento enlazado a la
 *  web del courier (si se conoce), fecha de recogida y el aviso al cliente.
 *  Courier y seguimiento se corrigen aquí (✎) sin volver a «Marcar recogido». */
export function ExternalShipmentSection({
  orderId, info, canManage, onChanged,
}: {
  orderId: string;
  info: OrderShipmentInfo;
  canManage: boolean;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const shipped = SHIPPED.includes(info.transport_status);
  const pickedUp = carrierDate(info.picked_up_at);

  return (
    <section className="erp-genei erp-ext-shipment" aria-label="Envío con otro courier">
      <div className="erp-genei-head">
        <h4>Envío con otro courier</h4>
        {shipped ? (
          <span className="badge courier-ext">{externalShipmentLabel(info)}</span>
        ) : null}
      </div>
      <div className="erp-genei-state">
        <div className="erp-flow-kv"><span className="k">Courier</span>
          <span className="v">{info.courier || "Sin indicar (otro courier)"}</span></div>
        <div className="erp-flow-kv"><span className="k">Seguimiento</span>
          <span className="v mono">
            {info.tracking && info.tracking_url ? (
              <a href={info.tracking_url} target="_blank" rel="noopener noreferrer">
                {info.tracking}
              </a>
            ) : (info.tracking ?? "—")}
          </span></div>
        {pickedUp ? (
          <div className="erp-flow-kv"><span className="k">Recogido</span>
            <span className="v">{pickedUp}</span></div>
        ) : null}
        {canManage ? (
          editing ? (
            <CourierTrackingEditor
              orderId={orderId} tracking={info.tracking} courier={info.courier}
              onSaved={() => { setEditing(false); onChanged(); }}
              onCancel={() => setEditing(false)}
            />
          ) : (
            <div className="erp-genei-actions">
              <button type="button" className="button small secondary"
                      onClick={() => setEditing(true)}>
                ✎ Editar courier / seguimiento
              </button>
            </div>
          )
        ) : null}
        {shipped ? (
          <CustomerEmailBlock
            orderId={orderId}
            status={info.customer_email ?? null}
            hasTracking={Boolean(info.tracking)}
            canManage={canManage}
            onSent={() => onChanged()}
          />
        ) : null}
      </div>
    </section>
  );
}
