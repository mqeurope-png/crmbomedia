# FACTUSOL — Esquema de tablas relevantes para el ERP

> ✅ **URLs y formatos verificados contra la API real** (2026-08-04, PR C-1-fix1,
> vía navegador sobre `apidoc.sdelsol.com` + curl contra producción: fabricante
> 1626, cliente 22870, base `3FS003`, empresa 003 Bomedia SL, JWT `AdminUser`).
> Los endpoints de datos cuelgan de **`/admin/`** — las rutas `/registros/*` que
> asumió el Sprint 0 daban 404. Ver la tabla de endpoints y el formato real de
> body/response en `factusol-write-flows.md`.

**Estado:** base construida desde documentación pública + convención
verificada en código comunitario. La validación en vivo (CargaTabla real)
está **pendiente de credenciales** (Bart habilita el acceso API en el
hosting DELSOL) y de red (el entorno de desarrollo bloquea `*.sdelsol.com`;
ejecutar `python -m scripts.factusol_discover_schema` desde una máquina con
acceso — genera `factusol-schema-DISCOVERED.md` para fusionar aquí).

## Cómo funciona la API DELSOL (validado en doc pública)

- API **genérica sobre tablas**, no REST por entidad: `CargaTabla` (leer),
  `EscribirRegistro` (insertar 1), `ActualizarRegistro` (modificar por
  filtro), `BorrarRegistros` (borrar por filtro). Documentación oficial:
  https://apidoc.sdelsol.com/ (requiere registro para descargarla + API key).
- **Auth:** endpoint Login → **Bearer token temporal**. Toda operación con
  token caducado devuelve `401 Unauthorized` → renovar. El cliente
  (`app/integrations/factusol/client.py`) renueva con margen de 60s y
  re-autentica una vez ante un 401 en vuelo.
- Los documentos (presupuestos/pedidos/albaranes/facturas) van **por
  ejercicio** (año fiscal) — el parámetro `ejercicio` acompaña las llamadas.

## Convención de nombres (verificada)

Tablas `F_XXX`; columnas = prefijo de 3 letras del campo + sufijo de 3
letras de la tabla. Ej.: `CODCLI` (código de cliente en F_CLI), `CODART`
(código de artículo en F_ART). Verificado en la doc de importación oficial
(F2745) y en integradores comunitarios.

## Tablas y columnas esperadas

> Leyenda: ✅ columna confirmada en fuente pública · ◻ esperada por
> convención, confirmar en vivo.

### F_CLI — Clientes

| Columna | Descripción | Estado |
|---|---|---|
| `CODCLI` | Código de cliente (PK) | ✅ |
| `PCOCLI` | Nombre comercial | ✅ |
| `NIFCLI` | CIF/NIF | ✅ verificado en prod (C-3-fix1; **no** es `CIFCLI`) |
| `NOFCLI` | Nombre fiscal | ✅ verificado en prod (C-3-fix1) |
| `NOCCLI` | Nombre comercial | ✅ verificado en prod (C-3-fix1) |
| `DOMCLI` | Domicilio | ✅ |
| `POBCLI` | Población | ✅ |
| `PROCLI` | Provincia | ✅ |
| `PAICLI` | País (ISO 3166-1 **numérico**: `724` = ES) | ✅ verificado en prod |
| `CPOCLI` | Código postal | ✅ |
| `TELCLI` | Teléfono | ✅ |
| `EMACLI` | Email | ✅ |
| `WEBCLI` | Web | ✅ |
| `NOFCLI` | Nombre fiscal | ◻ |
| `FPACLI` | Forma de pago por defecto | ◻ |
| `TARCLI` | Tarifa asignada | ◻ |

Mapeo BoHub: `companies` (el mapa validado dice qué columnas del CRM
alimentan cada campo). `CODCLI` se guardará en
`companies.external_references_json` bajo la clave `factusol`.

### F_ART — Artículos

| Columna | Descripción | Estado |
|---|---|---|
| `CODART` | Código de artículo (PK, 13 chars alfanumérico, sin duplicados) | ✅ |
| `DESART` | Descripción (50 chars) | ✅ |
| `FAMART` | Familia | ◻ |
| `PCOART` | Precio de coste | ◻ |
| `IVAART` | Tipo de IVA | ◻ |
| `EANART` | Código EAN | ◻ |
| `MODART` | Modelo | ◻ |

Relacionadas: `F_FAM` (familias), `F_STO` (stock por almacén), `F_ALM`
(almacenes), `F_TAR` (tarifas de precios).

### Documentos de venta (cabecera + líneas)

| Cabecera | Líneas (esperado) | Documento |
|---|---|---|
| `F_PRE` | `F_LPR` ◻ | Presupuestos |
| `F_PED` | `F_LPE` ◻ | Pedidos |
| `F_ALB` | `F_LPA` ◻ | Albaranes |
| `F_FAC` | `F_LFA` ◻ | Facturas |

Columnas de cabecera esperadas (confirmar): `CODPRE`/`CODPED`… (número de
documento, PK dentro de ejercicio+serie), `CLIPRE` (cliente), `FECPRE`
(fecha), `TOTPRE` (total), serie. Columnas de líneas esperadas: documento
padre, posición, `ARTLPR` (artículo), cantidad, precio, descuento, IVA.

> ⚠️ El descubrimiento en vivo debe confirmar: (1) el nombre real de las
> tablas de líneas, (2) cómo se numeran los documentos (¿la API asigna el
> número o lo asigna el cliente? — crítico para no pisar numeración), (3)
> si existe `F_CON` (contactos por cliente).

## Preguntas abiertas para el descubrimiento en vivo

1. **Rate limits** — ¿peticiones/segundo? La doc pública no lo indica.
2. **Bulk** — ¿hay operación de inserción múltiple o todo es 1 registro por
   llamada? (impacta duración del sync inicial de artículos/clientes).
3. **Errores** — códigos exactos ante: registro duplicado, columna
   requerida ausente, FK inexistente (cliente inexistente en un pedido).
4. **Concurrencia** — ¿lock por ejercicio? ¿escrituras concurrentes seguras?
   Decisión Sprint 0 (independiente de la respuesta): **serializar TODAS
   las escrituras vía worker RQ dedicado `worker-factusol` con cola única**
   — mismo patrón que worker-sync. Ver factusol-write-flows.md.
5. **Caducidad real del token** — el test `--auth-test` (5 auth / 30 min)
   la mide empíricamente.

---

## Actualización C-1 (2026-08-04) — adaptador backend

Fase C PR C-1 construye el adaptador contra este esquema. Confirmado en
producción por Bart (login) y encapsulado en `app/integrations/factusol/`.

### Auth (confirmada en prod)

```
POST {FACTUSOL_BASE_URL}/login/Autenticar
{ "codigoFabricante": "...", "codigoCliente": "...",
  "baseDatosCliente": "3FS003", "password": "<base64(plano)>" }
→ 200 { "resultado": "<JWT>", "respuesta": "OK" }   (JWT exp ≈ 3 min)
```

El cliente (`client.py`) cachea el JWT con margen de 30s sobre `exp`, y ante
un 401 en vuelo re-autentica una vez. Password cifrada Fernet en
`FACTUSOL_PASSWORD_ENCRYPTED`; se descifra y se envía en base64.

### Mapeo usado por el adaptador (`mapper.py`)

- **`Company` → F_CLI**: CODCLI (PK, decidido por el servicio), PCOCLI /
  NOFCLI/NOCCLI ← `name`, NIFCLI ← `tax_id`, DOMCLI ← `address_line`, POBCLI ←
  `city`, CPOCLI ← `postal_code`, PAICLI ← `country`, WEBCLI ← `website`.
- **`Order` → F_FAC + F_LFA**: cabecera CODFAC/EJEFAC/CLIFAC/FECFAC/TOTFAC/
  REFFAC; líneas CODLFA (documento padre)/POSLFA/ARTLFA (CODART)/REFLFA
  (SKU)/DESLFA/CANLFA/PRELFA/IVALFA/TOTLFA.

### Vínculo en el CRM

- CODCLI del cliente → `companies.factusol_company_id` (columna Fase A 0080).
- Nº de factura FACTUSOL → `orders.factusol_invoice_number` (columna nueva
  migración 0084) + `orders.invoice_status = 'invoiced_by_erp'`.

### Pendiente de validación en vivo (smoke-test C-1 → cerrar en C-2)

1. Nombres EXACTOS de las rutas de datos (`/registros/*`) y de las columnas
   marcadas ◻ (aislados en constantes de `client.py` / claves del `mapper.py`).
2. **Política de numeración de facturas**: ¿la asigna la API o el integrador?
   El mapper deriva un CODFAC candidato del nº de pedido (dry-run para revisar).
3. Rango libre de CODCLI para clientes nuevos (`service.CODCLI_BASE`).


---

## Proformas, pedidos de cliente y artículos (C-4) — ✅ VERIFICADO 2026-08-05

Salida real de `scripts/factusol_discover_quotes.py` sobre la base de Bomedia
(ejercicio 2026), ejecutado por Bart en el VPS. **Estos nombres sí están
comprobados**; los de la versión anterior de esta sección (`F_PRO`, `F_LPP`
como líneas de presupuesto) eran candidatos y resultaron ser incorrectos.

### 🚨 F_PRE es MONO-LÍNEA — no existe tabla de líneas de presupuesto

Es el hallazgo que condiciona todo C-4. `F_PRE` tiene **653 filas** en 2026 y
**cada fila es un presupuesto completo**: cliente, importes y totales. No hay
`F_LPRE`, ni `F_LPR`, ni ninguna otra tabla de líneas de presupuesto (`F_LPP`
existe, pero pertenece a **F_PPR**, pedidos a *proveedor*, no a presupuestos).

El detalle del presupuesto vive como **texto libre en `REFPRE`, 250 caracteres**.

Consecuencia para el CRM: si BoHub quiere poder duplicar una proforma o volcar
sus líneas a un pedido, **tiene que guardar el desglose por su cuenta**. Eso es
la tabla `factusol_quote_lines_cache` (migración 0088). Las proformas creadas
en el FACTUSOL de escritorio no tienen desglose y degradan a modo «simple».

### F_PRE — presupuestos / proformas (653 filas, 2026)

| Columna | Contenido |
|---|---|
| `CODPRE` | PK numérica. Se asigna con `MAX+1` (cola serializada). |
| `TIPPRE` | Siempre `'1'` en los 653 registros. |
| `REFPRE` | Texto libre, 250 car. **Es donde vive el desglose.** |
| `FECPRE` | Fecha del presupuesto. |
| `CLIPRE` | FK a `F_CLI.CODCLI`. |
| `CNOPRE` / `CDOPRE` / `CPOPRE` / `CCPPRE` / `CPRPRE` / `CNIPRE` | Nombre, domicilio, población, CP, provincia y NIF copiados del cliente. |
| `TELPRE` / `EMAPRE` / `CEMPRE` | Teléfono y email. |
| `CPAPRE` | País ISO numérico (`'724'` = España). |
| `ALMPRE` | Almacén (`'GEN'`). |
| `TIVPRE` | Tipo de IVA del documento. |
| `NET1PRE` / `BAS1PRE` | Base imponible banda 1. |
| `PIVA1PRE` / `IIVA1PRE` | % e importe de IVA banda 1. |
| `PREC1PRE` / `IREC1PRE` | % e importe de recargo de equivalencia banda 1. |
| `TOTPRE` | Total del presupuesto. |
| `FOPPRE` | Forma de pago (F_FOP). |
| `USUPRE` / `USMPRE` / `FUMPRE` / `HORPRE` | Auditoría de FACTUSOL. |

Existen además las bandas 2/3/4 (`NET2PRE`, `PIVA2PRE`, …) y ~92 columnas más.
El CRM **solo escribe la banda 1**: las proformas de Bomedia son de un único
tipo de IVA y repartir en bandas sin necesidad añadiría riesgo sin ganancia.

### F_PCL / F_LPC — pedidos de cliente (503 cabeceras, 1527 líneas)

Estos **sí** tienen cabecera + líneas. Los crea la app externa Woo→FACTUSOL.

- **F_PCL**: `CODPCL, TIPPCL, REFPCL, FECPCL, CLIPCL, CNOPCL, CDOPCL, CPOPCL,
  CCPPCL, CPRPCL, CNIPCL, TIVPCL, REQPCL, TELPCL, ESTPCL, ALMPCL, NET1PCL,
  NET2PCL…`
- **F_LPC**: `TIPLPC, CODLPC, POSLPC, ARTLPC, DESLPC, CANLPC, DT1LPC, DT2LPC,
  DT3LPC, PRELPC, TOTLPC, PENLPC, IVALPC…`
- Join: `F_LPC.TIPLPC = F_PCL.TIPPCL AND F_LPC.CODLPC = F_PCL.CODPCL`.

> **Por qué C-4 NO escribe F_PCL.** «Convertir proforma en pedido» crea un
> pedido **del CRM**, no un F_PCL. Escribir un F_PCL exigiría dar por buena una
> correspondencia de columnas `F_PRE → F_PCL` por sufijo que nadie ha
> verificado, y en `EscribirRegistro` una columna inexistente **sí revienta**
> (en `CargaTabla` devuelve `[]` en silencio). Además duplicaría lo que ya hace
> la app externa Woo→FACTUSOL — que es exactamente el origen del
> `BDEscribirRegistroError` de C-2-fix1.

### F_ART — artículos (1828 filas)

`CODART` (PK, texto), `EANART`, `EQUART`, `FAMART`, `DESART` (descripción),
`DEEART`, `DETART`, `TIVART` (% IVA), `PCOART` (precio de coste), `DT0ART` /
`DT1ART` / `DT2ART` (descuentos), `STOART` (stock), `UMEART`, `FUMART` + 78 más.

`PCOART` es **coste**, no tarifa de venta: en la UI se ofrece como sugerencia
editable, nunca como precio final.

### F_ALB / F_LAL — albaranes (388 / 1493)

Cabecera + líneas, misma convención de sufijos.

### Recordatorio permanente: la trampa del filtro

> En la API DELSOL un filtro sobre una columna **inexistente no da error:
> devuelve `[]`**. C-3 salió a producción con `NOMCLI`/`CIFCLI`/`DIRCLI`/
> `NACCLI` inventados y la búsqueda devolvía siempre vacío sin un solo log
> (ver C-3-fix1).
>
> Antes de filtrar por una columna nueva: `list(rows[0].keys())` primero.
