# Panel de filtros de contactos (CRM-1.5)

`/contacts` tiene un buscador general (`q=`) **encima** y, debajo, el panel de
filtros avanzados. El buscador cubre **nombre + apellidos + email + teléfono**;
el panel es para todo lo demás.

Los filtros son campos del **motor de reglas** (el mismo que usan los segmentos
y las vistas guardadas): se combinan con AND/OR y se guardan en la vista. No hay
un panel a medida — cada filtro es un campo del registro
`app/services/segments/fields.py`, agrupado por `grouped_under`.

---

## Los 8 grupos

| Grupo | Filtros |
|---|---|
| **Datos del contacto** | Tags · Notas (texto libre) · Lead score · Estrellas · Estado comercial |
| **Dirección** | País · Provincia · Ciudad |
| **Propiedad y origen** | Propietario · Cuenta de origen · Creado en origen (fecha) · **Origen** (los valores salen de los datos) |
| **Pertenencia** | En segmento · En lista Brevo · En pipeline · En etapa de pipeline · Interacción con campañas |
| **Actividad reciente** | Fecha última interacción · Días sin contactar · Con tareas · Con emails · Con notas · En workflow |
| **Llamadas** | Resultado · Acción posterior · Duración · Fecha · **Con llamadas / Sin llamadas** (CRM-1.6) |
| **ERP y FACTUSOL** | Vinculado a FACTUSOL · Con pedidos ERP |
| **Empresa, parque y compras** | Ver más abajo: hablan de la **empresa** del contacto |

> **«Con llamadas / Sin llamadas»** (CRM-1.6): presencia de llamadas registradas.
> Para filtrar por **fecha** de llamada está «Fecha de llamada» (antes de /
> después de / periodo), que ya existía.

---

## La UI del panel (CRM-1.6)

El panel envuelve el constructor de reglas con tres capas de UX (sin
reescribirlo):

- **Secciones colapsables.** Los 7 grupos salen como un accordion. Cada cabecera
  lleva su **contador de filtros aplicados** —gris `(0)`, en azul si hay alguno—
  y despliega los campos de la sección como botones «+ Campo» para añadirlos.
  Por defecto abren **«Datos del contacto»** y **«Llamadas»**; el resto cerradas.
- **Chip stack.** Arriba, cada filtro aplicado es un chip `campo · comparador ·
  valor` con una **×** para quitarlo, más **«Limpiar todo»**.
- **Selector agrupado.** El desplegable de campo del constructor separa los
  campos por sección (`optgroup`).
- **Persistencia.** El estado abierto/cerrado de las secciones se guarda en
  `localStorage['crm-contacts-filter-sections']`. Los **filtros** aplicados NO
  se persisten ahí: para eso están las **Vistas** guardadas del CRM.

El **editor de «En workflow»** es un desplegable de los workflows **activos**
(`GET /api/workflows/active`), no un campo donde pegar el UUID a mano.

> Esta capa es genérica del `EntityFilterBuilder`, así que las demás entidades
> que lo usan (empresas, condiciones de workflow) ganan la misma UX.

**En las condiciones de los workflows** vale cualquier campo de este filtro.
Los de la ficha (nombre, estado, lead score, etiquetas…) los evalúa el motor
de workflows directamente; los que viven en otras tablas —«En segmento», «En
pipeline», «Etapa», «En workflow», llamadas, tareas, notas, emails, pedidos,
listas de Brevo…— se compilan con este mismo motor de filtros y se pregunta a
la base de datos si el contacto cumple la regla
(`app/workflows/conditions.py`, `_cumple_en_bd`). También van al motor los
operadores que el motor de workflows no sabe evaluar en memoria aunque el
campo sea de la ficha («hace más de N días», «no en los últimos N días», un
«entre» de fechas, «etiqueta contiene»). Antes daban **400 al activar**
(«unknown field: in_segment») y en ejecución salían siempre «No». Al activar
se comprueba que el motor acepta cada regla (comparador y valor), y en «En
segmento» también las reglas de los segmentos que nombra.

- Dentro de una ejecución, **«En workflow» no cuenta la ejecución en curso**:
  «¿ya pasó por este workflow?» es «no» la primera vez.
- El **estimador de audiencia** y el **cron** cuentan o recorren los contactos
  con **una sola consulta** cuando el filtro tiene campos de otras tablas y
  está entero en el formato del editor; los filtros de siempre se siguen
  evaluando contacto a contacto, como hasta ahora.
- Si la base de datos falla al evaluar, la condición no se cumple y queda en
  el log: la evaluación nunca lanza.

---

## Qué se retiró del panel (y por qué)

Se **quitaron del selector de filtros**, pero **siguen** como columnas de la
tabla y como parámetros del backend — solo desaparecen de la lista de filtros.

| Retirado | Motivo |
|---|---|
| Nombre completo, Nombre, Apellidos, Email, Teléfono | Ya los cubre el buscador general `q=` |
| Empresa, Email válido, Cargo | Decisión de producto: fuera del panel |
| ID del contacto | UUID, solo para depurar |
| LinkedIn, Web personal | URLs, no se filtra por ellas |
| Asignado a, Responsable (primary) | Cubierto por «Propietario» |
| Sistema de origen | Cubierto por «Cuenta de origen» |
| Fecha creación, Última modificación, Última mod. externa, Última mod. en origen | Se deja solo «Creado en origen» |
| Código postal, Calle, Región | Dirección simplificada a País/Provincia/Ciudad |
| Consentimiento marketing, Activo | Fuera del panel principal |

---

## Filtros nuevos de «Actividad reciente»

Todos son **datos locales del CRM** (llamadas, notas, tareas, emails, workflows):
no consultan FACTUSOL, así que no añaden latencia.

| Filtro | Responde a | Cómo compila |
|---|---|---|
| **Fecha última interacción** | «Contactos con actividad en tal ventana» | EXISTS una llamada/nota/email/tarea con fecha en el rango |
| **Días sin contactar** | «Leads dormidos» | `≥ N` = sin ninguna interacción más reciente que hace N días (incluye a los **nunca** contactados); `≤ N` = con interacción en los últimos N días |
| **Con tareas** | «Follow-up pendiente» | Pendientes / **Vencidas** (pendiente + `due_at` pasada) / Sin tareas / Cualquiera |
| **Con emails** | «Con quien se ha escrito» | EXISTS un email en la ventana de fechas |
| **Con notas** | «Documentados o no» | Con nota / Sin nota |
| **En workflow** | «Metidos en un flujo» | EXISTS un `workflow_run` para ese workflow |

> **«Fecha última interacción» es «tiene una interacción en la ventana»**, no «la
> más reciente cae en la ventana». Para «lleva X sin contactar» está **Días sin
> contactar**, que es el que mira la ausencia de actividad reciente.

---

## Filtros nuevos de «ERP y FACTUSOL»

Vía la **empresa** del contacto (`contacts.company_id`) y el ERP local.

| Filtro | Responde a | Cómo compila |
|---|---|---|
| **Vinculado a FACTUSOL** | «Ya es cliente contable» | La empresa del contacto tiene `factusol_company_id`. En «No» entran también los contactos sin empresa |
| **Con pedidos ERP** | «Trabajo en el taller» | EXISTS un `order` de su empresa: En cola / Embalado (estado de preparación) · En tránsito / Entregado (estado de transporte) · Cualquiera |

### Diferidos a CRM-1.6b (consulta viva a FACTUSOL)

Dos filtros necesitan preguntar a FACTUSOL **en cada carga del listado** y no hay
datos locales que reflejen su estado:

- **Con proformas** (por estado: activas/aceptadas/rechazadas) → `F_PRE`.
- **Con facturación** (importe mínimo, rango de fechas) → `F_FAC`.

Siguen **diferidos** (ahora a CRM-1.6b): meter una `CargaTabla` por cada listado,
sobre miles de contactos y sin poder validarlo contra FACTUSOL real, es un riesgo
de latencia en la ruta caliente. Irán con su estrategia de caché (una sola
lectura por request + tope de resultados si el set es grande). El vínculo
`factusol_linked` sí está porque es dato local.

---

## «Empresa, parque y compras» (1/11/2026)

Reglas sobre lo que la **empresa** del contacto tiene y compra. Un contacto sin
empresa no entra en ninguna, ni en positivo ni en negativo. Las de parque y
compras solo miran empresas **vinculadas a un cliente de FACTUSOL**: de una
empresa que solo tiene pedidos del ERP no se sabe el parque ni el histórico, así
que no cumple «0 máquinas», «sin baja» ni «gasto ≤ …».

| Subgrupo | Filtros |
|---|---|
| Ficha | Sector · Con CIF/VAT · País / Región / Provincia / Ciudad **(empresa)** · Vinculada a FACTUSOL |
| Parque | Gama · Modelo · Nº de máquinas · Meses de la más nueva / de la más vieja · Tiene alguna baja |
| Compras | Dormido de… · Hueco de… (una gama o «cualquiera») · Gasto total · Última compra · Meses sin comprar · Artículo · Familia · Clase · **Nunca ha comprado** de la clase |

- **Mismas cuentas que «Dormidos y huecos»**: dormido y hueco salen del mismo
  informe, umbrales incluidos. Si se cambia un umbral, cambian en los dos sitios.
- **Modelo y artículo con sus equivalentes**: «ha comprado L1» encuentra también
  a quien compró un código equivalente (el mismo modelo con otro código), y un
  equivalente de una máquina marcada cuenta como máquina.
- **Familia de ahora**: si un artículo cambió de familia, cuenta la del
  ejercicio más reciente en que la empresa lo compró.
- **«Nunca ha comprado…»** mira solo las empresas que han comprado algo: para
  las que no han comprado nada está «YA CLIENTE».
- **Opciones leídas de los datos**: gamas, modelos, familias, clases y orígenes
  se leen de lo que hay, no están escritos a mano.
- **Tablas precalculadas** (`crm_empresa_*`): se borran y se rehacen enteras
  tras cada carga del histórico (también la nocturna), tras los cambios de Bart
  en el histórico o el parque (a los 45 s del primero; los que llegan mientras
  tanto van en el mismo pase) y con «Recalcular ahora».
  Encima del panel se ve de cuándo son los datos y, si el último recálculo
  falló, se dice. No escriben en ninguna tabla de Bart.
- Valen igual en la pantalla, en los segmentos, en Brevo (los segmentos
  dinámicos se recalculan en cada sincronización; los estáticos son una foto) y
  en los workflows.

### Etiquetas automáticas

- **YA CLIENTE**: contactos de una empresa con gasto en el histórico (> 0) o
  con pedidos en el ERP.
- **ORIGEN WOO**: contactos con origen `woocommerce`.

Las mantiene el recálculo: las pone a quien cumple y quita **solo las que puso
él** cuando deja de cumplir. Una puesta a mano no se quita nunca. Si alguien
quita a mano una que el contacto merece, queda apuntado («quitada a mano») y
el recálculo no se la vuelve a poner; si alguien se la vuelve a añadir a mano,
el apunte se borra y vuelve al régimen normal. Encima del panel se ve cuántos
contactos tienen ese apunte.

La cabecera dice «YA CLIENTE N contactos (M empresas)»: los contactos que llevan
la etiqueta ahora y las empresas de esos contactos (1/11/2026). Es la misma
cuenta que la del script del despegue. Una empresa que ha comprado pero no tiene
ningún contacto no entra, porque nadie suyo lleva la etiqueta.

---

## Un contacto por empresa (1/11/2026)

Encima de la lista hay dos opciones:

- **Todos los contactos de las empresas que cumplen.** Es lo de siempre.
- **Un solo contacto por empresa:** el primero de cada empresa según el orden de
  preferencia.

**Por defecto** va «uno por empresa» solo cuando las reglas miran la empresa
(empresa, parque o compras). Las vistas de siempre siguen con «todos». Lo que se
elija se guarda con «Guardar» en la vista. Al crear un segmento o mandar a Brevo
desde una vista, va lo que hay en pantalla (aunque no se haya guardado).
Duplicar un segmento lo conserva.

**Orden de preferencia.** Cualquier criterio se puede quitar o mover. Si dos
contactos empatan en uno, decide el siguiente; si empatan en todos, el id.

1. **Contacto principal**: el marcado en la ficha de empresa (pestaña
   «Contactos», casilla «Principal», uno por empresa). Lo marca quien puede
   editar la empresa. Si el contacto cambia de empresa, o su empresa se fusiona
   en otra, deja de ser principal: la nueva ya tiene el suyo. El despegue de
   dominios compartidos no toca a un contacto principal: cuenta como puesto a
   mano.
2. **Con email válido.**
3. **Interacción más reciente**: llamada, nota, email o tarea.
4. **El más antiguo.**

**Por qué salió cada uno.** Bajo el nombre se dice el criterio real: se compara
al elegido con el segundo de su empresa y se nombra el primer criterio en el que
gana. Si es el único que cumple, se dice. Nunca pone «principal» si no hay
ninguno marcado.

**Recuentos.** Se muestran como «N empresas · M contactos · K seleccionados».
Con «uno por empresa», M va seguido de «de los X que cumplen». Los contactos
sin empresa no tienen por quién elegirse: en «uno por empresa» quedan fuera, y
se cuentan aparte.

**Brevo.** La lista se recalcula en cada sincronización: es dinámica, no una
foto. Con «uno por empresa», el contacto se elige entre los que tienen email y
están activos: si el preferido no tiene email, va el siguiente de su empresa.

**Columnas y CSV.** En «Columnas» están, del grupo «Empresa, parque y compras»:
Empresa, Sector, Gamas, Nº de máquinas, meses de la más nueva y de la más vieja,
Gasto total, Última compra, Meses sin comprar, Dormido en y Hueco en. Las de un
número o una fecha se pueden ordenar. Las cifras del ERP (parque, gasto,
compras) las ve a partir del rol usuario; el lector ve solo la empresa y el
sector. El CSV de la selección sale con las columnas que se ven, con su nombre
en pantalla y en el orden de la lista (menos «Elegido por», que depende de la
pantalla). Lo que empieza como una fórmula (`=`, `+`, `-`, `@`) sale con un
apóstrofo delante para que la hoja de cálculo no lo ejecute. Los teléfonos y
los números se quedan igual.

**El cuadre** (`scripts/cuadre_huecos.py`, solo lee) explica por qué la pestaña
«Dormidos y huecos» y la lista de contactos no dan el mismo número. La pestaña
cuenta clientes de FACTUSOL y la lista, contactos de empresas de BoHub. Entre
una y otra:

- clientes sin empresa en BoHub;
- códigos de cliente repetidos en varias empresas;
- empresas sin contactos;
- la antigüedad: la pestaña mira la máquina más reciente de esa gama; las reglas
  de meses, todas las máquinas de la empresa.

## Rendimiento

Todos los filtros son EXISTS sobre tablas **locales** indexadas
(`call_logs.contact_id`, `notes.contact_id`, `orders.company_id`, etc.), así que
no cambian el coste del listado de forma apreciable. Las de «Empresa, parque y
compras» van contra las tablas precalculadas: con 17.558 contactos y tres
reglas de parque, ~30 ms la página (medido en local con figurantes). Con «uno
por empresa» y 60.000 interacciones, unos 200 ms con esas tres reglas y algo
más de medio segundo sin ninguna regla. Los dos filtros que tocarán
FACTUSOL (CRM-1.6b) llevarán su propia salvaguarda de caché + tope.
