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
