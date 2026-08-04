# FACTUSOL — Flujos de escritura (crear cliente / presupuesto / factura)

> ⚠️ **Estado de las URLs (2026-08-04, PR C-1-fix1).** El login
> `POST /login/Autenticar` está **CONFIRMADO en producción** (200 + JWT).
> Las rutas de **datos** de este documento (`/registros/*`) **NO lo están**:
> devuelven 404 en la API real. Eran una conjetura del Sprint 0 y no se han
> sustituido por otra: `apidoc.sdelsol.com` está bloqueado por la política de
> egress de CI/dev, así que la verificación tiene que hacerse desde el VPS con
> `python -m scripts.factusol_discover_paths`. Mientras tanto las rutas son
> configurables por env (`FACTUSOL_PATH_LOAD_TABLE`, `FACTUSOL_PATH_WRITE_RECORD`,
> `FACTUSOL_PATH_UPDATE_RECORD`, `FACTUSOL_PATH_DELETE_RECORDS`) — corregirlas
> NO requiere cambio de código.

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

## Descubrimiento de las rutas de datos (C-1-fix1, pendiente)

`/registros/cargaTabla` y hermanas devuelven **404** en la API real; el 404 lo
emite ASP.NET (Azure App Service + IIS 10 + ASP.NET 4.0.30319), es decir el
routing no reconoce la ruta. Rutas ya descartadas (todas 404): `/CargaTabla`,
`/cargaTabla`, `/registros/{CargaTabla,cargatabla,Cargar,CargarTabla}`,
`/tabla/*`, `/tablas/*`, `/datos/*`, `/dato/Cargar`, `/api/*`, `/consultas/*`,
`/servicios/*`, `/factusol/*`, `/empresa(s)/CargarTabla`. Los endpoints de
discovery (`/swagger`, `/openapi.json`, `/help`, `/docs`) también dan 404.

### Oráculo

En ASP.NET Web API el **routing se resuelve antes que la autorización**:

| Respuesta a `POST {ruta}` con body `{}` | Significado |
|---|---|
| `404` + "No HTTP resource was found…" | la ruta **no existe** |
| `401` (sin token) | la ruta **existe** ✅ |
| `200` / `400` (con token) | la ruta **existe** ✅ (acierto seguro) |

### Procedimiento (desde el VPS, que sí alcanza la API)

```bash
docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \
    python -m scripts.factusol_discover_paths
```

Barre una matriz `controller × acción` (el patrón confirmado es
`/{controller}/{action}`, como `/login/Autenticar`), y para el controller
ganador busca además las acciones de escritura/actualización/borrado. Admite
candidatos extra por CLI:

```bash
... python -m scripts.factusol_discover_paths /Datos/Cargar /Api/v1/CargaTabla
```

Al terminar imprime las líneas `FACTUSOL_PATH_*` listas para pegar en
`.env.production`. Tras añadirlas: `up -d --force-recreate api worker-sync`.
Si la matriz no acierta, sacar los paths de `apidoc.sdelsol.com` (requiere
navegador, la página es Postman JS-rendered) y pasarlos como argumentos.
