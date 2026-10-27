"""El índice de nuestras webs (WordPress, REST pública, sin credenciales).

Páginas y entradas (sin legales, carrito ni pruebas) y productos de la Store
API sin repuestos, consumibles ni servicios, una fila por idioma, en la tabla
`web_indice`. Lo recorre una pasada al mes en worker-web (o «Reindexar ahora»)
y el redactor busca en él full-text y se lleva fragmentos con su URL.

- `lector`: lee una web (red, idiomas, reglas, precios «como dice la web»).
- `indice`: lo vuelca a la tabla (visto / ido, sin borrar) y da el estado.
- `pasadas`: los recorridos, una web por trabajo en la cola `webs:indice`.
- `job`: el tic diario que lanza el recorrido mensual.
- `reglas`: qué entra y qué no (tabla `web_indice_reglas`).
- `busqueda`: lo que usa el redactor (`buscar`, `fragmentos_de`).

Nada de esto escribe en las webs ni en FACTUSOL. El precio que se guarda es el
de la WEB: el redactor puede decir «en la web está a X», nunca ofertar con él.
"""
from app.services.webs.busqueda import Fragmento, buscar, fragmento_como_dict, fragmentos_de

__all__ = ["Fragmento", "buscar", "fragmento_como_dict", "fragmentos_de"]
