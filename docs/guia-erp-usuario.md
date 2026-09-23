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

1. **«Por revisar»** — *esperando tu aprobación*.
2. **«Por facturar»** — *aprobados, sin factura en FACTUSOL*.
3. **«Por cobrar»** — *facturados, sin cobro registrado en FACTUSOL*.
4. **«Por enviar»** — *cobrados, pendientes de salir*.
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
defecto»** (pagados y sin completar) reinician la vista.

**Ver.** Un selector **«Ver»** con tres modos:

- **«Activos»** — la bandeja normal.
- **«Ocultados»** — solo los que quitaste a mano de la bandeja, con su motivo y
  la opción **«Reincluir»**.
- **«Anulados»** — solo los pedidos anulados (se restauran desde su ficha).

**Vista.** Botones **«Tarjetas»** (por defecto) y **«Lista»** (tabla con columnas
**Nº · Cliente · Tienda · Fecha · Importe · Estado · Cola · siguiente paso ·
Acciones**).

**Las pastillas de estado.** Cada pedido muestra una rejilla de cuatro celdas —
**Pago · Factura · Cobro · Envío** — más la pastilla **«Completado»**. El color
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
- **«Enviar por email»** — manda el pedido (con su albarán) por email al taller
  (SAT). Abre **«Enviar pedido por email»** para elegir destinatarios, adjuntos
  e idioma.
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
  emisora/serie, fecha y forma de pago; botón **«Emitir factura»**.
- **«Registrar cobro en FACTUSOL»** — eliges la **cuenta/contrapartida**, la
  fecha y la forma de pago, marcas la casilla de confirmación y pulsas
  **«Registrar cobro»** (es un apunte contable en FACTUSOL).
- **«Anular pedido»** — solo pedidos **no web**. Puedes marcar **«Borrar también
  en FACTUSOL»** el albarán/presupuesto; **la factura no se borra nunca**. El
  pedido sale de la bandeja, las colas y el seguimiento, y **se puede restaurar**
  desde la propia ficha.

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

**Colas:** **«Aceptadas · por convertir»**, **«Pendientes de respuesta»**,
**«Rechazadas»** y **«Convertidas»**. Cada fila muestra la **antigüedad** en
lenguaje claro (p. ej. *«Enviada hace 41 días · sin respuesta»*), y a partir de
30 días sin respuesta se resalta en ámbar.

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
- **«Duplicar»** — crea una proforma nueva a partir de otra (con vista previa de
  sus líneas y su PDF) para el cliente que elijas. La copia parte de la **misma
  empresa emisora** que la plantilla (la vista previa la dice), y puedes
  cambiarla en el selector antes de crearla.
- **«PDF»** — descarga el PDF de la proforma.

**«Nueva proforma».** Eliges la **empresa destino** (vinculada a FACTUSOL) y la
**empresa emisora (serie)** —1 Bomedia · 2 MQ Europe · 4 Lambert · 5 Streamtec,
las mismas que en el alta de pedido manual—, añades líneas (buscando por **SKU**
o **Descripción**, o escribiéndolas a mano), los **portes** en su campo aparte y
una **referencia** opcional. Se guarda como un presupuesto **real** en FACTUSOL,
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
  albarán o factura lo crea directamente.
- **«PDF»** — descarga el documento (o varios en ZIP si seleccionas).
- **«Ver detalle»** — abre una ventana con las líneas, los cobros y opciones de
  PDF; desde ahí también puedes **crear el albarán/la factura** o **vincular** el
  documento a un pedido existente (**«Vincular a pedido»**, sin tocar FACTUSOL).

### Cola SAT (el taller)

**Menú: «ERP · Taller (SAT)». Título: «Cola SAT».** Es la pantalla del taller:
lo que hay que preparar y enviar.

**Vistas (pestañas):** **«Por embalar»**, **«Listos»**, **«Global»** (las dos
juntas), **«No requieren envío»** (los marcados como que no se envían) y
**«Enviados»** (historial de lo ya mandado al taller). Vista **«Tarjetas»** /
**«Lista»**.
**Filtros:** buscador (*«Nº de pedido o cliente…»*), **«Desde»**/**«Hasta»**,
**«Tienda»** y **«Estado»** (*Todos / Por embalar / Bloqueados / En cola /
Preparando / Listos*), con **«Limpiar filtros»**.

**«No requiere envío» (en lote).** Hay pedidos que no se envían nunca
(servicios, RMA, asistencias remotas, tintas ya entregadas…). En **«Por embalar»**
o **«Listos»**, marca sus casillas (o **«Seleccionar todo»**) y pulsa **«Marcar
“No requiere envío”»**; tras confirmar, **salen de la Cola SAT** (y de la cola
«Por enviar» de la bandeja) y su casilla/hito de **Envío** pasa a **«No aplica»**.
**No toca la factura ni el cobro** y es **reversible**: en la pestaña **«No
requieren envío»** puedes seleccionarlos y **«Volver a requerir envío»** (vuelven
al taller). También por pedido, desde el menú **«⋯»** de la ficha.

En cada tarjeta:

- **Datos técnicos**: **«Nº de serie»**, **«Licencia WhiteRIP»**, **Origen** y
  **«Observaciones del comercial»** (editables por administración/pedidos).
- **Albarán**: **«Imprimir albarán»** / **«Descargar albarán»** en PDF (o
  **«Falta albarán»** si aún no existe en un pedido manual).
- **Etiqueta**: **«Subir etiqueta»** (admite imagen o PDF) y, una vez subida,
  **«Imprimir etiqueta»**.
- **Nº de seguimiento**: campo para guardar el tracking del transportista.

**Flujo del taller** (también desde la pantalla del pedido en el taller):

1. **«▶ EMPEZAR»** — el pedido pasa a *Preparando*.
2. **«📦 EMBALADO»** — abre **«Embalado — bultos»**: indica **peso y medidas de
   cada bulto** (**«+ Añadir bulto»**) y pulsa **«Guardar y embalar»**. El pedido
   pasa a **Listos**.
3. **«📤 Marcar recogido»** — confirma *«¿El paquete ha salido?»* → **«Sí,
   recogido»**. El transporte pasa a *En tránsito*.

**«Añadir pedido a la cola»** — para meter a mano un pedido en el taller por su
número (p. ej. *BOP-1234*).

### Seguimiento

**Menú: «ERP · Seguimiento». Título: «Seguimiento de pedidos».** Es la tabla que
sustituye el Excel manual de seguimiento: la app la genera y la **ordena sola**,
sin mover filas a mano. Una **fila por pedido**, con el estado en una **columna**
(no en la posición). Por defecto muestra los pedidos en curso.

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
*Pendiente* / *—*), **Preparación** y **Envío** (estado del taller y del
transporte; **«No aplica»** si el pedido no requiere envío), **Tracking**,
**Nº serie · WhiteRIP** y **Nota / Incidencia**.

**Situación** es la cola de la línea de vida del pedido —la misma de la bandeja—
y va **coloreada**: `Incidencia` (rojo), `Por revisar` (ámbar), `Por facturar` /
`Por cobrar` (azul), `Por enviar` (teal), `Listo` (verde) y `Reembolsado`
(gris). La tabla se **ordena
por Situación** (lo urgente arriba: primero las incidencias, al final lo listo
y los reembolsos) y,
dentro de cada grupo, por fecha (lo más nuevo primero); así lo importante sube
solo. Puedes reordenar pulsando en las cabeceras y filtrar como siempre (buscar,
empresa/serie, transportista, origen, estado, fechas).

Botones útiles: **«Descargar Excel»**, **«Actualizar hoja de Drive…»** (vuelca
los datos a la hoja de Google Drive, con vista previa antes de escribir),
**«Poner al día estados Woo…»** y **«Vincular facturas de FACTUSOL…»**. Por fila,
**«PDF»** (de la factura) y **«Quitar»**/**«Reincluir»**.

**«Poner al día estados Woo…»** vuelve a preguntar a las tiendas por los pedidos
que BoHub tiene como activos y aplica la misma regla: saca los que se
**cancelaron**, **fallaron**, se fueron a la **papelera** o volvieron a **sin
pagar / en espera**, y marca **«Reembolsado»** los reembolsados (esos no salen).
Los pedidos web que estaban **sin estado** (importados antes de que existiera
el dato) se consultan **uno a uno**: recuperan su estado real, y el que la
tienda **ya no tiene** queda marcado *No encontrado en la tienda* y oculto.
Siempre enseña antes una previsualización con los números; nada se cambia hasta
que confirmas.

> Un pedido web que **sigue sin estado** después de ponerlos al día (la tienda
> no respondió, o es un pedido interno de prueba) **no se ve** en Seguimiento:
> sale en «Ver ocultos por estado» como *Estado desconocido*. Si es legítimo,
> «Reincluir» lo fuerza a la lista.

> **«Incidencia» es solo lo que marcáis vosotros.** Un pedido llega a esa
> situación cuando alguien **reporta un problema a mano** desde la Cola SAT
> («Reportar problema»): esa es la lista de incidencias del equipo, y es lo que
> alimenta la pestaña «Incidencias (app)». Al resolver la incidencia el pedido
> sale de ahí.
>
> Lo que detecta la app sola —una empresa sin vincular a FACTUSOL, un NIF-IVA
> que VIES da por no válido— cae en **«Por revisar»**: hay que arreglarlo antes
> de facturar y sigue destacado, pero no ensucia la lista de incidencias.

El **Excel** que se descarga trae dos pestañas: **«Pedidos»** (las 17 columnas,
ordenadas por Situación, con la celda Situación coloreada, la cabecera fija, el
autofiltro y el importe con formato €) y **«Incidencias»** (los mismos pedidos
que están en Situación=Incidencia, con más detalle: nº pedido, cliente, tipo,
motivo, asignado, fecha y estado, tomado de la bandeja de Excepciones).

**«Actualizar hoja de Drive»** vuelca ese mismo formato nuevo al Google Sheet,
en **pestañas propias de la app**. Cada una tiene **dos zonas**:

- **«Seguimiento (app)»** — arriba, los pedidos vivos: **los mismos que ves en
  la pantalla** (en curso; fuera los quitados a mano y los ocultos por estado),
  con las 17 columnas, **ordenados por fecha del pedido, del más reciente al
  más antiguo**, la celda Situación coloreada, la **fila 1 de encabezados
  congelada** (no se va al hacer scroll) y el **autofiltro** sobre esa misma
  cabecera, con el que puedes reordenar por Situación o por cualquier otra
  columna. Debajo de una fila separadora *«──── HISTÓRICO — no se actualiza
  ────»*, el **histórico** en formato nuevo, que conserva su propio orden.
- **«Incidencias (app)»** — arriba, las incidencias que habéis reportado a
  mano; debajo del separador, los **pendientes heredados** de la hoja vieja.

**Solo se regenera la zona de arriba.** Del separador hacia abajo se conserva
tal cual, cambie como cambie el número de pedidos vivos, y repetir la
actualización no duplica el separador ni descuadra nada.

La **vista previa** sigue estando antes de escribir: te dice a qué pestañas va,
cuántas filas, el desglose por Situación y **cuántas filas del histórico se
conservan**.

> **La pestaña histórica no se toca.** Es la hoja de siempre, con miles de filas
> que el equipo ha editado a mano, y queda como archivo de consulta: la app
> nunca la reescribe ni borra nada de ella. Si el título de la pestaña
> gestionada coincidiera con el de la histórica, la actualización se niega a
> escribir y te lo dice.
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
- Otras: **Facturación**, **Abreviaturas de empresa**, **Email del SAT /
  taller**, **Empresas emisoras**, **Almacenes de recogida**, **Contrapartidas
  de cobro**, **Orígenes del envío** y **Hoja de seguimiento en Drive**.

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
**solo cuando el pedido está en estado *processing*** (pagado y listo para
preparar), BoHub lo crea automáticamente. Nace con:

- **Preparación = Pend. revisión** → aparece en la cola **«Por revisar»**.
- **Pago = Pagado** si la web ya registró el pago.
- Como está pagado, entra **también** a la **Cola SAT** de forma automática (sin
  esperar a la aprobación). No hay que teclear nada: el pedido ya está en BoHub.

**Paso 1 — Aprobar.** En la **Bandeja**, cola **«Por revisar»**, pulsa
**«Aprobar»** en el pedido (o **«Aprobar seleccionados»** para varios). *Qué
pasa:* el pedido pasa a *En cola* y queda listo para el taller. Si algo lo
bloquea (una incidencia abierta), no dejará aprobar y estará en **«Incidencias»**.

**Paso 2 — Taller: preparar y embalar.** En **Cola SAT**, pestaña **«Por
embalar»**, abre el pedido. Pulsa **«▶ EMPEZAR»** (pasa a *Preparando*). Cuando
esté hecho, **«📦 EMBALADO»**: rellena **peso y medidas de cada bulto** y
**«Guardar y embalar»**. *Qué pasa:* el pedido pasa a **«Listos»**.

**Paso 3 — Albarán.** En un pedido web **no hay que crear albarán**: lo genera
WooCommerce. En la Cola SAT puedes **«Descargar albarán»** para imprimirlo.

**Paso 4 — Etiqueta y tracking.** En la tarjeta de **«Listos»** (o en la ficha,
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

**Anular un pedido manual.** En la ficha, **«⋯» → «Anular pedido»**. Puedes marcar
**«Borrar también en FACTUSOL»** para que se borre el **albarán** (si aún no está
facturado) o el **presupuesto** (si sigue pendiente). **La factura no se borra
nunca desde aquí**: primero se anula en FACTUSOL y luego el pedido. El pedido
anulado sale de la bandeja, las colas y el seguimiento, y **se puede restaurar**
con **«Restaurar pedido»**.

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
- Se **anula** como cualquier pedido (no hay documentos de FACTUSOL que borrar).

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
enlazado.

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

**Un pedido web no aparece en la bandeja.** BoHub solo crea el pedido cuando en
WooCommerce está en *processing* (pagado y listo para preparar). Los pedidos
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
