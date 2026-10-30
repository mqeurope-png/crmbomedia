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
| `historico_documentos` | Cabeceras de `F_FAC` y `F_ALB`: tipo, serie (tal cual, sin interpretar), número, fecha (solo si es razonable) y la original, `codcli`, nombre del cliente. Único por (ejercicio, tipo, serie, número). |
| `historico_lineas` | `F_LFA` / `F_LAL`: `codart`, descripción, cantidad, precio, total, `es_producto` (False = línea de nota, sin artículo). |
| `historico_articulos` | El `F_ART` de **cada** ejercicio (clave ejercicio + `codart`): un artículo descatalogado sale con el nombre de su año. |
| `historico_familias` | `F_FAM` y la `clase` (`maquina` / `consumible` / `otro`) que pone oficina en la pantalla; se conserva al recargar. |

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

## API (`/api/erp/historico`)

| Ruta | Permiso |
|---|---|
| `GET /ejercicios` (estado + resumen de notas) | `erp.office.view` |
| `POST /cargar` | `erp.documents` |
| `GET /familias`, `PATCH /familias/{codigo}` | ver: `erp.office.view`; clasificar: `erp.config` |
| `GET /articulos?q=` | `erp.office.view` |
| `GET /compradores`, `GET /compradores.csv` | `erp.office.view` |

## Fuera de alcance

Consumos y previsiones, recuperar las líneas de nota por su texto, los SKU de
las tiendas WooCommerce.
