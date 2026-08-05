# Proformas FACTUSOL desde BoHub (Fase C · C-4)

Crear, listar, duplicar y convertir presupuestos de FACTUSOL sin salir del CRM.

---

## Lo primero: F_PRE es mono-línea

`F_PRE` (presupuestos) **no tiene tabla de líneas**. Cada fila es un
presupuesto completo, y el desglose vive como texto en `REFPRE`, 250
caracteres. Verificado sobre los 653 presupuestos del ejercicio 2026 de Bomedia
(ver `factusol-schema.md`).

Eso obliga a una decisión de diseño que explica casi todo lo demás:

> **BoHub guarda el desglose de las proformas que crea él**, en la tabla
> `factusol_quote_lines_cache` (migración 0088). Sin esa copia no se podría
> duplicar una proforma ni volcarla a un pedido con cantidades reales.

De ahí salen los dos «modos» que verás en la UI y en la API:

| `line_source` | Qué significa | Qué se puede hacer |
|---|---|---|
| `cache` | La proforma la creó el CRM y tenemos su desglose | Duplicar y volcar con líneas reales |
| `ref_text` | Se creó en el FACTUSOL de escritorio | Solo el texto de `REFPRE`: se vuelca como **una** línea con la base imponible, y el operador la ajusta |

`ref_text` no es un fallo: es todo lo que se puede reconstruir de una tabla
mono-línea. La UI lo dice explícitamente en vez de fingir un desglose.

---

## Flujo de trabajo

### Crear una proforma

Ficha de empresa → pestaña **Proformas FACTUSOL** → *Nueva proforma*. Dos
modos:

1. **Con artículos** — tabla de líneas con autocomplete contra `F_ART`. Al
   elegir un artículo se rellenan SKU, descripción y **precio de venta**. Ver
   «Buscar artículos» más abajo.
2. **Duplicar** — parte de una proforma anterior de **cualquier** cliente. Ver
   «Duplicar como plantilla».

> **Una proforma «simple»** (un concepto y un importe, sin catálogo) se hace en
> «Con artículos» con **una sola línea escrita a mano**, dejando el SKU vacío.
> Es lo normal para mano de obra, portes o reparaciones.
>
> C-4-fix2 retiró la pestaña «Rápida» que hacía exactamente esto: dos caminos
> para el mismo resultado, y el de «Rápida» además no dejaba añadir una segunda
> línea sin empezar de cero.

Requisito: la empresa debe estar **vinculada a un cliente de FACTUSOL**
(`companies.factusol_company_id`, que gestiona C-3). Sin CODCLI la proforma no
tendría dueño en la contabilidad → la API responde `409 company_not_linked`.

### Buscar artículos

El autocomplete busca el texto en **6 columnas de `F_ART`** a la vez, así que
da igual si tecleas el SKU, el EAN o media descripción:

`CODART` · `EANART` · `EQUART` · `DESART` · `DEEART` · `DETART`

La distinción que importa: **`CODART` es el código interno** (`00001`) y
**`EQUART` el SKU comercial** (`CDR80WPT`) — el que usan los operativos. La
lista muestra el comercial, con el interno como reserva si está vacío.

```
CODART '00001'   EQUART 'CDR80WPT'
DESART 'CD TQ 700 MB white Thermal WPT'   ← «CDR80» NO aparece aquí
DEEART 'CD TQ 700 MB white Thermal WPT'
DETART 'CD TQ 700 MB white T'
```

> C-4-fix1 arregló justo esto: se buscaba solo en `CODART`/`EANART`/`DESART`,
> así que teclear «CDR80» no devolvía nada aunque el artículo existiera. La
> descripción también puede vivir solo en `DEEART`/`DETART`.

El desplegable devuelve hasta **200 resultados** con scroll interno (C-4-fix2;
antes cortaba en 50 y escondía artículos válidos: «tinta» pasa de 100).

#### El precio de venta se detecta en runtime

`PCOART` es **coste**, nunca lo que se factura. El nombre de la columna de
**venta** no está verificado contra la base de Bomedia, así que el adaptador
**no lo fija en el código**: mira las claves que devuelve `CargaTabla` — que
sirve la fila completa — y usa la primera de `ARTICLE_PRICE_CANDIDATES` que
exista de verdad (`PVPART`, `PVP1ART`, `PV1ART`, `TAR1ART`, `PRE1ART`,
`PREART`).

> **Por qué no se hardcodea.** En lectura, `row.get("PVPART")` sobre una columna
> inexistente devuelve `None` **sin error**: todos los precios saldrían en
> blanco y no habría un solo log que lo delatara. Es la misma familia de trampa
> que C-3-fix1, pero más silenciosa.

Si ninguna candidata existe, `precio_venta` es `null`, la UI **deja el campo en
blanco** (nunca un `0.00`, que invitaría a emitir una proforma a cero sin que
nadie lo note) y el backend deja un `WARNING` con las columnas reales.

Para confirmar cuál usa esta base:

```bash
docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \
    python -m scripts.factusol_discover_article_prices
```

o, sin shell, mirando `precio_venta_columna` en la respuesta de
`GET /api/erp/factusol/articles/search?q=...`.

Las tarifas multinivel (`TAR1ART`, `TAR2ART`…) se devuelven en `tarifas` como
información, pero **ningún cálculo las usa**: es backlog.

### Artículos en el pedido manual

`/erp/orders/new` usa el mismo autocomplete en las columnas **SKU** y
**Descripción** de cada línea (`<ArticleAutocompleteInput>`, compartido con el
modal). Al elegir un artículo se rellenan SKU, descripción y precio.

Solo se activa si la empresa elegida está **vinculada a FACTUSOL** — sin CODCLI
no hay catálogo contra el que buscar. Escribir a mano sigue funcionando: el
autocomplete sugiere, no obliga.

### Duplicar como plantilla

Se puede duplicar **cualquier proforma, sea del cliente que sea**: en Bomedia
la mayoría de las plantillas vienen de otro cliente parecido («la de
Laboratorios Duaner sirve para Laboratorios Porta»).

En el modo «Duplicar» hay un buscador libre sobre todas las proformas del
último año. El texto casa contra **referencia**, **nombre del cliente de
origen** y **número de proforma**. Al pulsar *Cargar esta plantilla* el modal
se rellena con su desglose (o con su texto, si la proforma venía del
escritorio) y se crea una proforma **nueva** para el cliente destino, que se
puede cambiar arriba del todo con *Cambiar*.

> **Por qué no se usa `POST /quotes/{codpre}/duplicate` aquí.** Ese endpoint
> copia la fila `F_PRE` **entera**, incluido `CLIPRE`. Duplicar la proforma de
> otro cliente dejaría la nueva a nombre del cliente equivocado. La UI crea una
> proforma nueva con `POST /quotes` y el `company_id` destino. El endpoint de
> duplicado sigue existiendo para una copia exacta del mismo cliente.

Solo se ofrecen como destino empresas **ya vinculadas** a un cliente de
FACTUSOL: sin `CODCLI` el backend responde `409 company_not_linked`.

### Convertir una proforma en pedido

Desde la misma pestaña, *Convertir en pedido*: crea un **pedido manual del
CRM** (`MANUAL-000001`) con las líneas de la proforma y te lleva a su ficha. El
pedido sigue el circuito normal (preparar → embalar → enviar → facturar).

**No escribe un F_PCL en FACTUSOL**, y es deliberado:

- El pedido de cliente de los pedidos Woo lo crea la app externa
  Woo→FACTUSOL. Duplicar esa escritura es justo lo que produjo el
  `BDEscribirRegistroError` de C-2-fix1.
- El mapeo `F_PRE → F_PCL` por sufijo de columna es plausible pero **no está
  verificado**. En lectura una columna inexistente devuelve `[]` en silencio;
  en `EscribirRegistro` revienta. No se escribe a ciegas contra la
  contabilidad de producción.

### Cargar líneas en un pedido nuevo

En `/erp/orders/new`, al elegir una empresa vinculada aparece el desplegable
**«Proformas FACTUSOL disponibles»** con las 5 más recientes (180 días).
*Cargar líneas al pedido* las **añade** a las que ya haya en el formulario.

---

## API

Todo bajo `/api/erp/factusol`. Lectura: rol de vista. Escritura: `require_erp_edit`.

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/articles/search?q=` | Busca en las 6 columnas de F_ART |
| GET | `/quotes?company_id=&days_back=180` | Proformas del cliente. `unlinked: true` si la empresa no tiene CODCLI |
| GET | `/quotes/search?q=&days_back=365&limit=50` | Proformas de **cualquier** cliente (plantillas) |
| GET | `/quotes/{codpre}` | Proforma + desglose + `line_source` |
| GET | `/quotes/status/{job_id}` | Estado del job (`pending` / `finished` / `failed`) |
| POST | `/quotes` | Crea la proforma → **202 + job_id** |
| POST | `/quotes/{codpre}/duplicate` | Duplica → **202 + job_id** |
| POST | `/quotes/{codpre}/convert-to-order` | Crea el pedido CRM → **202 + job_id** |

> `/quotes/search` y `/quotes/status/{job_id}` se declaran **antes** que
> `/quotes/{codpre}`: FastAPI resuelve por orden y si no, «search» y «status»
> se interpretarían como un CODPRE. Hay un test para cada uno.

El filtro de texto de `/quotes/search` se aplica **antes** del recorte a
`limit`. Al revés, buscar una plantilla antigua no la encontraría nunca porque
las más recientes se habrían comido el cupo.

### Por qué 202 y no 200

Las escrituras van a la cola **`factusol:writes`**, que procesa
`worker-factusol` con **concurrency=1**. El `CODPRE` se calcula con `MAX+1`
justo antes de escribir; dos altas simultáneas se pisarían la numeración. Es la
misma cola y el mismo motivo que la emisión de facturas (`CODFAC`).

`convert-to-order` no escribe en FACTUSOL, pero comparte cola para que las tres
acciones tengan el mismo contrato de polling en el frontend.

---

## Detalles de implementación que conviene recordar

**Totales.** Solo se usa la **banda 1** de IVA (`NET1PRE` / `PIVA1PRE` /
`IIVA1PRE`). Si llegan líneas con tipos distintos se aplica el de la primera a
toda la base y se deja un `WARNING` en el log — repartir en las bandas 2/3/4
añadiría riesgo para un caso que Bomedia no tiene.

**Truncado de REFPRE.** El recorte a 250 lo hace el CRM y añade «…», en vez de
dejar que FACTUSOL corte a medias sin avisar.

**Orden de escritura.** Primero FACTUSOL, después la caché local. Si FACTUSOL
falla no hay nada que limpiar; si falla el commit local, la proforma existe y
solo se pierde el desglose (degrada a `ref_text`). Es el menos malo de los dos
fallos posibles.

**Reintentos.** `_save_lines_cache` borra las filas del CODPRE antes de
insertar, así que reescribir la misma proforma no duplica líneas ni choca con
el UNIQUE `(factusol_codpre, ejercicio, position)`.

---

## Fuera del alcance de C-4

Documentado para que no se dé por hecho:

- Albarán → factura.
- Editar una proforma ya emitida.
- Enviar o aceptar proformas (email al cliente, firma).
- Flujo Woo → FACTUSOL de proformas.
- `search_customers` sobre proformas.
