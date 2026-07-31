# Conciliación SKU — WooCommerce ↔ FACTUSOL

Cada producto Woo tiene un `sku` libre (`SKU-MBO-3050`) y cada artículo
FACTUSOL un `CODART` interno (`MBO3050`, 13 chars máx). **No se puede
asumir que coinciden** — esta conciliación es prerequisito para facturar
pedidos online sin intervención manual.

## Script de matching

`backend/scripts/sku_conciliation.py` (stdlib, sin deps nuevas):

1. **Exacto por SKU** normalizado: case-insensitive, quita prefijo `SKU-`
   y separadores (`-`, `_`, espacios, puntos) → `SKU-MBO-3050` ≡ `MBO3050`.
2. **Fuzzy por descripción** (difflib `SequenceMatcher`, umbral **0.84**)
   para productos sin SKU o sin match exacto. Umbral deliberadamente
   conservador: mejor huérfano revisable que match falso facturando el
   artículo equivocado.
3. Clasifica: exactos / fuzzy propuestos / **ambiguos** (≥2 candidatos
   sobre el umbral) / **huérfanos Woo** (sin candidato — no se puede
   facturar hasta crear el artículo) / huérfanos FACTUSOL (informativo).

Salidas: `sku-conciliation-report.md` (reporte legible) +
`sku-conciliation-pairs.csv` (semilla para `product_sku_mapping`).
Validado con `--demo`; la ejecución real necesita credenciales de ambos
lados (bloqueo Bart). Ejecutarlo **por tienda**.

## Tabla `product_sku_mapping` (migración real en Sprint 1, NO ahora)

| Columna | Tipo | Notas |
|---|---|---|
| `id` | String(36) PK | uuid4, patrón del CRM |
| `woo_sku` | String(128) NOT NULL | SKU tal cual en Woo (sin normalizar) |
| `woo_store_id` | String(64) NOT NULL | slug de tienda (`mbolasers`…) |
| `woo_product_id` | Integer NULL | id numérico Woo (estable ante cambios de SKU) |
| `factusol_codart` | String(13) NOT NULL | CODART destino |
| `matched_by` | String(16) NOT NULL | `auto` (exacto), `fuzzy`, `manual` |
| `match_score` | Numeric(3,2) NULL | similitud del fuzzy (NULL en exacto/manual) |
| `confirmed_at` | DateTime NULL | NULL = propuesto, pendiente de confirmar |
| `confirmed_by_user_id` | FK users.id NULL | quién confirmó |
| UNIQUE | (`woo_store_id`, `woo_sku`) | un mapping por SKU y tienda |
| INDEX | `factusol_codart` | lookup inverso |

Regla de uso: la facturación SOLO usa mappings con `confirmed_at NOT NULL`.
Los `auto` exactos se pueden auto-confirmar en bloque tras la primera
revisión de Bart; los `fuzzy` requieren confirmación individual.

## Endpoint admin (Sprint 1)

`GET/POST /api/admin/erp/sku-mappings` — lista con filtros
(`pending`/`confirmed`/`orphan`), acción confirmar/rechazar/editar por
fila y "confirmar todos los exactos". UI: tabla con los mismos badges del
reporte (pantalla incluida en el prototipo como referencia visual futura;
no está en las 6 pantallas clave del MVP).

## SKU nuevos (producto Woo sin artículo FACTUSOL)

1. El sync de pedidos detecta línea con SKU sin mapping confirmado →
   marca el pedido con excepción `sku_unmapped` (bandeja de excepciones) y
   el dominio de facturación queda **bloqueado** (los otros 3 dominios
   siguen su curso — el palé puede salir aunque la factura espere).
2. Notificación a Bart (mismo canal que notify_owner de web-forms).
3. Bart crea el artículo en FACTUSOL (o mapea a uno existente) desde la
   pantalla de mappings → la excepción se resuelve y la facturación
   continúa automáticamente.
4. Anti-repetición: la excepción es única por (`store`,`sku`), no por pedido.
