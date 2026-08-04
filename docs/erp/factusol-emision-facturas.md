# BoHub ERP — Emisión de facturas FACTUSOL (guía de operación)

Guía **operativa** del flujo de facturación: qué pasa al pulsar el botón, por
qué el worker es serial, cómo diagnosticar un fallo y cómo se vincula un pedido
a una factura que ya existe. El detalle técnico de la API DELSOL y de los
mappers está en [`factusol-write-flows.md`](./factusol-write-flows.md).

## Estado desplegado (Fase C)

| PR | SHA squash | Qué aportó |
|---|---|---|
| C-1 (#293) | `b5b72a8` | Adaptador backend: client + mapper + service + smoke-test |
| C-1-fix1 (#294) | `821fb85` | URLs y formatos reales de la API DELSOL (`/admin/CargaTabla`, registro como array `{columna,dato}`) |
| C-2 (#295) | `539dda6` | UI de emisión + worker RQ serializado (`factusol:writes`) |
| C-2-fix1 (#296) | `f6c7155` | `emit_invoice` convierte **F_PCL → F_FAC** (no crea cliente ni recalcula importes) |
| C-2-fix2 (#297) | `48e25d8` | Detecta factura/albarán existente + auto-vinculación + modal de opciones |
| C-2-fix3 (#298) | `5eec661` | Retirados los avisos `sku_unmapped` / `company_missing_factusol` |

## Flujo end-to-end (del clic a la factura en FACTUSOL)

```
Ficha del pedido  ──►  «Emitir factura FACTUSOL»
        │
        ├─(1) Al cargar la ficha, si factusol_live está ON:
        │      GET /orders/{id}/factusol-status
        │        ├─ ya hay factura  → se AUTO-VINCULA (badge verde, sin emitir)
        │        ├─ solo albarán    → badge amarillo, se permite emitir
        │        └─ nada            → botón de emisión
        │
        ├─(2) Modal de opciones: Tipo · Serie · Fecha · Forma de pago · Observaciones
        │      (la Serie viene precargada de /erp/settings; ver abajo)
        │
        ├─(3) POST /orders/{id}/emit-factusol-invoice  → 202 {job_id}
        │      encola en la cola RQ `factusol:writes`
        │
        ├─(4) worker-factusol (concurrencia 1) ejecuta emit_invoice:
        │      a. re-comprueba que NO exista ya la factura (anti-duplicado)
        │      b. localiza el F_PCL del pedido por REFPCL
        │      c. CODFAC = max(CODFAC del ejercicio) + 1
        │      d. escribe cabecera F_FAC (copia por sufijo del F_PCL) + líneas F_LFA
        │      e. si falla una línea → borra lo escrito (compensación) y revierte
        │
        └─(5) La UI hace polling a /orders/{id}/factusol-invoice-status
               hasta `invoiced` (badge «Facturado FACTUSOL #CODFAC») o `failed`.
```

**El IVA lo calcula FACTUSOL.** El CRM no inventa ningún porcentaje: la cabecera
se construye copiando por sufijo las bandas del pedido de cliente
(`NET1PCL→NET1FAC`, `PIVA1PCL→PIVA1FAC`, `IIVA1PCL→IIVA1FAC`, …), que ya vienen
calculadas por la app externa WooCommerce→FACTUSOL.

## Por qué el worker es serial

La numeración `CODFAC` se calcula con **`max(CODFAC) + 1`** leyendo F_FAC justo
antes de escribir. Si dos emisiones corrieran a la vez, ambas leerían el mismo
máximo y **colisionarían con el mismo número** — y esa numeración es correlativa
con las facturas que se siguen haciendo a mano en el escritorio FACTUSOL.

Por eso **todas** las escrituras van por una única cola, `factusol:writes`,
atendida por **un solo worker** (`worker-factusol`, concurrencia 1). No añadas un
segundo worker a esa cola: romperías la garantía.

```
API ──enqueue──► cola factusol:writes ──► worker-factusol (1 sola instancia)
                                            └─ emisión A ─┐ (en serie)
                                            └─ emisión B ─┘
```

## Serie de facturación (`SERFAC`)

Configurable en **`/erp/settings` → «Serie de facturación»**:

- **Serie por defecto** — la que se usa si no se indica otra.
- **Override por origen** — una serie distinta por origen del pedido
  (WooCommerce / Manual / Proforma FACTUSOL). Vacío = usa la por defecto.

Resolución en el momento de emitir (`service.resolve_serfac`):

```
serie del modal  →  by_source[store_id]  →  by_source[origen]  →  default  →  "A"
```

Si no hay nada configurado se usa `"A"` y queda un **warning en los logs** del
worker (`sin serie configurada para origen …`).

## Diagnóstico de fallos

**1. Ver el estado del job**
```bash
docker compose -f docker-compose.prod.yml logs worker-factusol --tail 100
```
Cada emisión deja `factusol: factura emitida order=… codfac=…` o el error
completo de la API DELSOL (`FactusolError` con status + cuerpo recortado).

**2. Ver el histórico del pedido** — la ficha muestra en el timeline la
transición de facturación con el `metadata` (`factusol_codfac`,
`factusol_codpcl`, `factusol_ejercicio`).

**3. Errores frecuentes**

| Síntoma | Causa | Solución |
|---|---|---|
| «Este pedido aún no está en FACTUSOL» | La app externa Woo→FACTUSOL todavía no ha creado el F_PCL | Esperar al sync o crear el pedido de cliente a mano; reintentar |
| `respuesta: "Unauthorized"` (HTTP 200) | JWT caducado (dura ~3 min) | El cliente re-autentica y reintenta solo; si persiste, revisar credenciales |
| `BDNoExiste` | Ejercicio sin base de datos en FACTUSOL | Revisar `factusol_default_ejercicio` en `/erp/settings` |
| La factura sale con serie equivocada | Override por origen mal puesto | `/erp/settings` → Serie de facturación |
| Botón no aparece / badge verde inesperado | Ya existe factura y se auto-vinculó | Es el comportamiento correcto (C-2-fix2) |

**4. Reintentar** — si la emisión falló, el pedido **no** queda marcado como
facturado: basta con volver a pulsar «Emitir factura FACTUSOL». La compensación
ya borró cualquier factura a medias, y el re-chequeo previo evita duplicados.

## Pedido ya facturado a mano en FACTUSOL

Si Bart creó la factura directamente en el escritorio FACTUSOL, **no hay que
hacer nada**: al abrir la ficha con `factusol_live` activo, el ERP la detecta por
la referencia común (`REFFAC` = `BOP-099866`) y la **vincula sola**, dejando el
pedido como `invoiced_by_erp` con su `CODFAC` y una entrada de historial
`auto_linked_from_factusol`.

> **Pendiente (futuro)**: no existe aún una pantalla para vincular *a mano* un
> pedido con un número de factura concreto cuando la referencia no coincide. Si
> hiciera falta, es un endpoint pequeño (`POST /orders/{id}/link-factusol-invoice`
> con el CODFAC) — hoy fuera de alcance.

## Fuera de alcance (hoy)

- Rectificativas y notas de abono.
- Emisión automática (siempre es manual, con el botón).
- Importar clientes de FACTUSOL al CRM.
