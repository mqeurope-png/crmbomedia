# Auditoría de permisos: qué ve y qué puede hacer cada rol (30/10/2026)

Hecha leyendo el código de `main` (#581), sin clicar ni tocar datos. Para cada pantalla y acción se
comparan tres cosas: **¿sale en el menú?**, **¿deja entrar la pantalla?** y **¿responde la API?**
Donde no coinciden hay un hallazgo. Las rutas de fichero son relativas a `backend/app/` o a
`frontend/src/app/` según se indica; las líneas son de `main` a 30/10/2026.

> Esto es un inventario, no un diseño. Dice qué falta; el modelo de permisos se decide con Bart.
> Cuando se arregle algo, márcalo aquí (columna «Estado») para que el documento siga diciendo la verdad.

## Resumen

| Grupo | Hallazgos | De ellos, graves |
|---|---|---|
| 1 · Filtraciones (la API responde a quien no debería) | 21 | **4** |
| «Solo lectura escribe» | 22 endpoints (5 temas) | — |
| 2 · Permisos que no existen (usar ≠ configurar) | 17 | — |
| 3 · Cosmética (menú ≠ pantalla ≠ API, sin riesgo) | 19 | — |

**Las cuatro graves, en una línea cada una** (verificadas a mano en el código, además de por la
lectura por zonas):

1. **Asuntos de correo de todos los buzones, con Solo lectura.** `POST /api/entities/email_thread/search`.
2. **Un Usuario se pone cualquier remitente y envía como otro.** Deshace el control del #570.
3. **El ERP · Comercial modifica pedidos web**, que no debería ni ver (masivas que escriben antes de
   comprobar, Genei, excepciones…).
4. **El Responsable cambia el estado de pago, crédito, transporte y factura de los pedidos** sin rol ERP.

No hay **escalada de rol** (cambiar roles, roles ERP, contraseñas o remitentes ajenos es solo de
admin) ni **credenciales** en respuestas a no-admin.

### Decisiones ya tomadas (no son hallazgos)

- **Usuario y Responsable del CRM ven el dato de oficina del ERP** (facturas, PDF, fichas fiscales,
  cobros, proformas, ofertas, «Quién compró qué»): lo tienen por `erp.office.view`
  (`erp/capabilities.py:99-109`). **Bart confirmó el 30/10 que es intencionado.** Lo que queda es que
  el menú no se lo enseña (cosmética C-3).
- **Lectura de todos los contactos y empresas por cualquier rol del CRM**: el código lo hace a
  propósito (filtros «asignados a mí» opcionales, conmutador mío/equipo). No lo cuento como filtración.
  Ver «No lo sé» sobre el CSV.

## Cómo decide BoHub hoy (lo que explica casi todo)

- **Dos mecanismos que no se hablan.** El CRM compara el **rol principal** con una escalera de niveles
  (`core/auth.py:15-30, 112-128`: viewer=0, user=1, manager=2, admin=3; los roles ERP como principal,
  0 o 1). El ERP suma **capacidades** de rol principal + roles ERP añadidos (`erp/capabilities.py:166-177`).
  Consecuencia: **los roles ERP añadidos a un rol de CRM no cambian nada en el CRM**, y un combo
  «Usuario + ERP · X» es un Usuario en el CRM y la unión de capacidades en el ERP.
- **El ámbito CRM** (`require_crm_access`, `crm_scope_monolith`, `core/auth.py:145-256`) bloquea a quien
  tiene un rol ERP como **principal**, salvo empresas y contactos para Comercial y Pedidos.
- **La máquina de estados de pedidos va por rol, no por capacidad** (`erp/state_machine/engine.py:156-163`,
  `definitions.py:43`): su grupo «oficina» es admin, manager, pedidos y comercial.
- **Dentro de cada endpoint** hay comprobaciones de propiedad (`services/ownership.py`: admin u owner;
  lo global, solo admin). La guarda del decorador no es toda la verdad.
- **Frontend**: el menú usa capacidad si la entrada la declara y, si no, el rol principal
  (`lib/appNav.tsx:296-318`). No hay `middleware.ts` ni guard común: **cada página se protege sola, y la
  mayoría no se protege** (carga y enseña lo que la API devuelva).
- **Usuario y Responsable tienen las mismas capacidades ERP** (`capabilities.py:108-109`), pero tres
  sitios los tratan distinto: el docstring dice que el Responsable es «como ERP Pedidos»
  (`capabilities.py:27`), la máquina de estados le deja escribir (`definitions.py:43`) y la pantalla del
  taller le deja entrar (`frontend erp/sat/layout.tsx:142`).

## Tabla rol × pantalla × acción

Columnas: **A** Administrador · **R** Responsable · **U** Usuario · **SL** Solo lectura ·
**Co / Pe / Ta** ERP · Comercial / Pedidos / Taller como rol **principal**.
Cada casilla: **menú / pantalla / API**. `✓` sí · `✗` no · `◐` en parte (ver nota) ·
`→` la pantalla redirige · `!` la pantalla enseña botones que la API rechaza.
Co/Pe/Ta en pantallas del CRM: la aplicación les manda a `/erp` y la API les da 403 (salvo empresas
para Co/Pe): se resume como «—».

### CRM

| Pantalla · acción | A | R | U | SL | Co | Pe | Ta | Hallazgo |
|---|---|---|---|---|---|---|---|---|
| Contactos · ver lista y ficha | ✓✓✓ | ✓✓✓ | ✓✓✓ | ✓✓✓ | — | — | — | decidido |
| Contactos · crear / editar | ✓✓✓ | ✓✓✓ | ✓✓✓ | ✓!✗ | — | — | — | C-14 |
| Contactos · notas de otros: editar / borrar | ✓✓✓ | ✓✓✓ | ✓✓✓ | ✗ | — | — | — | F-11 |
| Contactos · exportar CSV | todos | todos | solo suyos | ✗ | — | — | — | «No lo sé» |
| Tags · aplicar a un contacto | ✓✓✓ | ✓✓✓ | ✓✓✓ (crea tags nuevos) | ✗ | — | — | — | P-1 |
| Tags · gestionar catálogo | ✓✓✓ | ✓✓✓ | ✓!✗ | ✓!✗ | — | — | — | P-1, C-5 |
| Pipelines · mover / añadir / quitar contactos | ✓✓✓ | ✓✓✓ cualquier pipeline | ✓✓✓ cualquier pipeline | ✓!✗ | — | — | — | F-6, P-2 |
| Pipelines · crear / configurar los propios | ✓✓✓ | ✓✓✓ | ✓✓✓ | ✓✓**✓** | — | — | — | SL-2 |
| Pipelines · configurar los del equipo | ✓✓✓ | ✓!✗ | ✓!✗ | ✓!✗ | — | — | — | P-2, C-4 |
| Segmentos · crear / editar los propios | ✓✓✓ | ✓✓✓ | ✓✓✓ | ✓!✗ | — | — | — | P-3 |
| Vistas guardadas · crear (también compartidas) | ✓✓✓ | ✓✓✓ | ✓✓✓ | ✓✓**✓** | — | — | — | SL-1 |
| Tareas · ver las de todos | ✓✓✓ | ✓✓✓ | ✓✓✓ | ✓✓✓ | — | — | — | F-15 |
| Empresas · ver | ✓✓✓ | ✓✓✓ | ✓✓✓ | ✓✓✓ | API ✓ | API ✓ | ✗ | — |
| Empresas · crear / editar | ✓✓✓ | ✓✓✓ | ✓✓✓ | ✓!✗ | API ✓ | API ✓ | ✗ | P-12 |
| Empresas · borrar / archivar / fusionar | ✓✓✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | — |
| Reglas de asignación · ver / editar | ✓✓✓ | ✓✓✓ | ✗✓✓ / ✗ | ✗ | — | — | — | C-10 |
| Búsqueda genérica de entidades (`/api/entities/*`) | API ✓ | API ✓ | API ✓ | API ✓ | — | — | — | **F-1** |

### Correo, workflows, marketing

| Pantalla · acción | A | R | U | SL | Co/Pe/Ta | Hallazgo |
|---|---|---|---|---|---|---|
| Correo · mi bandeja | ✓✓✓ | ✓✓✓ | ✓✓✓ | ✓✓✗ | — | C-12 |
| Correo · bandejas de otros | ✓✓✓ | ✓✓✓ | ✗ (pero F-1, F-7) | ✗ | — | P-6 |
| Correo · elegir mis remitentes | ✓✓✓ | API ✓ | API **✓** | ✗ | — | **F-2** |
| Correo · marcar leído / responder en hilo ajeno | ✓ | ✓ | API ✓ por id | ✗ | — | F-17 |
| Asistente IA · borrador para cualquier contacto | ✓ | ✓ | ✓ (ve correo ajeno) | ✗ | — | F-7 |
| Workflows · ver | ✓✓✓ | ✓✓✓ | ✓✓✓ | ✓✓✗ | — | C-12 |
| Workflows · crear y activar los propios | ✓✓✓ | ✓✓✓ | ✓✓✓ | ✓!✗ | — | P-4 |
| Workflows · editar los del equipo | ✓✓✓ | ✓!✗ | ✓!✗ | ✗ | — | C-3 |
| Workflows · aplicar a un contacto ajeno | ✓ | ✓ | ✓ | ✗ | — | F-12 |
| Workflows · ver ejecuciones de workflows ajenos | ✓ | ✓ | API ✓ | ✗ | — | F-16 |
| Plantillas de correo · editar carpetas «equipo» | ✓ | ✓ | ✓ | ✗ | — | P-7 |
| Marketing · ver | ✓✓✓ | ✓✓✓ | ✓✓✓ | ✓✓✗ | — | C-12 |
| Marketing · crear / enviar campañas | ✓✓✓ | ✓✓✓ | ✓!✗ | ✗ | — | P-8, C-6 |

### ERP

Combos: **U+Co** = Usuario + ERP · Comercial; **U+Ta** = Usuario + ERP · Taller. Un combo vive en modo
CRM (manda el rol principal), así que su menú ERP aparece dentro del CRM.

| Pantalla · acción | A | R | U | SL | Co | Pe | Ta | U+Co | U+Ta | Hallazgo |
|---|---|---|---|---|---|---|---|---|---|---|
| Bandeja · ver pedidos | ✓✓✓ | ✓✓✓ | ✓✓✓ | ✗ | ✓✓✓ sin web¹ | ✓✓✓ | ✓✓✓ | ✓✓✓ | ✓✓✓ | F-3 |
| Pedido · cambiar estado (pago, crédito, transporte, factura) | ✓ | API **✓** | ✗ | ✗ | ✓ | ✓ | arcos taller | ✓ | arcos taller | **F-4** |
| Pedido · completar / anular / emitir | ✓ | ✗ | ✗ | ✗ | ✓ sin web¹ | ✓ | muestras | ✓ | muestras | F-3 |
| Pedidos web · traer / masivas | ✓ | ✗ | ✗ | ✗ | API **✓** (no debería) | ✓ | ✗ | API ✓ | ✗ | **F-3** |
| Taller (SAT) · cola | ✓✓✓ | ✓✓✓ | ✓→✓ | ✗ | ✓→✓ | ✓✓✓ | ✓✓✓ | ✓→✓ | ✓→✓ | C-1 |
| Excepciones · ver / gestionar | ✓✓✓ | ✓!◐ | ✓!◐ | ✗ | ✓✓✓ | ✓✓✓ | ✗✓✓ | ✓✓✓ | ✓!◐ | C-2, F-8 |
| Documentos · Proformas · Ofertas · Plantillas · ver | ✓✓✓ | ✗✓✓ | ✗✓✓ | ✗ | ✓✓✓ | ✓✓✓ | ✗ | ✓✓✓ | ✗✓✓ | decidido; C-3b |
| Documentos · Proformas · Ofertas · crear / enviar | ✓ | ✗ | ✗ | ✗ | ✓ | ✓ | ✗ | ✓ | ✗ | — |
| Quién compró qué · ver y CSV | ✓✓✓ | ✓✓✓ | ✓✓✓ | ✗ | ✓✓✓ | ✓✓✓ | ✗ | ✓✓✓ | ✓✓✓ | decidido |
| Cobros FACTUSOL · registrar | ✓ | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ | — |
| Seguimiento · Cuadre | ✓ | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ | — |
| Conciliación · Configuración (guardar) · Integraciones | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | — |
| Configuración ERP · ver | ✓✓✓ | ✗✓✓ | ✗✓✓ | ✗ | ✗✓✓ | ✗✓✓ | ✗✓✓ | ✗✓✓ | ✗✓✓ | C-9, F-8 |

¹ «sin web»: los endpoints de la ficha y la bandeja filtran los pedidos web al Comercial; **los de F-3 no**.

### Administración

| Pantalla · acción | A | R | U | SL | Co/Pe/Ta | Hallazgo |
|---|---|---|---|---|---|---|
| Usuarios, roles, contraseñas, remitentes de otros | ✓✓✓ | ✗ | ✗ | ✗ | ✗ | — (sin escalada) |
| Integraciones · ver cuentas (sin secretos) | ✓✓✓ | ✗✓✓ | ✗✓◐ | ✗✓◐ | ✗ | C-11 |
| Integraciones · crear, claves, lanzar sync | ✓ | ✗ | ✗ | ✗ | ✗ | — |
| Copias, auditoría, campos personalizados, Gmail backfill | ✓ | ✗ | ✗ | ✗ | ✗ | — |
| Formularios web | ✓ | ✓ | ✗ | ✗ | ✗ | P-10 |
| Google · estado de la cuenta y calendarios | ✓ | ✓ | ✓ | API ✓ | API ✓ | F-19 |

## Qué ve exactamente un «Usuario» del CRM sin rol ERP (el caso de Bart)

- **Menú, modo CRM:** Dashboard, Contactos, Tareas, Correo, Empresas, Pipelines, Segmentos, Marketing,
  Tags, Workflows, Tutorial; y del ERP: Pedidos (con Excepciones), Quién compró qué y Taller (SAT).
- **Puede trabajar:** todos los contactos y empresas (leer, editar, asignarse cualquiera), sus tareas,
  su correo, aplicar tags, mover contactos en **cualquier** pipeline, crear pipelines, segmentos y
  workflows propios, ver la bandeja de pedidos y su ficha, Quién compró qué, y por URL (no por menú)
  documentos, proformas, ofertas y plantillas del ERP (decidido).
- **Ve y no puede (botones que dan 403):** gestionar el catálogo de tags, configurar pipelines y
  workflows del equipo, crear campañas de marketing, gestionar excepciones.
- **Ve en el menú y le echa:** Taller (SAT) (C-1).
- **No debería poder y puede:** asuntos de correo de todos (F-1), ponerse cualquier remitente (F-2),
  pipelines privados ajenos (F-6), notas ajenas (F-11), correo ajeno a través del asistente (F-7).

### Qué cambia al añadirle cada rol ERP

- **+ ERP · Comercial**: en el ERP gana crear, aprobar y completar pedidos no web, albaranes, facturas,
  proformas, ofertas, correos y envíos. En el CRM, **nada**. Como sigue siendo Usuario, conserva
  `orders.view_web` y **ve y opera pedidos web** (la restricción del Comercial deja de aplicar).
  Taller (SAT) en el menú, pero la pantalla le echa (C-1).
- **+ ERP · Pedidos**: lo anterior más cobros, seguimiento y cuadre. Taller le sigue echando (C-1).
- **+ ERP · Taller**: en el ERP gana preparar, embalar, enviar y tracking, y **conserva la oficina** del
  Usuario (facturas, fichas fiscales: coherente con la decisión de Bart, pero un operario del taller con
  cuenta de Usuario deja de ser «taller»). La pantalla del taller **le echa** aunque tiene todas sus
  capacidades (C-1).
- **Solo lectura + ERP · Comercial**: en el ERP es un Comercial completo (escribe pedidos); en el CRM
  sigue sin poder crear empresas aunque su capacidad ERP lo diga (P-12).

### Roles que en la práctica son el mismo

- **Usuario ≡ Responsable en el ERP** salvo dos excepciones accidentales: el Responsable entra al taller
  (lista fija `erp/sat/layout.tsx:142`) y escribe estados de pedido (F-4).
- **Usuario + ERP · X ≡ Usuario en todo el CRM**: ningún endpoint del CRM mira los roles ERP añadidos.
- **Comercial + Taller** (en cualquier orden) gana `orders.view_web`: deja de ser «Comercial sin web».
- **Solo lectura escribe** en 22 sitios (SL-1…SL-5), así que no es un rol de solo lectura.

## 1 · Filtraciones

Ordenadas por gravedad. **Estado** se rellena al arreglar.

| # | Gravedad | Qué se saca o se hace, y con qué rol | Dónde | Estado |
|---|---|---|---|---|
| **F-1** | ⚠️ grave | Con **Solo lectura** (y cualquier rol del CRM): asunto, contacto, quién lo inició, carpeta, estado, fechas y nº de mensajes de **los hilos de todos los buzones**, buscando por texto del asunto. El buzón real limita a los propios y exige Usuario. Mismo endpoint: plantillas y campañas de Brevo con Solo lectura. | `api/entities.py:168-243` (guarda `require_viewer`, sin filtro por usuario: `_visibility_clauses` 142-149); entidad en `services/entities/registry.py:186-195`; campos en `services/entities/fields_email.py` | Arreglado 30/10 (PR de las graves) |
| **F-2** | ⚠️ grave | Un **Usuario** se marca como remitente cualquier dirección y envía como un compañero o una dirección corporativa. Se salta «Remitentes», que es solo de admin (#570). | `api/emails.py:250-274` llama a `remitentes.guardar` **sin** `en_gmail` (`services/remitentes.py:198-300`); el envío solo mira esas preferencias (`emails.py:351-365`). Admin: `api/user_senders.py:47-59` | Arreglado 30/10 (PR de las graves) |
| **F-3** | ⚠️ grave | El **ERP · Comercial** (y Solo lectura + Comercial) **escribe** sobre pedidos web: marca procesados todos los pedidos de una tienda, completa y aprueba en bloque (escribe **antes** de comprobar y luego da 403), crea, paga y borra envíos Genei, resuelve incidencias de envío, vincula documentos, gestiona excepciones. **Lee** su línea de vida, ficheros de envío (etiquetas, fotos), historial e incidencias del taller, cancelados y excluidos. | `erp/api/orders.py:3446-3474` (filtro por tienda 3461), `2756-2818`, `2821-2875`; `erp/api/genei.py:96-100` y usos 397-1090; `erp/api/sat.py:1012-1022, 782-830, 929-1010, 1045-1077`; `erp/api/factusol.py:601-631`; `erp/api/exceptions.py:358-395`; `erp/api/order_timeline.py:72-82`; `erp/api/shipping.py:304-316`; `orders.py:1184-1196` | Arreglado 30/10 (PR de las graves) |
| **F-4** | ⚠️ grave | El **Responsable** (sin rol ERP) marca pedidos como pagados o con pago parcial, **aprueba crédito B2B** (ERP · Pedidos no puede), entregado, devuelto, incidencia de transporte, pide o reintenta la factura. La ficha le pinta los botones. Si `auto_mark_paid_when_order_paid` está activo, puede acabar en un cobro en FACTUSOL. | `erp/api/orders.py:1922-1931` (solo `erp.access`); `erp/state_machine/definitions.py:43` (`manager` en «oficina»), arcos 85-232; `engine.py:156-163, 212` | Arreglado 30/10 (PR de las graves) |
| F-5 | media-alta (según configuración) | **Sin autenticar**: si `BREVO_WEBHOOK_SECRET` está vacío, cualquiera desde Internet da de baja un contacto o marca su email como no válido sabiendo su email. El ejemplo de producción y la guía dicen dejarlo vacío. | `api/webhooks.py:44-98`; `.env.production.example:129`; `docs/integrations-brevo.md:83`; efecto en `integrations/brevo/webhooks.py:274-363` | |
| F-6 | media | Pipelines **privados** de otro usuario: cualquier rol del CRM lee su embudo, informe y estancados; un Usuario mete, mueve y quita contactos en ellos. | lectura `api/routes.py:4965-5090` (sin `can_user_see_resource`), id desde `routes.py:4779-4828`; escritura `routes.py:4838-4940`, `api/call_logs.py:280-297`, `api/bulk.py:328-365` | |
| F-7 | media | Asistente IA: un Usuario pide un borrador para **cualquier** contacto y el contexto lee todos los hilos de ese contacto y su empresa, de cualquier buzón; devuelve asunto, fecha e id y el texto puede citarlos. | `api/asistente.py:295-327`; `services/asistente/historico.py:284, 312`; `contexto.py:870-895`; `borradores.py:106` | |
| F-8 | media | El **Taller** (sin `office.view`) ve dato de oficina: nº de factura y estado de cobro en bandeja y ficha, PDF del albarán valorado, precio de coste de artículos, ajustes del ERP (contrapartidas, menciones, plantillas, hoja de Drive), lista de excepciones con nombre de cliente. | `erp/api/orders.py:279-466, 4240-4245, 4354-4360`; `erp/api/factusol.py:2775` + `integrations/factusol/quotes.py:377-383`; `erp/api/exceptions.py:722-731, 358-379` | |
| F-9 | media | Combo **Usuario + Taller**: el operario recupera la oficina (facturas, fichas fiscales, CSV de compradores) y puede adjuntar factura en «enviar al taller». Consecuencia de la unión de capacidades y de la decisión sobre `office.view`. | `erp/capabilities.py:166-177`; `erp/order_email.py:113-130` | |
| F-10 | media | El **CSV limitado a «mis contactos»** del Usuario se rodea paginando `GET /api/contacts` o `POST /api/contacts/search`, que devuelven lo mismo de todos. Solo es filtración si el límite pretendía impedir sacar la cartera ajena (ver «No lo sé»). | `api/bulk.py:624-632` frente a `api/routes.py:1657, 1858` | |
| F-11 | media | Un Usuario edita y borra **notas de otros autores** (las llamadas sí limitan al autor). | `api/contact_notes.py:36-43, 139-175` | |
| F-12 | media | Un Usuario aplica un workflow (propio o global) a un contacto **ajeno**; el disparo automático sí respeta la propiedad. Con un workflow propio que reasigna o envía correo con remitente libre, actúa sobre contactos de otros. No da nada nuevo frente a «asignarse cualquier contacto» (intencionado), pero lo amplía a envío. | `api/workflows.py:1570-1579`; `workflows/dispatcher.py:238-258`; `workflows/steps.py:508-536, 897-907` | |
| F-13 | media | Un Usuario se pone de responsable de cualquier contacto y luego lo exporta o lo trata en bloque: rodea en dos pasos el límite de masivas y exportación. Intencionado según el código («un comercial puede auto-asignarse»), por eso no es grave. | `api/routes.py:2545, 2709-2733`; `api/contact_assignments.py:12-16, 97-196` | |
| F-14 | media-baja | Webhook genérico `/api/webhooks/{system}/{account_id}` **sin firma**: guarda hasta 64 KB por petición en registros de sincronización y auditoría. | `api/webhooks.py:273-343` | |
| F-15 | baja | Cualquier rol del CRM lee las tareas de todo el equipo, mientras la vista «equipo» del dashboard es de A/R. | `api/tasks.py:123-235, 175-181` | |
| F-16 | baja | Un Usuario lee ejecuciones (contacto, estado, historial de pasos) de workflows **privados** ajenos. | `api/workflows.py:488-550, 1526-1547` | |
| F-17 | baja | Un Usuario marca leído o no leído cualquier hilo por id y puede responder enganchándose a un hilo ajeno. | `api/emails.py:985-1042`; `integrations/gmail/service.py:837-841` | |
| F-18 | baja | Un workflow puede usar la plantilla **privada** de otro si se conoce su id. | `workflows/steps.py:1044-1052` | |
| F-19 | baja | Rutas de Google sin ámbito CRM: roles solo-ERP y Solo lectura ven el email de la cuenta de la organización, fechas del token y calendarios. | `main.py:104`; `api/google_integrations.py:77-210, 293-318` | |
| F-20 | baja | Redirección abierta en el tracking de clics (redirige aunque el token no exista). | `email_tracking/router.py:163-204` | |
| F-21 | baja | Rutas públicas débiles: login y 2FA sin límite de intentos; límite del formulario público saltable con `X-Forwarded-For`; webhook de Gmail sin firma si no está configurado. | `api/routes.py` (login); `api/web_forms_public.py:186-191`; `integrations/gmail/webhook.py:51-57` | |

Otras incoherencias menores sin impacto real (masiva «añadir a segmento» acepta segmentos ajenos,
`bulk.py:412-441`; `assign-company` deja a un Usuario lo que el PATCH reserva a A/R,
`api/companies.py:733-765`; Solo lectura lee clasificaciones IA de leads, `erp/api/leads.py:186-195`)
quedan en las tablas de zona y se arreglan de paso.

## «Solo lectura escribe»

22 endpoints, en cinco temas. Ninguno toca datos de clientes salvo SL-3.

| # | Qué escribe Solo lectura | Dónde |
|---|---|---|
| SL-1 | Vistas guardadas de contactos y entidades: crear (**también compartidas con todo el equipo**), editar, borrar, duplicar, predeterminar | `api/routes.py:3721-3950`; `api/entity_views.py:147-315` |
| SL-2 | Pipelines propios y sus etapas: crear, desde plantilla, editar, archivar, reordenar | `api/routes.py:4348-4740, 5106` (`require_viewer`) |
| SL-3 | **Duplicar un pipeline con sus contactos** (copia asignaciones contacto-pipeline, también de pipelines del equipo) | `api/routes.py:4510-4530`; `repositories/pipelines.py:183-200` |
| SL-4 | Recuento de un segmento con `force_refresh` (un GET que guarda y audita) | `api/routes.py:5626-5653` |
| SL-5 | «Explicar segmento» con IA (gasta llamadas al modelo y audita) | `api/routes.py:5784-5789` |

Los `POST` de búsqueda (`/contacts/search`, `/segments/preview`, `/entities/*/search`) **leen**, no
escriben. En el ERP, Solo lectura sin roles ERP no llega a nada que escriba.

## 2 · Permisos que no existen

Acciones que hoy no se pueden separar porque no hay un permiso para ellas. Lo que hace falta, sin
diseñar cómo:

| # | Hoy va junto | Hace falta poder separar | Dónde se ve |
|---|---|---|---|
| P-1 | Aplicar un tag con nombre nuevo **crea** el tag en el catálogo | aplicar tag existente · crear tag · renombrar o borrar tag | `api/routes.py:3326-3530, 1551, 1639`; `api/bulk.py:511-532` |
| P-2 | Trabajar en un pipeline y configurarlo dependen solo de ser dueño; los del equipo solo el admin; cualquier Usuario trabaja en cualquier pipeline | trabajar en este pipeline · configurar pipelines del equipo (Responsable) · crear pipelines (fuera Solo lectura) | `services/ownership.py:32-49`; `api/routes.py:4348-4940` |
| P-3 | Segmentos: solo el dueño los edita, ni el admin | gestionar segmentos del equipo | `api/routes.py:5460, 5526` |
| P-4 | Cualquier Usuario crea y **activa** automatizaciones propias, y aplica cualquier workflow a cualquier contacto | usar un workflow · crear y activar automatizaciones · aplicarlas a contactos ajenos | `api/workflows.py:414-445, 945-969, 1570-1579` |
| P-5 | Usar mis remitentes y elegirlos | usar · elegir (admin, #570) | `api/emails.py:250-274` |
| P-6 | Ver y mover el correo del equipo va atado a ser Responsable | ver bandejas ajenas · gestionar hilos ajenos | `api/emails.py:595-607, 938-943`; `api/emails_mailbox.py:59-87` |
| P-7 | Carpetas de plantillas «equipo»: quien las ve las edita, renombra, borra o hace privadas | usar plantillas del equipo · gestionarlas | `email_templates/router.py:107-149, 707-874` |
| P-8 | Marketing: diseñar y **enviar** campañas | editar · enviar o programar | `api/brevo.py` (`require_role[manager]`) |
| P-9 | `erp.office.view` junta pedidos con facturas, fichas fiscales y compradores | ver pedidos · ver facturas y datos fiscales (ahora decidido que van juntos; queda anotado por si cambia) | `erp/capabilities.py:67-70` |
| P-10 | Formularios web: el Responsable los gestiona todos, incluido el dueño de los leads | gestionar formularios | `api/web_forms_admin` (`require_role[manager]`) |
| P-11 | La máquina de estados va por rol: no hay permiso para «cambiar pago en BoHub» ni «aprobar crédito B2B» | un permiso por grupo de transiciones | `erp/state_machine/engine.py:156-163`; `definitions.py:43-232`; `erp/api/orders.py:1922` |
| P-12 | Empresas: el router nuevo pide Usuario, el antiguo Responsable; Solo lectura + Comercial no crea empresas aunque su capacidad ERP lo diga | editar datos de empresa con una sola regla | `api/companies.py:219-837`; `api/routes.py:1191-1229` |
| P-13 | `erp.sat.view` existe pero ningún endpoint la mira: no se puede dar ERP sin la cola del taller | ver la cola del taller | `erp/api/deps.py:89`; `erp/api/sat.py:570-1049` |
| P-14 | Ver los ajustes del ERP = entrar al ERP | ver configuración | `erp/api/exceptions.py:722, 734` |
| P-15 | Desactivar un contacto tiene tres reglas (individual Responsable, masiva solo admin, edición A/R) | una sola | `api/routes.py:2478, 2890`; `api/bulk.py:212` |
| P-16 | Solo lectura ve la ficha pero no sus notas, teléfonos, llamadas ni línea de vida | leer la ficha completa | `api/contact_notes.py:74`; `contact_channels.py:88`; `call_logs.py:404`; `contact_timeline.py:78` |
| P-17 | Tareas: se crean para otro pero no se reasignan; llamadas: el Responsable no borra las ajenas | reasignar tareas · borrar llamadas ajenas | `api/tasks.py:248, 330-338`; `api/call_logs.py:436` |

## 3 · Cosmética

Molesto, no peligroso: la API ya decide bien (o el hallazgo está arriba).

| # | Qué | Dónde |
|---|---|---|
| C-1 | **Taller (SAT)**: el menú mira la capacidad, la pantalla una lista fija de roles principales (`admin, manager, pedidos, sat`). Echa al Usuario (el caso de Bart), al Comercial y a todos los combos, incluido Usuario + Taller; deja entrar al Responsable. La tarjeta del inicio ERP lleva al mismo rebote. | `frontend lib/appNav.tsx:176-182` frente a `erp/sat/layout.tsx:142, 163`; `erp/page.tsx:97-101` |
| C-2 | Excepciones: el menú pide `office.view`, la API de lista `erp.access` (el Taller entra por URL); los botones de gestionar salen a quien no puede. | `lib/appNav.tsx:93`; `erp/exceptions/page.tsx:83-88` |
| C-3 | **Workflows**: Pausar, Editar, Archivar, Borrar en todas las filas, también las del equipo; el editor no tiene modo lectura; Pausar, Archivar y Borrar no recogen el error, así que el botón «no hace nada». | `admin/workflows/page.tsx:400-483`; `admin/workflows/[id]/page.tsx:644-677` |
| C-3b | Documentos, Proformas, Ofertas, Plantillas: el menú pide `documents`/`proformas`, la API de lectura `office.view`; Usuario y Responsable entran por URL (decidido que pueden: falta enseñárselo en el menú, en modo lectura). | `lib/appNav.tsx:99-133` |
| C-4 | **Pipelines**: Etapas, Archivar y Editar etapas en todos los pipelines (403 en los del equipo); opción IA del asistente a quien no puede. | `pipelines/page.tsx:151-159, 276-314`; `pipelines/[id]/page.tsx:83-86`; `pipelines/[id]/edit-stages/page.tsx:318-324`; `components/CreatePipelineWizard.tsx:346` |
| C-5 | **Tags**: la pantalla no mira el usuario; formulario de crear y botones Editar/Borrar a todos. | `admin/tags/page.tsx:119-200, 247-260` |
| C-6 | Marketing: nueva campaña, plantilla o lista y borrar a un Usuario (API: Responsable). | `marketing/campaigns/page.tsx:135`; `marketing/templates/page.tsx:154`; `marketing/listas/page.tsx:160, 293` |
| C-7 | Woo en modo ERP del admin: sale en el menú y la pantalla le devuelve a `/erp`. | `lib/appNav.tsx:190-196`; `lib/appMode.ts:62` |
| C-8 | Kanban: Solo lectura puede arrastrar tarjetas (403 al soltar). | `components/PipelineKanban.tsx:69, 145` |
| C-9 | Configuración ERP se abre por URL con cualquier rol ERP (lectura). | `erp/settings/page.tsx:298` |
| C-10 | Reglas de asignación: el Usuario las lee por URL; el menú no se las enseña. | `lib/appNav.tsx:247-251`; `api/assignment_rules.py:114, 132` |
| C-11 | Integraciones: menú solo admin; la API de ver cuentas acepta Responsable y la pantalla no bloquea. | `lib/appNav.tsx:233-237`; `api/integration_settings.py:56, 73` |
| C-12 | Correo, Workflows y Marketing salen en el menú de Solo lectura y la API le da 403. | `lib/appNav.tsx:63-71, 213-221, 253-259` |
| C-13 | Páginas sin guard propio que solo enseñan el 403 (Seguimiento, Cuadre, Leads, Conciliación, Woo, Campos personalizados, Formularios, Nuevo pedido). | páginas en la tabla de zona |
| C-14 | Solo lectura ve Crear contacto, tarea, empresa y segmento. | `contacts/page.tsx:803`; `tasks/page.tsx:224`; `companies/page.tsx:564`; `segments/page.tsx:84` |
| C-15 | El Responsable tiene tres definiciones (docstring, mapa de capacidades, máquina de estados). | `erp/capabilities.py:27, 109`; `erp/state_machine/definitions.py:43` |
| C-16 | Cuatro formas de decir «admin» en el ERP (`require_erp_admin`, `require_config`, `ERP_ADMIN_ROLES`, `require_admin` del CRM). Hoy dan lo mismo. | `erp/api/deps.py:29-38, 113`; `erp/api/exceptions.py:1062`; `erp/api/cuadre.py` |
| C-17 | `GET /api/erp/settings` y `GET /api/segments/{id}/count?force_refresh` escriben. | `erp/api/exceptions.py:728-729`; `api/routes.py:5638-5653` |
| C-18 | `GET/POST /api/companies` del router antiguo es código muerto (lo tapa el nuevo). | `api/routes.py:1114-1182`; `main.py:91, 99` |

## Lo que no he podido comprobar

Mejor «no lo sé» que una casilla a ojo:

- **Configuración de producción**: si `BREVO_WEBHOOK_SECRET` y los secretos del webhook de Gmail
  tienen valor (F-5, F-21); si `auto_mark_paid_when_order_paid` está activo (alcance de F-4); qué
  direcciones tiene la cuenta de Gmail de la organización como «enviar como» (alcance real de F-2); si
  el proxy reescribe `X-Forwarded-For` y si hay límite de intentos de login fuera de la aplicación.
- **Intención de producto**: si el límite del CSV a «mis contactos» pretendía impedir sacar la cartera
  ajena (F-10); si editar notas ajenas es intencionado (F-11); si hay tareas personales que no deberían
  verse (F-15); si el Comercial debe ver desde Documentos las facturas de pedidos web.
- **Pantallas**: qué pintan exactamente las páginas sin guard ante un 403 (C-13); qué hacen
  `/admin/factusol-bulk-match` y el historial de sincronización para un no-admin.
- **Empresas**: cuál de los dos `POST /api/companies` responde, aunque el orden de montaje apunta al
  router nuevo (C-18); si editar una empresa siendo Comercial tiene efectos en FACTUSOL.
- **Contenido**: qué guarda exactamente el historial de pasos de un workflow (F-16); si alguien ha
  escrito un secreto en el campo «notas» de una integración (lo ve el Responsable).
- **Plantillas del ERP por carpeta**: los endpoints pasan el usuario, pero no he leído si el servicio
  aplica bien el acceso por carpeta.

## Coste de arreglarlo

Estimaciones con tests y una ronda de revisión por PR, sin contar el diseño con Bart.

| Bloque | Qué incluye | Horas |
|---|---|---|
| **Filtraciones graves** (F-1 a F-4) | F-1 filtrar por buzón y exigir Usuario (1–2 h); F-2 validar contra los remitentes que asigna el admin (1–2 h); F-3 el chequeo de pedidos web en los ~15 endpoints que lo olvidan, y comprobar antes de escribir en las masivas (3–4 h); F-4 sacar al Responsable de los arcos de oficina, o pedir capacidad en el endpoint (1–2 h) | **6–10 h** |
| **Resto de filtraciones** (F-5 a F-21) | F-5 es configuración más, si Brevo no firma, un token en la URL (1 h); F-6, F-7, F-11, F-16, F-17, F-18 son comprobaciones de visibilidad puntuales (5–6 h); F-8 depende de qué debe ver el taller (2–3 h); F-14, F-19, F-20, F-21 (3–4 h); Solo lectura escribe (SL-1…SL-5, 1–2 h) | **12–16 h** |
| **Usar ≠ configurar** (P-1 a P-17) | Depende del diseño. Lo mínimo que pidió Bart (tags, pipelines, workflows, más remitentes que ya va en F-2) son 3–4 permisos nuevos con su pantalla: **8–12 h**. Todo el grupo 2 con la máquina de estados por capacidad: **20–30 h**. | **8–30 h** |
| **Cosmética** (C-1 a C-18) | C-1 a una línea más su test; botones según permiso en Workflows, Pipelines, Tags, Marketing, Excepciones; menú alineado con la API; mensajes de 403 en las páginas sin guard | **5–7 h** |

Orden recomendado: las cuatro graves hoy, en un PR; F-5 (mirar el `.env` de producción) a la vez; el
resto de filtraciones y Solo lectura después; usar/configurar cuando Bart decida el diseño; la
cosmética, mejor junto con usar/configurar, porque los botones dependen de los permisos nuevos.
