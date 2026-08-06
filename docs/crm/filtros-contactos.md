# Panel de filtros de contactos (CRM-1.5)

`/contacts` tiene un buscador general (`q=`) **encima** y, debajo, el panel de
filtros avanzados. El buscador cubre **nombre + apellidos + email + teléfono**;
el panel es para todo lo demás.

Los filtros son campos del **motor de reglas** (el mismo que usan los segmentos
y las vistas guardadas): se combinan con AND/OR y se guardan en la vista. No hay
un panel a medida — cada filtro es un campo del registro
`app/services/segments/fields.py`, agrupado por `grouped_under`.

---

## Los 7 grupos

| Grupo | Filtros |
|---|---|
| **Datos del contacto** | Tags · Notas (texto libre) · Lead score · Estrellas · Estado comercial |
| **Dirección** | País · Provincia · Ciudad |
| **Propiedad y origen** | Propietario · Cuenta de origen · Creado en origen (fecha) |
| **Pertenencia** | En segmento · En lista Brevo · En pipeline · En etapa de pipeline · Interacción con campañas |
| **Actividad reciente** | Fecha última interacción · Días sin contactar · Con tareas · Con emails · Con notas · En workflow |
| **Llamadas** | Resultado · Acción posterior · Duración · Fecha · **Con llamadas / Sin llamadas** (CRM-1.6) |
| **ERP y FACTUSOL** | Vinculado a FACTUSOL · Con pedidos ERP |

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

## Rendimiento

Todos los filtros son EXISTS sobre tablas **locales** indexadas
(`call_logs.contact_id`, `notes.contact_id`, `orders.company_id`, etc.), así que
no cambian el coste del listado de forma apreciable. Los dos filtros que tocarán
FACTUSOL (CRM-1.6b) llevarán su propia salvaguarda de caché + tope.
