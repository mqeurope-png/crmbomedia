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

## 9. ~~F_PRE no tiene tabla de líneas~~ (corregido)

> ⚠️ **Esto era FALSO y se corrigió en C-4-fix3.** Se deja escrito porque el
> error en sí es la lección.

C-4 concluyó que `F_PRE` era mono-línea tras probar `F_LPRE`, `F_LPR` y `F_LPP`
sin acertar, y construyó una caché local entera (`factusol_quote_lines_cache`)
para suplir una tabla que **sí existe**: se llama **`F_LPS`**
(`CODLPS` → `CODPRE`, 3063 filas). Ver la tabla nº 12.

**La lección:** no des por cerrada la búsqueda de una tabla tras probar 5-10
candidatas. Los nombres de FACTUSOL no siguen un patrón fiable —
`F_LPS` ≠ `F_LPRE`— y una tabla ausente es indistinguible de una vacía porque
`CargaTabla` devuelve `[]` en ambos casos (trampa nº 1). Antes de concluir «no
existe», pide a Bart el listado de tablas o compara los totales con lo que
enseña el FACTUSOL de escritorio.

## 10. `PCOART` es coste — el precio de venta está en F_LTA

En `F_ART`, `PCOART` es el **precio de coste**. Usarlo como precio de venta
factura al cliente lo que cuesta comprar el artículo.

El de venta **no está en F_ART en absoluto**: vive en **`F_LTA`**, una fila por
artículo y tarifa (`ARTLTA` → `F_ART.CODART`, `PRELTA` = precio). Bomedia usa
`TARLTA=1`; sus precios son los que el FACTUSOL de escritorio enseña en la
columna «Venta».

```
F_LTA WHERE ARTLTA='99cy'  → TARLTA=1 PRELTA=80.0
F_LTA WHERE ARTLTA='1503'  → TARLTA=1 PRELTA=20.0
```

`PRELTA=0` es «tarifa sin configurar» (típico de los artículos que solo tienen
tarifa 2), no un artículo gratis: el adaptador lo trata como ausente y deja el
campo vacío, nunca `0.00`.

> Antes de C-4-fix3 se buscaba la columna dentro de F_ART (`PVPART`, `TAR1ART`…)
> con detección en runtime. No existía ninguna: el dato estaba en otra tabla.

## 11. Una tabla que no existe es indistinguible de una vacía

`CargaTabla` devuelve `[]` en los dos casos. Por eso C-4 concluyó que `F_LPS` no
existía tras probar `F_LPRE`/`F_LPR`/`F_LPP`, y construyó una caché local
entera para suplirla.

Antes de concluir «esta tabla no existe»: pide el listado real de tablas, o
compara los totales que devuelve la API con lo que enseña el FACTUSOL de
escritorio. Los nombres no siguen un patrón fiable.

## 12. F_LPS son las líneas de presupuesto

`F_LPS.CODLPS` → `F_PRE.CODPRE`, ordenadas por `POSLPS`. Columnas: `TIPLPS`,
`CODLPS`, `POSLPS`, `ARTLPS` (vacío en líneas de texto libre), `DESLPS`,
`CANLPS`, `DT1LPS`/`DT2LPS`/`DT3LPS`, `PRELPS`, `TOTLPS`, `IVALPS`.

Verificado: `CODLPS=574` → 4 líneas que suman 355, el `NET1PRE` de su cabecera.

Verificar ambas tablas en producción:

```bash
docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \
    python -m scripts.factusol_discover_article_prices
```

## 13. El mismo concepto cambia de nombre entre tablas

El email es `EMAPRE` en **F_CLI** y **F_ART**, pero `CEMPRE` en **F_PRE**. El
sufijo de tabla coincide, así que `EMAPRE` parece correcto y no lo es.

Y aquí la trampa nº 1 se invierte de la peor manera: **una columna inexistente
en `EscribirRegistro` no falla «esa columna», falla el registro ENTERO** con
`BDEscribirRegistroError`. Un solo nombre mal puesto bloquea la escritura
completa.

En producción impidió crear **cualquier** proforma con email hasta C-4-fix4.
Bisecarlo costó siete pruebas en vivo porque el error no dice qué columna
sobra:

```
payload mínimo                 ✅
payload completo               ❌
sin dirección                  ❌
sin bandas IVA + CPAPRE        ❌
sin TELPRE + EMAPRE            ✅   ← aquí estaba
solo TELPRE                    ✅
solo EMAPRE                    ❌   ← el culpable
solo CEMPRE                    ✅
```

**Regla:** no deduzcas el nombre de una columna del prefijo de otra tabla,
aunque el patrón encaje. Confírmalo leyendo una fila real de **esa** tabla.

Para que la próxima vez no haya que bisecar: `create_quote` y
`_write_quote_lines` registran en el log las columnas enviadas cuando
`EscribirRegistro` falla.

## 14. En las líneas, `ART…` es CODART interno — nunca EQUART

`F_LPS.ARTLPS`, `F_LPC.ARTLPC`, `F_LFA.ARTLFA`… **todos** los campos de artículo
de las tablas de líneas guardan el **`CODART` interno** (`99cy`, `1712`), no el
`EQUART` comercial (`Ink500mlCY`, `CDR80WPT`).

Meter un EQUART ahí **no da error al escribir**. El documento se crea, y luego
**el FACTUSOL de escritorio crashea al abrirlo**: «UPSS! Excepción no
controlada». Le pasó a la proforma 4350.

Es una trampa doble, porque la UI hace lo contrario a propósito: el autocomplete
muestra `EQUART` porque es el código que el operativo reconoce. La traducción
tiene que ocurrir **justo antes de escribir** (`quotes.resolve_codarts`), no en
la UI.

Comparar con una proforma que funcione es lo que lo delató: la 574 tiene
`ARTLPS='1712','99017','1682','99370'` — todo códigos internos, porque se creó
desde el escritorio.

## 15. `IVALPS` — sin confirmar si es porcentaje o código

La proforma 574, que abre bien, tiene `IVALPS=0` en **todas** sus líneas. Un
0 % de IVA no tiene sentido en un presupuesto español; un código «tipo general»
sí. Hasta cerrarlo, el CRM **no escribe la columna** y deja que FACTUSOL ponga
su default — que es justo el valor que tienen las proformas que funcionan. El
IVA real viaja en la cabecera (`PIVA1PRE` / `IIVA1PRE`), así que los totales
salen bien.

Para cerrarlo: `SELECT DISTINCT IVALPS FROM F_LPS`. Si salen 0/1/2 es código
(hay que traducir 21 %→0, 10 %→1, 4 %→2); si salen 21/10/4 es porcentaje. Lo
comprueba el script de descubrimiento.

## 16. El cliente lo crea la app externa Woo→FACTUSOL

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
