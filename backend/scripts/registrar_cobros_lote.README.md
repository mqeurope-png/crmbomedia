# registrar_cobros_lote.ps1 — registrar cobros en FACTUSOL en lote

Registra en FACTUSOL, **en lote**, los cobros conciliados a mano en el Excel
«Listado de facturas - cuenta y forma de pago», conduciendo el registro de cobro
de BoHub (**F-4-B**). No reinventa nada: por cada factura BoHub **inserta la
línea de cobro en `F_LCO`** (importe, fecha, contrapartida, concepto) y después
**marca la factura cobrada** (`ESTFAC=2`, el escritor de F-3).

**Solo escribe cobros.** No toca líneas, totales ni nada más de la factura.
Idempotente: si la factura ya está cobrada (saldo 0 / cobrada) la salta.

## Qué escribe en FACTUSOL (contrato)

Diseño basado en el discovery `--collections` ejecutado en producción
(2026-09-10): `F_LCO` son las líneas de cobro por factura, clave compuesta
`(TFALCO, CFALCO, LINLCO)`; `F_LCO` **no referencia** a `F_COB` (por eso no se
toca `F_COB`); `LINLCO` es correlativo 1..N por factura; `FALLCO` es el
vencimiento.

| Columna `F_LCO` | Valor |
|---|---|
| `TFALCO`, `CFALCO` | serie y número de la factura |
| `LINLCO` | siguiente correlativo de esa factura (max+1, o 1) |
| `FECLCO` | fecha del cobro (col. O FECHA COBRO) |
| `FALLCO` | = fecha del cobro (vencimiento de un cobro ya recibido) |
| `IMPLCO` | **saldo pendiente = total de la factura** (no el importe del banco) |
| `CPALCO` | **contrapartida** = cuenta (col. M) resuelta contra el catálogo de `/erp/settings` |
| `CPTLCO` | `COBRO FACTURA Nº: {serie} - {codigo} ({forma})` — la col. N va aquí |
| `FPALCO` | forma de pago de la propia factura (`FOPFAC`) |
| `OBSLCO` | col. P OBSERVACIONES (opcional) |

**Orden que exige DELSOL: cabecera `F_COB` → línea `F_LCO` → `ESTFAC=2`.** `F_COB`
es la cabecera del cobro de la factura y `F_LCO` sus líneas; el enlace es la
**clave de la factura** (`TFACOB/CFACOB` ↔ `TFALCO/CFALCO`), no un `CODCOB`.
DELSOL rechaza (`BDEscribirRegistroError`) una línea cuya cabecera no existe —
por eso fallaba aunque se mandaran las 23 columnas. Si la factura ya tiene
cabecera (cobro parcial previo) solo se añade la línea. La cabecera se construye
sobre una fila real de `F_COB` con las columnas del cobro retagadas `LCO→COB`
(`IMPCOB`, `FECCOB`, `CPACOB`, `CPTCOB`…), solo las que esa fila trae.

Solo se envían columnas reales de `F_LCO` (23, volcadas en vivo). **El registro se
construye sobre una fila REAL de `F_LCO`** (misma serie y contrapartida si la hay):
solo se sobreescriben las columnas de la tabla de arriba y el resto (`TIPLCO`,
`UALLCO`, `UUMLCO`, `FUMLCO`…) se hereda de esa fila, respetando el tipo con el
que DELSOL devuelve cada una — mandar solo las 10 del cobro dejaba vacías las
demás y DELSOL rechazaba el insert (`BDEscribirRegistroError`). El registro exacto
que se envía queda en el log del `worker-factusol` (`EscribirRegistro F_LCO …`).
Después: `F_FAC.ESTFAC = 2` por clave compuesta.

Para contrastar con una fila real (solo lectura):
`docker exec crmbo-api-1 python -m scripts.factusol_discover_invoice_payment --lco-row 5-260004`

Endpoint que conduce el script (permiso de EDICIÓN de ERP):

```
POST /api/erp/factusol/documents/facturas/{serie}/{codigo}/collection
     { "confirm": true, "cuenta": "Bomedia (Sabadell)" | "6", "fecha": "2026-09-10",
       "forma": "Transferencia"?, "observaciones": "…"?, "importe": 72.60? }
→ 202 { status: "queued", job_id, importe, contrapartida:{codigo,nombre}, … }
→ 202 { status: "already", … }   si ya está cobrada (no encola nada)
→ 400 unknown_account / invalid_date / confirmation_required · 404 invoice_not_found
GET  /api/erp/factusol/documents/facturas/collection-status/{job_id}
→ { status: "pending" | "finished" (+result) | "failed" (+error) }
```

## Mapping de cuentas (col. M → contrapartida FACTUSOL)

| Col. M CUENTA | Código | Nombre en FACTUSOL |
|---|---|---|
| Bomedia (Sabadell) | 6 | Bomedia Sabadell |
| Streamtec (Sabadell) | 8 | Streamtec Sabadell |
| MQ Europe (Belfius) | 2 | MQ Europe Belfius |
| Paypal Streamtec | 14 | Paypal Streamtec |
| Paypal MQ Europe | 12 | Paypal MQ Europe |

El script manda el **nombre** tal cual; **BoHub lo resuelve y valida** contra el
catálogo (tolera paréntesis y orden de palabras). Un nombre desconocido → 400 y
el lote se detiene: nunca se registra contra una cuenta adivinada.

## Uso

Requisitos: Windows PowerShell 5.1+ / PowerShell 7. Usuario de BoHub **sin 2FA**
con permiso de **edición** de ERP (admin/pedidos).

### 1. Prepara el CSV (desde el Excel)

Exporta/copia a un CSV UTF-8 con cabecera y estas columnas:

```
serie,codigo,cuenta,forma,fecha,observaciones
1,260729,Bomedia (Sabadell),Transferencia,2026-09-05,
5,260082,Streamtec (Sabadell),Transferencia,2026-09-06,
```

También vale una columna `numero` (`1-260729`) en vez de `serie,codigo`. La
fecha admite `2026-09-05`, `05/09/2026` o `05-09-2026`. Las filas **sin cuenta**
(las 16 sin cobro localizado) se saltan solas.

### 2. PRUEBA primero (obligatorio)

Con un CSV de 1-2 facturas (propuestas: **1-260729** Neonled 72,60 € y
**5-260082** Rocío Bueno 70,18 €):

```powershell
.\registrar_cobros_lote.ps1 -BaseUrl https://tu-dominio -Csv .\prueba.csv          # dry-run
.\registrar_cobros_lote.ps1 -BaseUrl https://tu-dominio -Csv .\prueba.csv -Apply   # escribe SI
```

Comprueba en FACTUSOL que el cobro queda bien (línea en la factura, contrapartida,
fecha, y que la tesorería lo refleja). **Solo entonces** lanza el resto.

### 3. Lote completo

```powershell
.\registrar_cobros_lote.ps1 -BaseUrl https://tu-dominio -Csv .\cobros.csv          # dry-run: revisa la lista
.\registrar_cobros_lote.ps1 -BaseUrl https://tu-dominio -Csv .\cobros.csv -Apply
```

El dry-run imprime: nº a registrar = localizadas − excluidas − ya cobradas, con
cliente, total, saldo, contrapartida, forma y fecha. Con `-Apply` pide «SI»,
registra de una en una (esperando al job de escritura), y **se detiene ante el
primer error real**. Las ya cobradas se saltan solas al reejecutar.

### Exclusiones (se registran a mano, tienen criterio)

Se saltan aunque estén en el CSV (salvo `-IncluirExcluidas`): doble cobro
**2-526081**; anticipos/parciales **2-526082, 2-526085, 5-260068, 5-260067**;
Scalapay **1-260738, 2-526075**; a confirmar **5-260085, 5-260064, 5-260074**.
`FLUXLA-5749` no tiene factura.

Sí entran (decidido por Bart): diferencias de céntimos por comisión (se registra
el total de la factura) y un cobro bancario para varias facturas (un cobro por
factura con su importe).

### Parámetros

| Parámetro | Por defecto | Qué hace |
|---|---|---|
| `-BaseUrl` | (se pregunta) | URL base de BoHub |
| `-Csv` | `.\cobros.csv` | CSV de entrada |
| `-Apply` | off | Escribe de verdad (pide «SI») |
| `-IncluirExcluidas` | off | Registra también las de la lista EXCLUIR |
| `-PausaSegundos` | 1 | Pausa entre cobros |
| `-LogFile` | `.\registrar_cobros_*.log` | Transcript |
