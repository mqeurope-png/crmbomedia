"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { ConvertOfertaDialog } from "../../components/erp/ConvertOfertaDialog";
import { ActionsMenu } from "../../components/erp/flow/ActionsMenu";
import { OfertaEditorModal } from "../../components/erp/OfertaEditorModal";
import { OfertaEmailModal } from "../../components/erp/OfertaEmailModal";
import { OfertaEstadoPill } from "../../components/erp/OfertaEstadoPill";
import { getCurrentUser } from "../../lib/api";
import { Cap, can } from "../../lib/capabilities";
import {
  listOfertas,
  OFERTA_ESTADO_LABELS,
  openOfertaPdf,
  type Oferta,
  type OfertaEstado,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

const ESTADOS: readonly OfertaEstado[] = ["borrador", "enviada", "caducada", "convertida"];
/** Hasta cuántas ofertas pide la pantalla (el servidor admite 500). */
const LIST_LIMIT = 500;
/** Retardo al teclear antes de consultar al servidor con el texto. */
const SEARCH_DEBOUNCE_MS = 300;

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return y && m && d ? `${d}/${m}/${y}` : iso;
}

/** Editor abierto: nueva (oferta null) o la de la fila; `marcadas` son las
 *  posiciones que «Convertir en presupuesto» rechazó por no tener artículo;
 *  `plantillaId`, la plantilla con la que se abre una nueva («Usar» desde
 *  ERP · Plantillas). */
type EditorState = { oferta: Oferta | null; marcadas?: number[]; plantillaId?: string };

/** Pantalla ERP · Ofertas: propuestas comerciales a un contacto del CRM antes
 *  del presupuesto de FACTUSOL. Pestaña «Ofertas»: listado con filtros (estado
 *  y buscador en el servidor) y, por fila, el menú «⋯» con Ver / editar, PDF,
 *  Enviar por correo y Convertir en presupuesto (la única acción que escribe en
 *  FACTUSOL, con su diálogo de confirmación). Las plantillas viven en su
 *  pantalla (ERP · Plantillas) y sirven también para proformas y pedidos. Por
 *  la URL: `?contact_id=` (desde «Ver todas» de la ficha del contacto),
 *  `?plantilla=` (nueva oferta con esa plantilla, desde ERP · Plantillas) y
 *  `?tab=plantillas` (enlaces viejos: lleva a ERP · Plantillas). */
function OfertasPageInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const plantillaParam = searchParams.get("plantilla");
  const tabViejo = searchParams.get("tab") === "plantillas";
  // Solo las ofertas de un contacto (el enlace de su ficha); se quita con
  // «Limpiar filtros».
  const [contactFilter, setContactFilter] = useState<string | null>(
    searchParams.get("contact_id") || null,
  );
  const [canEdit, setCanEdit] = useState(false);
  const [canEmail, setCanEmail] = useState(false);
  const [ofertas, setOfertas] = useState<Oferta[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [estado, setEstado] = useState<OfertaEstado | "">("");
  const [text, setText] = useState("");
  // El texto se manda al SERVIDOR con un pequeño retardo al teclear (busca por
  // nº, contacto, empresa y referencia; el listado no trae todo para filtrar aquí).
  const [serverText, setServerText] = useState("");
  // Nº de la última petición: una respuesta tardía no pisa a una más nueva.
  const reqId = useRef(0);
  // Con qué filtros vinieron las `ofertas` actuales (null = aún ninguna):
  // mientras no coincidan con los de ahora, la lista está cargando.
  const [loadedKey, setLoadedKey] = useState<string | null>(null);
  // Modales. Con `?plantilla=` se abre ya la nueva oferta con esa plantilla.
  const [editor, setEditor] = useState<EditorState | null>(
    () => (plantillaParam ? { oferta: null, plantillaId: plantillaParam } : null),
  );
  const [emailing, setEmailing] = useState<Oferta | null>(null);
  const [converting, setConverting] = useState<Oferta | null>(null);

  useEffect(() => {
    if (tabViejo) router.replace("/erp/plantillas");
  }, [tabViejo, router]);

  useEffect(() => {
    getCurrentUser()
      .then((u) => {
        setCanEdit(can(u, Cap.PROFORMAS));
        setCanEmail(can(u, Cap.EMAIL_CLIENT));
      })
      .catch(() => { setCanEdit(false); setCanEmail(false); });
  }, []);

  useEffect(() => {
    const handle = window.setTimeout(() => setServerText(text.trim()), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [text]);

  const filtersKey = `${estado}\u0000${serverText}\u0000${contactFilter ?? ""}`;
  const loading = loadedKey !== filtersKey;

  const load = useCallback(() => {
    const id = ++reqId.current;
    return listOfertas({
      limit: LIST_LIMIT,
      ...(contactFilter ? { contact_id: contactFilter } : {}),
      ...(estado ? { estado } : {}),
      ...(serverText ? { q: serverText } : {}),
    })
      .then((items) => {
        // Respuesta tardía (ya hay otra petición más nueva): no pisa nada.
        if (id !== reqId.current) return;
        setOfertas(items);
        setError(null);
        setLoadedKey(filtersKey);
      })
      .catch((e) => {
        if (id !== reqId.current) return;
        setError(extractErrorMessage(e, "No se pudieron cargar las ofertas."));
        setLoadedKey(filtersKey);
      });
  }, [estado, serverText, contactFilter, filtersKey]);

  useEffect(() => { void load(); }, [load]);

  async function pdf(o: Oferta) {
    setError(null);
    try {
      await openOfertaPdf(o.id, o.idioma);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo generar el PDF de la oferta."));
    }
  }

  function onSaved(saved: Oferta) {
    const created = editor?.oferta === null;
    setEditor(null);
    setNotice(`Oferta ${saved.numero} ${created ? "creada" : "guardada"}.`);
    void load();
  }

  function onConverted(converted: Oferta) {
    // El diálogo sigue abierto con el nº del presupuesto y el enlace; aquí se
    // refresca la lista y se deja el aviso para cuando lo cierre.
    setNotice(`Oferta ${converted.numero} convertida en el presupuesto ${converted.factusol?.numero ?? ""}.`);
    void load();
  }

  function menuFor(o: Oferta) {
    const convertida = o.estado === "convertida";
    return [
      <button key="ver" type="button" onClick={() => setEditor({ oferta: o })}>
        {canEdit && !convertida ? "Ver / editar" : "Ver"}
      </button>,
      <button key="pdf" type="button" onClick={() => void pdf(o)}>PDF</button>,
      canEmail && !convertida ? (
        <button key="email" type="button" onClick={() => setEmailing(o)}>Enviar por correo</button>
      ) : null,
      canEdit && !convertida ? (
        <button key="convertir" type="button" onClick={() => setConverting(o)}>
          Convertir en presupuesto
        </button>
      ) : null,
      o.factusol ? (
        <Link key="presupuesto" href="/erp/proformas">Presupuesto {o.factusol.numero}</Link>
      ) : null,
    ].filter(Boolean);
  }

  const hasFilters = Boolean(text.trim() || estado || contactFilter);
  const contactoFiltrado = contactFilter
    ? ofertas.find((o) => o.contact_id === contactFilter)?.contacto?.nombre ?? null
    : null;

  return (
    <main className="shell shell-wide erp-flow">
      <PageHeader
        title="Ofertas"
        eyebrow="ERP"
        description="Propuestas comerciales a un contacto, antes del presupuesto de FACTUSOL."
        actions={(
          <>
            <Link className="button small secondary" href="/erp/plantillas">Plantillas</Link>
            {canEdit ? (
              <button type="button" className="button small" onClick={() => setEditor({ oferta: null })}>
                + Nueva oferta
              </button>
            ) : null}
          </>
        )}
      />

      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {notice ? <p className="form-info" role="status">{notice}</p> : null}

      <div className="erp-flow-filters" role="search" aria-label="Filtros de ofertas">
        <label className="field erp-flow-filter-grow">
          <span className="sr-only">Buscar oferta</span>
          <input type="search" value={text} aria-label="Buscar oferta"
                 placeholder="Nº, contacto, empresa o referencia…"
                 onChange={(e) => setText(e.target.value)} />
        </label>
        <label className="field">
          <span>Estado</span>
          <select value={estado} aria-label="Estado"
                  onChange={(e) => setEstado(e.target.value as OfertaEstado | "")}>
            <option value="">Todos</option>
            {ESTADOS.map((s) => (
              <option key={s} value={s}>{OFERTA_ESTADO_LABELS[s]}</option>
            ))}
          </select>
        </label>
        {contactFilter ? (
          <span className="badge info" title="Solo las ofertas de este contacto (desde su ficha).">
            Contacto: {contactoFiltrado ?? "filtrado"}
          </span>
        ) : null}
        {hasFilters ? (
          <button type="button" className="button small secondary"
                  onClick={() => { setText(""); setEstado(""); setContactFilter(null); }}>
            Limpiar filtros
          </button>
        ) : null}
      </div>

      {loading && ofertas.length === 0 ? (
        <p className="muted">Cargando…</p>
      ) : ofertas.length === 0 ? (
        <p className="muted">
          {hasFilters ? "Ninguna oferta casa con los filtros." : "Aún no hay ofertas."}
        </p>
      ) : (
        <table className="data-table" aria-label="Ofertas">
          <thead>
            <tr>
              <th scope="col">Nº</th>
              <th scope="col">Contacto / empresa</th>
              <th scope="col">Emisora</th>
              <th scope="col">Fecha</th>
              <th scope="col">Válida hasta</th>
              <th scope="col">Estado</th>
              <th scope="col" className="num">Total</th>
              <th scope="col" aria-label="Acciones" />
            </tr>
          </thead>
          <tbody>
            {ofertas.map((o) => (
              <tr key={o.id} aria-label={`Oferta ${o.numero}`}>
                <td>
                  <strong className="mono">{o.numero}</strong>
                  {o.pendiente_de_envio ? (
                    <>
                      {" "}
                      <span className="badge warn"
                            title="Se cambió después del último envío: el cliente tiene una versión anterior.">
                        cambios sin enviar
                      </span>
                    </>
                  ) : null}
                  {o.referencia ? <div className="muted small">{o.referencia}</div> : null}
                </td>
                <td>
                  {o.contacto ? (
                    <Link href={`/contacts/${o.contacto.id}`}>{o.contacto.nombre}</Link>
                  ) : "—"}
                  {o.empresa ? (
                    <div className="small">
                      <Link href={`/companies/${o.empresa.id}`}>{o.empresa.nombre}</Link>
                    </div>
                  ) : null}
                </td>
                <td>{o.emisora}</td>
                <td className="mono">{fmtDate(o.fecha)}</td>
                <td>
                  <span className={`mono${o.vencida ? " form-error" : ""}`}
                        title={o.vencida ? "La fecha de validez ya pasó." : undefined}>
                    {fmtDate(o.valida_hasta)}
                  </span>
                </td>
                <td>
                  <OfertaEstadoPill estado={o.estado} vencida={o.vencida} />
                  {o.factusol ? (
                    <div className="small">
                      <Link href="/erp/proformas" title="Ver en Proformas">
                        Presupuesto {o.factusol.numero}
                      </Link>
                    </div>
                  ) : null}
                </td>
                <td className="num">
                  {o.total.toFixed(2)} €
                  <div className="muted small">{o.con_iva ? `IVA ${o.iva_pct} %` : "sin IVA"}</div>
                </td>
                <td>
                  <ActionsMenu label={`Acciones ${o.numero}`}>{menuFor(o)}</ActionsMenu>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {editor ? (
        <OfertaEditorModal
          oferta={editor.oferta}
          marcarPosiciones={editor.marcadas}
          plantillaInicialId={editor.plantillaId}
          readOnly={!canEdit}
          onClose={() => setEditor(null)}
          onSaved={onSaved}
        />
      ) : null}

      {/* Enviar por correo: previsualización obligatoria en el modal; aquí
          solo se recarga la lista para que cambie el estado a «Enviada». */}
      {emailing ? (
        <OfertaEmailModal
          oferta={emailing}
          onClose={() => setEmailing(null)}
          onSent={(r) => {
            setNotice(`Oferta ${r.numero} enviada a ${r.to.join(", ")}.`);
            void load();
          }}
        />
      ) : null}

      {converting ? (
        <ConvertOfertaDialog
          oferta={converting}
          onClose={() => setConverting(null)}
          onConverted={onConverted}
          onEditLines={(posiciones) => {
            const o = converting;
            setConverting(null);
            setEditor({ oferta: o, marcadas: posiciones });
          }}
        />
      ) : null}

    </main>
  );
}

/** `useSearchParams` exige Suspense en el app router (`?contact_id=`, `?plantilla=`). */
export default function OfertasPage() {
  return (
    <Suspense fallback={<main className="shell shell-wide erp-flow"><p className="muted">Cargando…</p></main>}>
      <OfertasPageInner />
    </Suspense>
  );
}
