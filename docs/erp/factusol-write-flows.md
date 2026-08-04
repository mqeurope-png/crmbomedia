# FACTUSOL — Flujos de escritura (crear cliente / presupuesto / factura)

> ✅ **URLs y formatos verificados contra la API real** (2026-08-04, PR C-1-fix1,
> vía navegador sobre `apidoc.sdelsol.com` + curl contra producción: fabricante
> 1626, cliente 22870, base `3FS003`, empresa 003 Bomedia SL, JWT `AdminUser`).
> Los endpoints de datos cuelgan de **`/admin/`** — las rutas `/registros/*` que
> asumió el Sprint 0 daban 404. Ver la tabla de endpoints y el formato real de
> body/response en `factusol-write-flows.md`.

**Estado:** flujos diseñados + plantillas curl + script end-to-end listo
(`backend/scripts/factusol_write_flow_test.py`). La **transcripción real**
(payloads/respuestas exactos) se añade al ejecutarlo con credenciales desde
una máquina con acceso a la API (este entorno de dev la tiene bloqueada por
política de red).

## Autenticación (plantilla curl)

```bash
# 1. Login → token temporal
curl -sS -X POST "$FACTUSOL_API_BASE_URL/login/autenticar" \
  -H "Content-Type: application/json" \
  -d '{"codigo":"'$FACTUSOL_API_CODE'","usuario":"'$FACTUSOL_API_USER'","password":"'$FACTUSOL_API_PASSWORD'"}'
# → {"token": "eyJ..."} (caducidad temporal; medirla con --auth-test)

TOKEN="eyJ..."
```

> Rutas exactas a confirmar contra apidoc.sdelsol.com con las credenciales
> (el portal requiere registro). El cliente Python aísla la ruta en
> `authenticate()` para ajustarla en un solo sitio.

## Crear cliente (F_CLI)

```bash
curl -sS -X POST "$FACTUSOL_API_BASE_URL/registros/escribirRegistro" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "tabla": "F_CLI",
    "ejercicio": "2026",
    "registro": {
      "CODCLI": "ZZTEST01",
      "PCOCLI": "BORRAR — PRUEBA API BOHUB",
      "CIFCLI": "00000000T",
      "DOMCLI": "Calle Ficticia 1",
      "POBCLI": "Barcelona",
      "CPOCLI": "08001",
      "EMACLI": "test-api@example.invalid"
    }
  }'
```

## Actualizar cliente

```bash
curl -sS -X POST "$FACTUSOL_API_BASE_URL/registros/actualizarRegistro" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"tabla":"F_CLI","ejercicio":"2026","filtro":"CODCLI='"'"'ZZTEST01'"'"'","registro":{"TELCLI":"600000000"}}'
```

## Crear presupuesto (cabecera F_PRE + 2 líneas)

Orden: cabecera primero, líneas después (cada línea = 1 llamada; no se ha
visto bulk en la doc pública — confirmar). El script usa número de
documento alto (`990001`) para no chocar con la numeración real; **política
de numeración a confirmar** (¿asigna la API el siguiente número o lo pone
el integrador?).

```bash
# cabecera
… -d '{"tabla":"F_PRE","ejercicio":"2026","registro":{"CODPRE":"990001","CLIPRE":"ZZTEST01","TOTPRE":0}}'
# línea 1 (tabla de líneas F_LPR a confirmar en descubrimiento)
… -d '{"tabla":"F_LPR","ejercicio":"2026","registro":{"CODLPR":"990001","POSLPR":1,"ARTLPR":"ART-TEST-1","CANLPR":1,"PRELPR":100.0}}'
```

## Consultar + cleanup

```bash
# releer
… -d '{"tabla":"F_PRE","ejercicio":"2026","filtro":"CODPRE='"'"'990001'"'"'"}' # cargaTabla
# borrar (líneas → cabecera → cliente, en ese orden)
… -d '{"tabla":"F_LPR","ejercicio":"2026","filtro":"CODLPR='"'"'990001'"'"'"}' # borrarRegistros
… -d '{"tabla":"F_PRE","ejercicio":"2026","filtro":"CODPRE='"'"'990001'"'"'"}'
… -d '{"tabla":"F_CLI","ejercicio":"2026","filtro":"CODCLI='"'"'ZZTEST01'"'"'"}'
```

## Estrategia de concurrencia (decisión Sprint 0)

Dos usuarios del CRM disparando sync a la vez NO deben producir escrituras
concurrentes contra FACTUSOL (riesgo de duplicar numeración de documentos y
de condiciones de carrera si hay lock por ejercicio). Decisión:

- **Toda escritura FACTUSOL pasa por una cola RQ dedicada** (`factusol`)
  procesada por un worker **con concurrencia 1** (`worker-factusol`),
  replicando el patrón worker-sync/worker-workflows existente.
- Cada job es idempotente: antes de EscribirRegistro comprueba con
  CargaTabla si el registro ya existe (por CODCLI/CIF o por referencia de
  documento) y pasa a ActualizarRegistro si procede.
- Los fallos quedan en `integration_events` con el payload y el error, y
  alimentan la bandeja de excepciones del ERP (ver data-model.md).

## Transcripción real (pendiente de credenciales)

> Ejecutar `python -m scripts.factusol_write_flow_test` y pegar aquí la
> salida completa (peticiones + respuestas + errores provocados: duplicado,
> columna ausente, FK inexistente) — cierra las preguntas 1-4 de
> factusol-schema.md.

---

## Actualización C-1 (2026-08-04) — servicio de escritura

`app/integrations/factusol/service.py` implementa dos operaciones idempotentes
y atómicas (ejercidas en C-1 solo desde tests + el endpoint admin de
smoke-test; la emisión real llega en C-2):

- **`ensure_customer_in_factusol(session, company_id, client)`** → CODCLI.
  Reusa `company.factusol_company_id`; si no, busca por CIF (`CIFCLI='...'`) y
  vincula sin duplicar; si no existe, genera el siguiente CODCLI y crea el
  cliente (F_CLI). Persiste el vínculo.
- **`emit_invoice(session, order_id, client)`**. Asegura el cliente, escribe
  cabecera F_FAC + una F_LFA por línea, y marca el pedido `invoiced_by_erp` +
  guarda el CODFAC. **Atómico**: si falla una línea, borra las líneas ya
  escritas + la cabecera en FACTUSOL (compensación) y hace rollback en la BD
  (sin cabecera huérfana ni estado sucio).

### Concurrencia (decisión Sprint 0, aún vigente)

Toda escritura FACTUSOL debe serializarse (cola RQ dedicada, worker con
concurrencia 1) para no duplicar numeración. C-1 deja el servicio listo; el
worker/cola se cablean en C-2 junto con la UI de emisión.

### Smoke-test (endpoint admin temporal, C-1)

`POST /api/erp/factusol/smoke-test?mode=login|read_customers|dry_run_invoice`
valida credenciales / lectura / payload del mapper (dry-run NO escribe).

---

## Referencia de endpoints (verificada 2026-08-04)

| Operación | Método | Path |
|---|---|---|
| Leer tabla filtrada | **POST** | `/admin/CargaTabla` |
| Leer un registro | GET | `/admin/LeerRegistro/{ejercicio}/{tabla}/{filtro}` |
| Consulta SQL libre | POST | `/admin/LanzarConsulta` |
| Insertar registro | **POST** | `/admin/EscribirRegistro` |
| Actualizar registro | **POST** | `/admin/ActualizarRegistro` |
| Borrar registros | **GET** | `/admin/BorrarRegistros/{ejercicio}/{tabla}/{filtro}` |
| Imagen de artículo | POST | `/admin/ArticulosImagen` (fuera de scope C-1) |

Auth: `POST /login/Autenticar` (sin cambios; JWT ~3 min).

### Body de `CargaTabla`

```json
{"ejercicio": "2026", "tabla": "F_CLI", "filtro": "1=1 ORDER BY CODCLI LIMIT 5"}
```

Solo esos 3 campos: **no existen** `campos` ni `numeroRegistros`. `filtro` es un
fragmento SQL **WHERE** (admite `LIKE`, `AND`, `>`, `ORDER BY`, `LIMIT`). Un
filtro vacío devuelve `resultado: null`, así que «sin filtro» se escribe `1=1`.
Para proyectar columnas hay que usar `LanzarConsulta` con SQL libre —
`CargaTabla` siempre devuelve todas las columnas de la tabla.

### Response de `CargaTabla` (lista ANIDADA)

```json
{"resultado": [[{"columna": "CODCLI", "dato": 1},
                {"columna": "NOFCLI", "dato": "Cliente ejemplo"}]],
 "respuesta": "OK"}
```

Cada fila es una **lista de `{columna, dato}`**, no un dict. El cliente lo
normaliza a `list[dict]` con `_rows_to_dicts()`.

### Body de `EscribirRegistro` / `ActualizarRegistro`

```json
{"ejercicio": "2026", "tabla": "F_CLI",
 "registro": [{"columna": "CODCLI", "dato": 12345},
              {"columna": "NOFCLI", "dato": "Nombre fiscal"}]}
```

`registro` es un **array de `{columna, dato}`**. En `ActualizarRegistro` debe
incluir la columna PK (p.ej. `CODCLI`) para identificar la fila. Los mappers del
CRM siguen devolviendo dicts planos; la conversión la hace el cliente
(`_to_api_record`). Respuesta: `{"resultado": "", "respuesta": "OK"}`.

### Códigos de `respuesta` (llegan con HTTP 200)

| `respuesta` | Significado |
|---|---|
| `OK` | correcto |
| `Unauthorized` | **token caducado** — NO llega como 401; el cliente re-autentica y reintenta |
| `BDNoExiste` | el ejercicio no tiene base de datos (2020-2022) |

### Datos confirmados de Bomedia

Empresa (`F_EMP`, ejercicio 2024): **CODEMP 003 · NIFEMP B63609309 · DENEMP
Bomedia SL · C Aribau 171 1º 1ª, Barcelona**. Ejercicios con datos: **2023-2026**
(2020-2022 → `BDNoExiste`). En 2026 hay **4531 clientes** en `F_CLI` (la tabla es
por ejercicio). Default del CRM: `FACTUSOL_DEFAULT_EJERCICIO=2026`.

### Redescubrir rutas si DELSOL las cambia

`scripts/factusol_discover_paths.py` barre una matriz `controller × acción` desde
el VPS usando el oráculo de ASP.NET (routing antes que autorización): **404** =
ruta inexistente, **401/400/200** = ruta válida. Las 4 rutas son además
sobreescribibles por env (`FACTUSOL_PATH_LOAD_TABLE`, `_WRITE_RECORD`,
`_UPDATE_RECORD`, `_DELETE_RECORDS`) para corregirlas sin redeploy de código.


---

## Actualización C-2 (2026-08-04) — emisión real desde la UI

- **Numeración CODFAC**: FACTUSOL numera solo. `service.next_codfac()` consulta
  `CargaTabla("F_FAC", "1=1 ORDER BY CODFAC DESC LIMIT 1")` y suma 1 justo antes
  de escribir la cabecera. Bomedia usa CODFAC secuencial de 6 dígitos (última
  observada en 2026: 526066 → siguiente 526067). La race read→write la evita el
  worker serializado (cola `factusol:writes`, `worker-factusol`, concurrencia 1).
- **Cabecera F_FAC**: `TIPFAC=2` (factura ordinaria). Bomedia **no usa serie**
  (F_SER vacía) → sin `SERFAC`. El mapper no pone `CODFAC`; lo inyecta el service
  en cabecera y en cada `CODLFA` de línea.
- **Trigger**: manual desde el botón «Emitir factura FACTUSOL» en la Cola PEDIDOS
  / ficha del pedido (`POST /api/erp/orders/{id}/emit-factusol-invoice` → encola
  en `factusol:writes`). No hay emisión automática por transición de estado.
- **Doble facturación**: el endpoint rechaza (409) si el pedido ya tiene
  `factusol_invoice_number`, ya está `invoiced_by_erp`, o `already_invoiced_externally`.
- **Toggle `factusol_live`**: OFF por defecto. Al activarlo (`/erp/settings`) los
  bloqueos gated (`sku_unmapped`, `company_missing_factusol`) vuelven a bloquear
  la Cola PEDIDOS.

---

## Actualización C-2-fix1 (2026-08-04) — factura desde el pedido F_PCL existente

Cambio de premisa: una app externa ya replica cada pedido de WooCommerce en
FACTUSOL como **Pedido de Cliente (F_PCL)** con el cliente y los importes ya
calculados. BoHub ERP **no crea clientes ni recalcula nada**: `emit_invoice`
localiza el F_PCL del pedido y lo convierte en factura F_FAC.

**Flujo:**
1. `find_pcl_by_order`: busca `F_PCL` por `REFPCL` = `<prefijo>-<nºWoo padding 6>`
   (ej. BOPRIN-99866 → `BOP-099866`). El prefijo sale de
   `IntegrationAccount.metadata_json["factusol_ref_prefix"]`; si no está, se
   deriva de las 3 primeras letras del segmento inicial del order_number.
   Si el F_PCL no existe → error claro al operador («aún no está en FACTUSOL»).
2. `next_codfac`: siguiente CODFAC secuencial (SELECT MAX+1; sin LIMIT — la API
   no lo soporta).
3. `pcl_row_to_fac_payload`: copia la cabecera **por sufijo** (`*PCL → *FAC`,
   arrastra CLIFAC, TOTFAC y las 4 bandas NET/PIVA/IIVA) excluyendo columnas de
   estado del pedido (`ESTPCL`, `USUPCL`, …); inyecta CODFAC, EJEFAC, TIPFAC=2,
   FECFAC (hoy) y **PEDFAC = `<serie>-<codpcl padding 6>`** (link al pedido).
4. `F_LPC → F_LFA` (`lpc_row_to_lfa_payload`, copia por sufijo + CODLFA/POSLFA).
5. Escribe F_FAC + F_LFA (compensación borra la factura a medias si falla una
   línea), marca el pedido `invoiced_by_erp` + guarda el CODFAC + historial +
   SyncLog.

**Se retira**: creación de clientes (`ensure_customer_in_factusol`), el
`order_to_factusol_invoice` que recalculaba, y el endpoint
`POST /companies/{id}/link-factusol`.

> ⚠️ El mapeo por sufijo asume la convención DELSOL (mismo prefijo de campo,
> distinto sufijo de tabla). Los nombres exactos de columnas de F_PCL/F_LPC se
> confirman con la validación real de Bart; si `EscribirRegistro` rechaza alguna
> columna de pedido no prevista, se añade a `mapper.PCL_ONLY_COLUMNS`.

---

## Actualización C-2-fix2 (2026-08-04) — detectar factura/albarán ya existente

**Problema real (BOPRIN-99866):** la factura **260695** ya estaba creada a mano
en el escritorio FACTUSOL (29-jul). El botón «Emitir factura» habría creado un
**duplicado**. Datos verificados de esa factura:

| Campo | Valor | Nota |
|---|---|---|
| `REFFAC` | `BOP-099866` | referencia común pedido↔factura (el ÚNICO enlace) |
| `CLIFAC` | `2458` | DUPLICODER, S.L. |
| `TOTFAC` | `186.34` | |
| `TIPFAC` | `'1'` (string) | **NO `2`** como asumía el mapper |
| `PEDFAC` | **vacío** | la app externa NO enlaza por PEDFAC |

La app externa WooCommerce→FACTUSOL solo crea el **Pedido de Cliente (F_PCL)**;
las facturas (y a veces albaranes) las crea Bart a mano. El nexo común entre
pedido, albarán y factura es la **referencia** `REF*` (`BOP-099866`), no PEDFAC.

**Flujo nuevo (detección antes de emitir):**

1. `service.check_factusol_status(client, order, ejercicio)` consulta en vivo
   `F_FAC` por `REFFAC` y `F_ALB` por `REFALB` → `{has_factura, factura,
   has_albaran, albaran, ref}`.
2. `GET /api/erp/orders/{id}/factusol-status` (solo si `factusol_live` ON):
   - **factura existe** → `service.get_and_link_factusol_status` la
     **auto-vincula** (`factusol_invoice_number=CODFAC`,
     `invoice_status=invoiced_by_erp`, historial `auto_linked_from_factusol`) y
     devuelve `{status:"invoiced", codfac, auto_linked:true}` → badge verde.
   - **solo albarán** → `{status:"albaran", albaran_codigo}` → badge amarillo +
     se permite emitir.
   - **nada** → `{status:"pending"}` → botón de emisión.
   - `factusol_live` OFF o FACTUSOL no responde → `{status:"unknown"}` (el
     frontend cae al botón manual; el worker reconfirma antes de escribir).
3. `emit_invoice` **reconfirma** `check_factusol_status` JUSTO antes de escribir:
   si la factura ya apareció (carrera / creación manual), auto-vincula en vez de
   duplicar (`already_existed:true`, 0 escrituras).

**Cambios en el mapper (`pcl_row_to_fac_payload`):**
- **`TIPFAC` por defecto `'1'`** (antes `2`).
- **Ya NO se inyecta `PEDFAC`** (el enlace es `REFFAC`, que viaja en la copia
  por sufijo `REFPCL→REFFAC`).
- Opciones del operador (`FacturaOptions`) aplicadas tras la copia: `TIPFAC`,
  `SERFAC`, `FECFAC`, `FOPFAC` (forma de pago), `COMFAC` (observaciones).

**Modal de emisión (5 campos, como el escritorio):** Tipo, Serie, Fecha, Forma
de pago (desplegable de `GET /api/erp/factusol/formas-pago` sobre `F_FOP`, cache
5 min) y Observaciones. La ficha del pedido usa el modal completo; la Cola
PEDIDOS mantiene el botón de confirmación simple.

> ⚠️ **Pendiente de confirmar por Bart en la validación real** (el modal se
> construyó con defaults sensatos): el código de `TIPFAC` (usamos `'1'`), si
> Bomedia usa `SERFAC`, y los nombres exactos de columna de forma de pago /
> observaciones (`FOPFAC`/`COMFAC`) y del catálogo `F_FOP` (`CODFOP`/`DESFOP`).
> Todos están centralizados en `mapper.py` / `api/factusol.py` para ajustarlos
> en un solo sitio.

---

## Actualización C-2-fix3 (2026-08-04) — «el ERP confía en la fuente» (sin validar SKU/empresa)

Filosofía definitiva (introducida en B-2-fix5, reforzada aquí): **BoHub NO valida
SKU ni el vínculo empresa→FACTUSOL**. La app externa WooCommerce→FACTUSOL es la
única responsable de gestionar clientes (F_CLI) y catálogo de artículos (F_ART);
BoHub solo lee el pedido (F_PCL) que esa app ya dejó preparado y lo convierte en
factura. Por tanto:

- `orders.py` **ya no calcula** los avisos `sku_unmapped` (líneas sin CODART) ni
  `company_missing_factusol` (empresa sin `factusol_company_id`). Se retiraron
  `_factusol_issues` y su gating; `_blockers` devuelve **solo** excepciones
  operativas abiertas reales (`open_exceptions`: SAT/transporte/facturación) y
  `_warnings` devuelve siempre `[]`.
- El toggle `factusol_live` **ya no interviene** en bloqueos/warnings: gobierna
  únicamente la consulta en vivo del estado de factura (endpoint
  `factusol-status`, C-2-fix2). Antes reactivaba esos avisos como efecto
  colateral; al activarlo en prod reaparecían como ruido (además
  `company_missing_factusol` saldría SIEMPRE, porque el vínculo empresa→FACTUSOL
  se retiró por completo en C-1-fix1: sin `link-factusol`, `LinkFactusolButton`
  ni `ensure_customer_in_factusol`, la columna `Company.factusol_company_id`
  nunca se rellena).
