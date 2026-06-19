"use client";

import { useEffect, useState } from "react";
import { EntityFilterBuilder } from "../entity/EntityFilterBuilder";
import {
  getEntityFilterSchema,
  type FieldDescriptor,
} from "../../lib/entitySchema";

type Props = {
  condition: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
};

/**
 * PR-Fixes-Pase-2 Bug B.
 *
 * Editor de condición del step `condition` que reutiliza el
 * `EntityFilterBuilder` del filtro de Contactos. Con esto el
 * comercial usa los MISMOS campos (Email, Tags, Estado del ciclo,
 * custom fields) y operadores en humano ("es", "contiene", "está
 * vacío") que en `/contacts`.
 *
 * El árbol IR que produce el builder (`{operator, children}` +
 * `{type: "rule", field, comparator, value}`) se persiste tal cual
 * en `step.config.condition`. El evaluador del backend (
 * `app/workflows/conditions.py:evaluate`) acepta tanto este formato
 * como el legacy de workflow — ver `_normalize_logical` y
 * `_normalize_leaf_op`.
 */
export function WorkflowConditionBuilder({ condition, onChange }: Props) {
  const [fields, setFields] = useState<FieldDescriptor[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    getEntityFilterSchema("contact")
      .then((schema) => {
        if (!cancelled) setFields(schema.fields);
      })
      .catch(() => {
        if (!cancelled) setFields([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (fields === null) {
    return <p className="muted small">Cargando filtros…</p>;
  }
  if (fields.length === 0) {
    return (
      <p className="form-error small">
        No se pudo cargar el catálogo de filtros del contacto.
      </p>
    );
  }
  return (
    <EntityFilterBuilder
      fields={fields}
      value={condition || {}}
      onChange={onChange}
    />
  );
}
