# FACTUSOL — Esquema de tablas relevantes para el ERP

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
| `CIFCLI` | CIF/NIF | ✅ |
| `DOMCLI` | Domicilio | ✅ |
| `POBCLI` | Población | ✅ |
| `PROCLI` | Provincia | ✅ |
| `PAICLI` | País | ✅ |
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
