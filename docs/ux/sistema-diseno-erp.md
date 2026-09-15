# Sistema de diseño del ERP · Lote 2 (tokens y patrones)

Referencia de lo que **está implementado** en `frontend/src/app/styles.css`
(bloques «Sistema de diseño ERP · Lote 2»: el de tokens dentro de `:root` y el
de reglas globales al final del fichero) a partir de la revisión de diseño
`docs/ux/revision-ux-erp/` (decisiones 1.1–1.7) y de la maqueta aprobada
`docs/ux/maqueta-erp-pedidos.html`.

Regla de fondo: **el color de marca solo se usa para acciones e interacción;
los colores de estado solo informan.** Si algo se pulsa es un botón o un chip
de filtro (con borde); una pastilla o casilla de estado nunca se pulsa.

Los aliases históricos del CRM (`--bg`, `--ink`, `--muted`, `--line`,
`--brand`, `--card`) se mantienen con sus valores: son los de la maqueta a
±2 % de luminancia y no se repinta el CRM. Se usan tal cual dentro del ERP.

---

## E1 · Color

| Token | Valor | Uso |
|---|---|---|
| `--faint` | `#9aa2ad` | **Solo elementos sin dato**: separadores, flechas de desplegable, placeholders. Da 2,5:1 sobre blanco: nunca en texto que aporte un dato. |
| `--muted` | `#6b7280` | Texto secundario **con dato** (fechas, resúmenes, etiquetas). 4,8:1. |
| `--line2` | `#eef0f4` | Separador interior (filas dentro de un panel). `--line` sigue siendo el borde exterior. |
| `--brand-ink` | `#1e40af` | Texto o enlace de marca sobre fondo claro (7,6:1). |
| `--focus` | `rgba(47,107,255,.4)` | Anillo de foco visible: 3 px, `#2f6bff` al 40 %. |
| `--ink-on-color` | `#1b2330` por defecto | Tinta sobre fondos de estado. Cada estado la redefine con su tinta (`.is-done { --ink-on-color: var(--st-green-ink) }`) y el texto usa `color: var(--ink-on-color)`; así nadie escribe gris sobre ámbar. |
| `--danger-ink` | `#b42318` | Rojo de **acción destructiva** («Anular»). Distinto en uso del rojo de estado «incidencia» (`--st-red-ink`) aunque compartan tono. |

Pastillas y casillas de estado (fondo · tinta), todas con contraste ≥ 4,5:1:

| Estado | Tokens | Significado |
|---|---|---|
| verde | `--st-green-bg` `#e7f6ec` · `--st-green-ink` `#1f7a45` | hecho (pagado, cobrado, ok) |
| ámbar | `--st-amber-bg` `#fdf1dd` · `--st-amber-ink` `#8a5c00` | pendiente. La tinta se oscureció respecto al brief (`#9a6700` daba 4,36:1); el fondo no cambia. |
| azul | `--st-blue-bg` `#e8f0ff` · `--st-blue-ink` `#2451c7` | informativo (por facturar). Es un color de estado, **no** `--brand`. |
| teal | `--st-teal-bg` `#e6f5f3` · `--st-teal-ink` `#0f766e` | manual |
| neutro | `--st-neutral-bg` `#eef0f4` · `--st-neutral-ink` `#5b6472` | no aplica / origen genérico |
| rojo | `--st-red-bg` `#fdeaea` · `--st-red-ink` `#b42318` | bloqueado / incidencia |
| morado | `--st-purple-bg` `#f0e9fd` · `--st-purple-ink` `#6d3fc4` | woo / intracomunitario |

Foco visible (global): botones, enlaces, `summary`, `[role=button]` y
`[tabindex]` llevan `outline: 3px solid var(--focus)`; los campos llevan el
mismo anillo por `box-shadow`; las casillas y radios, `outline`.

## E2 · Tipografía

Escala cerrada de seis pasos. Nada por debajo de 12 px (12 solo para
etiquetas). La fuente sigue siendo la del sistema (`--font-base`; el
prototipo usa Geist, no adoptada).

| Token | Tamaño | Dónde |
|---|---|---|
| `--fs-display` | 28 px | cifra grande de cola/contador, `h1` del taller |
| `--fs-title` | 20 px | título de página/modal, importe total del panel |
| `--fs-subtitle` | 16 px | nombre de cliente, «siguiente paso», botones grandes |
| `--fs-body` | 14 px | cuerpo, filas, pares etiqueta/valor, botones |
| `--fs-label` | 12 px | etiquetas, cabeceras de tabla, pastillas, chips |
| `--fs-mono` / `--font-mono` | 13 px · `ui-monospace, SF Mono, Menlo, Consolas…` | **todo dato numérico** |

Regla nueva: **todo dato numérico va en monoespaciada** para poder comparar
y copiar sin equivocarse: referencias, nº de pedido/albarán/factura, NIF/CIF,
importes, nº de serie y licencias. Utilidades:

- `.mono` — texto de dato en línea (`<span className="mono">ESB12345678</span>`).
- `.num` — celda numérica de tabla: monoespaciada **y alineada a la derecha**
  (`<td className="num">4.290,00</td>`, también en el `<th>`).

Ya la aplican por CSS: `.erp-flow-date`, `.erp-flow-amount`,
`.erp-flow-total .n`, `.erp-flow-kv .v`, `.erp-article-sku`,
`.erp-approval-num`, `.sat-card-num`, `.sat-td-num`, `.erp-doc-cobros-resumen dd`.

## E3 · Espaciado y radios

Escala de 4 px, siete pasos, sin valores fuera de lista:

`--sp-1` 4 · `--sp-2` 8 · `--sp-3` 12 · `--sp-4` 16 · `--sp-6` 24 · `--sp-8` 32 · `--sp-12` 48

- Dentro de una tarjeta: 12 / 16. Entre bloques: 24 / 32.
- Radios: `--r-control` 8 px (botones, campos, casillas), `--r-panel` 12 px
  (tarjetas, paneles, modales), `--r-pill` 99 px (solo pastillas).

Los bloques `.erp-*`, `.sat-*`, `.company-*` y `.erp-status-*` del
stylesheet están ya normalizados a esta escala; el CRM no se toca.

## E4 · Patrón de estado: rejilla fija

El estado del pedido es **estructura, no prosa**: cuatro casillas fijas
**Pago · Factura · Cobro · Envío**, siempre en el mismo orden y posición y
con el mismo color. Verde = hecho, ámbar = pendiente, gris = no aplica
todavía, rojo = bloqueado. Cada casilla lleva su palabra y la palabra del
estado, así que funciona sin distinguir colores.

Componente: `frontend/src/app/components/erp/OrderStatusGrid.tsx`.

```tsx
<OrderStatusGrid order={order} />            // tarjeta / ficha
<OrderStatusGrid order={order} size="sm" />  // celda de tabla (2×2 compacta)
```

- Entrada: `payment_status`, `invoice_status`, `factusol_invoice_number`,
  `factusol_cobro_status`, `transport_status` (cualquier `OrderSummary`).
- Criterio: Pago `paid` → hecho, `pending/partial_paid/credit_approved` →
  pendiente, `failed/refunded` → bloqueado. Factura emitida → hecho,
  `error` → bloqueado, resto pendiente. Cobro `cobrada` → hecho, facturada
  sin cobrar → pendiente, sin factura → no aplica. Envío
  `in_transit/delivered/already_shipped_externally` → hecho,
  `incident/returned` → bloqueado, resto pendiente.
- Accesibilidad: cada casilla es `role="img"` con `aria-label` «Pago: hecho»
  / «Cobro: no aplica»… y `title` con el detalle. El valor conserva el
  `aria-label` del contrato anterior («Pagado: sí», `is-on`/`is-off`), que
  siguen usando la bandeja y sus tests.
- Las casillas **informan, nunca se pulsan** (no son botones).
- Móvil (< 768): 2×2 con etiqueta a la izquierda y valor a la derecha.
- `orderStatusCells(order)` devuelve las cuatro casillas como datos por si
  una pantalla necesita pintarlas de otra forma.

`OrderStatusPills` (bandeja) = la rejilla + la pastilla «Completado» (marca
manual de BoHub, no es un paso del proceso; por eso va como pastilla aparte).

Pastillas: un solo tamaño por contexto; `.erp-flow-pill` (régimen),
`.erp-flow-src` (origen/tienda) y `.erp-status-pill` con radio `--r-pill`.

## E5 · Tablas o tarjetas: un solo criterio

| Patrón | Cuándo | Clases |
|---|---|---|
| **Fila de trabajo** | cada elemento tiene su acción y hay que decidir sobre él | `.erp-flow-item` (bandeja, proformas), `.sat-card` (taller). Fila alta, rejilla de estado, botón de siguiente acción. |
| **Tabla de consulta** | se mira para comparar cifras y fechas | `table.data-table` (+ `.data-table--responsive`). Densa, `td.num` a la derecha en mono, cabecera fija, envoltorio con scroll propio. |
| **Panel de ficha** | pares etiqueta/valor de una sola entidad | `.erp-flow-panel` + `.erp-flow-kv` (fila) o `dl.erp-kv-grid` (2 columnas ≥ 1280, 1 por debajo). Nunca tabla. |

Tabla responsive: añade `data-table--responsive` a la tabla y `data-label`
a cada `td`; por debajo de 768 px cada fila pasa a ser una tarjeta de pares
etiqueta/valor (`td::before { content: attr(data-label) }`).

```tsx
<table className="data-table data-table--responsive">
  <thead><tr><th>Nº</th><th>Cliente</th><th className="num">Importe</th></tr></thead>
  <tbody>
    <tr>
      <td data-label="Nº" className="mono">F-2026/118</td>
      <td data-label="Cliente">Rotulación Levante S.L.</td>
      <td data-label="Importe" className="num">4.290,00</td>
    </tr>
  </tbody>
</table>
```

Ya lo usan las tablas del detalle de documento FACTUSOL (líneas y cobros).
La vista lista de la bandeja (`.erp-bandeja-table`, < 1100) y la tabla del
taller (`.sat-table`, < 768) se apilan por CSS aunque aún no lleven
`data-label`. **No hay scroll horizontal de página en ninguna pantalla.**

## E6 · Botones y modales

Escala de botones (una sola en todo el ERP; un primario por pantalla):

| Variante | Clase | Aspecto |
|---|---|---|
| Primario | `.button` | azul de marca; la siguiente acción del sistema |
| Secundario | `.button.secondary` | blanco con borde `--line`, tinta `--ink` |
| Terciario | `.button.tertiary` (alias `.ghost`) | sin caja, `--muted`; dentro del panel al que pertenece |
| Destructivo | `.button.danger` | blanco, borde rojo suave, tinta `--danger-ink`; separado del grupo y con confirmación escrita |

Alto mínimo `--control-h` 40 px en escritorio y `--control-h-touch` 48 px
por debajo de 768 px (todo control pulsable). `.button.small` (32 px) queda
para filas de tabla y chips densos; `.button.lg` = 48 px.

Molde único de modal (`.modal-dialog`):

- Cabecera con título y contexto, cuerpo con scroll propio, pie fijo con la
  acción a la derecha.
- Dos anchos: `--modal-w-form` 520 px (por defecto) y `--modal-w-wide`
  720 px (`.modal-dialog.wide`: buscador con resultados, detalle con tabla).
  **Anchos ad hoc prohibidos.** La única excepción documentada es
  `.modal-wide` (editor de líneas de la proforma, 8 columnas), que
  desaparece cuando la proforma pase a página propia.
- Estructurado: `.modal-header` + `.modal-body` (scrollea) + `.modal-actions`
  (pie fijo). Ejemplo: `RegistrarCobroModal`.
- Plano del ERP (`.modal-dialog.erp-modal`): el contenido va directo en el
  diálogo; el `h2` inicial hace de cabecera pegajosa (su `<span
  className="muted">` es el contexto, en segunda línea) y `.modal-actions`
  de pie pegajoso. Alias: `.erp-emit-modal` (nombre anterior),
  `.company-picker-dialog`.
- Móvil: hoja a pantalla completa desde abajo (esquinas superiores
  redondeadas, botones a todo el ancho).

Normalizados en este lote: `CreateQuoteModal` (`erp-modal modal-wide`),
`InvoiceEmailModal`, `OrderEmailModal`, `ExcludeSeguimientoModal`,
`CancelOrderModal`, `EmbalarModal`, `EmitFactusolButton` (confirmación),
`EmitFactusolModal`, `FactusolDocumentDetailModal` (detalle `wide` + dos
confirmaciones) y `CompanyPickerModal` (520 / `wide` al crear).
`RegistrarCobroModal` ya seguía el molde estructurado.

## E7 · Responsive y accesibilidad

| Ancho | Comportamiento |
|---|---|
| ≥ 1280 | Ficha a dos columnas (`.erp-flow-grid2`): línea de vida a la izquierda, paneles a la derecha. `dl.erp-kv-grid` a dos columnas. |
| 768 – 1279 | Una columna; los paneles pasan debajo. |
| < 768 | Colas en fila con scroll táctil (la activa en oscuro), línea de vida vertical (mismo `<ol>`, otra dirección), rejilla de estado 2×2, importe y fecha apilados bajo el nombre, primario a todo el ancho, modales como hoja inferior, tablas como pares etiqueta/valor, targets de 48 px. |

Acción principal fija abajo en móvil:
`frontend/src/app/components/erp/PrimaryActionBar.tsx` (`.erp-primary-sticky`).

```tsx
<PrimaryActionBar hint="Se creará en FACTUSOL al guardar.">
  <button className="button">Crear pedido</button>
  <button className="button secondary">Guardar borrador</button>
</PrimaryActionBar>
```

Va al **final** del contenido de la pantalla (usa `position: sticky; bottom:
0`, que solo se mantiene visible mientras queda contenido por debajo). En
escritorio es una fila normal alineada a la derecha. La adopción en la ficha
de pedido y en el alta de pedido manual queda para los lotes de pantalla.

Mínimos que cumple todo lo anterior: contraste 4,5:1, target 48 px en móvil,
foco visible, nunca solo color (cada estado lleva su palabra).

## Guardas automáticas

`frontend/src/app/styles.test.ts` lee `styles.css` como texto y comprueba
que los tokens existen en `:root`, que los bloques ERP no usan tamaños de
fuente fuera de la escala ni los grises claros antiguos sobre datos, y que
los moldes (rejilla, modal, tabla responsive, barra fija) están definidos.
`OrderStatusGrid.test.tsx`, `OrderStatusPills.test.tsx` y
`PrimaryActionBar.test.tsx` cubren los componentes.

## Pendiente (fuera de este lote)

- Confirmación **escrita** en «Anular pedido» (hoy pide motivo opcional y
  confirma con botón): es lógica de la ficha/`CancelOrderModal`.
- Adoptar `PrimaryActionBar` en la ficha y en el alta de pedido manual.
- `data-label` en las tablas de páginas (documentos FACTUSOL, seguimiento,
  conciliación, actividad de empresa) para que el patrón responsive muestre
  la etiqueta de cada valor; el CSS ya está.
- Modales ERP pequeños que aún no llevan `erp-modal` (`MarkExternalModal`,
  `ReportExceptionModal`, `ConvertQuoteDialog`, `WooStoreForm`,
  `WooWebhookModal`, los de `CompanyFactusolPanel`): basta añadir la clase.
- La proforma como página propia (elimina la excepción `.modal-wide`).
