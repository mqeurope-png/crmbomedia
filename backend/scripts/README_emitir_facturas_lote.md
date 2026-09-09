# Facturación en lote — `emitir_facturas_lote.ps1`

Script PowerShell **independiente** para facturar en lote ~18 pedidos ya
cobrados **conduciendo la emisión que BoHub ya tiene** (el botón azul
«Solicitar/Emitir factura»). **No** es una feature ni toca la app: solo llama al
endpoint `emit-factusol-invoice`, así hereda el motor probado (mapper,
allowlist, `next_codfac` correlativo, marcado `ESTPCL`) y deja el pedido
sincronizado en BoHub. **Nunca** escribe en FACTUSOL directamente.

---

## Respuesta directa a la pregunta clave (¿serie por llamada?)

**SÍ.** La llamada de emisión acepta `serie` (1–9) y esa serie **manda sobre
todo lo demás**. En `resolve_serie` (backend), el orden de prioridad es:

1. **`serie` explícita de la llamada** ← lo que envía el script.
2. serie del pedido en FACTUSOL (`TIPPCL`).
3. `by_source` por tienda/origen de la configuración.
4. default de ajustes → 5.

Por tanto el plan de Bart (**una serie por cuenta de cobro**: 1 Bomedia, 2 MQ
Europe, 5 Streamtec) es viable **tal cual**: la columna `serie` del CSV decide
la empresa emisora de cada factura, sin tener que cambiar nada en el pedido. El
número (CODFAC) lo pone BoHub correlativo por serie (`MAX(CODFAC donde
TIPFAC=serie)+1`); el script **no** fuerza números ni rellena huecos.

---

## Cómo se rellena el CSV

Un fichero `pedidos.csv` con cabecera y una fila por pedido:

```csv
numero_pedido,serie
99928,5
9544,2
5773,1
```

- `numero_pedido`: el número de pedido **web** (el desnudo: `99928`, no
  `BOPRIN-99928`).
- `serie`: 1 = Bomedia · 2 = MQ Europe · 5 = Streamtec (según la cuenta de
  cobro con la que quieras facturarlo).

## Cómo se ejecuta

Requisitos: Windows PowerShell 5.1 o PowerShell 7, con acceso a la URL pública
de BoHub. El script pide el email y la contraseña de forma segura (la
contraseña con `-AsSecureString`, nunca en claro ni en el fichero).

**1) Previsualización (por defecto, NO emite nada):**

```powershell
.\emitir_facturas_lote.ps1 -BaseUrl https://tu-dominio -Csv .\pedidos.csv
```

Imprime, por pedido: número, cliente, serie elegida y si está o no ya facturado.
No escribe nada.

**2) Emitir de verdad (pide confirmación por teclado):**

```powershell
.\emitir_facturas_lote.ps1 -BaseUrl https://tu-dominio -Csv .\pedidos.csv -Apply
```

Muestra cuántas va a emitir y espera que escribas `SI`. Emite de una en una,
con pausa entre cada una, imprimiendo el nº de factura devuelto. **Se detiene
ante el primer error** (no sigue en cascada). Al final: resumen (emitidas,
saltadas por ya-facturadas, fallidas) y un **log** en fichero
(`emitir_facturas_YYYYMMDD_HHMMSS.log`).

Si algo falla a mitad, corrige y vuelve a ejecutar: los ya emitidos se **saltan
solos** (idempotencia), no se duplican.

---

## Contrato documentado (Parte 1) — leído del código, no supuesto

### 1. Login / auth
- **Endpoint:** `POST /api/auth/login`
- **Cuerpo (JSON):** `{ "email": "...", "password": "..." }`
- **Respuesta:** `{ "access_token": "<JWT>", "token_type": "bearer",
  "requires_2fa": false, "limited": false }`
- **Cabecera para el resto de llamadas:** `Authorization: Bearer <access_token>`
  (la API usa `HTTPBearer`).
- **Caducidad del token:** 8 h (`access_token_expire_minutes = 480`).
- **2FA:** si el usuario tiene 2FA, `requires_2fa` llega `true` y el
  `access_token` es solo pre-2FA (no sirve para la API). El script aborta con
  aviso: usa un usuario **sin** 2FA (rol `admin` o `pedidos`) para el lote.

### 2. Emisión (el botón azul)
- **Endpoint:** `POST /api/erp/orders/{order_id}/emit-factusol-invoice`
- **Identifica el pedido por el `order_id` interno de BoHub (UUID)**, no por el
  número web. El UUID se saca de `GET /api/erp/orders?limit=500` → cada item
  trae `id` (UUID), `order_number` (con prefijo de tienda, p.ej. `BOPRIN-99928`),
  `invoice_status` y `factusol_invoice_number`. El script cruza el número del
  CSV con el sufijo de `order_number`.
- **Cuerpo (todo opcional):**
  `{ "serie": 5, "fecfac": "YYYY-MM-DD"?, "fopfac": "..."?, "comfac": "..."? }`
  `serie` 1–9; sin cuerpo, emite con la serie del pedido y fecha de hoy.
- **Respuesta (202):** `{ "job_id": "...", "order_id": "...", "status": "queued" }`
  (la emisión real corre en el worker `factusol:writes`, serializado).
- **Rechazo de doble facturación:** `409` con `code=already_invoiced_by_erp`
  (o `already_invoiced_externally`).

### 3. La serie
**Se elige por llamada** (campo `serie`). Ver la respuesta de arriba: la serie
explícita de la llamada tiene prioridad sobre el `TIPPCL` del pedido. No hace
falta tocar el pedido ni la config por tienda.

### 4. Resultado / errores
- **Estado / resultado:** `GET /api/erp/orders/{order_id}/factusol-invoice-status?job_id={job_id}`
  → `{ "status": "invoiced", "codfac": "5-100123" }` cuando termina;
  `{ "status": "pending" }` mientras corre; `{ "status": "failed", "error": "..." }`
  si falla (aquí aparece el error de FACTUSOL, p.ej. `BDExisteRegistro`).
  El script hace polling de este endpoint tras cada emisión.

### 5. Idempotencia (no re-emitir)
Un pedido está **ya facturado** si `factusol_invoice_number` no está vacío **o**
si `invoice_status` ∈ {`generated`, `invoiced_by_erp`, `already_invoiced_externally`}.
El script lo comprueba **antes** de emitir (con la lista de pedidos) y lo
**salta**; y aunque se colara, el endpoint responde `409` y el script lo trata
como saltado, no como error.

---

## Alcance / seguridad

- Usa **solo** el endpoint de emisión existente. Nada de escribir `F_FAC`/`F_LFA`
  ni ninguna tabla de FACTUSOL.
- **No** fuerza números de factura ni rellena huecos de numeración (correlativo
  de BoHub). Los huecos los hace Bart a mano en FACTUSOL aparte.
- **No** toca pedidos ya facturados.
- La contraseña se pide con `-AsSecureString` y solo se convierte a texto en el
  instante del login, descartándose acto seguido.
