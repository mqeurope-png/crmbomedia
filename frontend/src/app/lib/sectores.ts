/** Los sectores de empresa: una lista corta, no texto libre (1/11/2026).
 *  Espejo de `backend/app/services/sectores.py` (el backend valida). Lo que
 *  una empresa tuviera escrito antes se conserva mientras nadie lo cambie. */
export const SECTORES: ReadonlyArray<string> = [
  "Centro educativo",
  "Administración pública",
  "Rotulación y señalética",
  "Imprenta y artes gráficas",
  "Personalización y regalo promocional",
  "Textil y estampación",
  "Packaging y embalaje",
  "Industria y fabricación",
  "Arquitectura, diseño y maquetas",
  "Artesanía, joyería y decoración",
  "Duplicación y audiovisual",
  "Distribuidor / revendedor",
  "Vending",
  "Maker / particular",
  "Otro",
];

/** El sector de la lista que es `valor` (sin mirar mayúsculas ni espacios),
 *  o null si es un texto de antes que no está en la lista. */
export function sectorDeLaLista(valor: string | null | undefined): string | null {
  if (!valor) return null;
  const v = valor.trim().toLowerCase();
  return SECTORES.find((s) => s.toLowerCase() === v) ?? null;
}

/** ¿Es un sector de la lista, o un texto de antes? */
export function esSectorDeLaLista(valor: string | null | undefined): boolean {
  return sectorDeLaLista(valor) !== null;
}
