# Guía de uso del ERP de BoHub

Guía práctica del ERP para el equipo (ventas, administración y taller). Explica
**qué es cada pantalla y cómo se usa**, y luego el **paso a paso del ciclo de un
pedido** de principio a fin. No hace falta saber nada técnico: cada apartado
dice qué se ve, qué se pulsa y qué pasa después.

> Los nombres de pantallas, botones y estados de esta guía son los que aparecen
> hoy en la aplicación. Si en tu pantalla ves algo distinto, avisa: puede que la
> app haya cambiado y toque actualizar la guía.

---

## Índice

- [Cómo está organizado el ERP](#cómo-está-organizado-el-erp)
- [Roles y permisos](#roles-y-permisos)
- [Conceptos clave: los pasos del pedido](#conceptos-clave-los-pasos-del-pedido)
- [El menú y las pantallas](#el-menú-y-las-pantallas)
- [Parte 1: recorrido por el ERP](#parte-1-recorrido-por-el-erp)
  - [Bandeja de pedidos](#bandeja-de-pedidos)
  - [Ficha de pedido](#ficha-de-pedido)
  - [Empresas](#empresas)
  - [Proformas](#proformas)
  - [Documentos de FACTUSOL](#documentos-de-factusol)
  - [Cola SAT (el taller)](#cola-sat-el-taller)
  - [Seguimiento](#seguimiento)
  - [Excepciones](#excepciones)
  - [Cuadre (descuadres)](#cuadre-descuadres)
  - [Ajustes del ERP](#ajustes-del-erp)
- [Parte 2: el ciclo de un pedido, paso a paso](#parte-2-el-ciclo-de-un-pedido-paso-a-paso)
  - [A) Pedido web (WooCommerce), de principio a fin](#a-pedido-web-woocommerce-de-principio-a-fin)
  - [B) Pedido manual, de principio a fin](#b-pedido-manual-de-principio-a-fin)
  - [C) Casos especiales](#c-casos-especiales)
- [Chuleta de pastillas y estados](#chuleta-de-pastillas-y-estados)
- [Preguntas frecuentes](#preguntas-frecuentes)

---

## Cómo está organizado el ERP

Hay **dos programas que trabajan juntos**:

- **BoHub** (esta aplicación, el ERP): es el panel de trabajo. Aquí ves los
  pedidos, sabes qué falta por hacer en cada uno y lanzas las acciones.
- **FACTUSOL**: es el programa de contabilidad y facturación. Ahí viven de
  verdad los clientes, los presupuestos, los albaranes y las facturas.

**Regla de oro:** BoHub **lee** FACTUSOL constantemente y solo **escribe** en él
cuando tú lanzas una acción concreta y confirmas (emitir una factura, crear un
albarán, registrar un cobro, crear/actualizar un cliente, guardar una proforma).
Las pantallas que solo consultan (como **Documentos FACTUSOL**) lo dicen: *«Solo
lectura»*. **Las facturas nunca se borran desde BoHub.**

**Quién puede hacer qué:** cada acción del ERP exige un **permiso** concreto
según tu **rol**. Lo tienes en detalle en [Roles y permisos](#roles-y-permisos);
en resumen: el **Comercial** trabaja sus pedidos (no web) de punta a punta pero
**no cobra en FACTUSOL** ni ve los pedidos web; **ERP Pedidos** hace todo lo de
pedidos (web y no web) más la Cola SAT y los cobros; **ERP SAT** (taller) prepara,
embala y gestiona el envío; y el **Administrador** puede todo, incluido asignar
roles.

---

## Roles y permisos

La autorización del ERP va **por capacidad**, no por «nivel»: cada pantalla y
cada acción pide el permiso que le toca, y el servidor lo comprueba **siempre**
(si te falta, la acción se rechaza con un aviso claro; la interfaz además esconde
o deshabilita lo que no puedes hacer). Un usuario puede tener **uno o varios
roles**: su permiso es la **unión** de lo que permite cada uno.

| Área / acción | Comercial | ERP Pedidos | ERP SAT (taller) | Admin |
|---|:---:|:---:|:---:|:---:|
| Ver el ERP | ✅ | ✅ | ✅ | ✅ |
| **Pedidos web** (WooCommerce): verlos y actuar | ❌ | ✅ | ✅ (solo lectura) | ✅ |
| Pedidos **no web**: crear / aprobar / anular | ✅ | ✅ | ❌ | ✅ |
| Crear albarán / emitir factura (FACTUSOL) | ✅ | ✅ | ❌ | ✅ |
| **Registrar cobro** (FACTUSOL) | ❌ | ✅ | ❌ | ✅ |
| Enviar al cliente por email (factura/pedido) | ✅ | ✅ | ❌ | ✅ |
| Enviar al taller (SAT) por email | ✅ | ✅ | ✅ | ✅ |
| Proformas · Empresas · Documentos FACTUSOL | ✅ | ✅ | ❌ | ✅ |
| **Cola SAT**: ver | ✅ | ✅ | ✅ | ✅ |
| Cola SAT: subir etiqueta | ✅ | ✅ | ✅ | ✅ |
| Cola SAT: preparar / embalar / técnicos (serie, WhiteRIP) | ❌ | ✅ | ✅ | ✅ |
| Seguimiento (hoja / Drive) | ❌ | ✅ | ❌ | ✅ |
| Cuadre (descuadres) | ❌ | ✅ | ❌ | ✅ |
| Conciliación bancaria | ❌ | ❌ | ❌ | ✅ |
| Configuración · Integraciones (Woo) | ❌ | ❌ | ❌ | ✅ |
| **Asignar roles** a usuarios | ❌ | ❌ | ❌ | ✅ |

**Regla dura de los pedidos web:** el **Comercial no ve los pedidos web** en
ninguna lista (bandeja, Cola SAT, seguimiento) ni puede abrir su ficha —
la app los filtra y el acceso directo por enlace se rechaza.

**Enviar al taller (SAT) por email** también **mete el pedido en la Cola SAT**
(si aún no estaba): así el taller siempre lo ve. Un pedido con una excepción
abierta no entra en la cola hasta resolverla (misma regla que «Añadir a mano»).

**Auditoría:** toda acción con efecto (aprobar, emitir, cobrar, enviar, preparar,
cambiar de estado…) queda registrada con **quién** y **cuándo** en el historial
del pedido.

> **Roles antiguos (reconciliación).** Antes solo había «ver / editar / admin».
> Ahora: **Responsable** y **Usuario** quedan como **solo lectura** del ERP
> (pásalos a **Comercial** si trabajan pedidos); **Solo lectura (viewer)** no
> entra al ERP; y la **conciliación bancaria** pasa a ser **solo de admin**
> (antes la tocaba pedidos). El **Comercial** es un rol nuevo. Un administrador
> asigna los roles en **Usuarios** (menú de Administración): el rol principal y,
> si hace falta, roles de ERP **adicionales** (multi-rol).

---

## Conceptos clave: los pasos del pedido

Todo pedido recorre una **línea de vida** con **6 pasos obligatorios**, siempre
en este orden:

**1. Creado → 2. Pagado → 3. Aprobado → 4. Albarán → 5. Factura → 6. Cobro**

- Cada paso se marca **hecho** cuando se cumple, y el primero que queda por hacer
  es el **paso actual** (la ficha lo llama **«Siguiente paso»** y te ofrece ahí
  mismo el botón que toca). Arriba, una barra resume **«Paso N de 6»**.
- El paso **Albarán** es opcional: en un **pedido web** lo crea WooCommerce, así
  que aparece como **«no aplica»** (*«lo crea WooCommerce»*) y nunca es el paso
  actual.
- Un pedido se da por **terminado** cuando llega hasta **Cobro** (o hasta
  **Factura** si el cobro no aplica). Entonces ya puedes **«Marcar completado»**.

Después de los obligatorios, la línea de vida enseña **hitos OPCIONALES** que
**no bloquean** el completado y no cuentan en el «Paso N de 6»:

- **Factura enviada** — si la factura se ha mandado al cliente por email (con la
  fecha) o si está pendiente (con el botón **«Enviar factura al cliente»**).
- **El envío al taller (SAT)** ya **no es un paso** de la línea de vida: hay
  pedidos que no pasan por el taller. La preparación, la etiqueta, el tracking y
  el «marcar recogido» viven en la sección **«Envío y seguimiento»** de la ficha
  y son **opcionales**: un pedido puede completarse sin haber pasado por SAT ni
  tener tracking.

Además, por debajo, cada pedido tiene **cuatro estados independientes** que
verás en pastillas por toda la app:

| Estado | Para qué | Valores que puedes ver |
|--------|----------|------------------------|
| **Pago** | ¿está cobrado el cliente en la web/CRM? | Pendiente · Pagado · Pago parcial · Crédito aprobado · Pago fallido · Reembolsado |
| **Preparación** | ¿en qué punto está el taller? | Pend. revisión · En cola · Preparando · Embalado · Bloqueado |
| **Transporte** | ¿ha salido el paquete? | Sin enviar · Etiqueta creada · En tránsito · Entregado · Incidencia · Devuelto |
| **Facturación** | ¿está la factura en FACTUSOL? | Sin facturar · Facturado FACTUSOL · Error factura · Abono |

Hay dos «pagos» que conviene no confundir: el **Pago** de la web/CRM (¿pagó el
cliente el pedido?) y el **Cobro** en FACTUSOL (¿consta la **factura** como
cobrada en contabilidad?). Son cosas distintas y cada una tiene su pastilla.

---

## El menú y las pantallas

En el menú lateral, en modo ERP, verás estas entradas (el nombre exacto entre
comillas):

- **«ERP · Pedidos»** → la [Bandeja de pedidos](#bandeja-de-pedidos) (y dentro,
  **«Excepciones»**).
- **«ERP · Proformas»** → [Proformas](#proformas).
- **«ERP · Documentos»** → [Documentos de FACTUSOL](#documentos-de-factusol).
- **«ERP · Seguimiento»** → [Seguimiento](#seguimiento).
- **«ERP · Cuadre»** → [Cuadre (descuadres)](#cuadre-descuadres).
- **«ERP · Conciliación»** → conciliación bancaria (cuadre de cobros).
- **«ERP · Taller (SAT)»** → la [Cola SAT](#cola-sat-el-taller).
- **«ERP · Configuración»** → [Ajustes del ERP](#ajustes-del-erp) (solo admin).
- **«ERP · Integraciones · Woo»** → alta de tiendas y webhooks (solo admin).

Las **Empresas** viven en la parte de CRM (menú **«Empresas»**, ruta
`/companies`), pero se usan a diario desde el ERP, así que las incluimos aquí.

---

## Parte 1: recorrido por el ERP

### Bandeja de pedidos

**Menú: «ERP · Pedidos». Título de la pantalla: «Pedidos».**
Subtítulo: *«Bandeja de trabajo — ordenada por lo que hay que hacer.»*

Es tu punto de partida cada día. Arriba a la derecha tienes **«+ Nuevo pedido
manual»** para dar de alta un pedido a mano.

**Las colas (por acción).** Debajo del título hay tarjetas con un número; cada
una es una **cola** de pedidos que están esperando lo mismo. En orden:

1. **«Por revisar»** — *esperando tu aprobación*. Incluye los pedidos **sin
   pagar** (detrás de los pagados, con su casilla «Pago» en ámbar); es el mismo
   número que **«Pedidos pendientes de aprobación»** del inicio del ERP.
2. **«Por facturar»** — *aprobados, sin factura en FACTUSOL*.
3. **«Por cobrar»** — *facturados, sin cobro registrado en FACTUSOL*.
4. **«Por enviar»** — *cobrados, pendientes de salir*. Un pedido facturado,
   cobrado y ya **entregado** no está aquí: está en «Listo», esperando a que lo
   marques completado.
5. **«Incidencias»** — *algo bloquea el pedido*.
6. **«Listo»** — *nada pendiente*.

Pulsa una tarjeta para ver solo esa cola; vuelve a pulsarla (o usa **«Ver todas
las colas»**) para quitar el filtro.

**La acción principal de cada pedido** cambia según la cola:

| Cola | Botón del pedido | Qué hace |
|------|------------------|----------|
| Por revisar | **«Aprobar»** | aprueba el pedido en el momento (pasa a la Cola SAT); no cambias de pantalla |
| Por facturar | **«Emitir factura»** | abre la ficha para emitir la factura en FACTUSOL |
| Por cobrar | **«Registrar cobro»** | abre ahí mismo la ventana para registrar el cobro |
| Por enviar | **«Marcar completado»** | facturado y cobrado: márcalo como terminado (el envío al taller es opcional, desde la ficha) |
| Listo / sin acción | **«Abrir»** | abre la ficha |

**Filtros (fila «Refinar»).** Son desplegables (el primer valor es «todos»):
**«Preparación»**, **«Pago»**, **«Completado»** (*Solo completados* / *Sin
completar*), **«Cobro FACTUSOL»** (*Cobrado en FACTUSOL* / *Pendiente de cobro* /
*Con factura, sin comprobar*), **«Facturado»** (*Facturado* / *Sin facturar*),
**«Factura enviada»** (*Enviada* / *No enviada* — al cliente por email) y
**«Tienda»**. Además: fechas **«Desde»** y **«Hasta»**, y un botón de orden que
alterna **«Fecha ↓»** (más nuevos primero) y **«Fecha ↑»**.
Con **«Actualizar cobros FACTUSOL»** vuelves a leer de FACTUSOL el estado de
cobro de las facturas (solo lectura). Los botones **«Limpiar filtros»** y **«Por
defecto»** (todos los pedidos —**también los no pagados**— sin completar)
reinician la vista. Al entrar, «Pago» está en **«todos»**: los pedidos pendientes
de pago se ven en su cola, **detrás** de los pagados.

**Ver.** Un selector **«Ver»** con tres modos:

- **«Activos»** — la bandeja normal.
- **«Ocultados»** — solo los que quitaste a mano de la bandeja, con su motivo y
  la opción **«Reincluir»**.
- **«Anulados»** — solo los pedidos anulados (se restauran desde su ficha).

**Vista.** Botones **«Tarjetas»** (por defecto) y **«Lista»** (tabla con columnas
**Nº · Cliente · Tienda · Fecha · Importe · Estado · Cola · siguiente paso ·
Acciones**). En pantallas anchas la lista **scrollea dentro de su recuadro**
(cabecera fija; casilla y **Nº** fijos a la izquierda, **Acciones** a la
derecha); por debajo de 1100 px se apila en fichas como antes.

**El cliente, de lejos.** En cada tarjeta el cliente es la **segunda línea**,
justo bajo el Nº de pedido, en negro y grande: **empresa · persona** (sin
empresa, la persona). En la vista Lista, la columna Cliente va en negrita.

**Las pastillas de estado.** Cada pedido muestra una rejilla de cuatro celdas —
**Pago · Factura · Cobro · Envío** — más la etiqueta de completado: **«No
completado»** (gris, neutra) o **«Completado ✓»** (verde; al pasar el ratón,
*«Marcado como completado el … por …»*). El color
manda: **verde** = hecho, **ámbar** = pendiente, **gris** = no aplica, **rojo** =
bloqueado. Verás también badges sueltos como **«Cobrado FACTUSOL»** /
**«Pendiente de cobro FACTUSOL»**, **«Bloqueado»**, **«Externalizado»**,
**«Oculto»** o **«Anulado»**.

En cada fila, el menú **«⋯»** ofrece: **«Abrir ficha»**, **«Marcar
completado»**/**«Desmarcar completado»**, **«Registrar cobro»**/**«Cobrado»** y
**«Reincluir en la bandeja»**/**«Quitar de la bandeja»**. Y con casillas
marcadas puedes actuar en lote: **«Aprobar seleccionados»**, **«Completar
seleccionados»**, **«Quitar de la bandeja»**, etc.

### Ficha de pedido

Es la pantalla de un pedido concreto. De arriba abajo:

- **Cabecera** — el título **«Pedido …»** y, justo debajo y al mismo nivel, el
  **cliente** en negro y grande (**empresa · persona**, enlazado a su ficha):
  se lee antes que el número. La cola, el régimen de IVA y el nº de FACTUSOL van
  en su línea de chips.
- **Línea de vida del pedido** — los **6 pasos obligatorios** en columna (hasta
  **Cobro**); el paso pendiente se marca **«Paso actual»** y, si un pedido web no
  lleva albarán propio, ese paso dice *«lo crea WooCommerce»*. Arriba, una barra
  resume *«Paso N de 6»*. Debajo, el **hito opcional «Factura enviada»**: si la
  factura se mandó al cliente por email (con la fecha) o, si no, un aviso ámbar
  con el botón **«Enviar factura al cliente»** incrustado. Los hitos opcionales
  no cuentan en el «Paso N de 6» ni impiden completar. El **SAT/envío** ya no es
  un paso de esta línea: vive en **«Envío y seguimiento»** y es opcional.
- **«Siguiente paso»** — una frase que explica qué toca ahora y, al lado, **el
  botón exacto** para hacerlo (Emitir factura, Registrar cobro, Crear albarán,
  Marcar completado…). Si el pedido está bloqueado, este bloque cambia a **«Hay
  que resolver esto»**.
- **«Resumen económico»** — **Base imponible**, **IVA** (con el régimen entre
  paréntesis, o *«exento»*), **Portes y otros cargos**, **Forma de pago**,
  **Total**, **Cobrado** y **Pendiente de cobro**.
- **«Pedido» · Fecha del pedido** — con **«Cambiar fecha»** en los pedidos que
  no vienen de la tienda (manuales, muestras, creados desde un documento de
  FACTUSOL). La nueva fecha se usa en todas partes: ficha, listas, bandeja, Cola
  SAT y Seguimiento (pantalla, Excel y la columna «Fecha» de la hoja de Drive) y
  en la ordenación. **No** cambia las fechas de factura, cobro ni envío, no
  vuelve a crear el pedido ni escribe en FACTUSOL, y queda en la auditoría
  (fecha anterior → nueva, quién y cuándo). En un **pedido web** el botón está
  bloqueado: su fecha es la de WooCommerce (el tooltip lo explica).
- **Panel «FACTUSOL»** — las claves del pedido en FACTUSOL:
  - **Cliente** (nº de cliente vinculado, o *«sin vincular»*).
  - **Albarán** (número, o *«lo crea WooCommerce»* en web).
  - **Serie** (solo en pedidos manuales), con **«Cambiar serie»** y **«Crear
    proforma de cobro»**.
  - **Factura**: botón **«Emitir factura FACTUSOL»** (o el badge **«Facturado
    FACTUSOL #…»** si ya está).
  - **Cobro**: botón **«Registrar cobro en FACTUSOL»** (o **«Cobrado en
    FACTUSOL»**).
- **«Documentos de envío»** — filas **«Albarán»** y **«Etiqueta»**, con sus
  botones para crear/ver/subir/reemplazar el albarán y **«Subir etiqueta»**.
  Debajo, un panel **«Seguimiento»** con **Nº de serie**, **Licencia WhiteRIP**
  y el **Origen del envío**.
- **«Líneas»** — la mercancía (columnas **SKU · Artículo · Cant. · Total ·
  Mapping**). Si una línea no está emparejada con un artículo de FACTUSOL,
  aparece **«sin mapear»** (no pasa nada: se factura como texto libre).
- **«Historial»** — el registro de todo lo que ha ocurrido con el pedido.
- **«Otras acciones de estado»** — una fila por cada estado (**Pago ·
  Preparación · Transporte · Facturación**) con las transiciones que puedes
  disparar a mano (reembolso, empezar preparación, bloquear, etc.).

**Botones de la cabecera de la ficha:**

- **«PDF del pedido (FACTUSOL)»** — descarga el PDF del documento de origen
  (presupuesto/pedido) en el idioma que elijas en **«Idioma del PDF»**.
- **«PDF de la factura»** — descarga el PDF de la factura (disponible cuando ya
  está emitida).

> **Qué es «tener factura».** Un pedido tiene factura cuando consta como
> **facturado** y tiene guardados el **número y la serie** de su factura de
> FACTUSOL (se ve como **«serie-número»**, p. ej. **2-526107**, en la línea de
> vida, el panel FACTUSOL, ERP · Seguimiento y la hoja). Da igual de dónde
> venga el pedido (web, manual, creado desde una factura, un albarán o una
> proforma): el PDF de la factura, «Enviar factura al cliente» y «Registrar
> cobro» usan esa factura. Si un pedido tiene el número pero **le falta la
> serie**, la ficha lo avisa en rojo (*«Falta la serie de la factura …»*) y
> esas tres acciones no se hacen: el mismo número existe en varias series, así
> que BoHub no busca la factura solo por el número. Hay que completar la serie.
- **«Enviar a SAT»** — manda el pedido (con su albarán) por email al taller y,
  a la vez, lo **mete en la Cola SAT** (si ya estaba, no se duplica). Abre
  **«Enviar pedido por email»** para elegir destinatarios, adjuntos e idioma.
- **«Enviar factura al cliente»** — abre **«Enviar factura por email»** con el
  PDF de la factura adjunto.

> **Destinatarios: los contactos de la empresa.** En los tres envíos (factura,
> pedido/proforma) el modal muestra la lista de **contactos de la empresa del
> pedido** con su email: marca a quién enviar y elige si va en **Para** o en
> **CC** (puedes marcar varios). También puedes escribir direcciones a mano.
> Un contacto sin email sale deshabilitado. El correo queda registrado en el
> **timeline del contacto** y de su empresa; la auditoría guarda todos los
> destinatarios.

> **Remitente: elígelo con «Enviar desde».** El correo sale por la cuenta de
> Gmail conectada del CRM, y el desplegable **«Enviar desde»** ofrece todos los
> **«enviar como» verificados** de esa cuenta (p. ej. `pedidos@streamtec.es`,
> `info@bomedia.net`, la cuenta base…). Viene **preseleccionado** el remitente
> por tienda/serie del documento; puedes cambiarlo y el correo sale con ese
> `De`. Si Gmail solo tiene la cuenta base, el desplegable muestra solo esa.


- **«Marcar completado»** / **«Desmarcar completado»** — marca el pedido como
  terminado (solo en BoHub; WooCommerce no cambia).
- **«⋯» (Más acciones del pedido)** — cambiar el **idioma del pedido**,
  **«Restaurar pedido»** o **«Anular pedido»**.

**Ventanas importantes:**

- **«Emitir factura en FACTUSOL»** — avisa: *«Se creará una factura real en
  FACTUSOL. Esta acción no es reversible desde el CRM.»* Puedes elegir empresa
  emisora/serie, fecha y forma de pago; botón **«Emitir factura»**. Si falla,
  sale solo el **mensaje** (la traza técnica se queda en el log del servidor).
  En un pedido web que la app WooCommerce→FACTUSOL **aún no ha importado**
  (*«Este pedido aún no está en FACTUSOL…»*), aparece **«Volver a comprobar»**:
  vuelve a buscarlo en FACTUSOL (solo lee) y, en cuanto está, **«Emitir
  factura»** vuelve a estar disponible.
- **«Registrar cobro en FACTUSOL»** — eliges la **cuenta/contrapartida**, la
  fecha y la forma de pago, marcas la casilla de confirmación y pulsas
  **«Registrar cobro»** (es un apunte contable en FACTUSOL). Si al dar de alta
  el pedido ya apuntaste el pago (forma, cuenta y fecha), la ventana **viene
  prellenada** con esos datos; solo tienes que confirmar. Si no, la cuenta
  viene **sugerida** según la tienda y el **método de pago del pedido web**
  (p. ej. Artisjet Europe pagado con **«Carte»** —tarjeta por Mollie— → **15 Tarjetas
  Mollie Belfius**; PayPal de Artisjet Europe → 12; PayPal de boprint o fluxlasers →
  14) o, si no hay regla, la cuenta de la serie. Debajo pone **«Sugerida por:
  tienda Artisjet Europe · método Carte»**; puedes elegir otra y se registra la que
  elijas. El método de pago de WooCommerce se ve en el **Resumen económico →
  Forma de pago** y en la cola **«Por cobrar»** («Pago: Carte»).
  El **importe** viene con lo **pendiente en FACTUSOL**; puedes bajarlo para
  registrar un **cobro parcial** (la factura queda *Parcial* con el resto
  pendiente, y el siguiente cobro propone ese resto). Nunca deja registrar
  **más de lo pendiente**. Si el total del pedido y el de la factura no
  coinciden, avisa: *«Pedido 325,49 · Factura 333,96 · diferencia 8,47»* (el
  mismo aviso sale en la ficha); el cobro va por lo que dice la factura.
- **«Anular / corregir cobro»** — solo para los cobros que **registró BoHub**
  (los hechos a mano en FACTUSOL no salen). Está en la ficha (junto al estado
  del cobro) y en el menú de la fila de **«Por cobrar»**. **Anular** borra en
  FACTUSOL esa línea de cobro y la factura vuelve a *Pendiente* (o *Parcial* si
  tiene otros cobros); el pedido vuelve a «Por cobrar» y el historial dice
  *«Cobro de 333,96 € del 23/09/2026 (contrapartida 8) anulado»*.
  **Corregir** hace lo mismo y registra a la vez el cobro bueno con la fecha,
  cuenta e importe que pongas. Si alguien cambió esa línea en FACTUSOL
  (importe, fecha o cuenta), **no se borra nada** y se explica qué no coincide.
  Es una escritura en FACTUSOL: pide confirmación.
- **«Antes de generar el albarán»** — al generar el albarán de un pedido cuyo
  pago aún no se ha decidido, se pide elegir: **confirmar el pago** (queda
  apuntado en el pedido, sin escribir el cobro en FACTUSOL) o marcarlo **«sin
  cobro»** de un clic (envío de cortesía: ni se factura ni se cobra, y sale de
  «Por facturar» / «Por cobrar»). No se genera el albarán dejando el pago «en
  el aire».
- **Tras «Aprobar»** — la ficha ofrece, sin obligar, los siguientes pasos:
  **«Enviar a SAT»** y **«Generar albarán»** (que decide el pago / sin cobro).
  Se descartan con la ×.
- **«Anular pedido»** — cualquier pedido (web, manual o muestra), **también si
  tiene factura**. La factura (y el albarán / la proforma) **se desvinculan** del
  pedido y **siguen en FACTUSOL tal cual**: el aviso lo dice, *«La factura
  2-526110 seguirá existiendo en FACTUSOL y dejará de estar vinculada a este
  pedido. Si hay que anularla o abonarla, hazlo en FACTUSOL.»* Solo en un pedido
  **sin factura** puedes marcar **«Borrar también en FACTUSOL»** el
  albarán/presupuesto. El pedido sale de la bandeja, las colas y el seguimiento,
  queda en el historial (*«Anulado; factura 2-526110 desvinculada (sigue en
  FACTUSOL)»*) y **se puede restaurar** desde la ficha (vuelve a vincular lo que
  se desvinculó, si nadie más lo ha cogido).
- **«Desvincular»** (bloque FACTUSOL → **Vinculado**) — para un documento
  vinculado **por error**: el pedido deja de apuntarlo y el documento sigue en
  FACTUSOL. Un pedido normal queda «sin factura» y facturable de nuevo; una
  muestra que se queda sin documentos vuelve a ser muestra (no facturable, 0 €).

### Empresas

**Menú: «Empresas» (parte de CRM). Título: «Empresas».**

**El listado.** Buscador único arriba (*«Buscar por nombre, dominio o CIF…»*),
casilla **«Ver archivadas»** y botón **«+ Crear empresa»**. Puedes seleccionar
varias y hacer acciones en lote (**Activar**, **Desactivar**, **Cambiar
sector**).

**La ficha de empresa.** Bajo el nombre verás pastillas de estado: **«Activa»**
/ **«Archivada»**, el vínculo con FACTUSOL (**«En FACTUSOL · CLI-…»** o **«Solo
CRM»**) y el **régimen de IVA** (**nacional · con IVA** / **intracomunitario ·
exento** / **exportación · exento**).

- **Panel «Datos fiscales»**: **NIF / VAT**, **Régimen IVA**, **VIES**,
  **Dirección**, **País** y **Web**. La fila **VIES** muestra si el NIF-IVA está
  **✓ verificado en VIES**, **no válido**, **pendiente** o **no disponible**, con
  el botón **«Volver a comprobar»**.
- **Barra de alerta** cuando hace falta: NIF-IVA no válido en VIES, datos que
  **difieren de FACTUSOL** (con **«Traer datos de FACTUSOL»**) o empresa **sin
  vincular** (*«sin cliente F_CLI no hay albarán, factura ni proforma»*).
- **Pestañas**: **«Datos»** (editar la ficha), **«Contactos»** y **«Proformas
  FACTUSOL»**.
- **«Actividad reciente»**: los últimos **Pedidos**, **Facturas** y
  **Proformas** de la empresa.

**Botones de la cabecera:** **«+ Nuevo pedido»**, **«Nueva proforma»**, **«Traer
datos»** (sobrescribe la ficha del CRM con lo que hay en FACTUSOL; pide
confirmación y **no toca FACTUSOL**) y el menú **«⋯»** con **«Comprobar régimen
de IVA»**, **«Revalidar en VIES»**, **«Fusionar»**, **«Archivar»**/**«Reactivar»**
y **«Borrar»**.

**Panel «FACTUSOL» de la empresa** (buscar/vincular/crear el cliente):

- **«Buscar en FACTUSOL»** (por NIF) y **«Buscar por nombre»** — listan los
  clientes de FACTUSOL que coinciden para que elijas cuál **«Vincular»**.
- **«Crear en FACTUSOL»** — solo si no hay ninguna coincidencia; da de alta el
  cliente.
- **«Traer datos de FACTUSOL»** — copia a la ficha del CRM los datos del cliente
  (con vista previa; escribe solo en el CRM).
- **«Comprobar régimen de IVA»** — compara el régimen que le tocaría con el que
  tiene su ficha en FACTUSOL y, si difiere, corrige **solo** las columnas
  necesarias (con confirmación).

**Fusionar duplicados.** Desde **«⋯» → «Fusionar»**, buscas la empresa destino y
confirmas: *«Todo lo de "A" (pedidos, contactos, tareas, actividad y vínculo
FACTUSOL) pasa a "B" y "A" se archiva (reversible: no se borra).»* También, al
intentar vincular un cliente FACTUSOL que **ya tiene otra empresa**, la app
ofrece **«Fusionar con esa empresa»** (la ficha actual se fusiona en la que ya
tiene el vínculo, sin duplicar).

**Archivar / Reactivar.** **«Archivar»** deja la empresa fuera de listados y
buscadores (**reversible**, no borra nada); mientras esté archivada no se pueden
crear pedidos/proformas ni tocar FACTUSOL. **«Reactivar»** la devuelve.

**Alta de contacto.** Se hace desde **Contactos → «Crear contacto»** (`/contacts/new`);
allí eliges o creas la empresa con el mismo buscador único.

### Proformas

**Menú: «ERP · Proformas». Título: «Proformas».** *«Presupuestos enviados y su
estado.»* Botón **«+ Nueva proforma»**.

**Colas:** **«Todas»**, **«Aceptadas · por convertir»**, **«Pendientes de
respuesta»**, **«Rechazadas»** y **«Convertidas»**. **«Todas»** enseña las
proformas de todas las colas juntas en una sola lista (respetando empresa
emisora, fechas y búsqueda); las demás acotan a su cola. Cada fila muestra la
**antigüedad** en lenguaje claro (p. ej. *«Enviada hace 41 días · sin
respuesta»*), y a partir de 30 días sin respuesta se resalta en ámbar.

**Todas las empresas emisoras.** La pantalla enseña las proformas de **todas
las series** (1 Bomedia · 2 MQ Europe · 4 Lambert · 5 Streamtec), igual que
Documentos. El filtro **«Empresa emisora»** acota a una; **«Todas»** (por
defecto) las muestra todas, y las colas cuentan sobre lo que se está viendo.

> Antes solo se veían, en la práctica, las de la serie 1. No era un filtro: los
> contadores de FACTUSOL son **por serie** (la 1 va por el nº 526.080 mientras
> la 5 va por el 5), y el listado se ordenaba por número y se recortaba, así que
> las series de numeración baja se quedaban siempre fuera. Ahora se ordena por
> **fecha**, que es lo que de verdad interesa.

Acciones por proforma:

- **«Convertir en pedido»** — abre **«Convertir proforma en pedido»**. Crea el
  pedido en BoHub y **su albarán en FACTUSOL (sin factura)**; botón **«Crear
  pedido y albarán»**.
- **«Enviar por email»** — abre la previsualización del correo (contactos de la
  empresa con el vinculado ya marcado, direcciones libres, idioma, asunto y
  mensaje editables, remitente de la empresa emisora) y adjunta el PDF del
  presupuesto. Con **«Buscar contacto del CRM»** puedes añadir a **cualquier
  contacto** (por nombre, email o empresa) en «Para» o «CC»; si el cliente de
  FACTUSOL no está vinculado a una empresa del CRM, el modal lo avisa y el
  buscador es la forma de elegir a quién va. Tras enviarlo, la fila enseña
  **«Enviada dd/mm»** (los destinatarios al pasar el ratón) y el botón pasa a
  **«Reenviar»**. Los textos por idioma se editan en Ajustes ERP → «Plantillas
  del email de presupuesto».
- **«⋯» → «Duplicar»** — abre **«Duplicar proforma nº X»** ya rellena con todo lo
  de la original (cliente, empresa emisora, referencia, líneas, portes, forma
  de pago y destinatario de envío si era distinto), con la fecha de hoy.
  Puedes cambiar la empresa (**«Cambiar»**) y la emisora antes de pulsar
  **«Crear proforma»**; la original no se toca. También está en el «⋯» de
  Documentos FACTUSOL → Presupuestos y en la ficha de empresa. La pestaña
  «Duplicar» de «+ Nueva proforma» sigue ahí para buscar una plantilla con
  vista previa. «Duplicar» y «Enviar por email» solo aparecen si tu usuario
  tiene permiso para hacerlo.
- **«Ver líneas»** — despliega en la propia fila el desglose de la proforma
  (SKU, descripción, cantidad, precio, IVA y total, y los portes si los lleva),
  para decidir si duplicar o convertir sin abrirla. Es solo lectura y se cierra
  con **«Ocultar líneas»**. Una proforma hecha en el FACTUSOL de escritorio no
  guarda sus líneas en la base (es de una sola línea), así que en ese caso lo
  dice en vez de una tabla vacía.
- **«PDF»** — descarga el PDF de la proforma.

**«Nueva proforma».** Eliges la **empresa destino** (vinculada a FACTUSOL) y la
**empresa emisora (serie)** —1 Bomedia · 2 MQ Europe · 4 Lambert · 5 Streamtec,
las mismas que en el alta de pedido manual—, añades líneas (buscando por **SKU**
o **Descripción**, o escribiéndolas a mano), los **portes** en su campo aparte,
la **forma de pago** (opcional: la del cliente en FACTUSOL viene propuesta; se
imprime en el PDF y pasa al pedido al convertir) y una **referencia** opcional.
Una descripción larga se guarda **entera en su línea**, con los saltos de línea
que escribas (Intro dentro de la descripción), y así sale en el PDF. Se guarda
como un presupuesto **real** en FACTUSOL,
bajo la serie elegida: ahí es donde la verás en esta pantalla y en Documentos.
Para dropshipping, activa **«Enviar a otro nombre / dirección»**: la proforma
sigue a nombre fiscal del cliente, solo cambia el destinatario del documento.

> La **emisora no tiene nada que ver con el cliente**: dice desde qué empresa de
> la casa sale el documento. Por defecto, Bomedia.
>
> La proforma nueva toma el **siguiente número de su propia serie**, como en el
> escritorio de FACTUSOL: cada serie lleva su contador (la 1 va por el 526.080 y
> la 5 por el 39), así que dos proformas de series distintas **pueden tener el
> mismo número**. Por eso en BoHub una proforma se identifica siempre por
> **serie + número** (el «5-000039» que ves en la lista): abrir, editar,
> duplicar o convertir actúa sobre la de esa serie, nunca sobre su homónima.
>
> **Al editar una proforma la serie no se cambia** (mover un documento de serie
> sería renumerarlo en la contabilidad): el modal enseña cuál es y ya está. Si
> te equivocaste de emisora, crea la proforma de nuevo en la correcta.

### Documentos de FACTUSOL

**Menú: «ERP · Documentos». Título: «Documentos FACTUSOL».** *«Solo lectura»* —
es una ventana a FACTUSOL: aquí no se cambia nada salvo las acciones concretas
(registrar cobro, crear pedido) que confirmes.

**Pestañas:** **«Presupuestos»**, **«Pedidos cliente»**, **«Albaranes»** y
**«Facturas»** (por defecto se abre en Facturas).

- Filtro **«Solo sin vincular»** — muestra los documentos que aún no tienen un
  pedido de BoHub.
- Otros filtros: buscador, cliente/CIF/email, serie, fechas, **«Ciclo»** (p. ej.
  *Sin facturar* / *Facturado*) y **«Cobro»**.
- **«Registrar cobro»** (en Facturas) — apunta el cobro de esa factura en
  FACTUSOL.
- **«Crear pedido»** — crea un pedido de BoHub a partir del documento. Desde un
  presupuesto o pedido de cliente te lleva al alta ya rellenada; desde un
  albarán o factura lo crea directamente. Si el **cliente FACTUSOL del documento
  no está vinculado a ninguna empresa del CRM**, se abre **«Vincular empresa»**:
  busca y elige una empresa que ya exista, o **créala** con los datos de
  FACTUSOL ya puestos (nombre, CIF, dirección, email…). Al vincularla, el pedido
  se crea solo. El vínculo queda guardado en la empresa: los siguientes
  documentos de ese cliente ya no lo piden. No escribe en FACTUSOL.
- **«Vincular empresa»** (en la fila, cuando el cliente no tiene empresa) — lo
  mismo sin crear el pedido. **No confundir** con **«Vincular a pedido»**, que
  enlaza el documento con un pedido de BoHub que ya existe.
- **«PDF»** — descarga el documento (o varios en ZIP si seleccionas).
- **«Ver detalle»** — abre una ventana con las líneas, los cobros y opciones de
  PDF; desde ahí también puedes **crear el albarán/la factura** o **vincular** el
  documento a un pedido existente (**«Vincular a pedido»**, sin tocar FACTUSOL).

### Cola SAT (el taller)

**Menú: «ERP · Taller (SAT)». Título: «Cola SAT».** Es la pantalla del taller:
lo que hay que preparar y enviar.

**Pestañas, por paso del taller**, en este orden (cada una con su contador,
que cuadra con lo que lista, y **su propio color** para distinguirlas de un
vistazo; la activa va un punto más intensa y subrayada). Al entrar se abre
**siempre «Por embalar»** (no se recuerda la última pestaña entre visitas; un
enlace directo con la pestaña en la dirección, como `?tab=enviados`, sí se
respeta):

| Pestaña | Color | Qué hay |
|---|---|---|
| **Todos pendientes** | azul | Las cuatro de pendientes juntas. En **Tarjetas**, la misma rejilla que «Por embalar», mezcladas por fecha del pedido según **«Orden»**; cada tarjeta lleva el estado y los botones de su paso. En **Lista**, dos columnas (por hacer · embalados). |
| **Por embalar** (la que se abre por defecto) | naranja | En cola, sin empezar (y los **bloqueados**, arriba). |
| **En preparación** | amarillo | Con **«Empezar preparación»** pulsado, aún sin embalar. |
| **Embalados** | verde | Embalados, sin etiqueta tramitada: crea el envío con Genei o sube la etiqueta. |
| **Pendiente de recogida** | lila | Embalados con el **envío Genei ya tramitado** (o la etiqueta puesta): esperando al transportista. |
| **Enviados** | gris | Los que ya salieron: **recogidos, en tránsito o entregados**. Los «Sin envío» **no** salen aquí. |
| **Sin envío** | marrón claro | Pedidos que **no se envían** (ver abajo). |
| **Incidencias** | rojo | Ver más abajo. |

Fondos claros con texto oscuro del mismo tono: el texto se lee bien en todas
(contraste ≥ 7:1).

Cuando la agencia recoge el paquete, pulsa **«📤 Marcar recogido»**: el pedido
pasa de **«Pendiente de recogida»** a **«Enviados»**. **Las pestañas solo se
mueven solas con una incidencia** (Genei avisa de una incidencia → el pedido va a
**«Incidencias»**); el resto de avisos de Genei y del transportista solo se
enseñan. Un pedido ya en «Enviados» pasa a *Entregado* solo cuando Genei lo
confirma (sigue en la misma pestaña).

**Estado real del envío (según el transportista).** En los envíos hechos con
Genei, BoHub lee los **escaneos del propio transportista** (CTT, UPS, GLS…) y
enseña el último **tal cual lo da la agencia** —p. ej. *«PENDIENTE DE ENTRADA EN
RED»*, *«EN REPARTO»*, *«ENTREGADO»*— con su fecha, en la columna **Envío** de
«Enviados», en la tarjeta de «Pendiente de recogida» y en la ficha (con el
**historial del transportista** y el enlace a la web de la agencia). Se
actualiza solo: al avisar Genei y, además, cada 30 minutos para los envíos en
curso. **Es informativo: no mueve el pedido de pestaña** (eso lo hace «📤 Marcar
recogido», o una incidencia). Colores: ámbar = aún sin escanear, azul = en
camino, verde = entregado, rojo = incidencia. En
Seguimiento (pantalla, Excel y hoja «Seguimiento (app)») la columna **Envío**
lleva ese paso real (*Pendiente de entrada en red*, *Recogido*, *En tránsito*,
*En reparto*, *Disponible en oficina*, *Entregado*, *Incidencia*) y la columna
**Courier**, la agencia del envío tal como la da Genei (p. ej. *Ctt Premium*). Si
Genei aún no tiene escaneos del transportista, el paquete **todavía no ha
entrado en su red**: Envío dice *Pendiente de entrada en red* (en ámbar en
«Enviados» y «Pendiente de recogida», con lo que dice Genei al lado en gris, p.
ej. *Genei: Recogida efectuada / en tránsito*), **también después de «📤 Marcar
recogido»** — nunca *En tránsito* sin un escaneo. En cuanto la agencia escanea,
manda su estado. (Si Genei ya informa de un paso del transportista —*En
reparto*, *Disponible en oficina*…— se enseña ese.) Los envíos con **otro
courier** (no Genei) se explican justo debajo.

**Envíos con otro courier (no Genei).** Hay envíos que no se hacen con Genei
(UPS, MRW, GLS, DSV…): subes la etiqueta a mano, apuntas el tracking y pulsas
**«📤 Marcar recogido»**, como siempre. Ahora, además:

- **Campo «Courier»** junto al **«Nº de seguimiento»** (en «Embalados» /
  «Pendiente de recogida», tarjeta y lista): *UPS, CTT Express, MRW, GLS, DSV,
  FedEx, DHL, Correos Express, Seitrans, TNT, DB Schenker, MBE* u **«Otro…»**
  (lo escribes). Si el tracking lo delata, **se propone solo**: `1Z…` → **UPS**;
  `0033…` (22 dígitos) → **CTT Express**. Puedes cambiarlo. «Marcar recogido»
  se lleva el tracking y el courier que haya en el campo (aunque no hayas pulsado
  «Guardar»). Courier y tracking **pueden quedar vacíos** al recoger.
- **Estado claro**: en «Enviados» sale **«Enviado · UPS»** (o **«Enviado · otro
  courier»** si no se indicó), **nunca** «Recogido · en tránsito» (BoHub no sigue
  el tracking de otras agencias). La columna **Agencia** es el courier y el
  **Seguimiento** va **enlazado a la web del courier** cuando se conoce (UPS, CTT
  Express, MRW, Correos Express, FedEx, DHL, TNT; GLS, DSV, Seitrans, DB
  Schenker, MBE y «Otro» salen sin enlace).
- **Colores**: los envíos de **Genei** van en **azul** con la etiqueta pequeña
  **«Genei»**; los de **otro courier**, en **verde azulado**. Lo mismo en
  «Pendiente de recogida».
- **Se corrigen después** sin volver a «Marcar recogido»: en «Enviados»,
  **«✎ Courier / seguimiento»** en la fila; en la ficha, **«✎ Editar courier /
  seguimiento»**.
- **Ficha**: el bloque **«Envío con otro courier»** (courier, tracking enlazado,
  fecha de recogida y el aviso al cliente) sustituye a «Envío con Genei», que no
  lo hay.
- **Seguimiento (pantalla, Excel y hoja «Seguimiento (app)»)**: **Envío** =
  **«Enviado»** al marcar recogido (BoHub no ve los escaneos de esas agencias);
  **«Entregado»** o **«Incidencia»** si lo marcas a mano en la ficha («Otras
  acciones de estado»). El courier va **aparte**, en la columna **Courier**
  (*UPS*, *MRW*, *Seitrans*…; **«otro courier»** si no se indicó). **Tracking** =
  el número y **Fecha recogido** = el día en que se pulsó «Marcar recogido».

> La antigua pestaña «Enviados» era el **historial de pedidos mandados al
> taller** (email al SAT o aprobación). Ahora «Enviados» son los que **han
> salido**. Para ver cuándo y quién mandó un pedido al taller, míralo en su
> ficha (línea de tiempo).

Vista **«Tarjetas»** / **«Lista»**. En tablet y ordenador la **lista scrollea
dentro del panel** con la cabecera fija y la casilla y el **Nº** fijos a la
izquierda (la barra horizontal, si hace falta, se ve sin bajar al final).
**Filtros:** buscador (*«Nº de pedido o cliente…»*), **«Desde»**/**«Hasta»**,
**«Tienda»** y **«Orden»** por fecha del pedido —**recientes primero** por
defecto, o **antiguos primero** (FIFO)—, con **«Limpiar filtros»**. (El filtro
«Estado» ya no hace falta: cada estado tiene su pestaña.) En **«Enviados»** se
listan los más recientes; usa las fechas para ver otros.

**Preparar y embalar, en una ventana sobre la cola.** En la tarjeta (o la
fila de la lista) de un pedido en cola, **«▶ Empezar preparación»** empieza la
preparación y abre una **ventana (modal) encima de la cola**, sin cambiar de
pantalla: las **líneas** para cotejar, los **bultos** (peso y medidas;
**«+ Añadir bulto»** si hay varios) y **«📦 Embalar»**. Si el pedido ya estaba
empezado, su botón es **«📦 Embalar»** y abre la misma ventana. En ella también
puedes **«📷 Subir foto / documento»** o **«⚠ Reportar problema»**. Al embalar
(o con **«Cerrar»**) vuelves a la cola **donde estabas**, y la cola se
**actualiza sola, sin recargar la página (sin F5)**: el pedido sale de su
pestaña, aparece en **«Embalados»** y los contadores cuadran al momento. Ya no
hay «modo trabajo» a pantalla completa: un enlace viejo a él te devuelve a la
cola.

**«Sin envío» (no requiere envío).** Para pedidos que **no se envían**: recogida
en tienda, licencia, servicio… Marca las casillas en las pestañas de pendientes
y pulsa **«No requiere envío»**. El pedido sale de los pendientes y pasa a su
pestaña **«Sin envío»**; **no cuenta como enviado** y **no aparece en
«Enviados»**. En la hoja «Seguimiento (app)» su Envío sale **«No aplica»** (no
enviado) con el tracking vacío. **No toca la factura ni el cobro** y es
**reversible**: en **«Sin envío»**, selecciónalos y **«Requiere envío (volver
al taller)»** (vuelven a su pestaña). También por pedido, desde el menú **«⋯»**
de la ficha (**«No requiere envío»** / **«Requiere envío (volver a SAT)»**).

> Durante unos días esta marca se llamó «Sin seguimiento» y contaba como
> enviado. Es **la misma marca**, así que los pedidos marcados entonces pasan
> solos a **«Sin envío»** (no hay que hacer nada ni se ha migrado nada). Si
> alguno de ellos **sí se envió**, quítale la marca («Requiere envío») y
> márcalo como recogido / enviado de la forma normal.

**Envío con Genei (en la tarjeta y en la ficha).** Si el pedido **no** tiene
envío, **«Crear envío con Genei»**: la dirección, el teléfono y el email del
destino se rellenan solos **venga de donde venga el pedido** (web, manual,
muestra, o creado desde factura, albarán o proforma de FACTUSOL). Si ya lo
tiene, el botón es **«Ver envío Genei»** (no se vuelve a ofrecer crear). La
etiqueta solo aparece cuando el envío está **pagado y tramitado**. Hasta
entonces sale el aviso *«La etiqueta estará disponible tras pagar y tramitar
el envío»*. **«🖨 Imprimir etiqueta»** la descarga y abre el diálogo de
imprimir **en un solo clic**.

**Aviso de envío al cliente (lo manda BoHub).** En cuanto un envío de Genei
tiene **nº de seguimiento** (normalmente al **«Pagar y tramitar»**), BoHub manda
al cliente **un email con el nº de seguimiento, el enlace para seguirlo y el nº
de pedido**, **en su idioma** y **una sola vez** (no se repite al actualizar el
estado). Va al email del destinatario que se puso al crear el envío.

- **Idioma:** el del pedido; si no tiene, el de la ficha del cliente; si no, el
  del **país de destino** del envío; si no, **español**.
- **Remitente:** pedidos **web**, el de su tienda (boprint y flux →
  *pedidos@streamtec.es*; artisJet → *info@artisjet-printers.eu*). Pedidos
  **manuales** (factura, proforma, albarán, manual o muestra): en **español** →
  *pedidos@streamtec.es*; en **cualquier otro idioma** →
  *info@artisjet-printers.eu*.
- En la sección de Genei de la ficha se ve si se envió (cuándo, a quién, en qué
  idioma y desde dónde; y, si el estado es otro —un reenvío fallido, pendiente…—,
  la fecha y el destinatario del **último aviso que sí salió**) y está
  **«Enviar / Reenviar aviso al cliente»** (con vista previa, idioma y
  destinatario editables). Queda en la línea de tiempo del pedido.
- En la **Cola SAT**, pestaña **«Enviados»**, la columna **«Aviso»** lo dice sin
  abrir la ficha: **«✉ Enviado 01/10, 12:03»** (verde; el destinatario en el
  tooltip) o **«✉ Sin enviar»** (gris). Con nº de seguimiento hay un botón
  **«Enviar aviso»** (o **«Reenviar»** si ya salió), con una confirmación de una
  línea: es el mismo envío que el de la ficha (misma plantilla e idioma) y queda
  registrado con fecha y usuario. Sin tracking no hay botón. Lo mismo en las
  tarjetas de **«Pendiente de recogida»** que ya tienen tracking.
- Solo se manda solo en los envíos **creados a partir de este cambio** (los
  anteriores ya los avisó Genei); en esos, se puede mandar a mano.
- **En Genei**: con esto en marcha, en **Perfil → Notificaciones →
  «Destinatario»** desmarca **«Al crear un envío»** y guarda. Deja «Si se
  producen incidencias» y «Al entregar»: esos los sigue mandando Genei.
- **Envíos con otro courier** (UPS, MRW…): el mismo aviso (mismo texto, idioma
  y remitente), **una sola vez por envío**. Sale al pulsar **«📤 Marcar
  recogido»** si ya hay tracking; si no, **en cuanto se pone el tracking**
  (desde «Enviados» o la ficha). El enlace es el de la web del courier; si no
  se conoce, el texto dice *«en la web de GLS»* (o solo el número si no hay
  courier). Va al **email de envío del pedido**; si no hay, al **email del
  cliente** de la ficha; si tampoco, **no se manda** y la ficha lo indica
  (puedes mandarlo a mano con otro destinatario). Corregir el tracking después
  **no lo repite**; el reenvío manual está en el bloque «Envío con otro
  courier» de la ficha. No se manda solo en los envíos recogidos antes de este
  cambio.

**«Incidencias».** Cuando un pedido **ya enviado** tiene un problema, **sale de
«Enviados»** y aparece en esta pestaña hasta que se resuelve. Reúne dos orígenes:

- **De envío** (transporte): el **seguimiento de Genei** (o **«Actualizar
  estado»**) detecta una incidencia del transportista. El motivo es la
  descripción de la incidencia. **«Resolver»** devuelve el envío a **«en
  tránsito»** y el pedido vuelve a **«Enviados»** (lo puede hacer el propio SAT).
- **De pedido**: una **excepción abierta** del taller/stock/VIES… (la misma que
  ves en la bandeja de excepciones). **«Resolver»** cierra la excepción.

Cada fila lleva su **tipo** (Envío / Pedido), el **motivo**, el nº de
**seguimiento** y el **estado de envío**. Resolver una incidencia de envío es de
**oficina o SAT**; cerrar la de pedido es de **oficina**.

En cada tarjeta:

- **Datos técnicos**: **«Nº de serie»**, **«Licencia WhiteRIP»**, **Origen** y
  **«Observaciones del comercial»** (editables por administración/pedidos).
- **Albarán**: **«Imprimir albarán»** / **«Descargar albarán»** en PDF (o
  **«Falta albarán»** si aún no existe en un pedido manual).
- **Etiqueta**: **«Subir etiqueta»** (admite imagen o PDF) y, una vez subida,
  **«🖨 Imprimir etiqueta»** (la descarga e imprime en un clic).
- **Nº de seguimiento**: campo para guardar el tracking del transportista.

**Flujo del taller** (todo en la ventana sobre la cola):

1. **«▶ Empezar preparación»** — el pedido pasa a *En preparación* y se abre
   la ventana con las líneas y los bultos.
2. **«📦 Embalar»** — indica **peso y medidas de cada bulto** (**«+ Añadir
   bulto»**) y pulsa **«📦 Embalar»**. La ventana se cierra, el pedido pasa a
   **Embalados** y la cola se actualiza sin recargar.
3. **«📤 Marcar recogido»** — confirma *«¿El paquete ha salido?»* → **«Sí,
   recogido»**. El transporte pasa a *En tránsito*. Si el envío **no es de
   Genei**, antes puedes indicar el **«Courier»** junto al nº de seguimiento
   (ver «Envíos con otro courier»).

**«Añadir pedido a la cola»** — para meter a mano un pedido en el taller por su
número (p. ej. *BOP-1234*).

### Seguimiento

**Menú: «ERP · Seguimiento». Título: «Seguimiento de pedidos».** Es la tabla que
sustituye el Excel manual de seguimiento: la app la genera y la **ordena sola**,
sin mover filas a mano. Una **fila por pedido**, con el estado en una **columna**
(no en la posición). Por defecto muestra los pedidos en curso.

**En curso = hasta que se marca completado.** Un pedido sigue en curso (en la
pantalla, en el Excel y en la zona viva de la hoja de Drive) hasta que alguien
pulsa **«Marcar completado»**, o lo **quita** del seguimiento, se **anula** o
se marca **gestionado fuera**. **Entregado no es completado**: un pedido
entregado —por ejemplo, entregado pero **por cobrar**— sigue en curso, con
Envío «Entregado», hasta que se da por cerrado. Al marcarlo completado baja a
los completados de la hoja.

**Qué entra y qué no.** Seguimiento es la lista de pedidos **vivos**, y la
puerta es distinta según de dónde venga el pedido:

- **Web** — entra el que **ha pasado por caja**: `processing` (pagado / en
  preparación), `completed` (servido) y `refunded` (pagado y devuelto después).
  **Quedan fuera** `pending` (sin pagar), `on-hold` (en espera), `cancelled`,
  `failed` y los borradores: son carritos que todavía no han entrado en el
  flujo real, no trabajo de nadie. En cuanto la tienda los pasa a
  `processing`, aparecen solos en el siguiente refresco (y si vuelven atrás o
  se cancelan, desaparecen).
- **Manual** — entra el **aprobado**, *aunque no esté pagado*: un pedido
  aprobado sin cobrar es justo lo que hay que ver. El que nadie ha aprobado
  todavía queda fuera hasta que se apruebe.
- **Fuera en los dos casos**: los **anulados**. Con **una excepción**: un
  pedido web **reembolsado se sigue viendo** aunque esté anulado en BoHub
  (ver abajo).

Nada de eso se borra: está a un clic en **«Ver ocultos por estado»**, con el
motivo por el que salió (*anulado*, *sin aprobar*, *Sin pagar*, *En espera*,
*Cancelado en la tienda*…). Si alguno hay que verlo igualmente, **«Reincluir»**
lo **fuerza** a la lista (sale marcado *forzado*) y se deshace desde esa misma
vista con **«Dejar de forzar»**. Ojo: eso es distinto de la casilla **«Ver
excluidos»**, que es la lista de los que se quitaron **a mano** con «Quitar del
seguimiento» — son dos ejes distintos, y «Reincluir» en uno no rescata del otro.
Todo esto vale igual para los tres sitios: la pantalla, **«Descargar Excel»** y
la pestaña **«Seguimiento (app)»** de Drive, que salen de la misma consulta.

**«Reembolsado» es un estado propio, no «anulado».** Un pedido web que se
reembolsa entero en la tienda se pagó y se sirvió de verdad: en BoHub se marca
**Reembolsado**, con su pastilla gris y su Situación «Reembolsado» al final de
la lista, y **se sigue viendo en Seguimiento** — normalmente queda el abono por
hacer. Sale de la bandeja y de las colas, y sus acciones siguen cerradas (es el
mismo sello que la anulación), pero se dice por lo que es, tanto en la ficha
como en la lista de pedidos. Cuando ya no haga falta verlo, se retira con
**«Marcar completado»**.

**Columnas (en orden):** **Situación**, **Nº pedido**, **Fecha**, **Cliente**,
**Origen** (WEB o el canal), **Productos**, **Importe**, **Empresa (serie)**
(p. ej. «2 · MQ Europe»), **Factura**, **Fecha factura**, **Factura enviada**
(cuándo se mandó la factura por email al cliente), **Cobro** (*Cobrado ✓* /
*Pendiente* / *—*), **Preparación** (estado del taller), **Envío**, **Courier**,
**Fecha recogido** (el día real en que el paquete salió del taller, según la
Cola SAT), **Tracking**, **Nº serie · WhiteRIP** y **Nota / Incidencia**. Son 19
columnas (20 en el Excel y en la hoja, con la «id» técnica oculta al final).

**Envío** es **solo el estado** del envío, siempre de esta lista cerrada:
*Sin enviar* · *Pendiente de entrada en red* · *Recogido* · *En tránsito* · *En
reparto* · *Disponible en oficina* · *Entregado* · *Incidencia* · *Enviado* ·
*No aplica*. Con Genei es el estado real del transportista; con otro courier,
*Enviado* al marcar recogido (y *Entregado* / *Incidencia* si se marca a mano en
la ficha); *No aplica* si el pedido no requiere envío. Una devolución cuenta
como *Incidencia*.

**Courier** dice **con quién** va: la **agencia del envío de Genei** (*Ctt
Premium*, *UPS*…), el **courier apuntado en la Cola SAT** para un envío con otro
courier (*UPS*, *MRW*, *Seitrans*…; **«otro courier»** si salió sin apuntarlo) o
**«—»** si no hay envío. El filtro **«Transportista»** de la pantalla busca en
esta columna (contiene, sin mayúsculas ni tildes: *ctt* encuentra *Ctt Premium*
y *CTT Express*), y en «Columnas» se puede ocultar como cualquier otra (se ve
por defecto). No hace falta rellenar nada: sale del envío de cada pedido, también
de los que ya existían.

Las columnas de **fecha** (Fecha, Fecha factura, Factura enviada, Fecha
recogido) van como **valor de fecha real**, no como texto, tanto en el Excel
como en Drive: se ven siempre como DD/MM/AAAA y, sobre todo, **ordenan por
fecha de verdad** (como texto, «1/9/2026» iría antes que «12/3/2026»). Una
fecha rota de origen en el histórico (p. ej. `27/07/202`, con un dígito de
menos) se deja como texto, sin inventarla.

**Situación** es la cola de la línea de vida del pedido —la misma de la bandeja—
y va **coloreada**: `Incidencia` (rojo), `Por revisar` (ámbar), `Por facturar` /
`Por cobrar` (azul), `Por enviar` (teal), `Listo` (verde) y `Reembolsado`
(gris). La tabla se **ordena por Fecha** al abrir (la más reciente primero), para
que un pedido recién llegado —una muestra, por ejemplo— no quede enterrado
abajo por su Situación. Pulsa la cabecera de **Situación** para volver a ese
orden (lo urgente arriba: primero las incidencias, al final lo listo y los
reembolsos, y dentro de cada grupo por fecha) o la de cualquier otra columna
ordenable; filtra como siempre (buscar, empresa/serie, transportista, origen,
estado, fechas).

Botones útiles: **«Descargar Excel»**, **«Actualizar hoja de Drive…»** (vuelca
los datos a la hoja de Google Drive, con vista previa antes de escribir),
**«Poner al día estados Woo…»** y **«Vincular facturas de FACTUSOL…»**. Por fila,
**«PDF»** (de la factura) y **«Quitar»**/**«Reincluir»**.

**La tabla se desplaza sola, no la página.** La tabla ocupa todo el ancho y el
alto que queda de pantalla y tiene **su propio scroll**: al moverte por las
columnas, el título, los filtros y los botones no se mueven. La **barra
horizontal se ve siempre** (justo debajo de la tabla, sin tener que bajar al
final de la lista; también en Windows con las barras ocultas), y con el teclado
se desplaza con las flechas tras pulsar en la tabla. En pantallas **bajas** (p.
ej. un portátil de 1366×768) la tabla no cabe entera bajo los filtros: baja la
página **una vez** y la tabla queda entera a la vista (con su barra) bajo el
título; desde ahí solo se mueve la tabla. Al desplazar:

- la **cabecera** queda fija arriba;
- **la casilla, Situación, Nº pedido y Cliente** quedan fijas a la izquierda y
  **«Quitar»** a la derecha, siempre a mano (en una pantalla estrecha —móvil o
  tablet en vertical— no caben y se sueltan, para poder llegar a todas las
  columnas);
- un **sombreado** en el borde avisa de que hay más columnas por ese lado.

Va en **modo compacto**: letra un punto menor, menos relleno, cabeceras en dos
líneas y **fechas cortas** (`30/09/26`; la completa al pasar el ratón). Los
textos largos (**Cliente**, **Productos**, **Tracking**, **Nº serie**, **Nota**)
van en **una línea con «…»**: el texto entero sale al pasar el ratón, y
**Productos** y **Nota** se despliegan con un clic (otro clic los recoge).

**«Columnas»** (junto a «Descargar Excel») elige qué columnas se ven: marca o
desmarca cada una (el orden es siempre el de la tabla; el **Nº pedido** no se
puede quitar) y **«Mostrar todas»** lo deja como al principio. Por defecto se
ven todas. La elección **se recuerda en este navegador para tu usuario** (otra
persona en el mismo ordenador ve las suyas). **No cambia el Excel ni la hoja de
Drive**, que siguen saliendo con todas sus columnas.

> Lo mismo en la **bandeja de pedidos** (vista «Lista», en pantallas de más de
> 1100 px) y en la **Cola SAT** (vista «Lista», en tablet y ordenador): la
> tabla scrollea dentro de su recuadro con la cabecera fija; en la bandeja la
> casilla y el **Nº** quedan fijos a la izquierda y **Acciones** a la derecha (el
> menú **«⋯»** se cierra si desplazas), y en la Cola SAT la casilla y el **Nº**.

**«Poner al día estados Woo…»** vuelve a preguntar a las tiendas por los pedidos
que BoHub tiene como activos y aplica la misma regla: saca los que se
**cancelaron**, **fallaron**, se fueron a la **papelera** o volvieron a **sin
pagar / en espera**, y marca **«Reembolsado»** los reembolsados (esos no salen).
Los pedidos web que estaban **sin estado** (importados antes de que existiera
el dato) se consultan **uno a uno**: recuperan su estado real, y el que la
tienda **ya no tiene** queda marcado *No encontrado en la tienda* y oculto.
Además **rellena el método de pago** («Carte», «PayPal»…) de **todos** los
pedidos web que no lo tienen, cambien o no de estado y aunque la pantalla esté
en «Solo en curso» (es lo que usa la cuenta sugerida al registrar un cobro). Lo
que ya tiene método no se toca. La previsualización dice cuántos se
rellenarían por tienda y el botón **«Aplicar (N cambios)»** los cuenta.
Siempre enseña antes una previsualización con los números; nada se cambia hasta
que confirmas. *(Al desplegar esta versión, BoHub rellena solo, una vez, el
método de pago de los pedidos antiguos.)*

> Un pedido web **sin estado** (los importados antes de que existiera el dato)
> **se ve** en Seguimiento: no se conoce su estado, y ocultarlo se llevaba
> pedidos legítimos. Solo se oculta por un estado **explícito** de la tienda
> (sin pagar, en espera, cancelado, fallido, borrador) o cuando la tienda ya
> no lo tiene (*No encontrado en la tienda*). «Poner al día estados Woo» les
> pone su estado real, de 150 en 150 por pasada.

> **«Incidencia» es solo lo que marcáis vosotros.** Un pedido llega a esa
> situación cuando alguien **reporta un problema a mano** desde la Cola SAT
> («Reportar problema»): esa es la lista de incidencias del equipo, y es lo que
> alimenta la pestaña «Incidencias (app)». Al resolver la incidencia el pedido
> sale de ahí.
>
> Lo que detecta la app sola —una empresa sin vincular a FACTUSOL, un NIF-IVA
> que VIES da por no válido— cae en **«Por revisar»**: hay que arreglarlo antes
> de facturar y sigue destacado, pero no ensucia la lista de incidencias.

El **Excel** que se descarga trae dos pestañas: **«Pedidos»** (las 20 columnas,
con «Courier» y la «id» técnica oculta al final; las filas en el mismo orden
que tengas puesto en la pantalla —Fecha al entrar—, con la
celda Situación coloreada, la cabecera fija, el autofiltro y el importe con
formato €) y **«Incidencias»** (los mismos pedidos
que están en Situación=Incidencia, con más detalle: nº pedido, cliente, tipo,
motivo, asignado, fecha y estado, tomado de la bandeja de Excepciones).

**«Actualizar hoja de Drive»** vuelca ese mismo formato nuevo al Google Sheet,
en **pestañas propias de la app**. Cada una tiene **dos zonas**:

> **El histórico se reparte en columnas.** Las filas antiguas llevaban todo
> apelotonado en «Nota / Incidencia» (`Vendedor: WEB · Transporte: UPS ·
> Preparado: 28/08/2026 · Recogido: 28/08/2026 · Proforma: 1543`). Ahora cada
> dato va a su columna —Vendedor a **Origen**, Transporte a **Envío**,
> Preparado a **Preparación**, Recogido a **Fecha recogido**, como fechas de
> verdad— y en la Nota solo queda lo que no tiene columna: el texto libre de
> «Orden» y la **Proforma**. Se arregla solo en el siguiente «Actualizar hoja
> de Drive»; lo que no sea una fecha (`X`, `pendiente`, `CANCELADO`…) se queda
> como texto en su columna, sin inventar nada.

- **«Seguimiento (app)»** — arriba, los pedidos vivos: **los mismos que ves en
  la pantalla** (en curso; fuera los quitados a mano y los ocultos por estado),
  con las 20 columnas (con «Courier»; la «id» técnica va oculta al final),
  **ordenados por fecha del pedido, del más reciente al
  más antiguo**, la celda Situación coloreada, la **fila 1 de encabezados
  congelada** (no se va al hacer scroll) y el **autofiltro** sobre esa misma
  cabecera, con el que puedes reordenar por Situación o por cualquier otra
  columna. Debajo de una fila separadora *«──── HISTÓRICO — no se actualiza
  ────»*, el **histórico** en formato nuevo, que conserva su propio orden.
  **Una fila de BoHub nunca desaparece de la hoja** sin motivo: si un pedido
  deja de estar en curso, baja a los completados; solo sale si se quita del
  seguimiento, se anula o se marca gestionado fuera. Si por un fallo de la
  selección una fila fuera a desaparecer, BoHub la conserva y lo deja en el
  log («se conservan sus filas») y en el resumen de la actualización.
  **Dos filas del mismo pedido se fusionan, no se tira ninguna:** si el pedido
  ya sale arriba y su fila del histórico sigue abajo (o alguien copió la fila),
  BoHub deja una sola, pero antes **conserva lo que traía la otra** y a la que
  se queda le falta (Tracking, Fecha recogido, Nº serie · WhiteRIP, Nota,
  Factura, Factura enviada, Cliente): se guarda en BoHub y se ve en la hoja.
  Si las dos tienen un valor distinto, **gana la de BoHub** y el otro queda en
  la **auditoría** del pedido («fila fusionada»: qué se conservó y qué se
  descartó). La vista previa lo dice antes de escribir.
- **«Incidencias (app)»** — arriba, las incidencias que habéis reportado a
  mano; debajo del separador, los **pendientes heredados** de la hoja vieja.

**Columna técnica «id» (clave estable).** La hoja lleva ahora una **última
columna «id», oculta**: es la clave con la que BoHub localiza cada fila para
actualizarla, en vez de casar por Nº de pedido (frágil: `9562.0`, con/sin
prefijo, cruces falsos). Para los pedidos de BoHub es su id interno; para el
histórico, un id propio. **No la toques ni la borres** (está oculta justo para
eso); tampoco aparece a la vista en «Descargar Excel». Los ids del histórico
antiguo se asignan **una sola vez**, con revisión, mediante
`python -m scripts.backfill_seguimiento_ids` (primero sin `--apply`, que **lista
las coincidencias dudosas** para que las revises; las claras se aplican solas).
Las dudosas se quedan **pendientes** (salen en cada informe) hasta que las
confirmas con `--confirm`: `ID` (su único candidato), `ID=ORDER_ID` (ese pedido)
o `ID=none` (no hay pedido detrás). `--confirm` también corrige una fila ya
resuelta. El histórico manual se conserva intacto.

**Fechas de recogida perdidas el 24/09.** Al deduplicar la hoja aquel día se
tiraron sin fusionar las filas del histórico de los pedidos que ya salían
arriba; sus *Fecha recogido* siguen en BoHub (`seguimiento_legacy`). Para
recuperarlas: `python -m scripts.recuperar_fechas_recogido --probar` lista los
pedidos (con la fecha de la hoja y la que tiene hoy el pedido) sin escribir
nada; `--apply` guarda la fecha **solo en los que no tienen ninguna** (nunca
pisa una existente), con rastro en la auditoría. Después, «Actualizar hoja de
Drive…».

**Formato de Nº pedido, Factura y Tracking.** BoHub reescribe la pestaña en su
sitio: cuando la zona viva crece o mengua, las filas de debajo bajan o suben,
pero los **formatos de celda** se quedan donde estaban. Para que un número de
pedido no se vea nunca como fecha (p. ej. *5559* como «1915-3»), esas tres
columnas se fuerzan a **texto** en toda la pestaña en cada actualización. Solo
cambia cómo se ven: el valor es el mismo. Otros formatos que pongas a mano en
celdas sueltas pueden quedar desplazados (es una limitación conocida).

#### La hoja como espejo de BoHub (en los dos sentidos)

BoHub es la fuente de verdad y «Seguimiento (app)» es su **espejo**: lo que
cambia en BoHub llega a la hoja, y lo que escribís a mano en las columnas que
se pueden editar **vuelve a BoHub**. Cada fila se casa por su «id», nunca por
el Nº de pedido.

**Qué se puede editar en una fila de BoHub:**

| Columnas | Qué pasa si las editas |
|---|---|
| **Cliente, Factura, Factura enviada, Nº serie · WhiteRIP** | Tu valor **manda**: BoHub ya no lo pisa y lo recuerda (se ve también en la pantalla de Seguimiento y en el Excel). Si **vacías** la celda, BoHub vuelve a rellenarla. Nunca se copia a FACTUSOL ni a la factura real del pedido. |
| **Tracking** | Si el pedido **no** tiene envío Genei, tu tracking se guarda **en el pedido** (lo ven la Cola SAT y la ficha). Si **tiene envío Genei, manda Genei**: la celda va protegida y no se puede cambiar a mano. |
| **Nota / Incidencia** | Si no hay nota escrita, BoHub pone el motivo del bloqueo; en cuanto escribes una nota, **manda la tuya** y BoHub no la toca. Si la vacías, vuelve el motivo. |
| **Todo lo demás** (Situación, Nº, Fecha, Origen, Productos, Importe, Empresa, Fecha factura, Cobro, Preparación, Envío, **Courier**, Fecha recogido e «id») | **Solo BoHub**. Esas columnas van **protegidas**: no se pueden editar. |

En las filas **tecleadas a mano** (Origen = MANUAL) y en el **histórico manual**
se puede editar todo, también **Courier**. El desplegable de **Envío** (en la
zona viva) es la lista cerrada de estados de arriba; Courier es texto libre.

**La columna Courier en una hoja ya existente.** La hoja pasó de 19 a 20
columnas. La primera vez que BoHub actualiza una pestaña escrita antes de
«Courier», **inserta la columna** entre «Envío» y «Fecha recogido» —en la
cabecera y en **todas** las filas: zona viva, completados e histórico manual—
como haría «Insertar columna» en Sheets: lo de detrás corre una posición con
sus formatos y la «id» sigue la última y oculta. Antes y después **cuenta las
celdas con dato de cada columna** y, si no cuadra (p. ej. alguien escribía en
ese momento), para sin escribir nada más. Se hace **una sola vez**: con la
columna ya puesta no se vuelve a insertar. La vista previa de «Actualizar hoja
de Drive…» avisa antes de hacerlo, y el resumen de después dice cuántas celdas
había antes y después. Las filas del **histórico manual** que llevaban el
transportista en **Envío** (*UPS*, *MRW*, *FEDEX*, *DSV*…) **se quedan como
están**: no se reinterpretan; su Courier queda vacío (y es editable).

Tres casos en los que **no toca nada** y lo dice (en la vista previa o al
actualizar), para que lo arregles a mano antes:

- **La columna Z tiene algo.** Al insertar la columna, lo de Z pasaría a AA,
  fuera de lo que lee la app, y se quedaría suelto. Muévelo a otra pestaña (o
  bórralo) y vuelve a actualizar. (En una pestaña aún más antigua, de 17
  columnas, pasa lo mismo con Y y Z.)
- **Las columnas están descolocadas**: una columna insertada, borrada o movida
  a mano («Courier» puesta dos veces, cortada y pegada detrás de la «id», la
  «id» borrada…), celdas insertadas o borradas en una fila, o la fila de
  cabecera borrada. El aviso dice **qué** no está donde toca. Lo que lo arregla
  es **deshacer ese cambio** (Ctrl+Z, o «Historial de versiones» de la hoja);
  cambiar el nombre de la cabecera **no**: los datos seguirían en otra columna.
- **La base de datos aún no está al día** (la actualización del programa no ha
  terminado): vuelve a intentarlo en un momento.

Cómo se comprueba (en cada actualización, no solo en la migración):

- **La cabecera** se compara **entera**, columna a columna, con la de cada
  versión. **Renombrar o vaciar** celdas de la cabecera no descoloca nada y se
  acepta (la pasada vuelve a escribir los nombres buenos). Lo que no se acepta
  es el nombre de **otra** columna fuera de su sitio: eso es una columna
  insertada, borrada o movida. Lo escrito a la derecha de la última columna
  (p. ej. «Courier» tecleado en T1 de la pestaña vieja) no cuenta, salvo que
  sea el nombre de una columna de la app con datos debajo (una columna movida).
- **Sin fila de cabecera** (borrada, o sin «Situación» ni «Nº pedido» en las 5
  primeras filas) no se puede ver si algo se ha movido: si hay filas con «id»,
  se pide volver a ponerla.
- **Las «id»**: todas tienen que estar en su columna (la T). Una fila con su
  «id» en otra tiene celdas corridas.
- **Las filas de BoHub** se comparan con lo último que BoHub escribió en ellas:
  si en sus columnas bloqueadas aparecen los datos de la columna de al lado,
  hay columnas corridas aunque la cabecera parezca buena.

> **Volver a la versión anterior** (solo si hiciera falta), en este orden:
>
> 1. **Para la sincronización**: apaga «Sincronizar la hoja automáticamente»
>    en Configuración ERP, para el `worker-sync` y que nadie pulse «Actualizar
>    hoja de Drive». Si no, la versión nueva, que sigue en marcha, volvería a
>    insertar la columna «Courier» en cuanto la quitaras.
> 2. **Baja la base de datos** con la imagen nueva, en un contenedor suelto:
>    `docker compose … run --rm api alembic downgrade 20260930_0122`. Devuelve
>    lo guardado a 19 columnas y quita la tabla de estado del espejo; desde ese
>    momento, cualquier pasada de la versión nueva se para sin tocar la hoja.
>    No reinicies el `api` nuevo: al arrancar vuelve a subir la base de datos.
> 3. **Quita la columna O («Courier»)** de «Seguimiento (app)» con
>    `docker compose … run --rm api python -m scripts.quitar_columna_courier`
>    (informe) y luego con `--apply`. Lo hace la cuenta de servicio, así que
>    funciona aunque la columna esté protegida o la hoja sea de BoHub; antes
>    comprueba que la base de datos ya está bajada y que la pestaña está bien
>    colocada (si no, dice qué falla: arréglalo antes de seguir). Se pierden
>    las celdas de «Courier». A mano solo podría el propietario de la hoja. **No
>    restaures una versión de la hoja anterior a la migración**: la versión
>    anterior tomaría lo que ha cambiado desde entonces por ediciones a mano
>    (y daría por borradas las filas a mano nuevas).
> 4. **Arranca la versión anterior.**
>
> Si se hace en otro orden, la versión anterior leería corrido todo lo de
> detrás de «Envío».

> Google siempre deja editar las celdas protegidas al **propietario** de la
> hoja. Si lo haces, BoHub **deshace** el cambio en la siguiente pasada: los
> datos buenos de esas columnas están en BoHub. Para que la protección bloquee
> **también al propietario actual**, la hoja tiene que pasar a ser de otra
> cuenta: mira «Hoja propiedad de BoHub» justo debajo.

#### Hoja propiedad de BoHub (la protección bloquea a todos)

Con la hoja a nombre de una persona, esa persona se salta las columnas
protegidas. El script `python -m scripts.migrar_hoja_seguimiento` pasa
«Seguimiento (app)» e «Incidencias (app)» a una **hoja nueva** cuya propietaria
**no es nadie del equipo**. Lo hace así:

- copia las dos pestañas celda a celda (ids, histórico, filas a mano y formato)
  y comprueba que la copia es idéntica;
- pone las protecciones (solo BoHub edita las columnas bloqueadas);
- comparte la hoja nueva con las mismas personas que la vieja, **como
  editores** (nadie queda de propietario);
- re-apunta BoHub a la nueva;
- deja la vieja de **solo lectura**, renombrada «ARCHIVO · …».

Los valores manuales (overrides), el histórico importado y las filas a mano
viven en BoHub, casados por id: la migración no los toca. Si alguien había
editado la hoja vieja desde la última pasada, esa edición también pasa.

1. **Informe** (no escribe nada): `python -m scripts.migrar_hoja_seguimiento`.
2. **Prueba de propiedad**: `… --probar`. Crea una hoja vacía como la cuenta de
   servicio y la borra. Dice si Google deja a la cuenta de servicio ser
   **propietaria** de archivos. Las cuentas de servicio creadas desde el
   **15/04/2025 no pueden** (no tienen cuota de Drive), y la propiedad tampoco
   se puede **transferir** a una cuenta de servicio.
3. **Si puede**: `… --crear` (plan) y luego `… --crear --apply`. La hoja nueva es
   de la cuenta de servicio desde el origen y **nadie** se salta la protección.
4. **Si no puede**: crea una **cuenta de Google dedicada** que no use nadie a
   diario (p. ej. `hoja.bohub@…`). Desde ella:
   1. crea una hoja **vacía**;
   2. compártela con la cuenta de servicio como **Editor**;
   3. en Compartir → ⚙, desmarca «Los editores pueden cambiar los permisos».

   Luego `… --destino ID_DE_ESA_HOJA` (plan) y `… --destino ID --apply`. Todo el
   equipo, Bart incluido, queda bloqueado. Solo la cuenta dedicada se salta la
   protección: guárdala como **cuenta de emergencia**.

Avisa al equipo de que no toque la hoja durante el minuto que dura. Si alguien
escribe en la vieja mientras tanto, la migración se **anula sin cambiar nada** y
se repite. Hace falta tener activada la **Google Drive API** en el proyecto de
la cuenta de servicio (además de la de Sheets); si no, el informe lo dice.

Después:
- `… --verificar` comprueba la hoja actual: quién es el propietario, que las
  columnas bloqueadas solo las edite BoHub y a quién bloquea.
- Con la hoja a nombre de la cuenta de servicio, los editores no pueden
  compartirla. Para dar acceso a alguien nuevo: `… --compartir email` (con
  `--rol reader` para solo lectura).
- En Drive, la hoja nueva aparece en **«Compartido conmigo»**; añádela a «Mi
  unidad» con un acceso directo si la queréis a mano.

**Filas a mano (Origen = MANUAL): se pueden editar enteras.** BoHub las **valida**:
tienen que llevar **Nº de pedido o Cliente**, y si pones fechas o importe, que
lo sean. Las válidas reciben su «id» y **se guardan en BoHub**. Una fila que no
valida se pone **en naranja** con una marca **«[⚠ revisar: …]»** en la Nota que
dice qué falla, y **no entra en BoHub** hasta que la corriges (no se pierde nada:
sigue en la hoja tal cual). Al corregirla, la marca desaparece sola. Para añadir
una fila nueva, **insértala arriba del todo** (bajo la cabecera) **o al final de
la zona viva**: entre dos filas de BoHub caería dentro de una zona protegida.

**Si borras una fila de la hoja:**

- **De BoHub** → vuelve a aparecer en la siguiente pasada (el borrado de verdad
  se hace en BoHub, con «Quitar del seguimiento», que es reversible).
- **A mano o del histórico** → se respeta: desaparece de la hoja y en BoHub queda
  como **borrada** (se conserva y se puede recuperar).
- Si desaparecen **muchas filas de golpe** (alguien vacía la pestaña, o una
  lectura sale mal), BoHub lo trata como un **accidente** y las **vuelve a
  poner** todas.

Si alguien está escribiendo en la hoja justo mientras se sincroniza, esa pasada
**no escribe nada** y lo recoge la siguiente: nunca se pisa lo que acabáis de
teclear.

**Sincronización automática.** En **Configuración ERP → Hoja de seguimiento en
Drive**, «**Sincronizar la hoja automáticamente**» hace que el espejo corra solo
cada pocos minutos (10 por defecto, mínimo 5). Viene **apagado**: antes de
encenderlo, pulsa **«Actualizar hoja de Drive»** y revisa en la vista previa el
bloque **«Espejo BoHub ↔ hoja»**. En la **primera** pasada, «histórico nuevas»
debería ser **~0** (si no, el casado del histórico no ha cuadrado: avisa antes
de seguir). El botón y el automático nunca corren a la vez.

**Los completados bajan al histórico (y siguen vivos).** Cuando pulsas **«Marcar
completado»**, el pedido **sale de la zona viva y baja al histórico** (bajo el
separador *«HISTÓRICO»*), en vez de desaparecer. No hay un bloque «COMPLETADOS»
etiquetado aparte: se integran con el histórico manual, arriba de él. El
disparador de bajar es **«Marcar completado»** (`completed_at`) —**nunca** el
estado de envío—. Y aunque ya estén abajo, **sus filas de BoHub no se congelan**:
en cada «Actualizar hoja de Drive» se **resincroniza la fila entera por Nº**, así
las columnas **Envío** y **Courier** quedan **vivas** (tracking, fecha recogido,
estado del transporte que manda el webhook de Genei, factura, cobro,
entregado…). Una **incidencia de envío** (transporte) **no mueve la fila**: solo
pone **Envío = Incidencia** (distinta de una incidencia de pedido —taller/stock—, que va a
«Incidencias»). El **histórico manual** (las miles de filas de siempre) se
**conserva byte a byte**: no es de BoHub, así que nunca se toca.

**Pedidos añadidos a mano (transición).** Mientras no todos los pedidos pasen
por la app, puedes **teclear uno en cualquier fila** de la zona viva de
«Seguimiento (app)» (por debajo de la cabecera) poniendo **`MANUAL` en la
columna Origen** (mejor en mayúsculas). Ese es el marcador: «Actualizar hoja
de Drive» **lee la hoja antes de escribir** y las
filas con Origen = MANUAL se **conservan intactas** (no se pisa ninguna celda
que hayas rellenado), **intercaladas con las de BoHub por Fecha** —la más
reciente primero, el mismo orden que el resto de la zona viva—: una fila
manual del 21/09 queda entre los pedidos de BoHub del 21/09, no fijada arriba.
Si no tiene una Fecha reconocible, sí **sube arriba del todo**, para que no se
pierda de vista. Una fila **sin** el marcador es de BoHub y se reescribe: no
pongas pedidos a mano sin él.

Si más tarde **el mismo pedido entra por BoHub** (mismo Nº de pedido), no se
duplica: queda **una sola fila**, BoHub **rellena los huecos** y lo que tú
escribiste se respeta. La Nota lleva entonces dos marcas de la app, siempre
**entre corchetes** (tu texto no se toca):

- **`[BoHub: Factura#…, Tracking#…]`** — las celdas que rellenó BoHub (el
  código tras `#` es una huella de lo que escribió). Esas se **mantienen al
  día** en cada «Actualizar» (si BoHub cambia la Situación o el tracking,
  cambian). **Si corriges tú una de ellas, se queda la tuya**: la huella ya no
  casa y la celda pasa a ser tuya como cualquier otra.
- **`[⚠ BoHub Cliente: Roca SL]`** — una celda que ya tenía **dato tuyo** y
  BoHub dice otra cosa: **se conserva lo tuyo** y se apunta aquí lo de BoHub.

Una celda con `—` o `-` cuenta como vacía (es la convención de la hoja para
«sin dato»). Mientras haya un aviso así —o algo que solo esté escrito a mano:
una celda que BoHub no tiene, tu nota, algo en columnas de más allá de la
última— la fila
sigue siendo **MANUAL**, para no perderlo en el siguiente «Actualizar». Cuando
ya no queda nada que perder (lo tuyo coincide con BoHub), la fila **pasa a
BoHub** con su Origen real y deja de ser manual. Para entregarla antes, borra
las celdas que no coincidan o cambia tú el Origen. El Nº se compara sin
espacios ni mayúsculas; si tecleas **solo el número** (`99931`), casa con
`BOPRIN-99931` solo si ninguna otra tienda tiene ese número (con prefijo, tiene
que coincidir entero). Sin Nº, la fila se queda como manual suelta. Si el mismo
pedido está en dos filas a mano, se quedan las dos (no se entrega ninguna).

Las filas se leen y se escriben **tal cual** (sin que Google Sheets
reinterprete lo tecleado: un `00123` sigue siendo `00123`, un texto que empiece
por `=` no se convierte en fórmula). Los pedidos manuales **creados en BoHub**
sin canal salen con Origen **«Manual (BoHub)»**, para no confundirse con los
tecleados a mano. Las filas que BoHub ya había escrito antes con Origen
«Manual» (así, con mayúscula inicial) se reconocen como suyas —llevan en
Cobro, Preparación y Envío las etiquetas exactas de BoHub— y se reescriben con
la etiqueta nueva: no se quedan congeladas como si fueran a mano. Por eso,
**escribe `MANUAL` en mayúsculas**: así nunca hay duda.

La vista previa te dice cuántas filas son de BoHub y cuántas **a mano se
conservan**. Reimportar el histórico tampoco las toca.

**Solo se regenera la zona de arriba.** Del separador hacia abajo se conserva
tal cual, cambie como cambie el número de pedidos vivos, y repetir la
actualización no duplica el separador ni descuadra nada.

La **vista previa** sigue estando antes de escribir: te dice a qué pestañas va,
cuántas filas, el desglose por Situación y **cuántas filas del histórico se
conservan**.

> **La pestaña histórica no se toca.** Es la hoja de siempre, con miles de filas
> que el equipo ha editado a mano, y queda como archivo de consulta: la app
> nunca la reescribe ni borra nada de ella. La app reconoce sus pestañas por el
> **contenido** (su cabecera, el separador del histórico o sus filas), no por la
> posición: puedes ordenar las pestañas como quieras, y «Seguimiento (app)»
> puede ser la primera. Si el título configurado apuntara a la hoja vieja (o a
> cualquier pestaña hecha a mano), la actualización se niega a escribir y te lo
> dice; la vista previa también.
>
> Ojo con el **modo antiguo** (solo añadir filas), si alguna vez lo reactivas:
> ese sí escribe en la **primera** pestaña del documento. Si la primera es la de
> la app, se niega; pon la hoja vieja la primera antes de usarlo.
>
> El comando de importación del histórico (abajo) también lee la **primera**
> pestaña como hoja vieja. Ya se ejecutó y no hace falta repetirlo; si alguna
> vez hubiera que hacerlo, pon antes la hoja vieja la primera.
>
> El **histórico** se genera una sola vez con el comando
> `python -m scripts.importar_historico_seguimiento` (primero sin `--apply`, que
> te dice qué columnas reconoce, cuántas filas mapea, cuántas descarta y cuántas
> quedan dudosas). El comando **parte la hoja vieja por el marcador
> «^^^^ Aquí arriba pedidos que faltan entregar»**:
>
> - lo de **debajo** (ya entregado) → zona de histórico de «Seguimiento (app)»,
>   con Situación «Histórico»;
> - lo de **encima** (tu lista de pedidos por entregar) → pendientes heredados
>   en «Incidencias (app)», con tipo «Pendiente entrega (histórico)» y estado
>   «Abierta». No se entierran en el archivo: siguen pendientes.
>
> Lo que no tiene columna equivalente (la columna «Orden», vendedor,
> transporte…) se recoge en «Nota / Incidencia». La fila del propio marcador se
> descarta: su función la cumple ahora el orden por Situación.
>
> Si te quedó una pestaña **«Histórico (formato nuevo)»** de una importación
> anterior, ya sobra — bórrala a mano cuando hayas comprobado la nueva.

### Excepciones

**Menú: «ERP · Pedidos → Excepciones». Título: «Excepciones».** *«Todo lo que
bloquea el flujo, con acción de resolución.»* Aquí aterrizan las incidencias:
falta de stock, material defectuoso, problema de preparación, tamaño que excede
al transportista, parada por el cliente, incidencia/devolución de transporte,
error de escritura en FACTUSOL o fallo al enviar la factura por email.

Filtra por **«Estado»** (*Abiertas* por defecto, *En curso*, *Resueltas*,
*Descartadas*) y por **«Asignación»**. Por fila: **«Asignarme»**, **«Marcar
vista»** y **«Resolver»**. Mientras haya una incidencia abierta, el pedido va a
la cola **«Incidencias»** y no se puede aprobar.

### Cuadre (descuadres)

**Menú: «ERP · Cuadre». Título: «Cuadre».** Reúne en un sitio lo que **no
cuadra** entre BoHub, FACTUSOL, los envíos y la hoja de Drive.

**Es solo lectura.** No escribe en FACTUSOL, no corrige datos y no cambia
estados. Cada descuadre lleva a la pantalla donde ya está el botón para
arreglarlo.

**Qué hay en la pantalla**

- **Contadores** de descuadres por severidad (**alta**, **media**, **baja**) y
  la fecha de la última comprobación. Pulsar un contador filtra por esa
  severidad.
- **Una tarjeta por comprobación**, con su título, la severidad y los
  descuadres abiertos (y cuántos son nuevos o están revisados). Al desplegarla
  se ven sus filas. Cada fila trae:
  - un enlace al pedido o documento;
  - qué no cuadra;
  - dónde se arregla;
  - el botón que lleva allí.

**Qué se comprueba**

| Grupo | Comprobaciones |
|---|---|
| Dinero (alta) | Factura con líneas que no son suyas; cobro distinto en BoHub y en FACTUSOL (o cobrada sin línea de cobro); factura sin cobrar pasados 30 días, con lo pendiente. |
| Envíos (media) | «No requiere envío» con tracking o courier; enviado sin aviso al cliente; en tránsito más de 10 días; entregado, facturado y cobrado sin «Marcar completado» a los 14 días. |
| Documentos y hoja | Pedido que falta en la hoja de Drive (o fila de un pedido que ya no existe); factura de FACTUSOL sin vincular al pedido o vinculada a un número que no existe; factura sin enviar a los 7 días; pedido sin aprobar a los 7 días; proforma aceptada sin convertir a los 30 días. |

**Si no es un descuadre**

Pulsa **«Revisado / no es un descuadre»** y escribe un motivo corto (es
obligatorio). No vuelve a salir mientras no cambien sus datos. Si cambian (por
ejemplo, cambia el importe pendiente), vuelve como nuevo.

Con **«Incluir revisados»** los ves y puedes pulsar **«Volver a incluir»**.

Lo que se arregla desaparece solo en la siguiente comprobación: queda como
*resuelto*, no se borra.

**Filtros:** severidad, comprobación, **«Solo nuevos desde la última vez»** e
**«Incluir revisados»**.

**«Descargar Excel»** baja todos los descuadres abiertos.

**«Comprobar ahora»**

- Las comprobaciones de BoHub se hacen al momento.
- Las de FACTUSOL van en segundo plano para no saturar su API: verás
  **«Comprobando FACTUSOL…»** y la lista se pone al día sola al terminar.
- Además, si está encendida, hay una comprobación **cada noche** (a las 03:00
  por defecto).

**Desde el inicio del ERP** sale una tarjeta **«N descuadres»** cuando hay
alguno de severidad alta o media. Lleva directamente al Cuadre.

**Configuración** (en **«Configuración ERP → Cuadre (descuadres)»**)

- Encender la comprobación nocturna y elegir su hora. Viene **apagada**: se
  enciende después de revisar el primer lote con «Comprobar ahora».
- Activar o desactivar cada comprobación.
- Cambiar los días de aviso de cada comprobación.

### Ajustes del ERP

**Menú: «ERP · Configuración». Título: «Configuración ERP».** Solo un
administrador puede guardar. Cada sección se guarda por separado y enseña al lado
lo que va a pasar. Secciones destacadas:

- **«Series FACTUSOL»** — la **serie** es la empresa que emite la factura. Fijas
  la **serie por defecto** y la que usa cada origen de pedido. Las series
  disponibles son **1 · Bomedia**, **2 · MQ Europe**, **4 · Lambert** y **5 ·
  Streamtec**. También decides los estados que BoHub escribe en FACTUSOL
  (ESTPCL/ESTPRE/ESTALB/ESTFAC).
- **«Tiendas y referencias»** — el **prefijo por tienda** que va delante del nº
  de pedido en la referencia de FACTUSOL (con él BoHub localiza el pedido web).
- **«Remitentes del email de factura»** — desde qué dirección sale la factura al
  cliente, por tienda y por serie (debe ser un *«enviar como»* verificado de
  Gmail).
- **«Plantillas del email de factura»** — el texto del correo por idioma
  (**Español, English, Deutsch, Français, Nederlands**), con marcadores como
  `{cliente}`, `{numero}` y `{pedido}` que se sustituyen al enviar. Puedes
  **«Enviarme una prueba»**.
- **«Aviso de envío al cliente»** — el email con el nº de seguimiento que
  BoHub manda al cliente al tramitar el envío: textos por idioma (**Español,
  English, Deutsch, Français, Nederlands**) con **«Ver ejemplo»** / **«Enviarme
  una prueba»**, y el remitente de los pedidos manuales en español y en otros
  idiomas.
- **«Contrapartidas de cobro»** — el catálogo (código → descripción; aquí se da
  de alta, p. ej., la **15 · Tarjetas Mollie Belfius**) y la tabla **«Contrapartida
  sugerida por tienda y método de pago»**: cada regla es **tienda** (o
  «Todas») + **método de pago** (el título de la tienda, p. ej. «Carte», o el
  id del gateway, p. ej. «mollie_wc_gateway_creditcard»; también casa con la
  forma de pago de FACTUSOL) + **coincidencia** (exacta / contiene, sin
  distinguir mayúsculas) → **contrapartida**. Se evalúan **de arriba abajo** y
  manda la primera que casa (↑/↓ para reordenar); si ninguna casa, la cuenta
  de la serie. Vienen ya con las de PayPal de antes (Artisjet Europe → 12;
  boprint y fluxlasers → 14) y Artisjet Europe + «Carte» / tarjeta de Mollie →
  15. Una regla cuya cuenta no está en el catálogo se marca en rojo y **no se
  aplica** hasta que la añadas (nunca se sugiere una cuenta inexistente). El
  desplegable **«Tienda»** lista las tiendas WooCommerce dadas de alta (las de
  «Integraciones · Woo»), con su nombre; es la misma tienda en toda la app
  (reglas, remitentes, prefijo de referencia y serie).
- Otras: **Facturación**, **Abreviaturas de empresa**, **Email del SAT /
  taller**, **Empresas emisoras**, **Almacenes de recogida**, **Orígenes del
  envío** y **Hoja de seguimiento en Drive**.

**«Envíos (Genei)».** Email y password de la cuenta de Genei (guardados
**cifrados**), URL del webhook de estados, origen, bulto por defecto y couriers
preferidos. **La sesión con Genei se abre y se renueva sola** con esas
credenciales: cuando el token de Genei caduca, BoHub pide otro y repite la
operación sin que se note. **No hay que volver a meter la password** por
caducidad. Debajo de la password verás el estado:

- **«✓ Conectado…»** — todo bien (y hasta cuándo vale la sesión actual).
- **«⚠ Genei ha rechazado el usuario o la contraseña guardados…»** — la
  password guardada ya no vale (la cambiaron en Genei, por ejemplo): escribe
  la buena y **«Guardar cambios»**. Mientras tanto, BoHub no insiste contra
  Genei (evita bloquear la cuenta) y cada acción de envío muestra ese mismo
  aviso.
- **«Probar conexión»** — comprueba **con las credenciales guardadas** que
  Genei responde (no hace falta escribir la password).

Guardar esta sección **ya no cambia el secreto del webhook**: los envíos creados
antes siguen avisando a BoHub de cada cambio de estado.

**«Aviso de envío al cliente»**: *«BoHub envía al cliente el aviso de
envío»* (encendido por defecto). Los textos por idioma (con **«Ver ejemplo»** y
**«Enviarme una prueba»**) y los remitentes de los pedidos manuales están en
**«ERP · Configuración» → «Aviso de envío al cliente»** (marcadores
`{cliente}`, `{pedido}`, `{tracking}`, `{enlace}`, `{agencia}`).

**«Estado real del envío (transportista)»**: *«Consultar automáticamente»*
(encendido por defecto) y cada cuántos minutos (mínimo 10) BoHub pregunta a
Genei por los escaneos del transportista de los envíos en curso. Solo lee de
Genei; no paga ni crea nada.

**Webhooks y fecha de corte** están en **«ERP · Integraciones · Woo»**: cada
tienda tiene su **URL de webhook** y un **secreto** (**«Regenerar»** si hace
falta; guárdalo, no se vuelve a ver entero) y una **«Fecha de corte»** opcional
(los pedidos anteriores se importan ya como *procesados externamente* y no entran
a la cola).

---

## Parte 2: el ciclo de un pedido, paso a paso

Aquí va el recorrido completo. En cada paso: **qué se ve, qué se pulsa y qué pasa
después**. Con esto un compañero nuevo puede llevar un pedido de principio a fin.

### A) Pedido web (WooCommerce), de principio a fin

**Cómo entra.** El cliente paga en la web. WooCommerce avisa a BoHub (webhook) y,
**solo cuando el pedido ha entrado en el flujo** —*processing* (pagado y listo
para preparar), *completed* o *refunded*—, BoHub lo crea automáticamente. Un
carrito *pending* / *on-hold* / *failed* / *draft* / *cancelled* no se crea; si
más tarde pasa a *processing*, se crea en ese momento. Nace con:

- **Preparación = Pend. revisión** → aparece en la cola **«Por revisar»**.
- **Pago = Pagado** si la web ya registró el pago.
- Como está pagado, entra **también** a la **Cola SAT** de forma automática (sin
  esperar a la aprobación). No hay que teclear nada: el pedido ya está en BoHub.

**Paso 1 — Aprobar.** En la **Bandeja**, cola **«Por revisar»**, pulsa
**«Aprobar»** en el pedido (o **«Aprobar seleccionados»** para varios). *Qué
pasa:* el pedido pasa a *En cola* y queda listo para el taller. Si algo lo
bloquea (una incidencia abierta), no dejará aprobar y estará en **«Incidencias»**.

**Paso 2 — Taller: preparar y embalar.** En **Cola SAT**, pestaña **«Por
embalar»**, pulsa **«▶ Empezar preparación»** en la tarjeta (pasa a *En
preparación*). Ahí mismo, rellena **peso y medidas de cada bulto** y **«📦
Embalar»**. *Qué pasa:* el pedido pasa a **«Embalados»** sin salir de él.

**Paso 3 — Albarán.** En un pedido web **no hay que crear albarán**: lo genera
WooCommerce. En la Cola SAT puedes **«Descargar albarán»** para imprimirlo.

**Paso 4 — Etiqueta y tracking.** En la tarjeta de **«Embalados»** (o en la ficha,
**«Documentos de envío» → Etiqueta**) pulsa **«Subir etiqueta»** y sube el PDF/imagen
de la etiqueta del transportista. Guarda el **«Nº de seguimiento»**. *Qué pasa:*
el transporte pasa a **«Etiqueta creada»**.

**Paso 5 — Marcar recogido.** Cuando el transportista se lleva el paquete, pulsa
**«📤 Marcar recogido»** → **«Sí, recogido»**. *Qué pasa:* el transporte pasa a
**«En tránsito»** (el paso **Enviado** queda hecho).

**Paso 6 — Emitir la factura.** En la **Bandeja**, cola **«Por facturar»** (o en
la ficha, panel **FACTUSOL**), pulsa **«Emitir factura FACTUSOL»**. Confirma en
**«Emitir factura en FACTUSOL»**. *Qué pasa:* BoHub crea la **factura real** en
FACTUSOL, guarda su número y el pedido pasa a **«Facturado FACTUSOL»**. Si por lo
que sea la factura ya existía en FACTUSOL, BoHub la **enlaza** en vez de
duplicarla. *(Emitir la factura no registra el cobro: eso es aparte.)*

**Paso 7 — Registrar el cobro.** En la cola **«Por cobrar»** (o en la ficha)
pulsa **«Registrar cobro en FACTUSOL»**, elige la **cuenta/contrapartida** y la
fecha, marca la confirmación y **«Registrar cobro»**. *Qué pasa:* BoHub apunta el
cobro en FACTUSOL y la factura queda **cobrada**.

**Paso 8 — Enviar la factura al cliente.** En la ficha, **«Enviar factura al
cliente»** → marca los **contactos de la empresa** que reciben (Para/CC) o
escribe una dirección, revisa idioma y mensaje → **«Enviar factura»**. Sale el
email con el PDF adjunto desde el remitente configurado y queda en el timeline
del contacto.

**Paso 9 — Marcar completado.** Cuando no quede nada, **«Marcar completado»** (en
la ficha o en la fila). El pedido sale de las colas de trabajo (solo en BoHub;
WooCommerce no cambia).

### B) Pedido manual, de principio a fin

Para encargos por teléfono, muestras o reparaciones sin ticket web.

**Paso 0 — Crear el pedido.** Bandeja → **«+ Nuevo pedido manual»** (título
**«Nuevo pedido manual»**):

1. **Empresa.** Busca el cliente con **«Cliente (NIF o nombre)»**. La empresa
   **debe estar vinculada a un cliente de FACTUSOL** (sin ella no se puede
   facturar ni crear el albarán). Si aún no lo está, la propia pantalla te deja
   **«Vincular ahora»**. Un contacto suelto no basta.
2. **Serie (empresa emisora).** Elige la serie con la que salen los documentos:
   **1 · Bomedia**, **2 · MQ Europe**, **4 · Lambert** o **5 · Streamtec**.
3. **Líneas.** Añade artículos buscando por **SKU** o **Descripción** (o
   escríbelos a mano para conceptos como mano de obra), con cantidad y precio.
   **«+ Añadir línea»** para más.
4. **Envío.** Rellena la dirección. En **«Portes (gastos de envío)»** pon el
   coste de envío (va como línea aparte). Si el envío es a otro destinatario
   (**dropshipping**), usa **«Enviar a → Otra dirección»** y **«Nombre de envío»**:
   *el albarán irá a ese nombre y dirección; la factura, siempre a la empresa*.
5. Pulsa **«Crear pedido»**. *Qué pasa:* se guarda en BoHub con nº **MANUAL-…**
   y te lleva a su ficha.

**Atajo: partir de una proforma que ya existe.** En el alta, bajo
**«Proformas FACTUSOL disponibles»**, cada proforma trae dos botones:

- **«Cargar todo»** — trae **el cliente y las líneas** de la proforma (como
  duplicarla, pero hacia un pedido). El cliente es el **mismo cliente FACTUSOL**
  de la proforma, con su NIF, su nombre fiscal y su dirección: es con quien se
  va a facturar. Si ese cliente aún no tiene empresa en el CRM, el aviso lo dice
  y ahí mismo puedes **crearla** o **vincularla** sin salir del formulario. El
  pedido que sale es un **pedido manual normal**: sigue su ciclo de siempre y
  **no** queda atado a la proforma. Si lo que quieres es convertir la proforma
  **como documento** (con su trazabilidad, su paso de pago y su albarán), eso se
  hace con **«Convertir en pedido»** desde la pantalla de
  [Proformas](#proformas).
- **«Solo conceptos»** — trae **solo las líneas**; el cliente que ya tengas
  elegido **no cambia**. Es lo que quieres para repetir los mismos artículos
  con otro cliente.

Los mismos dos botones salen al buscar una proforma **por referencia** en
**«Importar de FACTUSOL»**, así que da igual por dónde llegues.

En ambos casos: si ya habías escrito líneas, te pregunta si **añadirlas** o
**reemplazarlas**; las líneas quedan **editables** (cantidad, precio,
descripción) antes de crear el pedido, y los **portes** de la proforma se
cargan en su campo de portes, no como una línea suelta. Funciona con proformas
de **cualquier serie**, no solo la 1.

**Paso 1 — (Opcional) Proforma para cobro.** Si necesitas cobrar por
adelantado, en la ficha (panel FACTUSOL, sección Serie) pulsa **«Crear proforma
de cobro»**, o crea una desde **Proformas**. Envíala al cliente con **«PDF»**.

**Paso 2 — Crear el albarán en FACTUSOL.** En la ficha, **«Documentos de envío»
→ «Crear albarán en FACTUSOL»** (en un pedido manual **sí** hay que crearlo).
*Qué pasa:* BoHub crea el albarán en FACTUSOL y guarda su número. Es idempotente:
si ya existía, lo enlaza en vez de duplicarlo. *(Si el pedido lo creas desde una
proforma con «Convertir en pedido», el albarán se crea en ese momento.)*

**Paso 3 — Taller, etiqueta y recogida.** Igual que en el pedido web (pasos 2, 4
y 5 de arriba): **EMPEZAR → EMBALADO → Subir etiqueta + tracking → Marcar
recogido**. Para meterlo en el taller a mano, usa **«Añadir pedido a la cola»**.

**Paso 4 — Emitir la factura.** Panel FACTUSOL → **«Emitir factura FACTUSOL»** →
confirma. La factura hereda la serie del pedido en FACTUSOL.

**Paso 5 — Registrar el cobro.** **«Registrar cobro en FACTUSOL»** (cuenta,
fecha, confirmar).

**Paso 6 — Enviar la factura y completar.** **«Enviar factura al cliente»** y,
al final, **«Marcar completado»**.

**Anular un pedido.** En la ficha, **«⋯» → «Anular pedido»**. Se puede **aunque
tenga factura**: la factura, el albarán y la proforma se **desvinculan** del
pedido y siguen en FACTUSOL (si la factura hay que anularla o abonarla, se hace
en FACTUSOL). En un pedido **sin factura** puedes además marcar **«Borrar
también en FACTUSOL»** el **albarán** (si aún no está facturado) o el
**presupuesto** (si sigue pendiente). El pedido anulado sale de la bandeja, las
colas y el seguimiento, lo dice el historial y **se puede restaurar** con
**«Restaurar pedido»** (vuelve a vincular sus documentos si siguen libres).

**Un pedido web reembolsado** lleva el mismo cierre —sale de la bandeja y de las
colas—, pero **no se llama «anulado»**: la ficha y las listas lo enseñan como
**«Reembolsado»**, y en **Seguimiento se sigue viendo** (Situación
«Reembolsado»). Lo marca el propio sync con la tienda en cuanto el pedido pasa
a `refunded`; no hay que hacer nada a mano.

### C) Casos especiales

**Envío de muestra / no facturable.** Para mandar una **muestra** a un cliente o
prospecto, o un envío fuera de facturación (una pieza olvidada, un repuesto de
cortesía, material de prueba). **No se factura**: no lleva empresa, ni albarán,
ni factura, ni cobro — solo se prepara y se envía.

Bandeja → **«+ Nuevo envío / muestra»**. El formulario es corto a propósito:

1. **Destinatario** — **Empresa** (opcional), **Persona de contacto**
   (obligatoria: a quién va dirigido), **Email** y **Teléfono** (opcionales) y
   la **dirección**. **No** hace falta que sea un cliente del CRM ni que esté
   en FACTUSOL: puede ser un prospecto o un particular. La etiqueta sale a
   nombre de la **empresa** si la pones y, si no, de la persona.
2. **Qué se envía** — artículos o conceptos libres, para que el taller sepa qué
   preparar. El **precio es opcional** (0 por defecto): no se cobra.
3. **Motivo** — por qué se manda (*«muestra»*, *«pieza olvidada del pedido
   BOP-1234»*).

*Qué pasa al guardarlo:* se crea con nº **MUESTRA-…**, **no se escribe nada en
FACTUSOL**, y **entra directa a la Cola SAT** (no hay que aprobarla ni esperar
a ningún pago). El taller la prepara, embala, sube etiqueta y marca recogido
igual que cualquier otro pedido; luego se **marca completada**.

En qué se nota que es distinta:

- Pastilla **«Muestra · no facturable»** en la bandeja.
- **Factura** y **Cobro** salen en **«No aplica»** (gris) y sus botones no
  aparecen: no se puede emitir factura, crear albarán ni registrar cobro.
- **No** entra en las colas **«Por facturar»** ni **«Por cobrar»**; sí en
  **«Por enviar»** y en la **Cola SAT**.
- En **Seguimiento** aparece como envío no facturable (su situación es la del
  envío; Factura/Cobro, **«No aplica»**).
- Se **anula** como cualquier pedido, desde **«⋯» → «Anular pedido»**.

**Una muestra que al final se factura.** Si la muestra acaba teniendo su
**albarán, proforma o factura** en FACTUSOL, en su ficha pulsa **«Vincular
documento FACTUSOL»** (en el aviso de la muestra o en «⋯»): eliges el tipo, lo
buscas por serie-número o cliente, y antes de confirmar ves lo que cargará —
**cliente → empresa del CRM, líneas, base / IVA / total, serie y forma de
pago**. Al confirmar, la muestra pasa a comportarse como un pedido creado desde
ese documento: línea de vida real (pagado / albarán / factura / cobro),
**«Por cobrar»**, PDF y envío de la factura… Conserva su nº **MUESTRA-…** y la
pastilla **«Muestra»**. Nada se escribe en FACTUSOL.

- Si el cliente de FACTUSOL **no tiene empresa en el CRM**, la ventana te deja
  **crearla con sus datos** o **vincularla a una existente**, y luego sigues.
- Si la muestra ya apuntaba a una factura **sin haber cargado sus datos**
  (vinculada desde ERP · Documentos antes de este cambio, p. ej. MUESTRA-000003
  ↔ 2-526110), la ficha ofrece **«Reprocesar vínculo»**. ERP · Documentos ya no
  vincula documentos a muestras: remite a su ficha.
- **«Desvincular»** el documento (bloque FACTUSOL) devuelve la muestra a modo
  muestra: no facturable, 0 €, sus líneas originales.

**Quién puede crearla:** Comercial, ERP Pedidos, **ERP Taller (SAT)** y
Administración — el taller también manda muestras, no solo las prepara.

**Cliente intracomunitario (factura sin IVA).** Si la empresa es de otro país de
la UE **con un NIF-IVA válido**, su régimen es **intracomunitario** y la factura
sale **sin IVA**. La app lo valida en **VIES**:

- Si VIES da el NIF-IVA como **válido** (**«✓ verificado en VIES»**), se aplica la
  exención.
- Si VIES lo da como **no válido**, la ficha muestra una alerta y **se factura
  como nacional con IVA** hasta que se corrija el NIF-IVA (**«Revalidar en
  VIES»**). Cliente de fuera de la UE → **exportación**, también sin IVA.

**Dropshipping (enviar a otro nombre/dirección).** En el alta del pedido (o en la
proforma) usa **«Nombre de envío»** y la dirección de envío. El **albarán** sale a
ese destinatario; la **factura** va siempre a nombre fiscal de la empresa.

**Crear un pedido desde una factura o albarán de FACTUSOL.** En **Documentos
FACTUSOL**, en la pestaña correspondiente, pulsa **«Crear pedido»** en la fila.
BoHub crea el pedido copiando cliente, líneas e importes (lee FACTUSOL, no escribe
nada). Si viene de un albarán/factura, el pedido ya cuenta con ese documento
enlazado. Si el cliente FACTUSOL no tiene empresa en el CRM, BoHub pide
**«Vincular empresa»** (elegir una o crearla con los datos de FACTUSOL) y luego
sigue con el pedido.

**Vincular / fusionar una empresa con su cliente FACTUSOL.** En la ficha de la
empresa, panel **FACTUSOL**: **«Buscar en FACTUSOL»** → **«Vincular»**. Si ese
cliente ya está en otra ficha del CRM, usa **«Fusionar con esa empresa»** (la
actual se fusiona en la que ya tiene el vínculo; una sola ficha se queda el
código, sin duplicar).

**Cambiar la serie de un pedido manual.** En la ficha, panel FACTUSOL, sección
**Serie → «Cambiar serie»**. Cámbiala **antes** de emitir la factura: la factura
saldrá con la nueva serie (empresa emisora).

**Ante una incidencia.** Si un pedido está **«Bloqueado»** o en la cola
**«Incidencias»**, ve a **Excepciones**: **«Asignarme»**, trabájala y
**«Resolver»**. Mientras esté abierta no se puede aprobar.
Nota: las **líneas «sin mapear»** (sin artículo de FACTUSOL) **no son una
incidencia** ni bloquean nada; se facturan como texto libre.

---

## Chuleta de pastillas y estados

Colores: **verde** = hecho · **ámbar** = pendiente · **gris** = no aplica ·
**rojo** = bloqueado/incidencia.

**Rejilla de estado (Pago · Factura · Cobro · Envío):**

- **Pago:** Pagado · Pendiente · Parcial · A crédito · Fallido · Devuelto.
- **Factura:** Emitida · Por emitir · Error · Abonada.
- **Cobro:** Cobrado · Pendiente · — (no aplica).
- **Envío:** Entregado · Enviado · Etiqueta lista · Pendiente · Incidencia ·
  Devuelto.

**Otros badges:** **Completado** (terminado, solo BoHub) · **Cobrado FACTUSOL** /
**Pendiente de cobro FACTUSOL** / **Cobro FACTUSOL sin comprobar** ·
**Bloqueado** · **Externalizado** (gestionado fuera del ERP) · **Oculto** (quitado
a mano de la bandeja) · **Anulado**.

**Régimen de IVA:** **nacional · con IVA** / **intracomunitario · exento** /
**exportación · exento**.

---

## Preguntas frecuentes

**Hay carritos (pending / on-hold / failed / borradores) entre los pedidos.**
Entraron con la regla antigua del sync. Se borran de verdad con el comando
`python -m scripts.limpiar_pedidos_web_no_procesados` (dentro del contenedor
`api`): sin `--apply` solo **lista** lo que borraría (nº, cliente, estado,
importe y total) y lo que **protege**; con `--apply` pide teclear `BORRAR` y
los elimina con sus líneas, historial, excepciones, envíos y fila de Drive,
sin dejar restos. **Nunca** borra un pedido con factura, albarán, cobro, nº de
serie, tracking, WhiteRIP, excepción/tarea SAT o ya en preparación, ni un
*refunded* / *completed* / *processing* / *cancelled*, ni un manual, ni una
muestra. **Haz copia de seguridad de la base de datos antes**: no tiene vuelta
atrás. Reejecutarlo después da 0.

**Un pedido web no aparece en la bandeja.** BoHub solo crea el pedido cuando en
WooCommerce ha entrado en el flujo (*processing*, *completed* o *refunded*); un
carrito sin pagar o en espera no se crea hasta que pasa a *processing*. Los pedidos
anteriores a la **fecha de corte** de la tienda entran como *procesados
externamente* y no salen en las colas (marca **«Mostrar procesados externamente»**
para verlos).

**¿«Pagado» y «Cobrado» son lo mismo?** No. **Pagado** es que el cliente pagó el
pedido en la web/CRM. **Cobrado** es que la **factura** consta cobrada en la
contabilidad de FACTUSOL (paso **«Registrar cobro»**).

**Emití la factura pero el cobro sigue pendiente.** Es normal: emitir y cobrar
son dos pasos. Ve a **«Por cobrar»** o a la ficha y **«Registrar cobro en
FACTUSOL»**.

**No me deja emitir la factura.** Suele faltar el pago o que la empresa no esté
vinculada a FACTUSOL. Revisa las alertas de la ficha y el panel FACTUSOL.

**Me equivoqué al anular / archivar.** Casi todo es reversible: **«Restaurar
pedido»** en la ficha del pedido, **«Reactivar»** en la ficha de la empresa.

**¿BoHub puede estropear FACTUSOL?** Solo escribe en FACTUSOL cuando confirmas
una acción (factura, albarán, cobro, alta/edición de cliente, proforma). El resto
es solo lectura, y **las facturas no se borran nunca** desde BoHub.
