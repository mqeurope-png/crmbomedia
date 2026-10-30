# Histórico de ventas: «¿quién compró qué?» (30/10/2026)

Las ventas de FACTUSOL de 2014 a hoy, volcadas en tablas de BoHub, para
contestar qué clientes compraron un artículo o una familia. **Solo lee
FACTUSOL** (`CargaTabla`); la pantalla consulta lo volcado, nunca FACTUSOL en
vivo.

## Por qué se vuelca

Los ejercicios 2014-2022 solo los ve la API de FACTUSOL mientras estén
reabiertos en el escritorio (hasta el 30/10 respondían `BDNoExiste`). Se
vuelcan una vez y BoHub ya no depende de que sigan abiertos.

Medido el 30/10 (script `backend/scripts/diag_historico_ventas_factusol.py`):
13 ejercicios, 15.580 facturas / 50.521 líneas y 11.121 albaranes / 37.372
líneas; leerlo todo, 92 s. Por eso la carga es **entera por ejercicio**, sin
incremental fino.

## Tablas (migración `20261030_0153`)

| Tabla | Qué guarda |
|---|---|
| `historico_ejercicios` | Estado de la carga de cada ejercicio (`cargado`, `cargando`, `no_disponible`, `error`), fechas y recuentos. Sin datos de clientes. |
| `historico_documentos` | Cabeceras de `F_FAC`, `F_ALB` y, desde la 0155, `F_PRE` (proformas, `tipo = 'proforma'`, **no cuentan como venta**): tipo, serie (tal cual, sin interpretar), número, fecha (solo si es razonable) y la original, `codcli`, nombre del cliente, `total` con impuestos y `estado` tal cual (0155). Único por (ejercicio, tipo, serie, número). |
| `historico_lineas` | `F_LFA` / `F_LAL`: `codart`, descripción, cantidad, precio, total, `es_producto` (False = línea de nota, sin artículo). |
| `historico_articulos` | El `F_ART` de **cada** ejercicio (clave ejercicio + `codart`): un artículo descatalogado sale con el nombre de su año. |
| `historico_familias` | `F_FAM`, su `clase` (`maquina` / `consumible` / `recambio` / `accesorio` / `otro`) y su `gama`, que pone oficina en la pantalla; se conservan al recargar. `propuesta_descartada`: la propuesta que Bart vio y rechazó («Mantener la mía»). |
| `historico_gamas` (0154) | La lista de gamas: UV LED, Láser, DTF, CD-DVD, Packaging, Textil, CNC, Otros. Se amplía desde la pantalla. |
| `historico_reglas_familia` (0154) | Reglas que **proponen** clase y gama: por código exacto o por nombre que contiene un texto. Gana la de menor orden. Sembradas por código con la lista del encargo del 30/10; editables. |

No toca ninguna tabla existente.

## La carga (`app/erp/historico.py`)

- `cargar_ejercicio`: lee `F_FAC`, `F_LFA`, `F_ALB`, `F_LAL` y `F_ART` del
  ejercicio y **sustituye** lo de ese ejercicio en una transacción
  (idempotente: volcar dos veces deja lo mismo).
- Si FACTUSOL responde `BDNoExiste`: estado `no_disponible`, con el aviso de
  reabrirlo en el escritorio; **no se borra** lo ya volcado. Otro fallo, al
  leer o al guardar: se deshace la transacción y queda `error` con el tipo de
  excepción (nunca el mensaje: podría llevar datos). Un ejercicio que falla no
  para a los demás.
- Fechas imposibles (hay una de 1900): `fecha` vacía y `fecha_original` guardada.
- Líneas cuyo documento no está en la cabecera: se cuentan en `detalle` y no
  se guardan.
- Registros: solo ejercicio y recuentos.

## Cuándo se carga (`app/erp/historico_job.py`, cola `erp:historico`, worker-sync)

- **Cada día a las 05:20 (Madrid):** el ejercicio en curso entero y las
  familias; en enero, también el recién cerrado. Los demás cerrados no se
  recargan solos. Si a esa hora hay una carga pedida en marcha, el tic se
  reintenta cada 10 minutos (hasta 12 veces).
- **Desde la pantalla** (`POST /api/erp/historico/cargar`): los trece o uno.
  Una carga a la vez (cerrojo en Redis con ficha: cada carga suelta solo el
  suyo); los pedidos quedan `cargando` antes de encolar.
  Una carga `cargando` desde hace más de 40 min se enseña como interrumpida.
- A mano: `python -m app.erp.historico_job [2017 2018 …]`.

## La consulta

`compradores(codart | q | familia, desde, hasta)`: por cliente (`codcli`),
última compra (fecha o, si es imposible, el ejercicio; documento
serie-número), unidades, nº de compras y artículos. Solo líneas de producto.

- **Unidades y compras, por artículo:** de las facturas; de un artículo del
  que el cliente no tiene ninguna factura, de sus albaranes. Un albarán
  facturado y su factura son la misma venta.
- Los `%` y `_` que escriba el usuario se buscan tal cual (no son comodines).
- **Empresa de BoHub:** `Company.factusol_company_id == codcli`; si no hay,
  la pantalla marca «Sin empresa vinculada».
- `EQUART` (SKU comercial) no se usa para casar: solo lo tiene el 4 %.

## Ficha de un documento (`app/erp/historico_documento.py`, migración 0155)

`GET /api/erp/historico/documentos/{tipo}/{ejercicio}/{serie}/{codigo}` (tipo
`factura` / `albaran` / `proforma`), pantalla `/erp/documentos/ficha/...`. Se
abre desde la «Actividad reciente» de la ficha de empresa.

- **De lo volcado**, cualquier año: cabecera (número, fecha, ejercicio,
  cliente con su empresa, total, estado de cobro de la factura a fecha de la
  última carga), líneas con el nombre del artículo **del catálogo de su año**,
  y las líneas de nota aparte.
- **En vivo** solo si es del ejercicio en curso y aún no está volcado (creado
  después de la carga nocturna). Uno antiguo que no esté volcado da 404 con el
  aviso de cargarlo; nunca una ficha vacía.
- Pedido de BoHub enlazado (la misma regla que ERP · Documentos) o «Sin pedido
  de BoHub». Un pedido web que el usuario no puede ver cuenta como sin pedido.
- PDF: el de ERP · Documentos con su `ejercicio` (necesita que FACTUSOL tenga
  ese ejercicio abierto).
- Lo volcado antes de la 0155 no tiene total ni estado: la ficha enseña la
  suma de las líneas (sin impuestos) y lo dice. Se rellena al volver a cargar
  el ejercicio («Cargar los 13 ejercicios»), que es también lo que trae las
  proformas de los años cerrados.
- Solo lectura: cobrar, mención y vincular siguen donde estaban.

## Parque instalado (`app/erp/parque.py`, migraciones 0156 y 0157)

En la ficha de empresa, dos bloques (`/api/erp/parque/empresas/{id}` y
`…/compras`):

- **Máquinas:** las líneas de producto de sus facturas y albaranes cuyo
  artículo es de clase `maquina`: la **clase propia del artículo** si la
  tiene (`historico_articulos_clase`, ver abajo) o, si no, la de su familia
  en el catálogo de su año. Un accesorio, recambio o consumible no lo es.
  Con gama, fecha, ejercicio y documento (enlace a su ficha).
  - **Una venta, una vez (0157).** Un albarán y su factura cuentan como una
    máquina, la de la factura (con «y su albarán …» debajo). Se emparejan por
    el enlace que FACTUSOL guarda **en cada línea** (`DOC*`/`DTP*`/`DCO*`,
    ver `factusol-albaranes-discovery.md` §9), volcado en
    `historico_lineas.origen_tipo/serie/codigo`:
    1. la línea de factura apunta al albarán (`A`, serie, número): se
       facturó ese albarán;
    2. las dos apuntan al mismo presupuesto o pedido (`P`/`C`, serie, número).

    Mismo artículo y sin pasar de las unidades de la factura; si hay varias
    candidatas, la de fecha más cercana. El auto-enlace de un albarán
    autónomo (`A` a sí mismo) no cuenta. Un albarán que FACTUSOL da por
    facturado (`ESTALB` 1) tampoco cuenta, aunque su factura no esté aquí.
    Ya **no** se usa «albarán solo si no hay factura de ese artículo»: juntaba
    dos compras reales del mismo modelo.
  - **Limitación: sin enlace no se empareja.** Si el albarán y la factura se
    teclearon por separado, sin documento común, no hay dato que los una y
    salen los dos. Cuando un albarán sin enlace tiene una factura del mismo
    artículo, también sin enlace, a menos de 60 días (`DIAS_MISMA_VENTA`), la
    fila avisa «¿Misma venta que la factura …?»; si lo es, se da de baja el
    albarán (motivo «la misma venta»). El enlace no lleva el ejercicio: se
    casa por serie y número dentro del cliente. Una factura a otro código de
    cliente que el albarán tampoco se empareja.
  - **Hay que volver a cargar los ejercicios** después de la 0157: hasta
    entonces las líneas no tienen el enlace y no se empareja nada (solo el
    `ESTALB` 1).
  - **Abonos:** una línea en negativo anula la venta más reciente de ese
    artículo.
  - **A mano** (`parque_maquinas_manuales`): lo vendido antes de 2014, como
    línea de nota o en otro sitio. Se ven marcadas «Añadida a mano».
  - **Bajas:** una máquina que ya no está en servicio deja de contar en el
    parque activo (lo que usarán dormidos y huecos). Las volcadas se guardan
    en `parque_bajas` por una clave estable a las recargas
    `ejercicio|tipo|serie|codigo|posicion|codart`.
    **Limitación conocida:** si alguien edita el documento en FACTUSOL y
    cambian las líneas de orden, la posición cambia y la baja se pierde o cae
    en otra línea. En facturas antiguas no pasa.
- **Qué ha comprado:** sus documentos (facturas, albaranes, proformas). Por
  defecto el ejercicio en curso; «Ver todo el histórico» o un año concreto.
  Cada fila lleva su año.
- **Sin cliente de FACTUSOL vinculado** se dice: una lista vacía se leería
  como «no ha comprado nunca».
- Permisos: ver, `erp.office.view`; añadir a mano y dar de baja,
  `erp.orders.create`.

## Clase y gama (`app/erp/historico_clasificacion.py`, migración 0154)

- **Clase:** qué es. Un accesorio no es una máquina (el parque instalado no
  lo cuenta); un recambio se compra cuando algo se rompe (alerta de
  servicio); un consumible se repone con un ritmo (previsión).
- **Gama:** en una máquina, qué es; en lo demás, para qué máquina sirve.
  «Clientes con impresora UV LED» = máquina + UV LED; «qué ofrecerles» = el
  resto de la gama UV LED.
- **Propuestas:** se calculan al vuelo con las reglas. Estados:
  `propuesta` (falta algo y no choca), `coincide`, `revisar` (lo que puso
  Bart choca: no se cambia solo), `descartada` (chocaba y Bart mantuvo lo
  suyo; vuelve a `revisar` si cambia la propuesta), `sin_propuesta`.
- **«Aceptar todas»** solo rellena lo que falta en las que no chocan. Las de
  `revisar` cambian únicamente con «Aceptar» en su fila.
- La recarga de familias (`cargar_familias`) solo actualiza la descripción:
  clase y gama no se tocan.
- **Compradores** filtra también por `clase` y `gama` (de la familia del
  artículo en el catálogo de su año; la clase propia del artículo manda).
- **Clase propia de un artículo** (`historico_articulos_clase`, 0157): por
  encima de la de su familia, para las familias que mezclan máquinas y
  accesorios (la 021 de Flux: Beambox y Hexa con sus sensores). Vale para
  todos los años y todos los clientes. Se pone desde el parque de una
  empresa («No es una máquina», eligiendo qué es) y se cambia o se quita en
  Histórico → Familias → «Artículos con clase propia». La ficha de documento
  y compradores la usan también.
- Fuera de alcance: el modelo concreto (A2100, Beamo…).

## API (`/api/erp/historico`)

| Ruta | Permiso |
|---|---|
| `GET /ejercicios` (estado + resumen de notas) | `erp.office.view` |
| `POST /cargar` | `erp.documents` |
| `GET /familias` (con gamas y propuestas), `GET /reglas` | `erp.office.view` |
| `PATCH /familias/{codigo}` (clase y/o gama), `POST /familias/{codigo}/aceptar`, `POST /familias/{codigo}/mantener`, `POST /familias/aceptar-propuestas`, `POST /gamas`, `POST/PUT/DELETE /reglas` | `erp.config` |
| `GET /articulos?q=` | `erp.office.view` |
| `GET /articulos-clase` | `erp.office.view` |
| `PUT /articulos-clase/{codart}` (`{"clase": …}`; `null` la quita) | `erp.config` |
| `GET /compradores`, `GET /compradores.csv` | `erp.office.view` |
| `GET /documentos/{tipo}/{ejercicio}/{serie}/{codigo}` (ficha) | `erp.office.view` |
| `GET /api/erp/parque/empresas/{id}`, `…/compras?ejercicio=` | `erp.office.view` |
| `POST/DELETE /api/erp/parque/empresas/{id}/maquinas`, `POST …/bajas`, `POST …/bajas/deshacer` | `erp.orders.create` |

## Fuera de alcance

Consumos y previsiones, recuperar las líneas de nota por su texto, los SKU de
las tiendas WooCommerce.
