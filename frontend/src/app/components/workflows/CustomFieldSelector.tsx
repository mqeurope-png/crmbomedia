"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "../../lib/api";

type CustomFieldKey = { key: string; type: string };

type Props = {
  value: string;
  valueValue: string;
  onChange: (next: { field: string; value: string; type: string }) => void;
};

/**
 * PR-Fixes-Pase-3 Bug 6.
 *
 * Selector de custom field para el paso "Modificar campo". Carga la
 * unión de claves vistas en `contacts.custom_fields` desde el endpoint
 * nuevo `/api/contacts/custom-field-keys` (devuelve `{key, type}`).
 *
 * El input del valor adapta su tipo según el tipo inferido del field
 * elegido (number → input type=number, date → datepicker, boolean →
 * checkbox, resto → text).
 *
 * No bloqueamos al operador si quiere meter un field nuevo que aún no
 * existe en BD: hay un input "Otro campo" como fallback.
 */
export function CustomFieldSelector({ value, valueValue, onChange }: Props) {
  const [keys, setKeys] = useState<CustomFieldKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [newFieldMode, setNewFieldMode] = useState(false);

  useEffect(() => {
    let cancelled = false;
    apiFetch<CustomFieldKey[]>("/api/contacts/custom-field-keys")
      .then((rows) => {
        if (cancelled) return;
        setKeys(rows);
        // Si el field actual no está en la lista y no está vacío,
        // tampoco es un campo nuevo — solo asumimos modo "nuevo"
        // cuando el usuario lo pide explícitamente.
      })
      .catch(() => {
        if (!cancelled) setKeys([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const selected = keys.find((k) => k.key === value);
  const type = selected?.type ?? "text";

  if (loading) {
    return <p className="muted small">Cargando custom fields…</p>;
  }

  return (
    <>
      <label>
        Campo a modificar
        {newFieldMode || (value && !selected) ? (
          <>
            <input
              type="text"
              value={value}
              onChange={(e) =>
                onChange({ field: e.target.value, value: valueValue, type })
              }
              placeholder="ej. sector"
            />
            <button
              type="button"
              className="muted small workflow-link-button"
              onClick={() => {
                setNewFieldMode(false);
                onChange({ field: "", value: valueValue, type });
              }}
            >
              ← elegir uno existente
            </button>
          </>
        ) : (
          <>
            <select
              value={value}
              onChange={(e) =>
                onChange({
                  field: e.target.value,
                  value: valueValue,
                  type:
                    keys.find((k) => k.key === e.target.value)?.type ?? "text",
                })
              }
            >
              <option value="">— Selecciona —</option>
              {keys.map((k) => (
                <option key={k.key} value={k.key}>
                  {k.key} ({_typeLabel(k.type)})
                </option>
              ))}
            </select>
            <button
              type="button"
              className="muted small workflow-link-button"
              onClick={() => setNewFieldMode(true)}
            >
              + crear nuevo campo
            </button>
          </>
        )}
      </label>
      <label>
        Nuevo valor
        {type === "number" ? (
          <input
            type="number"
            value={valueValue}
            onChange={(e) =>
              onChange({ field: value, value: e.target.value, type })
            }
          />
        ) : type === "date" ? (
          <input
            type="date"
            value={valueValue}
            onChange={(e) =>
              onChange({ field: value, value: e.target.value, type })
            }
          />
        ) : type === "boolean" ? (
          <select
            value={valueValue}
            onChange={(e) =>
              onChange({ field: value, value: e.target.value, type })
            }
          >
            <option value="">—</option>
            <option value="true">Sí</option>
            <option value="false">No</option>
          </select>
        ) : (
          <input
            type="text"
            value={valueValue}
            onChange={(e) =>
              onChange({ field: value, value: e.target.value, type })
            }
            placeholder='Texto o variable {{ contact.first_name }}'
          />
        )}
      </label>
    </>
  );
}

function _typeLabel(type: string): string {
  return {
    text: "texto",
    number: "número",
    date: "fecha",
    boolean: "sí/no",
  }[type] ?? type;
}
