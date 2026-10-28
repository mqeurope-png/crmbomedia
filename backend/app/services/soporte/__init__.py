"""El soporte de terceros (FLUX; artisJet y MBO cuando tengan URL): buscar y
leer artículos de los centros de soporte de la lista blanca.

- `buscar(session, consulta, idioma=, fuente=)` → `Busqueda` (título,
  extracto y enlace de cada resultado, avisos y fuentes consultadas).
- `articulo(session, fuente, articulo_id, idioma=)` → `Articulo` (texto
  limpio y HTML saneado). Es lo que usa el redactor: por fuente + id, nunca
  por una URL libre.
- `con_plazo(segundos, por_peticion=)`: el tope de tiempo con el que el
  redactor consulta el soporte dentro de la redacción.
- `comprobar_fuente(session, clave)`: qué devuelve de verdad la fuente (`ok`,
  `vacio` si es una cáscara de JavaScript o no trae texto, `error`).
- `fuente_como_dict(f)`: la forma `SoporteFuente` de `soporteApi.ts`.

Solo lee, por endpoints públicos y con los cerrojos de `externo.red`.
"""
from app.services.soporte.adaptadores import con_plazo, fijar_red_para_tests
from app.services.soporte.fuentes import fuente_como_dict
from app.services.soporte.servicio import articulo, buscar, comprobar_fuente
from app.services.soporte.tipos import Articulo, Busqueda, Resultado, SoporteError

__all__ = [
    "Articulo",
    "Busqueda",
    "Resultado",
    "SoporteError",
    "articulo",
    "buscar",
    "comprobar_fuente",
    "con_plazo",
    "fijar_red_para_tests",
    "fuente_como_dict",
]
