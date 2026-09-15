import type { ReactNode } from "react";

/** Lote 2 · E7 — barra de la acción principal de una pantalla del ERP.
 *
 *  En escritorio es una fila normal con los botones a la derecha; por debajo
 *  de 768 px se queda pegada al borde inferior (`.erp-primary-sticky`,
 *  `position: sticky`), con el primario a todo el ancho y 48 px de alto: lo
 *  único pulsable a distancia de pulgar en el taller. Va al FINAL del
 *  contenido de la pantalla (con `sticky` solo así se mantiene visible
 *  mientras se hace scroll). Uno por pantalla: el primario es la siguiente
 *  acción del sistema; el resto, secundarios. */
export function PrimaryActionBar({
  children, hint, className, label = "Acción principal",
}: {
  /** Botones: el primario (`.button`) primero, luego secundarios. */
  children: ReactNode;
  /** Frase corta sobre la consecuencia («Se creará en FACTUSOL al guardar»). */
  hint?: ReactNode;
  className?: string;
  /** Nombre accesible de la región. */
  label?: string;
}) {
  return (
    <div
      className={`erp-primary-sticky${className ? ` ${className}` : ""}`}
      role="region"
      aria-label={label}
    >
      {hint ? <p className="erp-primary-sticky-hint">{hint}</p> : null}
      <div className="erp-primary-sticky-actions">{children}</div>
    </div>
  );
}
