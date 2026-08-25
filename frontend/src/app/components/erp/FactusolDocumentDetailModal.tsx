"use client";

import { useCallback, useEffect, useState } from "react";
import { getCurrentUser, type User } from "../../lib/api";
import {
  convertFactusolDocument,
  ERP_EDIT_ROLES,
  getFactusolConvertStatus,
  getFactusolDocument,
  getFactusolSeries,
  type FactusolConvertTarget,
  type FactusolCycle,
  type FactusolCycleRef,
  type FactusolDocType,
  type FactusolDocumentDetail,
  type FactusolSerie,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

const TYPE_LABELS: Record<FactusolDocType, string> = {
  pedidos: "Pedido de cliente",
  presupuestos: "Presupuesto",
  albaranes: "Albarán",
  facturas: "Factura",
};

const TARGET_LABELS: Record<FactusolConvertTarget, string> = {
  albaranes: "albarán",
  facturas: "factura",
};

/** Conversiones que ofrece cada tipo (espejo de `chain.ALLOWED_CONVERSIONS`). */
const CONVERSIONS: Partial<Record<FactusolDocType, FactusolConvertTarget[]>> = {
  presupuestos: ["albaranes", "facturas"],
  albaranes: ["facturas"],
};

/** E3-B-fix1 — etiquetas del ciclo POR TIPO, como fallback si el backend no
 *  mandó `estado_label` (espejo de `documents.CICLO_ESTADO_LABELS`). Un
 *  albarán «pendiente» está SIN FACTURAR — nunca «sin albarán». */
const CYCLE_FALLBACK_LABELS: Partial<
  Record<FactusolDocType, Record<string, string>>
> = {
  presupuestos: {
    pendiente: "Sin albarán ni factura",
    con_albaran: "Con albarán",
    facturado: "Facturado",
  },
  pedidos: {
    pendiente: "Sin albarán ni factura",
    con_albaran: "Con albarán",
    facturado: "Facturado",
  },
  albaranes: { pendiente: "Sin facturar", facturado: "Facturado" },
};

export function cycleBadge(
  docType: FactusolDocType,
  ciclo: FactusolCycle,
): { label: string; className: string } | null {
  if (!ciclo || !ciclo.estado) return null;
  const label =
    ciclo.estado_label ?? CYCLE_FALLBACK_LABELS[docType]?.[ciclo.estado];
  if (!label) return null;
  const className =
    ciclo.estado === "facturado"
      ? "badge ok"
      : ciclo.estado === "con_albaran"
        ? "badge warn"
        : "badge muted";
  return { label, className };
}

/** Disponibilidad de las acciones de conversión según el ciclo (E3-B-fix1):
 *  - primary: el camino normal del flujo.
 *  - secondary: posible pero requiere pasar por la confirmación explícita
 *    (duplicado / ya facturado) del modal de confirmación.
 *  - ausente: la regla de negocio lo prohíbe desde aquí (p. ej. facturar un
 *    presupuesto que YA tiene albarán: la factura se genera desde el
 *    albarán). */
type CycleAction = {
  target: FactusolConvertTarget;
  primary: boolean;
  contextWarning: string | null;
};

function availableActions(
  docType: FactusolDocType,
  ciclo: FactusolCycle,
): CycleAction[] {
  const targets = CONVERSIONS[docType] ?? [];
  if (targets.length === 0) return [];
  if (!ciclo) {
    // Sin anotación del ciclo (best-effort del backend): se ofrecen las
    // acciones normales — el anti-duplicado del backend sigue cubriendo.
    return targets.map((target) => ({
      target, primary: true, contextWarning: null,
    }));
  }
  const albaranes = ciclo.albaranes;
  const facturas = ciclo.facturas;
  const tipo = TYPE_LABELS[docType].toLowerCase();
  const out: CycleAction[] = [];
  for (const target of targets) {
    if (target === "albaranes") {
      // Crear (otro) albarán: primario solo con el ciclo virgen; con
      // albarán previo el confirm ya avisa del duplicado y exige «de todos
      // modos»; con factura previa se avisa del facturado.
      out.push({
        target,
        primary: albaranes.length === 0 && facturas.length === 0,
        contextWarning:
          albaranes.length === 0 && facturas.length > 0
            ? `Este ${tipo} ya está facturado en ${facturas
                .map((f) => f.numero).join(", ")}. ¿Crear un albarán ` +
              "igualmente?"
            : null,
      });
    } else {
      // Crear factura.
      if (docType !== "albaranes" && albaranes.length > 0) {
        // Regla de negocio: con albarán, la factura SE GENERA DESDE EL
        // ALBARÁN — aquí ni siquiera se ofrece (el aviso enlaza a él).
        continue;
      }
      if (docType === "albaranes" && facturas.length > 0) {
        // Albarán ya facturado: sin botón; el aviso enlaza a la factura.
        continue;
      }
      out.push({
        target,
        primary: facturas.length === 0,
        contextWarning: null,
      });
    }
  }
  return out;
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/** ERP-E3-A/E3-B — detalle de un documento FACTUSOL: cabecera + líneas +
 *  posición en el ciclo PRE→ALB→FAC, con las acciones de crear el siguiente
 *  documento de la cadena (albarán/factura). Los enlaces del ciclo navegan
 *  DENTRO del modal (el listado de fondo no cambia de pestaña). */
export function FactusolDocumentDetailModal({
  docType,
  serie,
  codigo,
  onClose,
  onChanged,
}: {
  docType: FactusolDocType;
  serie: number;
  codigo: number | string;
  onClose: () => void;
  /** Se llama cuando el modal CREÓ un documento (para refrescar el listado). */
  onChanged?: () => void;
}) {
  const [current, setCurrent] = useState({ docType, serie, codigo });
  const [doc, setDoc] = useState<FactusolDocumentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [convertTarget, setConvertTarget] =
    useState<FactusolConvertTarget | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [created, setCreated] = useState<FactusolCycleRef | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);

  useEffect(() => {
    // Mantiene la referencia si las props no cambiaron: un objeto nuevo
    // idéntico re-dispararía la carga del detalle en cada montaje.
    setCurrent((prev) =>
      prev.docType === docType && prev.serie === serie && prev.codigo === codigo
        ? prev
        : { docType, serie, codigo },
    );
  }, [docType, serie, codigo]);

  useEffect(() => {
    getCurrentUser().then(setUser).catch(() => undefined);
  }, []);

  const load = useCallback((fresh = false) => {
    setDoc(null);
    setError(null);
    let alive = true;
    getFactusolDocument(
      current.docType, current.serie, current.codigo,
      fresh ? { fresh: true } : undefined,
    )
      .then((d) => { if (alive) setDoc(d); })
      .catch((e) => {
        if (alive) setError(extractErrorMessage(e, "No se pudo cargar el documento."));
      });
    return () => { alive = false; };
  }, [current]);

  useEffect(() => load(), [load]);

  // Polling del job de conversión (E2-fix2: un job muerto NUNCA deja el
  // modal en «Generando…» — failed llega por aquí y se enseña).
  useEffect(() => {
    if (!jobId) return;
    const timer = setInterval(async () => {
      try {
        const st = await getFactusolConvertStatus(jobId);
        if (st.status === "finished") {
          setJobId(null);
          setCreated({
            doc_type: st.result.target_type,
            serie: st.result.serie,
            codigo: st.result.codigo,
            numero: st.result.numero,
          });
          // E3-B-fix1: recarga saltando el cache del índice del ciclo —
          // badge, avisos y botones se repintan AL MOMENTO, sin reabrir.
          load(true);
          onChanged?.();   // y la fila del listado de fondo
        } else if (st.status === "failed") {
          setJobId(null);
          setCreateError(st.error || "La creación falló en FACTUSOL.");
        }
      } catch {
        // Polling best-effort: un fallo puntual de red no aborta el job.
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [jobId, load, onChanged]);

  const canEdit =
    !!user && (ERP_EDIT_ROLES as readonly string[]).includes(user.role);
  const ciclo = doc?.ciclo ?? null;
  const badge = cycleBadge(current.docType, ciclo);
  const actions = doc ? availableActions(current.docType, ciclo) : [];
  const isSourceType = current.docType !== "facturas";

  function navigate(ref: FactusolCycleRef) {
    setCreated(null);
    setCreateError(null);
    setConvertTarget(null);
    setCurrent({ docType: ref.doc_type, serie: ref.serie, codigo: ref.codigo });
  }

  /** Hijos que ya existen del tipo destino — el aviso anti-duplicado. */
  function existingChildren(target: FactusolConvertTarget): FactusolCycleRef[] {
    if (!ciclo) return [];
    return target === "albaranes" ? ciclo.albaranes : ciclo.facturas;
  }

  function refLinks(refs: FactusolCycleRef[]) {
    return refs.map((ref) => (
      <button
        key={`${ref.doc_type}-${ref.numero}`}
        type="button"
        className="erp-doc-ciclo-link"
        onClick={() => navigate(ref)}
      >
        {ref.numero}
      </button>
    ));
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label={`Detalle ${TYPE_LABELS[current.docType]}`}>
      <div className="modal-dialog erp-emit-modal erp-doc-detail">
        <h2>
          {TYPE_LABELS[current.docType]}{" "}
          <span className="muted">
            {doc?.numero ?? `${current.serie}-${current.codigo}`}
          </span>
        </h2>

        {error ? <p className="form-error">{error}</p> : null}
        {!doc && !error ? <p className="muted">Cargando…</p> : null}

        {created ? (
          <p className="form-success">
            Creado {TARGET_LABELS[created.doc_type as FactusolConvertTarget]
              ?? created.doc_type}{" "}
            <button
              type="button"
              className="erp-doc-ciclo-link"
              onClick={() => navigate(created)}
            >
              {created.numero}
            </button>{" "}
            en FACTUSOL.
          </p>
        ) : null}
        {createError ? <p className="form-error">{createError}</p> : null}
        {jobId ? <p className="muted">Creando el documento en FACTUSOL…</p> : null}

        {doc ? (
          <>
            <dl className="erp-doc-detail-head">
              <dt>Cliente</dt>
              <dd>{doc.cliente_nombre ?? doc.cliente_codigo ?? "—"}</dd>
              <dt>Fecha</dt>
              <dd>{doc.fecha ?? "—"}</dd>
              <dt>Estado</dt>
              <dd>{doc.estado_label}</dd>
              <dt>Forma de pago</dt>
              <dd>
                {doc.forma_pago_nombre
                  ?? (doc.forma_pago ? `Código ${doc.forma_pago}` : "—")}
              </dd>
              {doc.referencia ? (
                <>
                  <dt>Referencia</dt>
                  <dd>{doc.referencia}</dd>
                </>
              ) : null}
              <dt>Total</dt>
              <dd>
                <strong>
                  {doc.total !== null ? `${doc.total.toFixed(2)} €` : "—"}
                </strong>
              </dd>
            </dl>

            {ciclo ? (
              <div className="erp-doc-ciclo">
                {badge ? <span className={badge.className}>{badge.label}</span> : null}
                {ciclo.origen.length > 0 ? (
                  <span>
                    {current.docType === "facturas" ? "Creada" : "Creado"} desde{" "}
                    {ciclo.origen.map((ref) => (
                      <button key={ref.numero} type="button"
                              className="erp-doc-ciclo-link"
                              onClick={() => navigate(ref)}>
                        {TYPE_LABELS[ref.doc_type].toLowerCase()} {ref.numero}
                      </button>
                    ))}
                  </span>
                ) : null}
              </div>
            ) : null}

            {/* Avisos de la regla de negocio (E3-B-fix1). */}
            {ciclo && isSourceType &&
             (current.docType === "presupuestos" || current.docType === "pedidos") &&
             ciclo.albaranes.length > 0 ? (
              <p className="erp-doc-ciclo-aviso">
                Este {TYPE_LABELS[current.docType].toLowerCase()} ya tiene el
                albarán {refLinks(ciclo.albaranes)}. La factura se genera
                desde el albarán.
              </p>
            ) : null}
            {ciclo && isSourceType && ciclo.facturas.length > 0 ? (
              <p className="erp-doc-ciclo-aviso">
                Ya facturado en {refLinks(ciclo.facturas)}.
              </p>
            ) : null}

            {doc.lines.length > 0 ? (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Artículo</th>
                    <th>Descripción</th>
                    <th>Cant.</th>
                    <th>Precio</th>
                    <th>Total</th>
                  </tr>
                </thead>
                <tbody>
                  {doc.lines.map((ln) => (
                    <tr key={`${ln.position}-${ln.description}`}>
                      <td>{ln.position}</td>
                      <td className="muted small">{ln.codart ?? "—"}</td>
                      <td>{ln.description}</td>
                      <td>{ln.quantity}</td>
                      <td>{ln.unit_price.toFixed(2)}</td>
                      <td>{ln.line_total.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="muted">Sin líneas.</p>
            )}
          </>
        ) : null}

        <div className="modal-actions">
          <button type="button" className="button secondary" onClick={onClose}>
            Cerrar
          </button>
          {doc && canEdit
            ? actions.map((action) => (
                <button
                  key={action.target}
                  type="button"
                  className={action.primary ? "button" : "button secondary"}
                  disabled={!!jobId}
                  onClick={() => {
                    setCreateError(null);
                    setConvertTarget(action.target);
                  }}
                >
                  Crear {TARGET_LABELS[action.target]}
                </button>
              ))
            : null}
        </div>
      </div>

      {doc && convertTarget ? (
        <ConvertConfirmModal
          doc={doc}
          docType={current.docType}
          target={convertTarget}
          existing={existingChildren(convertTarget)}
          contextWarning={
            actions.find((a) => a.target === convertTarget)?.contextWarning
              ?? null
          }
          submitting={!!jobId}
          onCancel={() => setConvertTarget(null)}
          onSubmit={async (opts) => {
            setCreateError(null);
            setCreated(null);
            try {
              const r = await convertFactusolDocument(
                current.docType, current.serie, current.codigo,
                { target: convertTarget, ...opts },
              );
              setConvertTarget(null);
              setJobId(r.job_id);
            } catch (e) {
              // 409 anti-duplicado (carrera: alguien lo creó después de abrir
              // el modal) u otro error — se enseña DENTRO del diálogo.
              throw new Error(
                extractErrorMessage(e, "No se pudo encolar la creación."),
              );
            }
          }}
        />
      ) : null}
    </div>
  );
}

/** Confirmación de conversión — mismo patrón que el modal de emisión E2:
 *  total, aviso de irreversibilidad, serie heredada con override y fecha.
 *  Si el origen YA tiene un hijo de ese tipo, avisa y exige «de todos
 *  modos» (`force`); `contextWarning` añade el aviso de negocio (p. ej.
 *  «ya está facturado, ¿crear un albarán igualmente?»). */
function ConvertConfirmModal({
  doc,
  docType,
  target,
  existing,
  contextWarning,
  submitting,
  onCancel,
  onSubmit,
}: {
  doc: FactusolDocumentDetail;
  docType: FactusolDocType;
  target: FactusolConvertTarget;
  existing: FactusolCycleRef[];
  contextWarning: string | null;
  submitting: boolean;
  onCancel: () => void;
  onSubmit: (opts: {
    serie?: number | null; fecha?: string | null; force?: boolean;
  }) => Promise<void>;
}) {
  const [serie, setSerie] = useState<number | null>(null);
  const [fecha, setFecha] = useState(today());
  const [series, setSeries] = useState<FactusolSerie[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // El aviso anti-duplicado puede venir del ciclo ya cargado o de un 409
  // del backend (carrera). En ambos casos el botón pasa a «de todos modos».
  const [serverDuplicate, setServerDuplicate] = useState(false);
  const hasDuplicate = existing.length > 0 || serverDuplicate;

  useEffect(() => {
    getFactusolSeries()
      .then((r) => setSeries(
        [...r.items].sort(
          (a, b) => Number(b.is_known) - Number(a.is_known) || a.serie - b.serie,
        ),
      ))
      .catch(() => undefined);
  }, []);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await onSubmit({
        serie,
        fecha: fecha || null,
        force: hasDuplicate,
      });
    } catch (e) {
      const message = extractErrorMessage(e, "No se pudo crear el documento.");
      setError(message);
      if (/ya tiene/i.test(message)) setServerDuplicate(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true"
         aria-label={`Crear ${TARGET_LABELS[target]}`}>
      <div className="modal-dialog erp-emit-modal">
        <h2>
          Crear {TARGET_LABELS[target]} desde{" "}
          {TYPE_LABELS[docType].toLowerCase()} {doc.numero}
        </h2>
        <p>
          Total:{" "}
          <strong>
            {doc.total !== null ? `${doc.total.toFixed(2)} €` : "—"}
          </strong>
        </p>
        <p className="form-error">
          Se creará un documento <strong>real</strong> en FACTUSOL, enlazado a
          este {TYPE_LABELS[docType].toLowerCase()}. Esta acción no es
          reversible desde el CRM.
        </p>
        {existing.length > 0 ? (
          <p className="form-error">
            Este {TYPE_LABELS[docType].toLowerCase()} ya tiene{" "}
            {TARGET_LABELS[target]}{" "}
            <strong>{existing.map((r) => r.numero).join(", ")}</strong>. Crear
            otro duplicará el documento en la contabilidad.
          </p>
        ) : null}
        {contextWarning ? (
          <p className="form-error">{contextWarning}</p>
        ) : null}
        {error ? <p className="form-error">{error}</p> : null}

        <label className="field">
          <span>Empresa emisora / Serie</span>
          <select
            value={serie ?? ""}
            aria-label="Serie del documento nuevo"
            onChange={(e) =>
              setSerie(e.target.value ? Number(e.target.value) : null)
            }
          >
            <option value="">
              Heredar la del origen{doc.serie !== null ? ` (serie ${doc.serie})` : ""}
            </option>
            {series.map((s) => (
              <option key={s.serie} value={s.serie}>
                {s.serie} · {s.nombre}
              </option>
            ))}
          </select>
        </label>
        <span className="muted small">
          {serie === null
            ? "El documento nuevo se numera en la serie del origen."
            : `Se fuerza la serie ${serie}, ignorando la del origen.`}
        </span>

        <label className="field">
          <span>Fecha del documento</span>
          <input
            type="date"
            value={fecha}
            aria-label="Fecha del documento nuevo"
            onChange={(e) => setFecha(e.target.value)}
          />
        </label>

        <div className="modal-actions">
          <button type="button" className="button secondary"
                  onClick={onCancel} disabled={busy || submitting}>
            Cancelar
          </button>
          <button type="button" className="button"
                  onClick={submit} disabled={busy || submitting}>
            {busy || submitting
              ? "Creando…"
              : hasDuplicate
                ? `Crear ${TARGET_LABELS[target]} de todos modos`
                : `Crear ${TARGET_LABELS[target]}`}
          </button>
        </div>
      </div>
    </div>
  );
}
