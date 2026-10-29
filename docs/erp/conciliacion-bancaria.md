# Conciliación bancaria — los extractos de verdad

**Pantalla:** ERP · Conciliación (solo administración). **Código:**
`backend/app/erp/bank/` (`parsing.py` lee el fichero, `service.py` importa,
casa y exporta), `backend/app/erp/api/bank.py`,
`frontend/src/app/erp/conciliacion/page.tsx`.

**Lo que no cambia:** nada se concilia solo (la máquina **propone**; confirma
una persona) y **FACTUSOL no se toca**. El cobro lo registra
`scripts/registrar_cobros_lote.ps1`, que simula primero.

## Por qué

La pantalla estaba hecha y nunca se había usado: el 28/10/2026, con las tres
cuentas dadas de alta, **ninguno de los dos extractos reales de Bart entraba**.

| Extracto | Error |
|---|---|
| Belfius (MQ Europe) | «no se encontró la fila de columnas (FECHA OPER / IMPORTE)» |
| Sabadell (Streamtec), detectando la cuenta | «El extracto es de la cuenta (sin IBAN en cabecera), que no está dada de alta» |
| Sabadell, con la cuenta elegida a mano | «fila 139: fecha vacía», y se descartaba el fichero entero |

## Los formatos: un diccionario por banco

`parsing.FORMATOS` dice, para cada banco, qué cabecera de columna es cada
campo. **Añadir un banco es añadir un `Formato`**, sin tocar el código. La
fila de columnas se encuentra sola: es la primera que trae las cabeceras de
fecha, concepto e importe de algún formato.

| Campo | Sabadell | Belfius |
|---|---|---|
| fecha de operación | `FECHA OPER` | `Boekingsdatum` |
| fecha valor | `FECHA VALOR` | `Valutadatum` |
| importe | `IMPORTE` | `Bedrag` |
| concepto | `CONCEPTO` | `Transactie` (texto largo: lleva la comunicación del pagador) |
| referencia 1 | `REFERENCIA 1` | `Transactienummer` |
| referencia 2 / pagador | `REFERENCIA 2` | `Naam tegenpartij bevat` |
| saldo | `SALDO` | — |
| cuenta propia | (en el preámbulo) | `Rekening`, en **cada fila** |
| divisa / IBAN del pagador | — | `Devies` / `Rekening tegenpartij` |
| `factura`, `pedido` (los rellena Bart) | rotuladas en la **primera fila** | en la fila de columnas |

Además:

- Una columna sin nombre en la fila de columnas toma el rótulo que tenga
  encima en el preámbulo, si es uno de los del formato. Así se recogen
  `factura` y `pedido` de Sabadell.
- Una columna sin nombre que trae datos (la tercera del Belfius) se guarda
  por su posición (`#3`) y el Excel conciliado la devuelve en su sitio.
- Las columnas vacías del final (el Sabadell trae 256) se descartan.
- Cada formato puede decir cómo es un campo (`coherencia`): en Belfius, la
  divisa son tres letras, el BIC un BIC (o el código de país de al lado) y la
  fecha valor una fecha. Ver «Filas corridas».
- Si una cuenta tiene un mapeo guardado a mano para un banco que no está en
  `FORMATOS`, ese mapeo cuenta como un formato más.

## De qué cuenta es el extracto

- **De la cabecera:** el IBAN o el **número antiguo**. Sabadell trae el CCC
  `0081-0202-17-0001259030`. Se lee celda a celda: unidas, el IBAN se comería
  la palabra de la celda de al lado («…9030 factura»). Un IBAN se recorta a
  la longitud de su país.
- **De la columna `Rekening` de cada fila (Belfius):** el valor más frecuente.
  Puede venir como IBAN o en formato belga antiguo (`068-2453123-20`).

`cuenta_casa(número, iban)` compara un IBAN entero. Un número antiguo se
compara **por sus dígitos, que tienen que ser el final del IBAN** dado de
alta: `0081-0202-17-0001259030` es `ES11 0081 0202 17 0001259030`. No se
calcula el dígito de control.

Una fila de Belfius que dice ser de **otra** cuenta no se importa: se enseña
como no entendida. Si la cuenta elegida a mano no es la del extracto, no se
importa nada, y se dice.

## Una fila mala no tumba el fichero

- **Continuación.** Una fila **sin fecha ni importe** (ni concepto ni fecha
  valor propios) justo detrás de un movimiento es su continuación. Sabadell parte así el pagador: la segunda
  fila trae el nombre completo («CLARA FIGUERAS M»), que el concepto corta a
  26 caracteres. Se une al movimiento y le aporta lo que no tenga:
  REFERENCIA 2 (el pagador), `factura`, `pedido`… El pagador del movimiento
  pasa a ser el nombre completo.
- **Fila vacía.** Se salta sin decir nada.
- **Sin fecha ni importe pero con concepto o fecha valor:** es otro
  movimiento al que le falta lo esencial, no una continuación. No entendida.
- **Sin fecha de operación pero con importe** (las filas antiguas de Belfius):
  vale la fecha valor.
- **«##» en una fecha** (lo que Excel enseña cuando no cabe, pegado como
  texto) cuenta como vacía: la fila entra sin fecha valor.
- **Lo demás que no se entiende** (importe que no es un número, fecha
  imposible, fila suelta sin movimiento delante, otra cuenta) **no aborta**.
  Se importa el resto y se devuelven las filas no entendidas, con su número de
  fila, el motivo y lo que traen. La pantalla da el resumen —«6.101
  movimientos importados, 194 filas no entendidas (54, 55, 56…)»— y deja
  desplegarlas.

### Filas corridas

Una celda de más empuja el resto de la fila a la derecha, y lo que queda en
la columna del importe ya no es el importe. Antes entraban así, con el
importe equivocado. Ahora la `coherencia` del formato lo detecta y la fila
sale como no entendida, con lo que trae:

- **«importe partido en dos columnas («-90» y «75»)»**: el tramo de las filas
  54 a 401 del Belfius real se pegó partiendo por la coma decimal
  (`-90 | 75 | EUR`). No se recompone: «5» puede ser ,05 o ,50. Se corrige en
  el fichero (o se vuelve a exportar ese tramo) y se reimporta; lo que ya
  entró no se duplica.
- **«fila descolocada: … trae «…»»**: la divisa, el BIC o la fecha valor no
  son lo que dicen (un texto del concepto partido por una coma, una fecha en
  la divisa…).

Un código de país en la columna del BIC, con la divisa en su sitio, no toca
el importe: esa fila entra.

Solo un fichero **sin fila de columnas reconocible** se rechaza entero, y el
error dice qué formatos conoce.

## Duplicados

La clave sale de cuenta, fecha, importe, concepto y saldo.

- **Con saldo** (Sabadell), dos filas idénticas son la misma: un solape al
  juntar exportaciones. El Sabadell real trae 5 (las filas 890-892 repetidas
  en 893-895, y dos más): entran una vez y se cuentan como repetidas.
- **Sin saldo** (Belfius), el número de transacción distingue dos iguales. Si
  tampoco lo traen, la segunda lleva «#2», la tercera «#3»…

Reimportar el mismo extracto, o uno solapado, da las mismas claves y no
duplica nada. Si el reimportado trae anotado lo que antes no, se guarda y se
casa (salvo que ese movimiento ya esté conciliado o descartado: lo decidido
en la pantalla manda).

## Las columnas `factura` y `pedido` ya vienen escritas

Lo que Bart anota a mano se guarda tal cual en el movimiento
(`anotacion_factura`, `anotacion_pedido`) y **no es una propuesta: es la
respuesta**. Solo cuenta en los cobros (importe > 0); en un pago, la columna
anota facturas de proveedor.

| Lo que pone la celda | Cómo entra |
|---|---|
| Solo números de factura: `5-260083`, `5-260088, 5-260089`, o `526043` sin serie (vale la de la cuenta) | **Conciliado**, confianza alta, con el porqué «Venía anotado en el extracto («5-260083»)» y la marca `origen = extracto`. Lo confirma quien importa el fichero |
| Factura con una nota: `5-260067 (parcial, …)`, `525067 - parcial`, `5-260027, 5-240054 (50%)`, o que nombra otro documento: `PF 5-260083, 5-260084` | **Propuesta** de confianza media, «…Revísalo antes de confirmar». «Confirmar todas las de confianza alta» no se la lleva, y el recálculo de propuestas no la pisa |
| Texto que no es una factura («falta facturar», «Invoice No.: …», una fecha, un importe, `proforma 260083`) | Nada se casa. La anotación se enseña en el movimiento, y el casado de siempre propone |

Varias facturas en un movimiento reparten el importe a partes iguales. El
porqué lo dice, y «Elegir otra / repartir» lo corrige.

Varias propuestas que suman **más** que el movimiento no son un reparto: son
facturas candidatas para el mismo cobro. «Confirmar» no las confirma todas
(pide elegir una con «Elegir / repartir») y «Confirmar todas las de confianza
alta» se las salta.

Con los extractos reales (29/10/2026):

| Extracto | Filas | Movimientos | Repetidas | No entendidas | Conciliados por la columna `factura` | Con nota, para revisar | Texto que no es factura |
|---|---|---|---|---|---|---|---|
| Sabadell Streamtec | 1.270 | 1.265 | 5 | 0 | 119 | 11 | 0 |
| Belfius MQ Europe | 6.296 | 6.101 | 1 | 194 | 323 | 66 | 34 |

Las 194 del Belfius: 153 con el importe partido (filas 54 a 401), 32 más
corridas, 6 sin importe, 2 sueltas sin movimiento delante y 1 con un nombre
en el importe. Reimportar los dos ficheros: 0 movimientos nuevos.

Lo que queda sin anotar es lo que falta por conciliar.

## Exportar

- **«Descargar Excel conciliado»**: el extracto con su formato (preámbulo,
  columnas y orden), sin tocar el original.
  - `factura` lleva las facturas confirmadas.
  - `pedido` lleva lo que traía anotado o, si no, el pedido de BoHub de esas
    facturas (`Order.factusol_invoice_number` + serie).
  - Lo no conciliado sale tal cual, con lo que Bart hubiera anotado.
  - Si el extracto no traía esas columnas, se añaden al final.
  - Las filas no entendidas no salen (no se guardan): pendiente.
- **«Descargar CSV de cobros»** (`GET /api/erp/bank/export-cobros`): el CSV
  que lee `scripts/registrar_cobros_lote.ps1`, con las columnas
  `serie,codigo,cuenta,forma,fecha,observaciones`.
  - Incluye lo conciliado y no descartado, una fila por factura.
  - `cuenta` es la contrapartida de cobro de la cuenta del banco (8 Sabadell
    Streamtec, 2 Belfius MQ Europe, 6 Sabadell Bomedia).
  - `forma` es «Transferencia» y `fecha` es la del movimiento.
  - El script no lleva importe: registra el **saldo entero** de la factura.
    Por eso se lee FACTUSOL (`pending_invoices`, solo lectura) y entra cada
    factura cuyo saldo pendiente cuadra con lo conciliado de ella (aunque
    venga de dos movimientos). Sin FACTUSOL, 503: no se sabe qué falta.
  - Se dejan fuera, y se cuentan en las cabeceras `X-Cobros-*` (la pantalla
    las dice en el aviso):
    - `Parciales`: lo conciliado no llega al saldo;
    - `No-Pendientes`: no está pendiente en FACTUSOL (ya cobrada, o de otro
      ejercicio);
    - `No-Cuadran`: el movimiento tiene conciliado más que su importe;
    - `Sin-Contrapartida`: la cuenta del banco no la tiene.
  - UTF-8 con BOM y observaciones en ASCII de hasta 200 caracteres, que
    Windows PowerShell 5.1 lee bien.
  - Respeta la cuenta y las fechas de los filtros.
  - **No escribe en FACTUSOL.** El script simula primero, lee el saldo en vivo
    y se salta lo ya cobrado.
