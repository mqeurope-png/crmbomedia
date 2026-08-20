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

### Resultado real

> _Pendiente: pegar aquí la salida del comando 3 y del comando 4._

---

## 7. Plan para ERP-E2/E3/E4

Condicionado a lo que salga arriba; se cierra cuando Bart pegue las salidas.

**ERP-E2 — arreglar facturación + vista central de proformas**
1. Fix del payload según el diff de H1 (quitar las columnas que no existan).
   Añadir un test que compare el payload del mapper contra la lista de columnas
   reales, para que no vuelva a colarse una inventada.
2. Vista `/erp/proformas` con estado del ciclo (proforma / albarán / factura),
   usando la referencia descubierta en §4 — o columnas propias del CRM si
   resulta que FACTUSOL no enlaza los documentos.

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
