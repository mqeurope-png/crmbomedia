# FACTUSOL — Discovery de albaranes y diagnóstico de facturación (ERP-E1)

Preparatorio de **ERP-E2/E3/E4** (vista de proformas, creación de albaranes,
PDFs) y diagnóstico del «la generación de facturas no funciona».

Este PR aporta el **script de descubrimiento** y el **análisis de código**. Las
tablas de resultados se rellenan con la salida que Bart pegue: CC no alcanza
`api.sdelsol.com` (sin credenciales ni red), igual que en C-4 (#308).

---

## 1. Cómo ejecutarlo (Bart)

### Paso previo — imprescindible para la trazabilidad

En el **FACTUSOL de escritorio**: coge una proforma de prueba y conviértela
→ **albarán** → **factura**. **Anota su CODPRE.** Sin esa cadena real el
script no puede descubrir dónde guarda FACTUSOL la referencia al documento
origen (no se puede adivinar: ver §4).

### Los tres comandos

```bash
# 0) ERP-E2-fix1 — serie, contador y ESTPCL a partir de un pedido real
docker exec crmbo-api-1 python -m scripts.factusol_discover_albaranes \
    --trace-order BOP-099917

# 1) Estructura de F_ALB + líneas + numeración + sondeo de impresión
docker exec crmbo-api-1 python -m scripts.factusol_discover_albaranes

# 2) Trazabilidad de la cadena que acabas de crear (pon TU CODPRE)
docker exec crmbo-api-1 python -m scripts.factusol_discover_albaranes \
    --trace-codpre <CODPRE>

# 3) Diagnóstico del pipeline de facturas (dry-run, NO escribe)
docker exec crmbo-api-1 python -m scripts.factusol_discover_albaranes \
    --check-invoice-pipeline

# 4) Logs del worker de escrituras
docker logs crmbo-worker-factusol-1 --since 240h 2>&1 \
  | grep -iE "(error|fail|exception|KO)" | tail -50
```

**El script solo lee** (`CargaTabla` + sondeo GET/POST de rutas de impresión).
No escribe nada en FACTUSOL: un guard (`is_safe_probe_path`) bloquea cualquier
ruta que contenga un verbo de escritura, y el chequeo de facturas construye el
payload y lo compara con las columnas reales **sin enviarlo**.

Pega las cuatro salidas en el PR de ERP-E1.

---

## 2. Punto de partida (lo que ya sabíamos)

Del discovery de C-4 (`factusol-schema.md`), ejecutado en vivo el 2026-08-05:

| Tabla | Filas (ej. 2026) | Estado |
|---|---|---|
| `F_ALB` | 388 | Existe. **Columnas sin volcar.** |
| `F_LAL` | 1493 | Existe. **Columnas sin volcar.** |

O sea: sabemos que los albaranes están ahí y que la convención de sufijos
apunta a `F_LAL` como líneas, pero **nadie ha visto una fila**. Y en esta API
eso no basta para escribir: la lectura miente en silencio y la escritura
explota (gotchas nº 1 y nº 13).

### Preguntas abiertas que cierra este discovery

1. Columnas reales de `F_ALB` / `F_LAL` (y confirmación de que `F_LAL` es la
   tabla de líneas y no `F_LIA`/`F_LPA`).
2. FK cabecera↔líneas (¿`CODLAL` → `CODALB`, como `CODLPS` → `CODPRE`?).
3. Cómo se numera `CODALB` (¿`MAX+1` como `CODFAC`? ¿hay serie?).
4. **Dónde vive la referencia PRE→ALB→FAC** — hoy no la conocemos, y por eso
   el guard de edición de proformas no sabe si una proforma ya se convirtió
   (gotcha nº 17).
5. Si la API DELSOL expone impresión/PDF por algún endpoint no documentado.

---

## 3. Resultados — A1/A3: estructura y numeración

> _Pendiente: pegar aquí la salida del comando 1._

| Concepto | Tabla | Filas | Columnas |
|---|---|---|---|
| Albarán cabecera | `F_ALB` | _(pendiente)_ | _(pendiente)_ |
| Albarán líneas | _(pendiente)_ | | |
| FK cabecera↔líneas | | | |
| Numeración `CODALB` | | | |
| Serie / tipo / estado | | | |

---

## 4. Resultados — A2: trazabilidad PRE → ALB → FAC

**Método: empírico, no por adivinanza.** Buscar a ciegas nombres candidatos
(`PREALB`, `ORIALB`, `DOCALB`…) es exactamente el error que costó C-4-fix3: se
descartó `F_LPS` tras probar tres nombres y se construyó una caché local entera
para suplir una tabla que sí existía.

Aquí en cambio Bart convierte una proforma real y el script busca ese CODPRE
**en todas las columnas** de `F_ALB`, luego el CODALB resultante en todas las de
`F_FAC`. Lo que aparezca —se llame como se llame— es la referencia real. Las
coincidencias en columnas con nombre de referencia (`…PRE…`, `…ORI…`, `…REF…`)
se marcan con ⭐; el resto se listan como probable casualidad.

> _Pendiente: pegar aquí la salida del comando 2._

| Enlace | Columna encontrada | Notas |
|---|---|---|
| `F_PRE` → `F_ALB` | _(pendiente)_ | |
| `F_ALB` → `F_FAC` | _(pendiente)_ | |
| `ESTPRE` tras convertir | _(pendiente)_ | ¿cambia de 0/1 a otro valor? |

**Si no aparece ninguna referencia**, la conclusión también sirve: FACTUSOL
copiaría los datos sin enlazar los documentos, y entonces **la trazabilidad
tendrá que vivir en el CRM** (columnas propias en `orders` / tabla puente), no
leerse de FACTUSOL. Eso condiciona ERP-E2 y hay que saberlo antes de diseñarlo.

---

## 5. Resultados — A4: impresión y modelos de documento

> _Pendiente: pegar aquí la sección A4 de la salida del comando 1._

Se sondean 8 rutas candidatas (`/admin/ImprimirDocumento`, `/admin/GenerarPDF`,
…) en GET y POST. Cómo leer las respuestas:

- **404** → la ruta no existe.
- **400 / 500 con mensaje** → ¡la ruta existe! y le faltan parámetros. Es el
  hallazgo bueno: el mensaje suele decir qué espera.
- **200** → jackpot, hay endpoint de impresión.

Y 8 tablas candidatas de modelos de impresión (`F_MOD`, `F_PLA`, `F_IMP`,
`F_FOR`, `F_DIS`, `F_INF`, `F_REP`, `F_DOC`) — si alguna trae filas, ahí viven
los diseños de documento de Bart y ERP-E4 podría reutilizarlos en vez de
maquetar los PDFs desde cero.

| Endpoint | GET | POST | Conclusión |
|---|---|---|---|
| _(pendiente)_ | | | |

---

## 6. Diagnóstico del bug de emisión de facturas

Análisis de código hecho en este PR (sin acceso a producción). Hipótesis
**ordenadas por probabilidad**, todas verificables con el comando 3.

### H1 — El payload inyecta columnas que probablemente NO existen ⭐ principal

`mapper.pcl_row_to_fac_payload` copia el F_PCL por sufijo y **además inyecta**:

```python
payload["CODFAC"] = codfac
payload["EJEFAC"] = ejercicio      # ← sospechosa
payload["TIPFAC"] = opts.tipfac
payload["FECFAC"] = ...
payload["SERFAC"] = opts.serfac    # ← sospechosa (siempre presente desde C-2)
```

Y `lpc_row_to_lfa_payload` inyecta `EJELFA`.

**Por qué `EJEFAC`/`EJELFA` son sospechosas:** en la API DELSOL el ejercicio es
un **parámetro de la petición**, no una columna. La prueba está en el código que
**sí funciona**: `quotes.create_quote` crea proformas en producción todos los
días y su payload **no lleva ninguna columna `EJEPRE`** — el ejercicio viaja
como argumento de `write_record(..., ejercicio=ejercicio)`. Además, la lista
verificada de columnas de `F_PRE` (C-4, 92+ columnas volcadas en vivo) **no
tiene `EJEPRE`**.

**Por qué `SERFAC` es sospechosa:** el mapper solo la envía si el operador la
indica… pero desde **C-2** el service la rellena SIEMPRE antes de llamar al
mapper (`resolve_serfac`, con fallback `"A"`). Así que desde C-2 toda emisión
manda `SERFAC`. Encaja con la cronología: C-2 es justo el PR que introdujo la
serie configurable. La tarea pendiente #67 ya apuntaba «**Sin SERFAC**».

**Consecuencia:** basta UNA columna inexistente para que `EscribirRegistro`
falle **entero** con `BDEscribirRegistroError` (gotcha nº 13) — no falla «esa
columna», falla el registro. Y el error **no dice cuál sobra**, que es
exactamente por qué esto lleva tiempo sin diagnosticarse.

**Cómo lo confirma el comando 3:** lee las columnas reales de `F_FAC`/`F_LFA`,
construye el payload que enviaría el mapper para un F_PCL real y hace el diff:

```
❌ COLUMNAS QUE NO EXISTEN EN F_FAC: EJEFAC, SERFAC
```

Si sale eso, el fix de ERP-E2 es de tres líneas en `mapper.py`.

### H2 — `factusol_live` apagado

Si el toggle de `/erp/settings` está en OFF, `GET /orders/{id}/factusol-status`
devuelve `{"status": "unknown", "reason": "factusol_live_off"}` y la ficha no
ofrece emitir. Se ve como «no funciona» sin ningún error en los logs. El
comando 3 lo imprime.

### H3 — Credenciales / token

`FACTUSOL_PASSWORD_ENCRYPTED` mal descifrada o password cambiada en DELSOL. El
comando 3 hace login y enseña los claims del JWT. Descartada si sale `✅ login OK`.

### H4 — Worker caído o cola atascada

Mismo patrón que pasó con Gmail. Todas las escrituras van por `factusol:writes`
con **un solo** worker (`worker-factusol`, concurrency 1). Si el contenedor está
caído, la API encola (202 + job_id) y la UI hace polling eternamente: la factura
nunca se escribe y **no hay error visible**. Lo delata el comando 4 (sin líneas
recientes = worker mudo) y `docker ps | grep worker-factusol`.

### H5 — El pedido no está en FACTUSOL

`emit_invoice` exige un `F_PCL` previo (lo crea la app externa Woo→FACTUSOL) y
si no lo encuentra lanza «Este pedido aún no está en FACTUSOL». Es un fallo
funcional legítimo, pero el operador lo describe igual: «no funciona».

### Descartada — el retry de `KO`

El spec sugería verificar si la emisión usa el retry de `respuesta: "KO"` de
C-6-fix1. **Sí lo usa**: vive en `client._request`, que es el camino común de
`load_table` / `write_record` / `update_record`. No hace falta tocar nada.

### Resultado real — ✅ H1 CONFIRMADA (2026-08-20)

Salida del comando 3 contra la base real:

```
❌ COLUMNAS QUE NO EXISTEN EN F_FAC: CEWFAC, EJEFAC, INCFAC, PENFAC, PPOFAC, SERFAC, SMDFAC
❌ COLUMNAS QUE NO EXISTEN EN F_LFA: ANULFA, PENLFA
```

Y el log del worker:

```
FactusolError: POST /admin/EscribirRegistro → respuesta='BDEscribirRegistroError'
[Job ...]: exception raised while executing (…jobs.emit_invoice_job)
```

Nueve columnas fantasma, y **basta una** para tumbar el registro entero. Dos
orígenes distintos:

| Origen | Columnas | Por qué |
|---|---|---|
| Copia por sufijo | `CEWFAC`, `INCFAC`, `PENFAC`, `PPOFAC`, `SMDFAC` (+ `ANULFA`, `PENLFA`) | F_PCL/F_LPC **sí** tienen `PENPCL`, `PPOPCL`, `INCPCL`… pero F_FAC/F_LFA no tienen su contrapartida. El mapeo por sufijo asumía simetría que no existe. |
| Inyectadas a mano | `EJEFAC`, `SERFAC` | El ejercicio es **parámetro** de la llamada, no columna. La serie **no es un dato de la factura** (ver §6b). |

**Arreglado en ERP-E2.** Además de quitarlas, el payload ahora se filtra contra
la lista canónica de columnas reales (`mapper.FAC_COLUMNS` / `LFA_COLUMNS`):
lo que no exista se descarta con un warning en el log en vez de tumbar la
emisión. Un test fija el invariante «toda columna del payload existe en la
tabla», así que reintroducir una inventada falla en CI, no en producción.

Ojo con la asimetría que delató el discovery: **`EJEFAC` no existe pero
`EJELFA` sí**. En la cabecera el ejercicio va como parámetro; en las líneas es
columna. Deducirlo por convención habría fallado en los dos sentidos.

## 6b. La serie es la empresa emisora — y vive en `TIPFAC`

✅ **CONFIRMADO EN VIVO** (ERP-E2-fix2, sondeo de F_FAC ejercicio 2026, 906
filas). `SERFAC` no existe; la serie identifica la **empresa que emite** y va
en la columna **`TIPFAC` / `TIPPCL` / `TIPPRE`**. El escritorio los muestra
como `<serie>-<número>`.

| `TIPFAC` | Empresa | Facturas | Rango CODFAC |
|---|---|---|---|
| 1 | Bomedia | 736 | 260000–260736 |
| 2 | MQ Europe | 83 | 526000–526082 |
| 3 | (sin nombrar) | 15 | 260001–260539 |
| 4 | (sin nombrar) | 6 | 260001–260006 |
| 5 | Streamtec | 66 | 260000–260065 |

> ### ⚠️ Dos modelos anteriores, los dos falsos
>
> 1. **«TIPFAC = tipo de documento»** (C-2-fix2): la factura verificada tenía
>    `TIPFAC='1'`… porque la emitió Bomedia. No hay un «1 = factura ordinaria».
> 2. **«la serie es el rango del número»** (ERP-E2): lo desmienten `2-526082`
>    y `5-000005`. Y la tabla de arriba lo remata — las series 1, 3, 4 y 5
>    comparten el rango 260xxx.

**La clave real de F_FAC es `(TIPFAC, CODFAC)`.** El mismo número convive en
varias series el mismo ejercicio: `260000` existe como serie 1 (REFFAC
ART-005587, 07-ene) **y** como serie 5 (REFFAC ENERO, 12-ene); `260065`
existe en la 1 y en la 5 (BOP-099894, 14-ago).

Consecuencias en el código:

- Numeración: `max(CODFAC WHERE TIPFAC=serie) + 1`, con guarda anti-colisión
  que avanza si el hueco estuviera ocupado.
- El borrado de compensación filtra por `TIPFAC` **y** `CODFAC`.
- Las líneas llevan `TIPLFA` = la serie.

### El bug de la segunda validación (`BDExisteRegistro`)

El pedido `BOPRIN-99917` (MOVIATICOS) es el F_PCL `5-000005`. La copia por
sufijo traía `TIPPCL='5'` → `TIPFAC='5'`, correcto… y el mapper lo pisaba:

```python
if opts.tipfac is not None:      # "1", default del modal → SIEMPRE ganaba
    payload["TIPFAC"] = opts.tipfac
```

Resultado: se intentaba escribir `(TIPFAC=1, CODFAC=260066)`, que **ya existe**
en la serie 1 (factura ART-005622 de Bomedia, 27-ene-2026) → `BDExisteRegistro`.
El campo «Tipo» del modal se ha retirado: no existe tal concepto.

### Marcar el pedido como facturado

`ESTPCL = 2` («Enviado») marca el pedido como facturado. Se configura en
`/erp/settings` → «Estado ESTPCL del pedido facturado»; sin valor, la factura
se emite igual y queda un aviso en el log en vez de escribir un código
inventado en la contabilidad.

## 6c. Columnas canónicas (referencia)

Volcadas en vivo por el discovery. Viven en `mapper.FAC_COLUMNS` (167) y
`mapper.LFA_COLUMNS` (36), con un test que verifica los recuentos para que un
error de transcripción no pase inadvertido. **Antes de escribir una columna
nueva, confírmala contra estas listas** — no la deduzcas del sufijo de otra
tabla (gotcha nº 13).

---

## 7. Plan para ERP-E2/E3/E4

Condicionado a lo que salga arriba; se cierra cuando Bart pegue las salidas.

**ERP-E2 — arreglar facturación** ✅ HECHO
1. ~~Fix del payload~~ — 9 columnas fantasma fuera + filtro contra la lista
   canónica + test del invariante.
2. ~~Serie~~ — numeración por rango + selector de empresa emisora en el modal.

**ERP-E3 — albaranes + vista central de proformas**
1. Vista `/erp/proformas` con estado del ciclo (proforma / albarán / factura),
   usando la referencia descubierta en §4 — o columnas propias del CRM si
   resulta que FACTUSOL no enlaza los documentos.
2. Crear albaranes desde el CRM (abajo).

**ERP-E3 — crear albaranes desde el CRM**
`create_albaran` siguiendo el patrón de `create_quote`: `next_codalb` con
`MAX+1` dentro de la cola serializada, payload con las columnas verificadas en
§3, y traducción `EQUART → CODART` antes de escribir las líneas (gotcha nº 14:
escribir el código comercial no falla, pero luego **el escritorio crashea al
abrir el documento**).

**ERP-E4 — PDFs**
Si §5 encuentra endpoint de impresión, se usa. Si no, se maquetan en el CRM
reutilizando `app/erp/albaran_pdf.py` (ya existe para el albarán interno).

---

## 8. Aviso sobre este documento

Las secciones §3, §4, §5 y el «resultado real» de §6 están **sin rellenar a
propósito**. Rellenarlas con lo que *suponemos* sería repetir el error de C-4:
`factusol-schema.md` afirmó durante semanas que `F_PRE` era mono-línea porque se
dio por buena una deducción. En esta base de datos, lo no verificado no se
escribe.


---

## 8. Explorador de documentos (ERP-E3-A) y discovery de la cadena

**E3-A** añade `ERP · Documentos`: 4 vistas solo-lectura EN VIVO (pedidos /
presupuestos / albaranes / facturas) con filtros (serie, cliente, fechas,
texto) y detalle con líneas. Endpoints:
`GET /api/erp/factusol/documents/{tipo}` y `.../{tipo}/{serie}/{codigo}`
(clave compuesta — el código solo es único por serie).

Filtrado: a SQL solo va UN predicado trivial (cliente o serie); el resto en
Python (mismo criterio que `quotes.list_quotes`). Red anti-gotcha-1: si el
predicado devuelve `[]` sin ser `1=1`, refetch completo + filtros en Python,
con warning — una columna mal asumida (F_ALB, aún sin volcar) degrada a un
fetch completo, nunca a un falso «no hay resultados».

Estados mostrados: solo los CONFIRMADOS se traducen (ESTPCL 0/2, ESTPRE 0/1);
el resto sale crudo («Estado N») hasta que el discovery los cierre.

**Discovery para E3-B** — tras convertir en el escritorio una proforma de
prueba → albarán → factura (anotando el CODPRE y su ESTPRE previo):

```bash
docker exec crmbo-api-1 python -m scripts.factusol_discover_albaranes \
    --trace-quote-chain <CODPRE>
```

Vuelca: la fila F_PRE completa (para comparar ESTPRE antes/después), el scan
de referencias en F_ALB + casado por cliente/importe/ref, las **columnas
completas de F_ALB y F_LAL** (el allowlist de escritura de E3-B), la
numeración de albaranes por serie (TIPALB) y la factura resultante en F_FAC.
Con esa salida se diseña E3-B (crear albarán/factura desde proforma + ciclo
cruzado en las vistas).
