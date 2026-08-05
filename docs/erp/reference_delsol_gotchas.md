# API DELSOL — trampas conocidas

Lista viva de comportamientos de la API de FACTUSOL/DELSOL que **no** están en
su documentación y que ya nos han costado un PR de arreglo cada uno. Léela
antes de tocar `app/integrations/factusol/`.

---

## 1. Un filtro sobre una columna inexistente devuelve `[]`, no un error

**La más cara.** `CargaTabla` acepta cualquier nombre de columna en el `filtro`
y, si no existe, responde `200 OK` con `resultado: null` → lista vacía. Sin
excepción, sin log, sin pista.

*Cómo nos mordió (C-3-fix1):* la búsqueda de clientes salió a producción
filtrando por `NOMCLI`, `CIFCLI`, `DIRCLI` y `NACCLI`, que no existen — los
reales son `NOFCLI`/`NOCCLI`, `NIFCLI`, `DOMCLI`, `PAICLI`. Estuvo días
devolviendo «no hay resultados» y parecía que la base estaba vacía.

**Regla:** antes de filtrar por una columna que no esté ya en
`factusol-schema.md`, vuelca los nombres reales:

```python
rows = client.load_table("F_XXX", filtro="1=1", ejercicio="2026")
print(list(rows[0].keys()))
```

`scripts/factusol_discover_quotes.py` hace justo eso sobre varias tablas
candidatas.

**Cuidado con el corolario:** en `EscribirRegistro` una columna inexistente
**sí** falla. O sea: la lectura te miente en silencio y la escritura te revienta
en producción. Nunca escribas contra un mapeo que no hayas verificado leyendo.

## 2. El token caducado llega como HTTP 200

No hay 401. Llega `200` con `respuesta: "Unauthorized"` en el cuerpo. Con un
JWT de ~3 minutos eso pasa a mitad de cualquier secuencia de escrituras.
`client._request` lo trata igual que un 401: re-autentica una vez y reintenta.

Otros valores de `respuesta` con HTTP 200 que significan error:
`"BDNoExiste"` (ejercicio sin base de datos).

## 3. `filtro` vacío devuelve `null`, no «todo»

Hay que mandar `1=1` para decir «sin filtro». Es el default de
`client.load_table` (`FILTRO_TODOS`).

## 4. No hay `LIMIT`

El `filtro` acepta `ORDER BY`, pero no hay forma de limitar filas. Todo recorte
se hace en Python **después** de traer el resultado completo:

- `next_codfac` / `next_codcli` / `next_codpre`: `1=1 ORDER BY <PK> DESC` y se
  toma la primera fila.
- Búsquedas: `SEARCH_NAME_LIMIT`, `ARTICLE_SEARCH_LIMIT`, `QUOTE_LIST_LIMIT`.

Y por lo mismo: **cuidado con las funciones de fecha en el filtro**. El
dialecto SQL subyacente no está documentado; una función no soportada caería en
la trampa nº 1 y devolvería `[]`. `list_quotes` filtra por fecha en Python a
propósito.

## 5. La respuesta viene anidada como `{columna, dato}`

`CargaTabla` no devuelve objetos: devuelve una lista de listas de pares.

```json
[[{"columna":"CODCLI","dato":1}, {"columna":"NOFCLI","dato":"Acme"}], …]
```

`_rows_to_dicts` lo normaliza. Las escrituras van en el formato simétrico
(`_to_api_record`).

## 6. `BorrarRegistros` es GET con path params

`GET /admin/BorrarRegistros/{ejercicio}/{tabla}/{filtro}` — no un DELETE ni un
POST con cuerpo.

## 7. Las rutas cuelgan de `/admin/`, no de `/registros/`

Los endpoints `/registros/*` del Sprint 0 daban 404. Los buenos son
`/admin/CargaTabla`, `/admin/EscribirRegistro`, `/admin/ActualizarRegistro`,
`/admin/BorrarRegistros`. Siguen siendo sobreescribibles por env
(`FACTUSOL_PATH_LOAD_TABLE`, …) por si DELSOL los cambia.

## 8. No hay inserción múltiple

`EscribirRegistro` inserta **un** registro. Una factura con 12 líneas son 13
llamadas. De ahí la compensación manual de `emit_invoice` cuando falla a mitad.

## 9. F_PRE no tiene tabla de líneas

Los presupuestos son **mono-línea**: cada fila de `F_PRE` es un presupuesto
completo y el desglose es texto en `REFPRE` (250 car.). `F_LPRE` no existe;
`F_LPP` son líneas de **F_PPR** (pedidos a proveedor), no de presupuestos.

Por eso C-4 guarda el desglose en `factusol_quote_lines_cache`. Ver
`factusol-proformas.md`.

## 10. `PCOART` es coste, no precio de venta

En F_ART, `PCOART` es el **precio de coste**. Usarlo como precio de venta
factura al cliente lo que cuesta comprar el artículo.

El nombre de la columna de venta **no está verificado** contra la base de
Bomedia: el descubrimiento de C-4 solo volcó las 15 primeras columnas. En vez
de apostar por `PVPART`, `quotes.detect_price_column()` mira las claves reales
que devuelve `CargaTabla` (que sirve la fila completa) y elige la primera
candidata que exista.

Es una variante especialmente traicionera de la trampa nº 1: **en lectura, una
columna inexistente no da error ni `[]` — `row.get()` devuelve `None`**. Los
precios saldrían todos en blanco, sin un solo log. Por eso, cuando no hay
columna reconocible, el adaptador deja `precio_venta = None` (la UI muestra el
campo vacío, nunca `0.00`) y escribe un `WARNING` con las columnas reales.

Confirmar cuál usa esta base:

```bash
docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \
    python -m scripts.factusol_discover_article_prices
```

## 11. El cliente lo crea la app externa Woo→FACTUSOL

En los pedidos de WooCommerce, la app externa crea el pedido **y el cliente**.
Si el CRM intenta crearlo también, choca: `BDEscribirRegistroError` (C-2-fix1).
Por eso `create_customer` deduplica por NIF y el endpoint rechaza con 409 los
clientes con pedidos Woo.

---

## Regla general

Toda escritura a FACTUSOL va por la cola **`factusol:writes`** con
`worker-factusol` a **concurrency=1**. Los códigos de documento (`CODFAC`,
`CODCLI`, `CODPRE`) se calculan con `MAX+1` justo antes de escribir; sin
serializar, dos operaciones simultáneas se pisan la numeración.
