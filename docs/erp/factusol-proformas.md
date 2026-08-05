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

Ficha de empresa → pestaña **Proformas FACTUSOL** → *Nueva proforma*. Tres
modos:

1. **Rápida** — un concepto de texto y un importe. Es la forma *nativa* de
   F_PRE y la que más se usa. Aun así se guarda como una línea en la caché,
   así que la proforma sigue siendo duplicable.
2. **Con artículos** — busca en `F_ART` (código, EAN o descripción) y compone
   el desglose. El precio que se propone es `PCOART`, que es **coste**: hay que
   revisarlo, no es una tarifa de venta.
3. **Duplicar** — parte de una proforma anterior del mismo cliente.

Requisito: la empresa debe estar **vinculada a un cliente de FACTUSOL**
(`companies.factusol_company_id`, que gestiona C-3). Sin CODCLI la proforma no
tendría dueño en la contabilidad → la API responde `409 company_not_linked`.

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
| GET | `/articles/search?q=` | Busca en F_ART por código, EAN o descripción |
| GET | `/quotes?company_id=&days_back=180` | Proformas del cliente. `unlinked: true` si la empresa no tiene CODCLI |
| GET | `/quotes/{codpre}` | Proforma + desglose + `line_source` |
| GET | `/quotes/status/{job_id}` | Estado del job (`pending` / `finished` / `failed`) |
| POST | `/quotes` | Crea la proforma → **202 + job_id** |
| POST | `/quotes/{codpre}/duplicate` | Duplica → **202 + job_id** |
| POST | `/quotes/{codpre}/convert-to-order` | Crea el pedido CRM → **202 + job_id** |

> `/quotes/status/{job_id}` se declara **antes** que `/quotes/{codpre}`: FastAPI
> resuelve por orden y si no, «status» se interpretaría como un CODPRE. Hay un
> test que lo fija.

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
