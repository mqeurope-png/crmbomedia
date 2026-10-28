"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Fragment, useEffect, useRef, useState } from "react";
import { parseBackendDate } from "../../lib/dates";
import { extractErrorMessage } from "../../lib/errors";
import {
  coincidePedidoWeb,
  errorAlTraer,
  listarPedidosWebEnEspera,
  numeroAConsultar,
  traerPedidoWeb,
  type PedidoWebConsulta,
  type PedidoWebEnEspera,
  type PedidosWebEnEspera,
} from "../../lib/pedidosWebApi";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { ErpModalShell } from "./ErpModalShell";

const TITULO = "Traer un pedido de la web";
/** Lo que se espera a que se deje de teclear un nº antes de preguntar a las
 *  tiendas qué es (cada consulta relee las tiendas: no en cada tecla). */
const ESPERA_CONSULTA_MS = 500;

function fecha(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = parseBackendDate(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("es-ES", { day: "2-digit", month: "2-digit", year: "numeric" });
}

function importe(p: PedidoWebEnEspera): string {
  return `${Number(p.total ?? 0).toFixed(2)} ${p.moneda || "EUR"}`;
}

/** Una fila = un pedido de una tienda (el mismo nº puede estar en dos). */
function clave(p: PedidoWebEnEspera): string {
  return `${p.store_id}:${p.woo_id}`;
}

type ErrorFila = { mensaje: string; orderId: string | null };
type Consulta = { items: PedidoWebConsulta[]; error: string | null };

/** «Traer un pedido de la web» (ERP · Pedidos): los pedidos «en espera» de
 *  las tiendas (transferencia que la web aún no ha cobrado), leídos en vivo.
 *  Se elige uno y se trae: queda en BoHub como un pedido normal aunque no
 *  esté cobrado, y cuando la tienda lo cobre se actualiza solo (el webhook de
 *  siempre lo reconoce: no crea otro). Al traerlo se abre el pedido, que dice
 *  qué ha encontrado en FACTUSOL. Nada de esto escribe en las tiendas ni en
 *  FACTUSOL. */
export function TraerPedidoWebModal({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const [data, setData] = useState<PedidosWebEnEspera | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [recarga, setRecarga] = useState(0);
  const [q, setQ] = useState("");
  // Lo que dicen las tiendas de cada nº buscado que no está en la lista.
  const [consultas, setConsultas] = useState<Record<string, Consulta>>({});
  const pedidas = useRef<Set<string>>(new Set());
  // La fila que se está trayendo (todas las demás esperan) y los errores por fila.
  const [trayendo, setTrayendo] = useState<string | null>(null);
  const [errores, setErrores] = useState<Record<string, ErrorFila>>({});
  // Un solo «Traer» aunque se pulse dos veces antes de que React repinte.
  const enviado = useRef(false);

  // El modal se monta al abrirlo: `loading` arranca en true y solo se apaga
  // aquí (nada de setState síncrono dentro del efecto).
  useEffect(() => {
    let alive = true;
    listarPedidosWebEnEspera()
      .then((r) => { if (alive) setData(r); })
      .catch((e) => {
        if (alive) {
          setLoadError(extractErrorMessage(e, "No se pudieron leer los pedidos en espera de las tiendas."));
        }
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [recarga]);

  const items = data?.items ?? [];
  // El nº que hay que consultar a las tiendas: con el texto ya estable (para
  // pedir) y con el de ahora (para enseñar solo lo que corresponde).
  const qEstable = useDebouncedValue(q, ESPERA_CONSULTA_MS);
  const numeroPedir = data ? numeroAConsultar(items, qEstable) : null;
  const numeroAhora = data ? numeroAConsultar(items, q) : null;

  useEffect(() => {
    if (!numeroPedir || pedidas.current.has(numeroPedir)) return;
    pedidas.current.add(numeroPedir);
    const numero = numeroPedir;
    listarPedidosWebEnEspera(numero)
      .then((r) => setConsultas((prev) => ({ ...prev, [numero]: { items: r.consulta ?? [], error: null } })))
      .catch((e) => setConsultas((prev) => ({
        ...prev,
        [numero]: { items: [], error: extractErrorMessage(e, "No se pudo consultar ese nº en las tiendas.") },
      })));
  }, [numeroPedir]);

  function volverALeer() {
    setLoading(true);
    setLoadError(null);
    setData(null);
    setConsultas({});
    setErrores({});
    pedidas.current.clear();
    setRecarga((n) => n + 1);
  }

  async function traer(p: PedidoWebEnEspera) {
    if (enviado.current) return;
    enviado.current = true;
    const k = clave(p);
    setTrayendo(k);
    setErrores((prev) => {
      const next = { ...prev };
      delete next[k];
      return next;
    });
    try {
      const r = await traerPedidoWeb(p.store_id, p.woo_id);
      // Se queda deshabilitado: la pantalla pasa al pedido (que enseña el
      // veredicto de FACTUSOL).
      router.push(`/erp/orders/${r.order_id}`);
    } catch (e) {
      enviado.current = false;
      setTrayendo(null);
      const { mensaje, orderId } = errorAlTraer(e);
      setErrores((prev) => ({ ...prev, [k]: { mensaje, orderId } }));
    }
  }

  function acciones(p: PedidoWebEnEspera, sePuedeTraer: boolean) {
    if (p.en_bohub) {
      return (
        <span className="pedidos-web-acciones">
          <span className="pedidos-web-ya">Ya está en BoHub</span>
          <Link href={`/erp/orders/${p.en_bohub.order_id}`} className="button small secondary"
                aria-label={`Abrir ${p.en_bohub.order_number}`}>
            Abrir
          </Link>
        </span>
      );
    }
    if (!sePuedeTraer) return null;
    const k = clave(p);
    return (
      <span className="pedidos-web-acciones">
        <button
          type="button" className="button small"
          aria-label={`Traer el pedido ${p.numero} de ${p.tienda_nombre}`}
          disabled={trayendo !== null}
          onClick={() => void traer(p)}
        >
          {trayendo === k ? "Trayendo…" : "Traer"}
        </button>
      </span>
    );
  }

  function avisoFila(p: PedidoWebEnEspera) {
    const err = errores[clave(p)];
    if (!err) return null;
    return (
      <p className="form-error pedidos-web-error" role="alert">
        {err.mensaje}
        {err.orderId ? (
          <>
            {" "}
            <Link href={`/erp/orders/${err.orderId}`} className="button small secondary">
              Abrir
            </Link>
          </>
        ) : null}
      </p>
    );
  }

  function cliente(p: PedidoWebEnEspera) {
    const principal = p.empresa || p.cliente || p.email || "—";
    const segundo = p.empresa && p.cliente ? p.cliente : null;
    return (
      <span className="pedidos-web-cliente">
        <span>{principal}</span>
        {segundo ? <span className="muted small">{segundo}</span> : null}
        {p.email && principal !== p.email ? <span className="muted small">{p.email}</span> : null}
      </span>
    );
  }

  function numero(p: PedidoWebEnEspera) {
    return p.enlace_tienda ? (
      <a href={p.enlace_tienda} target="_blank" rel="noopener noreferrer" className="mono"
         title={`Ver el pedido ${p.numero} en ${p.tienda_nombre}`}>
        {p.numero}
      </a>
    ) : (
      <span className="mono">{p.numero}</span>
    );
  }

  const filtrados = items.filter((p) => coincidePedidoWeb(p, q));
  const caidas = (data?.tiendas ?? []).filter((t) => !t.ok);
  const leidas = (data?.tiendas ?? []).filter((t) => t.ok);
  const consulta = numeroAhora ? consultas[numeroAhora] : undefined;

  return (
    <ErpModalShell
      label={TITULO}
      title={TITULO}
      onClose={onClose}
      disabled={trayendo !== null}
      dialogClassName="erp-modal wide pedidos-web-modal"
    >
      <p className="muted small">
        Son los pedidos «en espera» de las tiendas: los de transferencia que la web aún no
        ha cobrado. Traerlo lo deja en BoHub como un pedido normal (se aprueba, se prepara y
        se envía) aunque aún no esté cobrado; cuando la tienda lo cobre, se actualiza solo.
      </p>

      <label className="field">
        <span>Buscar</span>
        <input
          type="search"
          value={q}
          placeholder="Nº de pedido, cliente, empresa o email"
          aria-label="Buscar por nº de pedido, cliente, empresa o email"
          disabled={loading && !data}
          onChange={(e) => setQ(e.target.value)}
        />
      </label>

      {loading ? (
        <p className="muted" role="status">Leyendo los pedidos en espera de las tiendas…</p>
      ) : null}
      {loadError ? <p className="form-error" role="alert">{loadError}</p> : null}

      {caidas.length > 0 ? (
        <div className="pedidos-web-caidas" role="alert">
          <ul>
            {caidas.map((t) => (
              <li key={String(t.store_id)}>
                {t.tienda_nombre} no responde{t.error ? ` (${t.error})` : ""}
              </li>
            ))}
          </ul>
          <span>Sus pedidos no salen en la lista: vuelve a leer las tiendas en un rato.</span>
        </div>
      ) : null}

      {data && leidas.length > 0 ? (
        <p className="muted small">
          En espera ahora:{" "}
          {leidas.map((t) => `${t.tienda_nombre} (${t.en_espera})`).join(" · ")}
        </p>
      ) : null}

      {data && filtrados.length > 0 ? (
        <div className="pedidos-web-tabla-wrap">
          <table className="data-table data-table--responsive pedidos-web-tabla"
                 aria-label="Pedidos en espera de las tiendas">
            <thead>
              <tr>
                <th scope="col">Tienda</th>
                <th scope="col">Nº</th>
                <th scope="col">Fecha</th>
                <th scope="col">Cliente o empresa</th>
                <th scope="col" className="pedidos-web-num">Importe</th>
                <th scope="col">Forma de pago</th>
                <th scope="col"><span className="sr-only">Acción</span></th>
              </tr>
            </thead>
            <tbody>
              {filtrados.map((p) => {
                const aviso = avisoFila(p);
                return (
                  <Fragment key={clave(p)}>
                    <tr className={p.en_bohub ? "pedidos-web-fila-ya" : undefined}>
                      <td data-label="Tienda">{p.tienda_nombre}</td>
                      <td data-label="Nº">{numero(p)}</td>
                      <td data-label="Fecha" className="mono">{fecha(p.fecha)}</td>
                      <td data-label="Cliente o empresa">{cliente(p)}</td>
                      <td data-label="Importe" className="mono pedidos-web-num">{importe(p)}</td>
                      <td data-label="Forma de pago">{p.forma_pago || "—"}</td>
                      <td>{acciones(p, true)}</td>
                    </tr>
                    {aviso ? (
                      <tr className="pedidos-web-fila-aviso">
                        <td colSpan={7}>{aviso}</td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {data && !loading && items.length === 0 ? (
        <p className="muted">
          {caidas.length > 0
            ? "No hay pedidos en espera en las tiendas que han respondido."
            : "No hay pedidos en espera en las tiendas."}
        </p>
      ) : null}
      {data && items.length > 0 && filtrados.length === 0 && q.trim() ? (
        <p className="muted">Ningún pedido en espera coincide con «{q.trim()}».</p>
      ) : null}

      {/* Un nº que no está en espera: qué es en cada tienda (cancelado,
          carrito sin pagar, ya en BoHub, procesando…). */}
      {numeroAhora ? (
        <section className="pedidos-web-consulta" aria-label={`El nº ${numeroAhora} en las tiendas`}>
          {!consulta ? (
            <p className="muted small" role="status">
              Buscando el nº {numeroAhora} en las tiendas…
            </p>
          ) : consulta.error ? (
            <p className="form-error" role="alert">{consulta.error}</p>
          ) : consulta.items.length === 0 ? (
            <p className="muted small">
              No hay ningún pedido con el nº {numeroAhora} en las tiendas que han respondido.
            </p>
          ) : (
            <ul className="pedidos-web-consulta-lista">
              {consulta.items.map((c) => (
                <li key={clave(c)}>
                  <div className="pedidos-web-consulta-main">
                    <span>
                      <strong>{c.tienda_nombre}</strong> · nº {numero(c)}
                      {c.empresa || c.cliente ? ` · ${c.empresa || c.cliente}` : ""}
                      {" · "}<span className="mono">{importe(c)}</span>
                    </span>
                    <span className="pedidos-web-motivo">{c.motivo}</span>
                  </div>
                  {acciones(c, Boolean(c.se_puede_traer))}
                  {avisoFila(c)}
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : null}

      <div className="modal-actions">
        <button type="button" className="button secondary"
                disabled={loading || trayendo !== null} onClick={volverALeer}>
          Volver a leer las tiendas
        </button>
        <button type="button" className="button secondary"
                disabled={trayendo !== null} onClick={onClose}>
          Cerrar
        </button>
      </div>
    </ErpModalShell>
  );
}
