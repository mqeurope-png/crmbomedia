# FACTUSOL — Flujos de escritura (crear cliente / presupuesto / factura)

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
