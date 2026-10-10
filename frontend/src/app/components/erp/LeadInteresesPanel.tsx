"use client";

import { useState } from "react";
import {
  createLeadInteres,
  deleteLeadInteres,
  updateLeadInteres,
  type LeadInteresCambios,
  type LeadInteresConUso,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";

type Props = {
  /** El catálogo (null mientras carga). */
  items: LeadInteresConUso[] | null;
  error: string | null;
  onChange: (items: LeadInteresConUso[]) => void;
};

/** El catálogo de intereses del clasificador (Configuración ERP → Respuesta a
 *  leads): añadir, editar (etiqueta, descripción para el modelo, comercial,
 *  orden), desactivar y, solo si nada lo usa, borrar. Cada cambio se guarda
 *  al momento por su API (`/api/erp/leads/intereses`), aparte del «Guardar
 *  cambios» de la sección. */
export function LeadInteresesPanel({ items, error, onChange }: Props) {
  const [nuevo, setNuevo] = useState({ codigo: "", etiqueta: "", descripcion: "", comercial: true });
  const [creando, setCreando] = useState(false);
  const [errorAlta, setErrorAlta] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);

  const reemplazar = (fila: LeadInteresConUso) => {
    const lista = items ?? [];
    const next = lista.some((i) => i.codigo === fila.codigo)
      ? lista.map((i) => (i.codigo === fila.codigo ? fila : i))
      : [...lista, fila];
    next.sort((a, b) => a.orden - b.orden || a.codigo.localeCompare(b.codigo));
    onChange(next);
  };

  async function crear() {
    setCreando(true);
    setErrorAlta(null);
    setAviso(null);
    try {
      const fila = await createLeadInteres({
        codigo: nuevo.codigo.trim(), etiqueta: nuevo.etiqueta.trim(),
        descripcion: nuevo.descripcion.trim(), comercial: nuevo.comercial,
      });
      reemplazar(fila);
      setNuevo({ codigo: "", etiqueta: "", descripcion: "", comercial: true });
      setAviso(`Interés «${fila.label}» añadido: entra en el clasificador en la siguiente clasificación.`);
    } catch (e) {
      setErrorAlta(extractErrorMessage(e, "No se pudo añadir el interés."));
    } finally {
      setCreando(false);
    }
  }

  return (
    <div className="lead-intereses-panel">
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {items === null && !error ? <p className="muted small">Cargando el catálogo…</p> : null}
      {items && items.length > 0 ? (
        <table className="data-table data-table--responsive erp-settings-table lead-intereses-tabla">
          <thead>
            <tr>
              <th>Código</th>
              <th>Etiqueta</th>
              <th>Descripción (lo que lee el modelo)</th>
              <th>Comercial</th>
              <th>Orden</th>
              <th>Activo</th>
              <th>En uso</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((i) => (
              <FilaInteres
                key={i.codigo}
                interes={i}
                onGuardado={reemplazar}
                onBorrado={(codigo) => onChange((items ?? []).filter((x) => x.codigo !== codigo))}
              />
            ))}
          </tbody>
        </table>
      ) : null}
      {items && items.length === 0 && !error ? (
        <p className="muted small">El catálogo está vacío: se siembra la lista de partida al añadir el primero.</p>
      ) : null}

      <div className="lead-interes-alta">
        <label className="field">
          <span>Código</span>
          <input
            type="text"
            aria-label="Código del interés nuevo"
            placeholder="p. ej. smartjet"
            maxLength={40}
            value={nuevo.codigo}
            onChange={(e) => setNuevo({ ...nuevo, codigo: e.target.value })}
          />
        </label>
        <label className="field">
          <span>Etiqueta</span>
          <input
            type="text"
            aria-label="Etiqueta del interés nuevo"
            placeholder="p. ej. SmartJet"
            maxLength={80}
            value={nuevo.etiqueta}
            onChange={(e) => setNuevo({ ...nuevo, etiqueta: e.target.value })}
          />
        </label>
        <label className="field">
          <span>Descripción (para el modelo)</span>
          <textarea
            aria-label="Descripción del interés nuevo"
            placeholder="Cuándo aplica: qué pide el cliente, qué máquinas, qué palabras."
            maxLength={2000}
            value={nuevo.descripcion}
            onChange={(e) => setNuevo({ ...nuevo, descripcion: e.target.value })}
          />
        </label>
        <label className="field erp-check-field">
          <input
            type="checkbox"
            aria-label="Comercial del interés nuevo"
            checked={nuevo.comercial}
            onChange={(e) => setNuevo({ ...nuevo, comercial: e.target.checked })}
          />
          <span>Comercial (lleva plantilla de venta)</span>
        </label>
        <button
          type="button"
          className="button small"
          disabled={creando || !nuevo.codigo.trim() || !nuevo.etiqueta.trim()}
          onClick={crear}
        >
          {creando ? "Añadiendo…" : "Añadir interés"}
        </button>
      </div>
      {errorAlta ? <p className="form-error" role="alert">{errorAlta}</p> : null}
      {aviso ? <p className="form-success" role="status">{aviso}</p> : null}
    </div>
  );
}

function FilaInteres({
  interes, onGuardado, onBorrado,
}: {
  interes: LeadInteresConUso;
  onGuardado: (fila: LeadInteresConUso) => void;
  onBorrado: (codigo: string) => void;
}) {
  const [etiqueta, setEtiqueta] = useState(interes.label);
  const [descripcion, setDescripcion] = useState(interes.descripcion ?? "");
  const [comercial, setComercial] = useState(interes.comercial);
  const [orden, setOrden] = useState(String(interes.orden));
  const [ocupada, setOcupada] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const cambios: LeadInteresCambios = {};
  if (etiqueta.trim() !== interes.label) cambios.etiqueta = etiqueta.trim();
  if (descripcion.trim() !== (interes.descripcion ?? "")) cambios.descripcion = descripcion.trim();
  if (comercial !== interes.comercial) cambios.comercial = comercial;
  if (orden !== String(interes.orden) && orden.trim() !== "") cambios.orden = Number(orden);
  const hayCambios = Object.keys(cambios).length > 0;
  const enUso = interes.en_uso.clasificaciones + interes.en_uso.en_mapa > 0;
  const esOtro = interes.codigo === "otro";

  async function enviar(payload: LeadInteresCambios) {
    setOcupada(true);
    setError(null);
    try {
      const fila = await updateLeadInteres(interes.codigo, payload);
      // Lo guardado manda: los campos arrancan de lo que devolvió el servidor.
      setEtiqueta(fila.label);
      setDescripcion(fila.descripcion ?? "");
      setComercial(fila.comercial);
      setOrden(String(fila.orden));
      onGuardado(fila);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar el interés."));
    } finally {
      setOcupada(false);
    }
  }

  async function borrar() {
    setOcupada(true);
    setError(null);
    try {
      await deleteLeadInteres(interes.codigo);
      onBorrado(interes.codigo);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo borrar el interés."));
      setOcupada(false);
    }
  }

  const enUsoTexto = [
    interes.en_uso.clasificaciones
      ? `${interes.en_uso.clasificaciones} ${interes.en_uso.clasificaciones === 1 ? "lead" : "leads"}`
      : null,
    interes.en_uso.en_mapa
      ? `${interes.en_uso.en_mapa} ${interes.en_uso.en_mapa === 1 ? "fila" : "filas"} del mapa`
      : null,
  ].filter(Boolean).join(" · ");

  return (
    <tr className={interes.activo ? undefined : "is-inactivo"}>
      <td data-label="Código"><code>{interes.codigo}</code></td>
      <td data-label="Etiqueta">
        <input
          type="text"
          aria-label={`Etiqueta de ${interes.codigo}`}
          maxLength={80}
          value={etiqueta}
          disabled={ocupada}
          onChange={(e) => setEtiqueta(e.target.value)}
        />
      </td>
      <td data-label="Descripción">
        <textarea
          aria-label={`Descripción de ${interes.codigo}`}
          maxLength={2000}
          value={descripcion}
          disabled={ocupada}
          onChange={(e) => setDescripcion(e.target.value)}
        />
      </td>
      <td data-label="Comercial">
        <input
          type="checkbox"
          aria-label={`Comercial ${interes.codigo}`}
          checked={comercial}
          disabled={ocupada}
          onChange={(e) => setComercial(e.target.checked)}
        />
      </td>
      <td data-label="Orden">
        <input
          type="number"
          aria-label={`Orden de ${interes.codigo}`}
          min={0}
          max={10000}
          value={orden}
          disabled={ocupada}
          onChange={(e) => setOrden(e.target.value)}
        />
      </td>
      <td data-label="Activo">
        <input
          type="checkbox"
          aria-label={`Activo ${interes.codigo}`}
          checked={interes.activo}
          disabled={ocupada || esOtro}
          title={esOtro ? "«Otro» no se desactiva: es a donde va lo que no encaja" : undefined}
          onChange={(e) => enviar({ activo: e.target.checked })}
        />
      </td>
      <td data-label="En uso">
        <span className="muted small">{enUsoTexto || "—"}</span>
      </td>
      <td data-label="">
        <div className="lead-intereses-acciones">
          {hayCambios ? (
            <button
              type="button"
              className="button small"
              aria-label={`Guardar ${interes.codigo}`}
              disabled={ocupada}
              onClick={() => enviar(cambios)}
            >
              {ocupada ? "Guardando…" : "Guardar"}
            </button>
          ) : null}
          {!enUso && !esOtro ? (
            <button
              type="button"
              className="button small secondary"
              aria-label={`Borrar ${interes.codigo}`}
              disabled={ocupada}
              onClick={borrar}
            >
              Borrar
            </button>
          ) : (
            <span className="muted small" title="En uso: desactívalo en vez de borrarlo">
              {esOtro ? "fijo" : "en uso"}
            </span>
          )}
          {error ? <span className="form-error small" role="alert">{error}</span> : null}
        </div>
      </td>
    </tr>
  );
}
