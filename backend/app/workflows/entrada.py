"""El paso de entrada de un workflow: exactamente uno, y es el disparador.

El motor arranca cada run por el paso con `is_entry=True` (`_entry_step`). El
10/10/2026 el workflow «Respuesta a leads (Fase 1)» apareció en producción con
cinco pasos marcados como entrada —los cuatro de más con conexión de entrada,
así que no eran entradas reales— y el editor bloqueaba el guardado («Solo
puede haber un paso de entrada») sin decir cuáles sobraban. Aquí vive la regla
que aplican tanto el sembrado como el guardado del editor, para que la marca no
pueda volver a desdoblarse: si hay exactamente uno marcado, se respeta; si no,
se elige el disparador (sin conexiones de entrada si lo hay), y si no hay
disparador, el primer paso sin conexiones de entrada, y si no, el primero.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence


def elegir_entrada(
    pasos: Sequence[tuple[str, str, bool]], destinos: Iterable[str],
) -> str | None:
    """`pasos` = `(id, tipo, marcado_como_entrada)` en orden; `destinos` =
    ids que reciben alguna arista. Devuelve el id del único paso que debe ser
    la entrada, o None si no hay pasos."""
    if not pasos:
        return None
    con_entrada = set(destinos)
    marcados = [pid for pid, _tipo, marcado in pasos if marcado]
    if len(marcados) == 1:
        return marcados[0]
    disparadores = [pid for pid, tipo, _m in pasos if tipo == "trigger"]
    for candidatos in (
        [pid for pid in disparadores if pid not in con_entrada],
        [pid for pid in disparadores if pid in marcados],
        disparadores,
        [pid for pid, _tipo, _m in pasos if pid not in con_entrada],
    ):
        if candidatos:
            return candidatos[0]
    return pasos[0][0]


def describir_paso(tipo: str, display_name: str | None, paso_id: str) -> str:
    """«Esperar (wait_time) · 3f2a…» para nombrar un paso en un mensaje."""
    nombre = (display_name or "").strip()
    cola = (paso_id or "")[:8]
    base = f"{nombre} ({tipo})" if nombre else tipo
    return f"{base} · {cola}" if cola else base
