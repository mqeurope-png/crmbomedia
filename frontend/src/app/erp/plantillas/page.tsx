"use client";

import { FolderPlus, Pencil, Search, Trash2, Users } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { ErpModalShell } from "../../components/erp/ErpModalShell";
import { ActionsMenu } from "../../components/erp/flow/ActionsMenu";
import { PlantillaAccesoModal } from "../../components/erp/PlantillaAccesoModal";
import { PlantillaCarpetasTree } from "../../components/erp/PlantillaCarpetasTree";
import { PlantillaEditorModal } from "../../components/erp/PlantillaEditorModal";
import {
  CARPETA_SIN,
  CARPETA_TODAS,
  filtroCarpeta,
  findCarpeta,
  flattenCarpetas,
  IDIOMAS_PLANTILLA,
  origenTexto,
} from "../../components/erp/plantillasUi";
import { getCurrentUser } from "../../lib/api";
import { Cap, can } from "../../lib/capabilities";
import {
  createPlantillaCarpeta,
  deletePlantillaCarpeta,
  getPlantillasMeta,
  listPlantillaCarpetas,
  listPlantillaEtiquetas,
  listPlantillas,
  setPlantillaCarpetaDefecto,
  updatePlantilla,
  updatePlantillaCarpeta,
  type OfertaIdioma,
  type PlantillaCarpetaNode,
  type PlantillaDocumento,
  type PlantillaEtiqueta,
  type PlantillasMeta,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

const SEARCH_DEBOUNCE_MS = 300;

type Estado = "activas" | "todas" | "desactivadas";

/** Carpeta nueva (con su madre) o la elegida, para renombrar o mover. */
type CarpetaModal = { modo: "nueva"; parentId: string | null } | { modo: "editar"; carpeta: PlantillaCarpetaNode };

/** Ids de una carpeta y todas sus subcarpetas (no puede moverse dentro de sí). */
function idsDelSubarbol(node: PlantillaCarpetaNode): Set<string> {
  const out = new Set<string>([node.id]);
  for (const c of node.children) for (const id of idsDelSubarbol(c)) out.add(id);
  return out;
}

/** Pantalla ERP · Plantillas: el catálogo de plantillas de documento (líneas
 *  para una oferta, una proforma o un pedido). Carpetas jerárquicas (máx. 3
 *  niveles) con la ★ de carpeta por defecto y, para administración, el acceso
 *  por carpeta; buscador que mira dentro de las líneas (referencia y texto),
 *  filtro por idioma, estado y etiquetas. Por plantilla: editar (nombre,
 *  idioma, carpeta, etiquetas, líneas), activar / desactivar y «Usar» en una
 *  oferta, una proforma o un pedido (abre el alta de siempre con sus líneas).
 *  Nada de esto escribe en FACTUSOL. */
export default function PlantillasPage() {
  const [canEdit, setCanEdit] = useState(false);
  const [canOrders, setCanOrders] = useState(false);
  const [meta, setMeta] = useState<PlantillasMeta | null>(null);
  const [arbol, setArbol] = useState<PlantillaCarpetaNode[]>([]);
  const [carpeta, setCarpeta] = useState<string | null>(null);
  const [etiquetas, setEtiquetas] = useState<PlantillaEtiqueta[]>([]);
  const [filtroEtq, setFiltroEtq] = useState<string[]>([]);
  const [idioma, setIdioma] = useState<OfertaIdioma | "">("");
  const [estado, setEstado] = useState<Estado>("todas");
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [items, setItems] = useState<PlantillaDocumento[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editor, setEditor] = useState<{ plantilla: PlantillaDocumento | null } | null>(null);
  const [carpetaModal, setCarpetaModal] = useState<CarpetaModal | null>(null);
  const [acceso, setAcceso] = useState<PlantillaCarpetaNode | null>(null);
  // Nº de la última petición: una respuesta tardía no pisa a una más nueva.
  const reqId = useRef(0);

  useEffect(() => {
    getCurrentUser()
      .then((u) => {
        setCanEdit(can(u, Cap.PROFORMAS));
        setCanOrders(can(u, Cap.ORDERS_CREATE));
      })
      .catch(() => { setCanEdit(false); setCanOrders(false); });
  }, []);

  const loadCarpetas = useCallback(async () => {
    try {
      const r = await listPlantillaCarpetas();
      setArbol(r.items);
      return r;
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudieron cargar las carpetas."));
      return null;
    }
  }, []);

  const loadEtiquetas = useCallback(() => listPlantillaEtiquetas()
    .then(setEtiquetas)
    .catch(() => setEtiquetas([])), []);

  // Al entrar: meta, carpetas (se abre en la carpeta por defecto) y etiquetas.
  useEffect(() => {
    let alive = true;
    Promise.all([getPlantillasMeta().catch(() => null), loadCarpetas(), loadEtiquetas()])
      .then(([m, r]) => {
        if (!alive) return;
        setMeta(m);
        const defecto = r?.carpeta_defecto;
        setCarpeta(defecto && r && findCarpeta(r.items, defecto) ? defecto : CARPETA_TODAS);
      });
    return () => { alive = false; };
  }, [loadCarpetas, loadEtiquetas]);

  useEffect(() => {
    const t = window.setTimeout(() => setQ(text.trim()), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [text]);

  const load = useCallback(async () => {
    if (carpeta === null) return;
    const id = ++reqId.current;
    setLoading(true);
    try {
      const r = await listPlantillas({
        ...filtroCarpeta(carpeta), q, etiquetas: filtroEtq, idioma,
        activas: estado === "activas",
      });
      if (id !== reqId.current) return;
      setItems(estado === "desactivadas" ? r.items.filter((p) => !p.activa) : r.items);
      setError(null);
    } catch (e) {
      if (id !== reqId.current) return;
      setError(extractErrorMessage(e, "No se pudieron cargar las plantillas."));
    } finally {
      if (id === reqId.current) setLoading(false);
    }
  }, [carpeta, q, filtroEtq, idioma, estado]);

  useEffect(() => { void load(); }, [load]);

  async function refrescarTodo() {
    await Promise.all([load(), loadCarpetas(), loadEtiquetas()]);
  }

  async function toggleActiva(p: PlantillaDocumento) {
    setBusy(p.id);
    setError(null);
    try {
      await updatePlantilla(p.id, { activa: !p.activa });
      setNotice(`Plantilla «${p.nombre}» ${p.activa ? "desactivada" : "activada"}.`);
      await load();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo cambiar la plantilla."));
    } finally {
      setBusy(null);
    }
  }

  async function toggleDefecto(node: PlantillaCarpetaNode) {
    setError(null);
    try {
      await setPlantillaCarpetaDefecto(node.es_defecto ? null : node.id);
      await loadCarpetas();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo cambiar la carpeta por defecto."));
    }
  }

  async function borrarCarpeta(node: PlantillaCarpetaNode) {
    // Su contenido sube a la carpeta madre (a la raíz si no tiene).
    const madre = findCarpeta(arbol, node.parent_id);
    const destino = madre ? `«${madre.nombre}»` : "la raíz";
    if (!window.confirm(
      `¿Borrar la carpeta «${node.nombre}»? Sus plantillas y subcarpetas no se borran: pasan a ${destino}.`,
    )) return;
    setError(null);
    try {
      await deletePlantillaCarpeta(node.id);
      setNotice(`Carpeta «${node.nombre}» borrada; su contenido está ahora en ${destino}.`);
      setCarpeta(CARPETA_TODAS);
      await refrescarTodo();
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo borrar la carpeta."));
    }
  }

  function toggleEtiqueta(norm: string) {
    setFiltroEtq((cur) => (cur.includes(norm) ? cur.filter((x) => x !== norm) : [...cur, norm]));
  }

  const seleccionada = carpeta && carpeta !== CARPETA_TODAS && carpeta !== CARPETA_SIN
    ? findCarpeta(arbol, carpeta) : null;
  const restringido = Boolean(meta?.acceso_restringido);
  const hayFiltros = Boolean(text.trim() || idioma || estado !== "todas" || filtroEtq.length);

  function menuUsar(p: PlantillaDocumento) {
    const id = encodeURIComponent(p.id);
    return [
      <Link key="oferta" href={`/erp/ofertas?plantilla=${id}`}>En una oferta</Link>,
      <Link key="proforma" href={`/erp/proformas?plantilla=${id}`}>En una proforma</Link>,
      canOrders ? <Link key="pedido" href={`/erp/orders/new?plantilla=${id}`}>En un pedido</Link> : null,
    ].filter(Boolean);
  }

  return (
    <main className="shell shell-wide erp-flow">
      <PageHeader
        title="Plantillas"
        eyebrow="ERP"
        description="Líneas listas para crear una oferta, una proforma o un pedido. Organízalas en carpetas (máx. 3 niveles), etiquétalas y búscalas por lo que llevan dentro."
        actions={canEdit ? (
          <button type="button" className="button small"
                  onClick={() => setEditor({ plantilla: null })}>
            + Nueva plantilla
          </button>
        ) : null}
      />

      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {notice ? <p className="form-info" role="status">{notice}</p> : null}
      {restringido ? (
        <p className="muted small" role="note">
          Ves solo las carpetas a las que tienes acceso.
        </p>
      ) : null}

      <section className="et-layout">
        <aside className="et-sidebar card">
          <div className="et-sidebar-header">
            <h3>Carpetas</h3>
            {canEdit ? (
              <button type="button" className="button secondary small"
                      onClick={() => setCarpetaModal({ modo: "nueva", parentId: seleccionada?.id ?? null })}>
                <FolderPlus size={13} aria-hidden /> Nueva
              </button>
            ) : null}
          </div>
          <PlantillaCarpetasTree nodes={arbol} selected={carpeta ?? CARPETA_TODAS}
                                 onSelect={setCarpeta} onToggleDefault={toggleDefecto}
                                 showSinCarpeta={!restringido} />
          {seleccionada && (canEdit || meta?.gestiona_acceso) ? (
            <div className="et-sidebar-actions erp-contact-channel" role="group"
                 aria-label={`Acciones de la carpeta ${seleccionada.nombre}`}>
              {canEdit ? (
                <>
                  <button type="button" className="button secondary small"
                          onClick={() => setCarpetaModal({ modo: "editar", carpeta: seleccionada })}>
                    <Pencil size={12} aria-hidden /> Renombrar / mover
                  </button>
                  {/* Quien solo ve algunas carpetas no las borra: su contenido
                      iría a la raíz, que no ve (el servidor lo rechaza igual). */}
                  {!restringido ? (
                    <button type="button" className="button secondary small"
                            onClick={() => void borrarCarpeta(seleccionada)}>
                      <Trash2 size={12} aria-hidden /> Borrar
                    </button>
                  ) : null}
                </>
              ) : null}
              {meta?.gestiona_acceso ? (
                <button type="button" className="button secondary small"
                        onClick={() => setAcceso(seleccionada)}>
                  <Users size={12} aria-hidden /> Acceso
                </button>
              ) : null}
            </div>
          ) : null}
        </aside>

        <article className="et-main card">
          <div className="pl-filtros" role="search" aria-label="Filtros de plantillas">
            <div className="et-search">
              <Search size={13} aria-hidden />
              <input type="search" value={text} aria-label="Buscar plantilla"
                     placeholder="Nombre, referencia (DM3H) o texto de una línea (horno)…"
                     onChange={(e) => setText(e.target.value)} />
            </div>
            <label className="field">
              <span className="sr-only">Idioma</span>
              <select value={idioma} aria-label="Idioma"
                      onChange={(e) => setIdioma(e.target.value as OfertaIdioma | "")}>
                <option value="">Todos los idiomas</option>
                {IDIOMAS_PLANTILLA.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
              </select>
            </label>
            <label className="field">
              <span className="sr-only">Estado</span>
              <select value={estado} aria-label="Estado"
                      onChange={(e) => setEstado(e.target.value as Estado)}>
                <option value="todas">Activas y desactivadas</option>
                <option value="activas">Solo activas</option>
                <option value="desactivadas">Solo desactivadas</option>
              </select>
            </label>
            {hayFiltros ? (
              <button type="button" className="button small secondary"
                      onClick={() => { setText(""); setIdioma(""); setEstado("todas"); setFiltroEtq([]); }}>
                Limpiar filtros
              </button>
            ) : null}
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

          {loading && items === null ? (
            <p className="muted">Cargando…</p>
          ) : !items || items.length === 0 ? (
            <p className="muted">
              {hayFiltros || carpeta !== CARPETA_TODAS
                ? "Ninguna plantilla con estos filtros."
                : "Aún no hay plantillas. Se crean aquí con líneas a mano o con «Guardar como plantilla» desde una proforma, un albarán o un pedido."}
            </p>
          ) : (
            <div className="table-wrapper" aria-busy={loading}>
              <table className="data-table" aria-label="Plantillas">
                <thead>
                  <tr>
                    <th scope="col">Nombre</th>
                    <th scope="col">Idioma</th>
                    <th scope="col">Carpeta</th>
                    <th scope="col" className="num">Líneas</th>
                    <th scope="col">Estado</th>
                    <th scope="col" aria-label="Acciones" />
                  </tr>
                </thead>
                <tbody>
                  {items.map((p) => (
                    <tr key={p.id} aria-label={`Plantilla ${p.nombre}`}>
                      <td>
                        <div className="pl-tabla-nombre">
                          <strong>{p.nombre}</strong>
                          {p.descripcion ? <span className="muted small">{p.descripcion}</span> : null}
                          {p.etiquetas.length > 0 ? (
                            <span className="tag-chip-list tag-chip-list--dense">
                              {p.etiquetas.map((e) => <span key={e} className="tag-chip chip is-info">{e}</span>)}
                            </span>
                          ) : null}
                          {origenTexto(p) ? <span className="muted small">{origenTexto(p)}</span> : null}
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
                        </div>
                      </td>
                      <td><span className="chip is-muted">{p.idioma.toUpperCase()}</span></td>
                      <td className="small">{p.carpeta ?? <span className="muted">—</span>}</td>
                      <td className="num">{p.num_lineas}</td>
                      <td>
                        <span className={`badge ${p.activa ? "ok" : "muted"}`}>
                          {p.activa ? "Activa" : "Desactivada"}
                        </span>
                      </td>
                      <td>
                        <div className="pl-acciones" role="group" aria-label={`Acciones ${p.nombre}`}>
                          {canEdit ? (
                            <>
                              <button type="button" className="button small secondary"
                                      disabled={busy === p.id}
                                      onClick={() => setEditor({ plantilla: p })}>
                                Editar
                              </button>
                              <button type="button" className="button small secondary"
                                      disabled={busy === p.id}
                                      onClick={() => void toggleActiva(p)}>
                                {p.activa ? "Desactivar" : "Activar"}
                              </button>
                            </>
                          ) : null}
                          {canEdit && p.activa ? (
                            <ActionsMenu label={`Usar ${p.nombre}`} floating>{menuUsar(p)}</ActionsMenu>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </article>
      </section>

      {editor ? (
        <PlantillaEditorModal
          plantilla={editor.plantilla}
          carpetaInicial={seleccionada?.id ?? null}
          onClose={() => setEditor(null)}
          onSaved={(saved) => {
            const nueva = editor.plantilla === null;
            setEditor(null);
            setNotice(`Plantilla «${saved.nombre}» ${nueva ? "creada" : "guardada"}.`);
            void refrescarTodo();
          }}
        />
      ) : null}

      {carpetaModal ? (
        <CarpetaModalView
          estado={carpetaModal}
          arbol={arbol}
          maxProfundidad={meta?.max_profundidad ?? 3}
          permiteRaiz={!restringido}
          onClose={() => setCarpetaModal(null)}
          onSaved={(texto, id) => {
            setCarpetaModal(null);
            setNotice(texto);
            if (id) setCarpeta(id);
            void refrescarTodo();
          }}
        />
      ) : null}

      {acceso ? (
        <PlantillaAccesoModal
          carpeta={acceso}
          onClose={() => setAcceso(null)}
          onSaved={(ids) => {
            setAcceso(null);
            setNotice(ids.length
              ? `«${acceso.nombre}»: acceso acotado a ${ids.length} usuario(s).`
              : `«${acceso.nombre}»: sin acotar (la ve todo el mundo que no esté acotado a otras).`);
            void loadCarpetas();
          }}
        />
      ) : null}
    </main>
  );
}

/** Crear una carpeta (dentro de la elegida) o renombrar / mover la elegida. */
function CarpetaModalView({
  estado, arbol, maxProfundidad, permiteRaiz, onClose, onSaved,
}: {
  estado: CarpetaModal;
  arbol: PlantillaCarpetaNode[];
  maxProfundidad: number;
  permiteRaiz: boolean;
  onClose: () => void;
  onSaved: (texto: string, id?: string) => void;
}) {
  const editando = estado.modo === "editar" ? estado.carpeta : null;
  // Dónde está ahora (al editar). Para quien solo ve algunas carpetas puede
  // ser «ninguna que vea»: entonces se queda donde está.
  const original = editando?.parent_id ?? "";

  // Madres posibles: no la propia carpeta ni sus subcarpetas, y sin pasar de
  // `maxProfundidad` niveles (el backend lo vuelve a comprobar).
  const opciones = useMemo(() => {
    const fuera = editando ? idsDelSubarbol(editando) : new Set<string>();
    return flattenCarpetas(arbol).filter((c) => !fuera.has(c.id) && c.depth < maxProfundidad - 1);
  }, [arbol, editando, maxProfundidad]);
  const [nombre, setNombre] = useState(editando?.nombre ?? "");
  const [parentId, setParentId] = useState<string>(() => {
    if (editando) return original;
    // Nueva: dentro de la seleccionada si se puede; si no, una opción válida.
    const pedida = estado.modo === "nueva" ? (estado.parentId ?? "") : "";
    if (pedida && opciones.some((c) => c.id === pedida)) return pedida;
    return permiteRaiz ? "" : (opciones[0]?.id ?? "");
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cambiaMadre = Boolean(editando) && parentId !== original;
  const valid = nombre.trim().length > 0
    && (editando ? (!cambiaMadre || permiteRaiz || Boolean(parentId)) : (permiteRaiz || Boolean(parentId)));

  async function save() {
    if (!valid || saving) return;
    setSaving(true);
    setError(null);
    try {
      if (editando) {
        // La madre solo viaja si cambia: renombrar no la toca.
        await updatePlantillaCarpeta(editando.id, {
          nombre: nombre.trim(),
          ...(cambiaMadre ? { parent_id: parentId || null } : {}),
        });
        onSaved(`Carpeta «${nombre.trim()}» guardada.`, editando.id);
      } else {
        const c = await createPlantillaCarpeta({ nombre: nombre.trim(), parent_id: parentId || null });
        onSaved(`Carpeta «${c.nombre}» creada.`, c.id);
      }
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar la carpeta."));
      setSaving(false);
    }
  }

  return (
    <ErpModalShell label={editando ? `Carpeta ${editando.nombre}` : "Nueva carpeta"}
                   title={editando ? "Renombrar o mover carpeta" : "Nueva carpeta"}
                   onClose={onClose} disabled={saving}>
      <form onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <label className="field">
          <span>Nombre</span>
          <input type="text" value={nombre} maxLength={200} autoFocus aria-label="Nombre de la carpeta"
                 disabled={saving} onChange={(e) => setNombre(e.target.value)} />
        </label>
        <label className="field">
          <span>Dentro de</span>
          <select value={parentId} aria-label="Carpeta madre" disabled={saving}
                  onChange={(e) => setParentId(e.target.value)}>
            {permiteRaiz || (editando && !original) ? <option value="">— La raíz —</option> : null}
            {editando && original && !opciones.some((c) => c.id === original) ? (
              <option value={original}>(donde está ahora)</option>
            ) : null}
            {opciones.map((c) => <option key={c.id} value={c.id}>{c.ruta}</option>)}
          </select>
          <span className="muted small">Hasta {maxProfundidad} niveles.</span>
        </label>
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose} disabled={saving}>
            Cancelar
          </button>
          <button type="submit" className="button" disabled={!valid || saving}>
            {saving ? "Guardando…" : editando ? "Guardar" : "Crear carpeta"}
          </button>
        </div>
      </form>
    </ErpModalShell>
  );
}
