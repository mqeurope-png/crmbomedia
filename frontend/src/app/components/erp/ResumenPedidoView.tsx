"use client";

import type { ResumenPedido } from "../../lib/erpApi";

function fmtFecha(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return y && m && d ? `${d}/${m}/${y}` : iso;
}

function eur(n: number): string {
  return `${n.toFixed(2)} €`;
}

/** Lo que se va a crear al confirmar «Crear pedido»: empresa, fecha, origen,
 *  los conceptos con sus cantidades e importes y el total. Viene del servidor,
 *  calculado con el mismo reparto de líneas y el mismo importe que la
 *  creación, así que lo que se ve es lo que se crea. */
export function ResumenPedidoView({
  resumen,
  empresa,
}: {
  resumen: ResumenPedido;
  /** La empresa con la que se va a crear, si la elige quien abre el modal
   *  (el cruce CRM de la fila). Si no, la que resuelve el servidor. */
  empresa?: string | null;
}) {
  const nombreEmpresa = empresa ?? resumen.company_name;
  const o = resumen.origen;
  return (
    <div className="pl-resumen-pedido" role="region" aria-label="Resumen del pedido">
      <dl>
        <dt>Empresa</dt>
        <dd>{nombreEmpresa ? <strong>{nombreEmpresa}</strong> : <span className="form-error">Sin empresa del CRM vinculada</span>}</dd>
        <dt>Fecha</dt>
        <dd className="mono">{fmtFecha(resumen.fecha)}</dd>
        <dt>Origen</dt>
        <dd>
          {o.label.charAt(0).toUpperCase() + o.label.slice(1)}{" "}
          <span className="mono">{o.numero}</span>
          {o.referencia ? <span className="muted"> · {o.referencia}</span> : null}
        </dd>
      </dl>
      <table className="data-table" aria-label="Conceptos del pedido">
        <thead>
          <tr>
            <th scope="col">Concepto</th>
            <th scope="col" className="num">Cant.</th>
            <th scope="col" className="num">Precio</th>
            <th scope="col" className="num">Dto.</th>
            <th scope="col" className="num">Importe s/IVA</th>
          </tr>
        </thead>
        <tbody>
          {resumen.lineas.map((l, i) => {
            const texto = l.quantity === 0 && l.unit_price === 0;
            return (
              <tr key={i}>
                <td>
                  {l.product_sku ? <span className="mono small">{l.product_sku} · </span> : null}
                  {l.description}
                </td>
                <td className="num">{texto ? "" : l.quantity}</td>
                <td className="num">{texto ? "" : eur(l.unit_price)}</td>
                <td className="num">{l.discount_pct ? `${l.discount_pct} %` : ""}</td>
                <td className="num">{texto ? "" : eur(l.line_total)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="erp-manual-total">
        Importe (IVA incl.): <strong>{eur(resumen.total)}</strong>
      </p>
    </div>
  );
}
