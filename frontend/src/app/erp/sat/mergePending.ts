import type { SatQueueItem } from "../../lib/erpApi";

/** «Todos pendientes» (vista Tarjetas): los pedidos de los cuatro pasos del
 *  taller juntos en UNA rejilla, ordenados por la fecha del pedido según el
 *  selector «Orden» (recientes primero por defecto) y mezclando estados. Sin
 *  fecha, al final. Orden estable a igual fecha (el de las listas). */
export function mergePending(
  lists: SatQueueItem[][], sort: "fecha_desc" | "fecha_asc",
): SatQueueItem[] {
  const dir = sort === "fecha_asc" ? 1 : -1;
  return lists.flat()
    .map((o, i) => ({ o, i, k: o.placed_at ?? "" }))
    .sort((a, b) => {
      if (a.k === b.k) return a.i - b.i;
      if (!a.k) return 1;
      if (!b.k) return -1;
      return a.k < b.k ? -dir : dir;
    })
    .map(({ o }) => o);
}
