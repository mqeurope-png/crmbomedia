"use client";

import { useEffect, useState } from "react";
import {
  listPlantillaUsuarios,
  setPlantillaCarpetaAcceso,
  type PlantillaCarpetaNode,
  type PlantillaUsuario,
} from "../../lib/erpApi";
import { extractErrorMessage } from "../../lib/errors";
import { ErpModalShell } from "./ErpModalShell";

/** Administración: a quién se acota una carpeta de plantillas (la
 *  «compartición» de las carpetas de correo, al revés): las plantillas son de
 *  la empresa y se ven todas; quien esté marcado en alguna carpeta pasa a ver
 *  SOLO sus carpetas (y sus subcarpetas). Quien no esté en ninguna, todas. */
export function PlantillaAccesoModal({
  carpeta,
  onClose,
  onSaved,
}: {
  carpeta: PlantillaCarpetaNode;
  onClose: () => void;
  onSaved: (userIds: string[]) => void;
}) {
  const [usuarios, setUsuarios] = useState<PlantillaUsuario[] | null>(null);
  const [marcados, setMarcados] = useState<Set<string>>(() => new Set(carpeta.acceso_user_ids ?? []));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    listPlantillaUsuarios()
      .then((items) => { if (alive) setUsuarios(items); })
      .catch((e) => { if (alive) setError(extractErrorMessage(e, "No se pudieron cargar los usuarios.")); });
    return () => { alive = false; };
  }, []);

  function toggle(id: string) {
    setMarcados((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const r = await setPlantillaCarpetaAcceso(carpeta.id, [...marcados]);
      onSaved(r.acceso_user_ids);
    } catch (e) {
      setError(extractErrorMessage(e, "No se pudo guardar el acceso."));
      setSaving(false);
    }
  }

  const propios = new Set(carpeta.acceso_user_ids ?? []);

  return (
    <ErpModalShell label={`Acceso a la carpeta ${carpeta.nombre}`}
                   title={<>Acceso a «{carpeta.nombre}»</>}
                   onClose={onClose} disabled={saving}>
      <p className="muted small">
        Sin nadie marcado, la carpeta la ve todo el mundo. Quien marques aquí (o
        en cualquier otra carpeta) verá <strong>solo</strong> sus carpetas y sus
        subcarpetas, también en el buscador. Administración lo ve siempre todo.
      </p>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      {usuarios === null && !error ? <p className="muted">Cargando usuarios…</p> : null}
      {usuarios ? (
        <ul className="pl-acceso-lista" aria-label="Usuarios">
          {usuarios.map((u) => (
            <li key={u.id}>
              <label className="field-toggle">
                <input type="checkbox" checked={marcados.has(u.id)} disabled={saving}
                       aria-label={`Acceso de ${u.nombre}`}
                       onChange={() => toggle(u.id)} />
                <span>
                  {u.nombre} <span className="muted small">{u.email}</span>
                  {u.restringido && !propios.has(u.id) ? (
                    <span className="chip is-warning" title="Ya está acotado a otras carpetas">
                      acotado
                    </span>
                  ) : null}
                </span>
              </label>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="modal-actions">
        <button type="button" className="button secondary" onClick={onClose} disabled={saving}>
          Cancelar
        </button>
        <button type="button" className="button" onClick={() => void save()}
                disabled={saving || usuarios === null}>
          {saving ? "Guardando…" : "Guardar acceso"}
        </button>
      </div>
    </ErpModalShell>
  );
}
