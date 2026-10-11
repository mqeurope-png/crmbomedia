import type { AsistenteAlcance } from "./emailsApi";

/** Qué histórico mira la IA al redactar: solo el hilo (nivel 1), todo lo del
 *  contacto (hilo + expediente + correos viejos que vienen a cuento, el
 *  defecto) o todo lo de la empresa (también lo de sus compañeros, etiquetado).
 *  Sin hilo (un correo nuevo) «solo este hilo» no significa nada. */
export const ALCANCES_ASISTENTE: ReadonlyArray<{ id: AsistenteAlcance; label: string }> = [
  { id: "hilo", label: "Solo este hilo" },
  { id: "contacto", label: "Todo lo de este contacto" },
  { id: "empresa", label: "Todo lo de la empresa" },
];

export const ALCANCES_SIN_HILO: ReadonlyArray<AsistenteAlcance> = ["contacto", "empresa"];

const CLAVE = "bohub.asistente.alcance";
const TODOS: ReadonlyArray<string> = ALCANCES_ASISTENTE.map((a) => a.id);

/** El histórico elegido por este usuario en este navegador (por id de usuario,
 *  así dos personas en el mismo navegador no se pisan). */
export function leerAlcance(usuarioId: string | null | undefined): AsistenteAlcance | null {
  if (!usuarioId) return null;
  try {
    const v = window.localStorage.getItem(`${CLAVE}.${usuarioId}`);
    return v && TODOS.includes(v) ? (v as AsistenteAlcance) : null;
  } catch {
    return null;
  }
}

export function guardarAlcance(usuarioId: string | null | undefined, alcance: AsistenteAlcance): void {
  if (!usuarioId) return;
  try {
    window.localStorage.setItem(`${CLAVE}.${usuarioId}`, alcance);
  } catch {
    /* sin almacenamiento local no pasa nada: se recuerda mientras dure la página */
  }
}

export function etiquetaAlcance(alcance: string | null | undefined): string {
  return ALCANCES_ASISTENTE.find((a) => a.id === alcance)?.label.toLowerCase() ?? "todo lo de este contacto";
}
