"use client";

/** «Un contacto por empresa» (1/11/2026): el control de la lista de
 *  contactos («Todos los contactos de las empresas que cumplen» / «Un solo
 *  contacto por empresa»), el orden de preferencia (cada criterio se puede
 *  quitar o mover) y los recuentos «N empresas · M contactos · K
 *  seleccionados», con los que se quedan fuera por no tener empresa. */

export const CRITERIOS = ["principal", "email_valido", "interaccion", "antiguedad"] as const;
export type Criterio = (typeof CRITERIOS)[number];

export const ETIQUETA_CRITERIO: Record<Criterio, string> = {
  principal: "Contacto principal (marcado en la ficha de empresa)",
  email_valido: "Con email válido",
  interaccion: "Interacción más reciente",
  antiguedad: "El más antiguo",
};

export type Recuentos = { contactos: number; empresas: number; sin_empresa: number };

/** ¿Las reglas miran la empresa (empresa, parque o compras)? Con alguna, la
 *  vista va por defecto «un contacto por empresa». */
export function tieneReglasDeEmpresa(arbol: unknown): boolean {
  if (!arbol || typeof arbol !== "object") return false;
  const nodo = arbol as { field?: unknown; children?: unknown };
  const campo = typeof nodo.field === "string" ? nodo.field : "";
  if (/^(empresa_|parque_|compra_)/.test(campo)) return true;
  return Array.isArray(nodo.children) && nodo.children.some(tieneReglasDeEmpresa);
}

const NF = new Intl.NumberFormat("es-ES");

export function textoRecuentos(r: Recuentos | null, total: number, seleccionados: number,
                               uno: boolean): string {
  if (!r) return `${NF.format(total)} contactos · ${NF.format(seleccionados)} seleccionados`;
  const partes = [
    `${NF.format(r.empresas)} empresa${r.empresas === 1 ? "" : "s"}`,
    uno
      ? `${NF.format(total)} contacto${total === 1 ? "" : "s"} (uno por empresa, de `
        + `${NF.format(r.contactos)} que cumplen)`
      : `${NF.format(total)} contacto${total === 1 ? "" : "s"}`,
    `${NF.format(seleccionados)} seleccionado${seleccionados === 1 ? "" : "s"}`,
  ];
  if (r.sin_empresa > 0) {
    partes.push(uno
      ? `${NF.format(r.sin_empresa)} sin empresa, fuera`
      : `${NF.format(r.sin_empresa)} sin empresa`);
  }
  return partes.join(" · ");
}

type Props = {
  uno: boolean;
  onUnoChange: (uno: boolean) => void;
  /** Los criterios activos, en orden. null = el de siempre. */
  orden: string[] | null;
  onOrdenChange: (orden: string[]) => void;
  recuentos: Recuentos | null;
  total: number;
  seleccionados: number;
};

export function UnoPorEmpresa({
  uno, onUnoChange, orden, onOrdenChange, recuentos, total, seleccionados,
}: Props) {
  const activos = (orden ?? [...CRITERIOS]).filter(
    (c): c is Criterio => (CRITERIOS as readonly string[]).includes(c));
  const inactivos = CRITERIOS.filter((c) => !activos.includes(c));

  const mover = (i: number, d: -1 | 1) => {
    const j = i + d;
    if (j < 0 || j >= activos.length) return;
    const next = [...activos];
    [next[i], next[j]] = [next[j], next[i]];
    onOrdenChange(next);
  };
  const quitar = (c: Criterio) => onOrdenChange(activos.filter((x) => x !== c));
  const poner = (c: Criterio) => onOrdenChange([...activos, c]);

  return (
    <section className="uno-por-empresa" aria-label="Contactos por empresa">
      <div role="radiogroup" aria-label="Qué contactos de cada empresa"
           style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        <label style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <input type="radio" name="uno-por-empresa" checked={!uno}
                 onChange={() => onUnoChange(false)} style={{ width: "auto" }} />
          Todos los contactos de las empresas que cumplen
        </label>
        <label style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <input type="radio" name="uno-por-empresa" checked={uno}
                 onChange={() => onUnoChange(true)} style={{ width: "auto" }} />
          Un solo contacto por empresa
        </label>
      </div>
      {uno ? (
        <div className="muted small" aria-label="Orden de preferencia">
          <span>Orden de preferencia (si empatan, el siguiente):</span>
          <ol style={{ margin: "4px 0 0", paddingLeft: 20 }}>
            {activos.map((c, i) => (
              <li key={c}>
                <label style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <input type="checkbox" checked onChange={() => quitar(c)}
                         style={{ width: "auto" }} aria-label={`Quitar «${ETIQUETA_CRITERIO[c]}»`} />
                  {ETIQUETA_CRITERIO[c]}
                </label>{" "}
                <button type="button" className="button small secondary" disabled={i === 0}
                        aria-label={`Subir «${ETIQUETA_CRITERIO[c]}»`} onClick={() => mover(i, -1)}>
                  ↑
                </button>{" "}
                <button type="button" className="button small secondary"
                        disabled={i === activos.length - 1}
                        aria-label={`Bajar «${ETIQUETA_CRITERIO[c]}»`} onClick={() => mover(i, 1)}>
                  ↓
                </button>
              </li>
            ))}
          </ol>
          {inactivos.length ? (
            <p style={{ margin: "4px 0 0" }}>
              Sin usar:{" "}
              {inactivos.map((c) => (
                <label key={c} style={{ display: "inline-flex", alignItems: "center", gap: 4,
                                        marginRight: 12 }}>
                  <input type="checkbox" checked={false} onChange={() => poner(c)}
                         style={{ width: "auto" }} aria-label={`Usar «${ETIQUETA_CRITERIO[c]}»`} />
                  {ETIQUETA_CRITERIO[c]}
                </label>
              ))}
            </p>
          ) : null}
        </div>
      ) : null}
      <p className="muted small" role="status" aria-label="Recuentos">
        {textoRecuentos(recuentos, total, seleccionados, uno)}
      </p>
    </section>
  );
}
