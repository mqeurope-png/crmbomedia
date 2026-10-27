"use client";

import { AlertTriangle, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  getPlantillaLineas,
  getPlantillasMeta,
  listPlantillaCarpetas,
  listPlantillaEtiquetas,
  listPlantillas,
  type PlantillaCarpetaNode,
  type PlantillaCliente,
  type PlantillaDestino,
  type PlantillaDocumento,
  type PlantillaEtiqueta,
  type PlantillaLineasPorDestino,
  type PlantillaListResult,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";
import { ErpModalShell } from "./ErpModalShell";
import { PlantillaCarpetasTree } from "./PlantillaCarpetasTree";
import {
  CARPETA_TODAS,
  DESTINO_LABEL,
  filtroCarpeta,
  findCarpeta,
  IDIOMAS_PLANTILLA,
} from "./plantillasUi";

export type PlantillaElegida<D extends PlantillaDestino> = {
  plantilla: PlantillaDocumento;
  lineas: PlantillaLineasPorDestino[D][];
  avisos: string[];
};

function idiomaNombre(code: string | null | undefined): string {
  const hit = IDIOMAS_PLANTILLA.find((l) => l.value === code);
  return hit ? hit.label.split(" · ")[1] ?? hit.label : (code ?? "").toUpperCase();
}

/** Elegir una plantilla de documento en el punto de uso (crear oferta,
 *  proforma o pedido). No es un desplegable plano: carpetas (se abre en la
 *  carpeta por defecto del usuario), buscador que mira DENTRO de las líneas
 *  (referencia y texto), filtro por etiquetas y, con un cliente, primero las
 *  plantillas de su idioma; las de otros idiomas siguen ahí, con aviso.
 *  Al elegir pide las líneas en la forma del destino y se las da al
 *  formulario de siempre (la plantilla solo aporta las líneas). */
export function PlantillaSelector<D extends PlantillaDestino>({
  destino,
  cliente,
  onClose,
  onPick,
}: {
  destino: D;
  /** Para poner primero las de su idioma. */
  cliente?: PlantillaCliente | null;
  onClose: () => void;
  onPick: (elegida: PlantillaElegida<D>) => void;
}) {
  const [arbol, setArbol] = useState<PlantillaCarpetaNode[]>([]);
  const [restringido, setRestringido] = useState(false);
  const [carpeta, setCarpeta] = useState<string | null>(null);
  const [etiquetas, setEtiquetas] = useState<PlantillaEtiqueta[]>([]);
  const [filtroEtq, setFiltroEtq] = useState<string[]>([]);
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [res, setRes] = useState<PlantillaListResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [picking, setPicking] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const contactId = cliente?.contact_id ?? null;
  const companyId = cliente?.company_id ?? null;
  const orderId = cliente?.order_id ?? null;
  const serie = cliente?.serie ?? null;

  // Carpetas, etiquetas y la carpeta por defecto (donde se abre).
  useEffect(() => {
    let alive = true;
    Promise.all([
      getPlantillasMeta().catch(() => null),
      listPlantillaCarpetas().catch(() => ({ items: [], carpeta_defecto: null })),
      listPlantillaEtiquetas().catch(() => []),
    ]).then(([meta, carpetas, etqs]) => {
      if (!alive) return;
      setArbol(carpetas.items);
      setEtiquetas(etqs);
      setRestringido(Boolean(meta?.acceso_restringido));
      const defecto = carpetas.carpeta_defecto;
      setCarpeta(defecto && findCarpeta(carpetas.items, defecto) ? defecto : CARPETA_TODAS);
    });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedQ(q.trim()), 300);
    return () => window.clearTimeout(t);
  }, [q]);

  useEffect(() => {
    if (carpeta === null) return;
    let alive = true;
    setLoading(true);
    listPlantillas({
      ...filtroCarpeta(carpeta), q: debouncedQ, etiquetas: filtroEtq, activas: true,
      contact_id: contactId, company_id: companyId, order_id: orderId, serie,
    })
      .then((r) => { if (alive) { setRes(r); setError(null); } })
      .catch((e) => { if (alive) setError(extractErrorMessage(e, "No se pudieron cargar las plantillas.")); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [carpeta, debouncedQ, filtroEtq, contactId, companyId, orderId, serie]);

  const idiomaCliente = res?.idioma_cliente ?? null;
  const { suyas, otras } = useMemo(() => {
    const items = res?.items ?? [];
    if (!idiomaCliente) return { suyas: items, otras: [] as PlantillaDocumento[] };
    return {
      suyas: items.filter((p) => p.idioma_coincide),
      otras: items.filter((p) => !p.idioma_coincide),
    };
  }, [res, idiomaCliente]);

  async function elegir(p: PlantillaDocumento) {
    if (picking) return;
    if (idiomaCliente && p.idioma !== idiomaCliente && !window.confirm(
      `«${p.nombre}» está en ${idiomaNombre(p.idioma)} y el cliente en `
      + `${idiomaNombre(idiomaCliente)}. ¿Usarla igualmente?`,
    )) {
      return;
    }
    setPicking(p.id);
    setError(null);
    try {
      const r = await getPlantillaLineas(p.id, destino);
      onPick({ plantilla: r.plantilla, lineas: r.lineas, avisos: r.avisos });
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron cargar las líneas de la plantilla."));
      setPicking(null);
    }
  }

  function toggleEtiqueta(norm: string) {
    setFiltroEtq((cur) => (cur.includes(norm) ? cur.filter((x) => x !== norm) : [...cur, norm]));
  }

  const enCarpeta = carpeta !== null && carpeta !== CARPETA_TODAS;
  const total = res?.items.length ?? 0;

  return (
    <ErpModalShell
      label={`Elegir plantilla para la ${DESTINO_LABEL[destino]}`}
      title={`Cargar plantilla en la ${DESTINO_LABEL[destino]}`}
      onClose={onClose}
      disabled={picking !== null}
      dialogClassName="erp-modal modal-wide pl-selector"
    >
      <div className="pl-sel-layout">
        <aside className="pl-sel-tree">
          <PlantillaCarpetasTree nodes={arbol} selected={carpeta ?? CARPETA_TODAS}
                                 onSelect={setCarpeta} showSinCarpeta={!restringido} />
        </aside>
        <div className="pl-sel-main">
          <div className="et-search pl-sel-search">
            <Search size={13} aria-hidden />
            <input type="search" value={q} autoFocus aria-label="Buscar plantilla"
                   placeholder="Nombre, referencia (DM3H) o texto de una línea (horno)…"
                   onChange={(e) => setQ(e.target.value)} />
          </div>
          {etiquetas.length > 0 ? (
            <div className="pl-sel-etiquetas" role="group" aria-label="Filtrar por etiqueta">
              {etiquetas.map((e) => {
                const on = filtroEtq.includes(e.norm);
                return (
                  <button key={e.norm} type="button" aria-pressed={on}
                          className={`chip pl-etq-filtro${on ? " is-info" : ""}`}
                          onClick={() => toggleEtiqueta(e.norm)}>
                    {e.etiqueta} <span className="muted">{e.num}</span>
                  </button>
                );
              })}
            </div>
          ) : null}
          {idiomaCliente ? (
            <p className="muted small" role="note">
              Cliente en <strong>{idiomaNombre(idiomaCliente)}</strong>: primero sus plantillas.
            </p>
          ) : null}
          {error ? <p className="form-error" role="alert">{error}</p> : null}

          {loading && !res ? (
            <p className="muted">Cargando plantillas…</p>
          ) : total === 0 ? (
            <p className="muted">
              No hay plantillas con estos filtros.
              {enCarpeta ? (
                <>
                  {" "}
                  <button type="button" className="link-button" onClick={() => setCarpeta(CARPETA_TODAS)}>
                    Buscar en todas las carpetas
                  </button>
                </>
              ) : null}
            </p>
          ) : (
            <div className="pl-sel-results" aria-busy={loading}>
              {idiomaCliente ? (
                <Grupo titulo={`En ${idiomaNombre(idiomaCliente)} (el idioma del cliente)`}
                       vacio={`No hay plantillas en ${idiomaNombre(idiomaCliente)} con estos filtros.`}
                       items={suyas} picking={picking} onPick={elegir} />
              ) : (
                <Grupo items={suyas} picking={picking} onPick={elegir} />
              )}
              {otras.length > 0 ? (
                <Grupo
                  titulo="Otros idiomas"
                  aviso={`No están en ${idiomaNombre(idiomaCliente)}: revisa los textos antes de enviarlos.`}
                  items={otras} picking={picking} onPick={elegir} otroIdioma
                />
              ) : null}
              {enCarpeta ? (
                <p className="muted small">
                  Solo en esta carpeta y sus subcarpetas.{" "}
                  <button type="button" className="link-button" onClick={() => setCarpeta(CARPETA_TODAS)}>
                    Ver todas
                  </button>
                </p>
              ) : null}
            </div>
          )}
        </div>
      </div>

      <div className="modal-actions">
        <button type="button" className="button secondary" onClick={onClose} disabled={picking !== null}>
          Cancelar
        </button>
      </div>
    </ErpModalShell>
  );
}

function Grupo({
  titulo, aviso, vacio, items, picking, onPick, otroIdioma = false,
}: {
  titulo?: string;
  aviso?: string;
  vacio?: string;
  items: PlantillaDocumento[];
  picking: string | null;
  onPick: (p: PlantillaDocumento) => void;
  otroIdioma?: boolean;
}) {
  return (
    <section className="pl-sel-grupo" aria-label={titulo ?? "Plantillas"}>
      {titulo ? <h3 className="pl-sel-grupo-titulo">{titulo}</h3> : null}
      {aviso ? (
        <p className="form-warning small">
          <AlertTriangle size={12} aria-hidden /> {aviso}
        </p>
      ) : null}
      {items.length === 0 && vacio ? <p className="muted small">{vacio}</p> : null}
      <ul className="pl-sel-list">
        {items.map((p) => (
          <li key={p.id}>
            <button type="button" className={`pl-sel-item${otroIdioma ? " is-otro-idioma" : ""}`}
                    aria-label={`Usar la plantilla ${p.nombre}`}
                    disabled={picking !== null}
                    onClick={() => onPick(p)}>
              <span className="pl-sel-item-head">
                <strong>{p.nombre}</strong>
                <span className={`chip${otroIdioma ? " is-warning" : " is-muted"}`}
                      title={idiomaNombre(p.idioma)}>
                  {p.idioma.toUpperCase()}
                </span>
                <span className="muted small">
                  {p.num_lineas} {p.num_lineas === 1 ? "línea" : "líneas"}
                  {p.carpeta ? ` · ${p.carpeta}` : ""}
                </span>
                {picking === p.id ? <span className="muted small">Cargando…</span> : null}
              </span>
              {p.descripcion ? <span className="muted small pl-sel-desc">{p.descripcion}</span> : null}
              {p.etiquetas.length > 0 ? (
                <span className="tag-chip-list tag-chip-list--dense">
                  {p.etiquetas.map((e) => <span key={e} className="tag-chip chip is-info">{e}</span>)}
                </span>
              ) : null}
              {p.coincidencias && p.coincidencias.length > 0 ? (
                <span className="pl-sel-coinc">
                  {p.coincidencias.map((c) => (
                    <span key={c.posicion} className="small">
                      Línea {c.posicion}
                      {c.sku ? <> · <span className="mono">{c.sku}</span></> : null}
                      {c.descripcion ? ` — ${c.descripcion}` : ""}
                    </span>
                  ))}
                </span>
              ) : null}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
