# Conciliación bancaria — los extractos de verdad

**Pantalla:** ERP · Conciliación (solo administración). **Código:**
`backend/app/erp/bank/` (`parsing.py` lee el fichero, `service.py` importa,
casa y exporta, `drive.py` y `drive_job.py` leen y escriben la hoja de
Drive), `backend/app/erp/api/bank.py`,
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
| fecha de operación | `FECHA OPER` o `F. Operativa` | `Boekingsdatum` |
| fecha valor | `FECHA VALOR` o `F. Valor` | `Valutadatum` |
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

Un mismo banco puede rotular igual una columna de dos maneras: la hoja de
Bart dice `FECHA OPER` y el export de la web de Sabadell (29/10/2026) dice
`F. Operativa`. **Añadir una variante es añadir el rótulo** a la tupla del
campo en su `Formato`. El resto (`Concepto`, `Importe`, `Saldo`,
`Referencia 1/2`) ya casa porque se compara sin mayúsculas.

El fichero puede ser `.xls` (el Excel antiguo que da hoy la web de Sabadell:
preámbulo de siete filas con «Consulta de movimientos», la cuenta y la fecha,
y la cabecera en la novena), `.xlsx` o `.csv`. Manda lo que es el fichero, no
su extensión.

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
la columna del importe ya no es el importe. Antes del #571 entraban así, con
el importe equivocado. La pista es la **divisa** (`Devies` en Belfius): si
está en su sitio, el importe es el suyo y la fila entra, aunque lo de detrás
(el BIC, el país) venga corrido. Si no, se busca la celda que trae la divisa
(`EUR`…): cuántas columnas está a la derecha es lo que se corrió la fila
(`parsing._recolocar`).

- **Se recoloca** cuando delante de la divisa están el importe y la fecha
  valor. Lo que se partió es el nombre del pagador («Streamtec, Sl»), la
  calle, el texto del movimiento, o varios (el nombre suele ir también dentro
  del texto).
  - El texto del movimiento empieza en la celda que empieza como uno
    (`Formato.transaccion`: «STORTING VAN…», «OVERSCHRIJVING…»); lo que sobra
    después se le une, con la coma que lo partió.
  - Lo que sobra antes es del nombre mientras el nombre así unido siga
    estando dentro del texto del movimiento; lo demás, de la calle. Si ni el
    nombre está en el texto, no se adivina.
  - Solo se corre el tramo del banco: las columnas de Bart (`factura`,
    `pedido`) se quedan donde están.
  - El resumen lo cuenta («N filas corridas recolocadas») y el movimiento
    queda marcado (`recolocada`). Hasta 4 columnas; más ya no es una coma
    suelta.
- **No se recoloca, y se dice:**
  - **«importe partido en dos columnas («-90» y «75»)»**: el tramo de las
    filas 54 a 401 del Belfius real se pegó partiendo también por la coma
    decimal (`-90 | 75 | EUR`). No se recompone: «5» puede ser ,05 o ,50. Si
    además está corrida, lo añade («…, y la fila corrida 1 columna más»). Se
    corrige el importe en el fichero (como se hizo con 153 de esas filas el
    29/10) y, si lo único que queda es el nombre partido, la fila se recoloca
    sola al reimportar.
  - **«fila corrida N columnas (…): no se sabe …»**: delante de la divisa no
    están el importe y la fecha valor, o no se sabe qué texto se partió.
  - Con la fecha valor ilegible («##») o vacía, el importe partido se dice
    igual.
  - **«fila descolocada: la fecha valor trae «…»»** y **«…divisa trae «…»»**:
    la fila no está corrida pero no cuadra.

Con el Belfius real, las 41 que quedaban no entendidas tras arreglar las 153:

| Qué les pasa | Filas | Cómo quedan |
|---|---|---|
| El importe partido **y** el nombre, la calle o el texto partidos | 24 | Siguen fuera, con «importe partido…, y la fila corrida N columnas más». Unido el importe, 17 se recolocan solas (comprobado con el fichero real en local) |
| Corridas sin el importe y la fecha valor delante de la divisa | 5 | Fuera: «fila corrida…: no se sabe dónde acaba el importe» |
| Corrida más allá de las columnas del banco | 1 | Fuera: «fila descolocada: divisa trae…» |
| Sin importe | 6 | Fuera, como antes |
| Sueltas (sin fecha ni importe) | 2 | Fuera, como antes |
| Un nombre en la columna del importe | 1 | Fuera, como antes |
| La divisa en su sitio y algo raro en el BIC | 2 | **Entran** (la regla del BIC ya no existe: no tocaba el importe) |

Solo un fichero **sin fila de columnas reconocible** se rechaza entero, y el
error dice qué formatos conoce.

## Desde Drive: «Traer de Drive», el repaso diario y la factura escrita en la hoja

Las dos hojas del banco viven en el Drive de Bart y él las actualiza casi a
diario: pega arriba los movimientos nuevos y, al conciliar, escribe la
factura en su columna. BoHub las lee solo y escribe la factura y el pedido
que se concilian en la pantalla. Es el patrón de Seguimiento
(`app/erp/drive_sheets.py`) con su misma **cuenta de servicio**.

### Configuración (pestaña Cuentas)

Cada cuenta puede tener su hoja: el **enlace** (o el identificador), la
**pestaña** (vacía: la primera) y si BoHub **escribe** en ella (encendido de
partida; apagado, solo lee). La pantalla dice con quién compartirla:

- con la cuenta de servicio de Configuración ERP (la de Seguimiento,
  `bohub-seguimiento@bomedia-crm.iam.gserviceaccount.com`);
- como **Lector** si BoHub solo lee, como **Editor** si además escribe.

Se reutiliza esa cuenta de servicio y no una aparte: es la misma clave, y
cada una solo ve los ficheros que se le comparten. Una cuenta aparte solo
serviría para tener otra clave que guardar.

### `.xlsx` o hoja de Google

Las dos hojas del banco son **ficheros `.xlsx` subidos a Drive**, no hojas de
Google (comprobado el 30/10: `mimeType` de Office). La de Seguimiento es una
hoja de Google nativa. Eso decide qué se puede hacer:

| | Hoja de Google | `.xlsx` subido |
|---|---|---|
| Leer | Se exporta a `.xlsx` (Drive `files.export`) | Se baja tal cual (`alt=media`) |
| Escribir la factura y el pedido | Sí: celdas sueltas con la API de Sheets (`values:batchUpdate`) | **No**: habría que subir el fichero entero y pisaría lo que Bart tenga abierto |

**Recomendación (y lo que hace la pantalla):** convertirlas a hoja de Google
(en Drive, abrir el `.xlsx` → Archivo → Guardar como hoja de cálculo de
Google), compartir la hoja **nueva** con la cuenta de servicio como Editor y
poner su enlace en la cuenta. Hasta entonces BoHub lee el `.xlsx` y avisa de
que no puede escribir en él; lo conciliado queda pendiente y se escribe
cuando la cuenta apunte a la hoja de Google.

### Leer

- **«Traer de Drive»**, al lado del fichero de siempre: la hoja de la cuenta
  elegida o de todas las que la tienen. Un resultado por cuenta: si Drive
  falla en una, lo dice ahí y las demás siguen.
- **El repaso diario a las 05:20 de Madrid** (el tic del índice de las webs,
  fuera de FLUX y del catálogo), en `worker-sync`, cola `bank:drive`. Lee cada
  hoja, importa lo que falta, recalcula las propuestas y escribe lo
  pendiente.
- **El fichero a mano se queda** como respaldo (Drive caído, otro fichero).

Se lee la hoja **entera** con el lector de siempre (solo cambia de dónde
sale el fichero) y entra lo que falta: la identidad de cada movimiento es la
clave anti-duplicados del importador, que no depende de la fila, así que las
filas nuevas por arriba no mueven nada.

### Escribir

Al confirmar una conciliación («Confirmar», «Elegir / repartir», «Confirmar
todas las de confianza alta») el movimiento queda **pendiente de escribir**
(`hoja_estado`) y se encola la escritura de su cuenta: una sola, unos 20 s
después, aunque se confirmen veinte seguidas. Sin Redis, lo pendiente espera
al repaso diario, a «Traer de Drive» o a «Escribir ahora».

Las reglas, las de Seguimiento:

- **Nunca pisar lo que ya hay.** Solo se rellena la celda vacía. Una celda
  con algo, aunque sea una nota, no se toca (`ocupada`). La única excepción
  es lo que escribió el propio BoHub (`hoja_factura`, `hoja_pedido`): si el
  movimiento se concilia con otra factura, BoHub cambia lo suyo, también el
  pedido (lo deja vacío si la factura nueva no tiene pedido en BoHub).
- **Nunca borrar ni reordenar.** El código solo escribe celdas sueltas.
- **La fila se busca por el movimiento**, con la misma clave del importador,
  nunca por su número.
- **Si la fila no está**, no se escribe nada y queda constancia
  (`no_encontrada`, y una línea en el registro con el id del movimiento, sin
  datos de la hoja). Se vuelve a mirar en cada pasada: si la fila llega
  después (se concilió desde el fichero a mano antes de que Bart la pegara),
  se escribe entonces.
- **Justo antes de escribir** se vuelven a leer esas celdas y las que
  identifican la fila (importe, saldo, concepto, nº de transacción): si Bart
  ha insertado filas entretanto, no se escribe y se reintenta en la
  siguiente pasada. Dos movimientos gemelos (mismo día, importe y concepto)
  no se confunden.
- **Una nota en la fila de continuación** (Sabadell parte el movimiento en
  dos filas) también cuenta: si Bart anotó abajo, arriba no se escribe.
- **Una fila corrida** (recolocada al leer) no se escribe: su `factura` no
  está en su columna (`corrida`). Si se corrige, se escribe.
- **Si se reasigna o se reabre mientras se escribe**, gana eso: la pasada no
  pisa el estado y la siguiente vuelve a mirar el movimiento.
- **Si la escritura falla** (la hoja compartida como Lector, Drive caído),
  la lectura cuenta igual: lo nuevo se importa y se casa, y el error se dice
  aparte; lo pendiente sigue pendiente.
- **Cambiar la hoja de la cuenta** (por ejemplo, a la convertida a hoja de
  Google) vuelve a poner como pendiente lo que no se pudo escribir en la
  anterior.

`factura` lleva las facturas confirmadas (varias, separadas por coma) y
`pedido` el pedido de BoHub de esas facturas, si lo hay: lo mismo que el
Excel conciliado (`service.valores_conciliados`). Lo que escribió BoHub,
al volver a leer la hoja, **no cuenta como anotación de Bart**: un
movimiento que se reabre no se vuelve a casar solo.

La pantalla dice, en cada movimiento, qué se escribió en la hoja o por qué
no; y en cada cuenta, la última lectura, la última escritura y lo pendiente
(«Escribir ahora»).

### El export del banco, directo a la hoja

Bart baja de la web del banco los movimientos de los últimos días y antes los
copiaba a mano en su hoja. Ahora, en Conciliación, con la cuenta elegida:
**«Export del banco → hoja de Drive»**, y se sube el fichero tal cual
(`POST /api/erp/bank/drive/anadir`).

1. **Vista previa**: «34 movimientos en el fichero · 21 ya están · 13 se
   añadirán», con la lista de los 13. No escribe nada.
2. **«Añadir 13 movimientos a la hoja»**: se insertan **arriba, encima de la
   primera fila de datos** (debajo de la cabecera), del más reciente al más
   antiguo, con `factura` y `pedido` vacías. Insertar y rellenar va en una sola
   petición a Google (`batchUpdate`: `insertDimension` + `updateCells`): o entra
   todo o nada. Las filas nuevas heredan el formato de la de debajo; las fechas
   van como fecha de hoja (o como «dd/mm/aaaa» si la hoja las tiene como texto).
3. Después se lee la hoja como en «Traer de Drive»: lo añadido ya está en
   BoHub (y recibe propuestas) sin pulsar nada más.

**Nunca se toca una fila existente**: ni se borra, ni se reordena, ni se
corrige. Ya está = `mismo_movimiento` contra lo que hay en la hoja, así que
vale aunque el export traiga movimientos anteriores a los de arriba (se
añaden también, arriba). Si el fichero es de otra cuenta, o la hoja es un
`.xlsx` subido, no se añade nada y se dice. Comprobado con el export real del
29/10/2026 contra la hoja de Sabadell: 34, 21 y 13.

### Datos de clientes

Las hojas llevan nombres de pagadores, direcciones e IBAN de terceros. Los
registros, los errores y el estado guardado de cada pasada
(`drive_resultado_json`) solo llevan cuentas, recuentos, filas e
identificadores de movimiento. Los errores de Google se traducen sin su
texto. Los tests y las capturas usan figurantes.

## El marco: ejercicio y facturas externas (29/10/2026)

- **Ejercicio.** Es el de FACTUSOL (cada ejercicio es una base de datos; BoHub
  lee el de Configuración ERP, `app.erp.ejercicio`). `list_movements(ejercicio=)`
  deja los movimientos de ese año (fecha de operación) y los contadores también.
  «Ver el movimiento» desde la ficha no se acota.
- **Solo pedidos de BoHub.** Sin `externas`, las propuestas contra una factura
  sin pedido no se ofrecen (`_ocultar_externas`): se quitan de `proposals` y se
  cuentan en `externas_ocultas`, y la confianza sale de lo que queda.
  `counters.solo_externas` cuenta los pendientes que solo casan con externas, y
  `solo_externas=true` filtra a esos. `confirm-high` confirma solo lo que se ve
  (con su `ejercicio` y `externas`). Ningún movimiento se esconde por la casilla
  y lo conciliado (`reconciled`) no se toca.
- Las propuestas se siguen calculando igual (contra todas las facturas
  pendientes): lo que cambia es qué se enseña y qué se ofrece.

### El año en Conciliación (30/10/2026)

El selector de Conciliación no se ciñe a los ejercicios de FACTUSOL: ofrece
**todos los años que tienen movimientos** (`GET /api/erp/bank/ejercicios`,
`service.ejercicios_de_movimientos`), cada uno con su recuento, el ejercicio
en curso aunque aún no tenga movimientos, y **«Todos los años»**. El extracto
es un hecho: ningún movimiento queda fuera de alcance, y «Todos los años» suma
lo mismo que hay en la base de datos. Un año que no es el en curso es solo
consulta. «Registrar cobro» solo se ofrece en movimientos del ejercicio en
curso, se elija el año que se elija: FACTUSOL se escribe en ese ejercicio.

### El prepago: la factura, después del cobro

En esta casa lo normal es que el cliente pague y la factura se emita
después. El casador acepta una factura emitida hasta **15 días después** del
cobro (`matching.VENTANA_FACTURA_POSTERIOR`). Es una pista, no un filtro:

- el motivo lo dice: «factura emitida 1 día después del cobro»;
- sola, no sube a confianza alta: se queda en media;
- si el pedido de BoHub de la factura consta pagado (prepago), no penaliza, y
  el motivo añade «(pedido pagado por adelantado)».

Más allá de los 15 días no se propone. La búsqueda a mano de «Elegir /
repartir» nunca ha mirado fechas.

## Duplicados

La clave sale de cuenta, fecha, importe, concepto y saldo.

**La identidad de un movimiento** (`parsing.mismo_movimiento`). La hoja de
Bart corta el concepto a 26 caracteres («ABONO TRANSFERENCIA DE DAN») y el
export de la web lo trae entero: la clave cambia, pero es el mismo
movimiento. Por eso, cuando la clave no casa, se mira la identidad: misma
**fecha de operación**, mismo **importe** y mismo **saldo** (dos del mismo día
e importe tienen saldos distintos); sin saldo en los dos, el número de
transacción (Belfius) y, si tampoco, el concepto recortado al prefijo común.
Cada movimiento conocido vale para uno solo (`Identidades`). La usan los
tres sitios que reconocen un movimiento: el importador (no duplica lo que
entró por la hoja), la escritura de la factura en la hoja (encuentra la fila
aunque el movimiento entrara con el concepto entero) y el export a Drive. La
clave guardada (`dedupe_key`) no cambia: no hay que migrar nada.

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
  - `forma` es «Transferencia» y `fecha` es la **fecha valor** del movimiento
    (la de operación si no la trae). Hasta el 30/10/2026 era la de operación.
  - El script no lleva importe: registra el **saldo entero** de la factura.
    Por eso se lee FACTUSOL (`pending_invoices`, solo lectura) y entra cada
    factura cuyo saldo pendiente cuadra con lo conciliado de ella (aunque
    venga de dos movimientos). Sin FACTUSOL, 503: no se sabe qué falta.
  - Lo ya cobrado (total − saldo) se descuenta de todo lo conciliado de la
    factura, sin el filtro de cuenta ni de fechas. Así, tras un cobro parcial
    registrado desde la ficha, la factura no vuelve a entrar por ese mismo
    movimiento (el script registraría lo que el cliente aún debe). Cuando se
    concilia el resto, entra, con la fecha valor del último pago.
  - Pagada en varios movimientos, la fila (cuenta, fecha y observaciones)
    sale del último. Si los pagos vienen de cuentas con contrapartidas
    distintas, se deja fuera (`No-Cuadran`): el script registra una sola línea
    en una cuenta.
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


## Una sola herramienta de cobro (29/10/2026)

`RegistrarCobroModal` es el diálogo de cobro de la ficha (y su paso «Cobro»),
de Conciliación y de ERP · Documentos: mismo texto, mismas comprobaciones y
mismo apunte en Auditoría.

- El contexto lo da `app/erp/cobro_contexto.py` (en `…/cobro` de la factura y
  `factusol-cobro` del pedido, con `?movimiento=`): `externa`, `movimiento`
  (el pedido, o el conciliado cuyo cobro aún no es una línea de FACTUSOL),
  `hoja` (si se escribirá en la hoja y en cuál) y `cobrado_con`.
- Con movimiento se escribe por `orders.cobro_con_movimiento`, el de la ficha
  de siempre (concilia, cobra con la fecha valor y la contrapartida de la
  cuenta, avisa del posible doble cobro, solo concilia si ya está en
  FACTUSOL), que ahora vale también por factura:
  `POST /factusol/documents/facturas/{serie}/{codigo}/cobro-banco`, con su pedido
  o como externa (Auditoría a la factura).
- Después escribe la factura en la hoja **en el momento**
  (`escribir_hoja_del_movimiento` → `drive.escribir_cuenta`, con el cerrojo de
  la cuenta) para decirlo en el diálogo. Son las reglas del #572: solo celdas
  vacías, la fila por la identidad del movimiento, nunca reordenar. Un fallo
  en la hoja no toca el cobro: se devuelve en `hoja`, queda en el movimiento
  (`hoja_estado`) y en la cuenta, y la cola lo reintenta.
- El CSV de cobros sigue: es el respaldo del día que la escritura directa falle.

## Desde la ficha del pedido (30/10/2026)

El paso «Cobro» de la ficha propone el movimiento que pagó la factura del
pedido. Es el mismo casador, pero visto desde la factura
(`service.movimientos_de_factura`):

- **Qué movimientos mira:** los de entrada aún pendientes, en cuentas de la
  serie de la factura (o sin serie), desde 3 días antes de la fecha del pedido.
  Un web se paga antes de facturarse. Fuera los excluidos.
- **En qué orden los prueba:**
  1. el nº de factura en el concepto o en lo anotado («2-526081» o el código
     suelto): alta con importe exacto, media si no;
  2. si no, lo que Conciliación ya proponía;
  3. si no, `propose()` contra esta factura sola. Coincidir solo en el importe
     baja a confianza baja («solo coincide el importe»), y se descarta si hay
     algo mejor.
- **Las marcas:** `parcial` (menor que lo pendiente) y `excede` (mayor, se
  reparte en Conciliación).

**Confirmar** (`POST /api/erp/orders/{id}/cobro-banco`, `erp.cobro.register`):

1. Lee la factura en vivo (`collection_status`).
2. **Concilia** el movimiento con la factura (`conciliar_desde_pedido`). Si
   Conciliación lo proponía, confirma esa propuesta; si no, queda como elegida
   desde la ficha (`origen = "pedido"`).
3. Marca y encola la **hoja de Drive**, como cualquier conciliación.
4. Registra el **cobro** por el endpoint F-4-B: fecha valor, contrapartida de
   la cuenta, Transferencia y, si es parcial, ese importe.
5. Auditoría `erp.order_cobro_banco` (target `order`).

**Nunca dos veces.** Antes de registrar, se casan los movimientos de la
factura (los conciliados y el que se confirma) con sus líneas de cobro en
FACTUSOL (`casar_lineas_de_cobro`):

- casa el mismo importe (±0,01) con una fecha a ±7 días de la fecha valor o
  de operación;
- cada línea vale para un solo movimiento, y se reparten para casar los más
  posibles, primero las de la misma contrapartida que la cuenta del banco.

Si el movimiento ya tiene su línea, no se registra: se concilia (si no lo
estaba) y responde `status: "already"`, con la fecha y la cuenta de la línea,
y avisa si es otra cuenta. Un cobro mandado desde la ficha que sigue en cola
(menos de 30 minutos y su trabajo aún pendiente) bloquea otro igual (409
`en_cola`); uno fallido se puede reintentar. Si no se puede encolar, el
movimiento queda conciliado y se dice (`cobro_no_encolado`).

**Cobros sueltos.** Las líneas de cobro de FACTUSOL que no casan con ningún
movimiento (efectivo, tarjeta, o un pago apuntado a mano con otra fecha) se
enseñan en la ficha (`cobros_sueltos`). Si una tiene el mismo importe que lo
que se va a registrar, puede ser ese mismo pago: el POST responde 409
`posible_doble_cobro` y no hace nada, salvo que llegue `forzar` (la persona
marcó «es otro pago distinto»).

Los números de otros documentos del concepto («PROFORMA 526090», «pedido
1234») no cuentan como facturas al buscar la factura en el concepto.

La vuelta: `GET /api/erp/bank/movements?movimiento=<id>` filtra un movimiento,
y cada propuesta o conciliación lleva `pedido: {id, order_number}`.
