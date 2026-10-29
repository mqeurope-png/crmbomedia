"use client";

import { useCallback, useEffect, useState } from "react";
import { CompanySearch } from "../CompanySearch";
import { extractErrorMessage } from "../../lib/errors";
import {
  getClienteWebPedido,
  vincularEmpresaPedido,
  type ClienteWebPedido,
  type PropuestaEmpresaPedido,
} from "../../lib/erpApi";

/** Id del panel: el aviso de la barra y «Siguiente paso» llevan hasta aquí. */
export const VINCULAR_EMPRESA_PANEL_ID = "vincular-empresa-pedido";

const ORIGEN_LABEL: Record<PropuestaEmpresaPedido["origen"], string> = {
  pedido_factusol: "pedido de cliente en FACTUSOL",
  nif_factusol: "NIF en FACTUSOL",
  nif: "NIF en BoHub",
};

/**
 * Pedido web que entró sin cliente (BOPRIN-100015, 28/10/2026): qué le
 * falta, la empresa que se propone si hay con qué (el pedido de cliente de
 * FACTUSOL con su referencia, o el NIF que trajo la web) y un buscador para
 * vincular cualquier otra. Hasta ahora no había forma de hacerlo desde el
 * pedido: se arregló por SQL. Nunca se vincula sola: decide una persona, y
 * queda en el historial del pedido. No escribe en FACTUSOL.
 */
export function VincularEmpresaPedidoPanel({
  orderId,
  canEdit,
  onLinked,
}: {
  orderId: string;
  canEdit: boolean;
  onLinked: () => void;
}) {
  const [data, setData] = useState<ClienteWebPedido | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [buscar, setBuscar] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await getClienteWebPedido(orderId));
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo comprobar el cliente del pedido."));
    }
  }, [orderId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function vincular(body: { company_id?: string; codcli?: string; origen: string }) {
    setBusy(true);
    setError(null);
    try {
      await vincularEmpresaPedido(orderId, body);
      onLinked();
    } catch (err) {
      setError(extractErrorMessage(err, "No se pudo vincular la empresa."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className="erp-card vincular-empresa-pedido"
      id={VINCULAR_EMPRESA_PANEL_ID}
      aria-label="Cliente del pedido"
    >
      <h3>Cliente del pedido</h3>
      {data && data.faltan_texto.length > 0 ? (
        <p className="form-warning">
          Pedido web {data.faltan_texto.join(", ").replace(/, ([^,]*)$/, " y $1")}: hasta
          que tenga empresa no se sabe a quién facturarlo.
        </p>
      ) : null}
      {data && (data.nombre || data.email || data.nif) ? (
        <p className="muted small">
          La web trae: {[data.nombre, data.email, data.nif ? `NIF ${data.nif}` : null]
            .filter(Boolean)
            .join(" · ")}
        </p>
      ) : null}
      {error ? <p className="form-error">{error}</p> : null}
      {!data && !error ? <p className="muted small">Comprobando…</p> : null}

      {data && data.propuestas.length > 0 ? (
        <ul className="vincular-empresa-propuestas" aria-label="Empresas propuestas">
          {data.propuestas.map((p) => (
            <li key={p.company_id ?? `codcli-${p.codcli}`}>
              <div>
                <strong>{p.nombre}</strong>
                {p.nif ? <span className="muted small"> · {p.nif}</span> : null}
                {p.codcli ? <span className="muted small"> · FACTUSOL {p.codcli}</span> : null}{" "}
                <span className="badge info small">{ORIGEN_LABEL[p.origen]}</span>
                <div className="muted small">{p.motivo}</div>
              </div>
              {canEdit ? (
                <button
                  type="button"
                  className="button small"
                  disabled={busy || (!p.company_id && p.sin_nif)}
                  title={!p.company_id && p.sin_nif
                    ? "El cliente de FACTUSOL no tiene NIF: búscalo o créalo a mano."
                    : undefined}
                  onClick={() => void vincular(
                    p.company_id
                      ? { company_id: p.company_id, origen: p.origen }
                      : { codcli: p.codcli ?? undefined, origen: p.origen },
                  )}
                >
                  Vincular
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      {data && data.propuestas.length === 0 && data.mensaje ? (
        <p className="muted">{data.mensaje}</p>
      ) : null}

      {canEdit ? (
        buscar ? (
          <div className="vincular-empresa-buscar">
            <CompanySearch
              label="Buscar empresa"
              autoFocus
              initialQuery={data?.nif ?? ""}
              onPick={(c) => void vincular({ company_id: c.id, origen: "busqueda" })}
            />
            <button type="button" className="button small secondary" onClick={() => setBuscar(false)}>
              Cancelar
            </button>
          </div>
        ) : (
          <button
            type="button"
            className="button small secondary"
            disabled={busy}
            onClick={() => setBuscar(true)}
          >
            {data && data.propuestas.length > 0 ? "Buscar otra empresa" : "Buscar la empresa"}
          </button>
        )
      ) : (
        <p className="muted small">Vincular la empresa es de quien edita pedidos.</p>
      )}
    </section>
  );
}
