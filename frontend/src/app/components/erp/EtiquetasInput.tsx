"use client";

import { useId, useState } from "react";

const ETIQUETA_MAX = 60;
const ETIQUETAS_MAX = 20;

function norm(s: string): string {
  return s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
}

/** Etiquetas libres y múltiples de una plantilla («DTF», «escuelas»,
 *  «FESPA»…): se añaden con Intro o coma, se quitan con ×, y se sugieren las
 *  que ya se usan. Sin repetir (sin distinguir mayúsculas ni acentos), como
 *  el backend. */
export function EtiquetasInput({
  value,
  onChange,
  sugerencias = [],
  disabled = false,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  sugerencias?: string[];
  disabled?: boolean;
}) {
  const [draft, setDraft] = useState("");
  const listId = useId();
  const tienen = new Set(value.map(norm));
  const lleno = value.length >= ETIQUETAS_MAX;

  function add(...raws: string[]) {
    const next = [...value];
    const vistas = new Set(tienen);
    for (const raw of raws) {
      const texto = raw.replace(/\s+/g, " ").trim().slice(0, ETIQUETA_MAX);
      if (!texto || vistas.has(norm(texto)) || next.length >= ETIQUETAS_MAX) continue;
      vistas.add(norm(texto));
      next.push(texto);
    }
    if (next.length !== value.length) onChange(next);
  }

  return (
    <div className="pl-etiquetas">
      {value.length > 0 ? (
        <ul className="tag-chip-list tag-chip-list--dense" aria-label="Etiquetas">
          {value.map((e) => (
            <li key={norm(e)} className="tag-chip chip is-info">
              {e}
              <button type="button" className="tag-chip-remove" disabled={disabled}
                      aria-label={`Quitar la etiqueta ${e}`}
                      onClick={() => onChange(value.filter((x) => x !== e))}>
                ×
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <input
        type="text"
        value={draft}
        list={listId}
        maxLength={ETIQUETA_MAX}
        aria-label="Añadir etiqueta"
        placeholder={lleno ? `Máximo ${ETIQUETAS_MAX} etiquetas` : "Añadir etiqueta (Intro)"}
        disabled={disabled || lleno}
        onChange={(e) => {
          const v = e.target.value;
          if (v.includes(",")) {
            const partes = v.split(",");
            add(...partes.slice(0, -1));
            setDraft(partes[partes.length - 1] ?? "");
          } else {
            setDraft(v);
          }
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            add(draft);
            setDraft("");
          } else if (e.key === "Backspace" && !draft && value.length > 0) {
            onChange(value.slice(0, -1));
          }
        }}
        onBlur={() => {
          if (!draft.trim()) return;
          add(draft);
          setDraft("");
        }}
      />
      <datalist id={listId}>
        {sugerencias.filter((s) => !tienen.has(norm(s))).map((s) => <option key={s} value={s} />)}
      </datalist>
    </div>
  );
}
