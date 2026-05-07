# Roadmap — Plataforma CRM propia con AgileCRM, Brevo, Freshdesk y FactuSOL

## 1. Visión general

El objetivo es construir una app propia que empiece como plataforma intermedia de sincronización, pero que pueda evolucionar hacia un CRM propio tipo Zoho/Salesforce, adaptado a las necesidades del despacho o empresa.

La plataforma centralizará:

- Contactos y clientes.
- Historial comercial.
- Email marketing.
- Estadísticas de campañas.
- Tickets de soporte.
- Facturación.
- Notas internas.
- Tareas.
- Automatizaciones.
- Auditoría y trazabilidad RGPD.

## 2. Arquitectura objetivo

```text
App propia / CRM central
  ├── AgileCRM
  │     └── Contactos, datos comerciales heredados
  │
  ├── Brevo
  │     └── Email marketing, campañas, estadísticas, eventos
  │
  ├── Freshdesk
  │     └── Tickets, soporte, incidencias, conversaciones
  │
  ├── FactuSOL / DELSOL
  │     └── Clientes, facturas, presupuestos, cobros
  │
  ├── Base de datos propia
  │     └── Contactos, eventos, logs, auditoría, reglas
  │
  └── Interfaz propia
        └── Fichas, campañas, notas, tareas, automatizaciones
```

## 3. Principio de diseño

La app propia debe ser el centro.

No debe depender internamente del modelo de AgileCRM, Brevo, Freshdesk o FactuSOL. Cada herramienta externa debe funcionar como un conector.

```text
Sistema externo → Conector → Modelo propio → Interfaz propia
```

Esto permite sustituir en el futuro cualquier herramienta sin rehacer toda la plataforma.

---

# Fase 0 — Definición funcional, técnica y jurídica

## Objetivo

Definir exactamente qué se va a construir, qué datos se tratan, qué reglas mandan y qué límites tendrá el MVP.

## Entregables

- Documento funcional del MVP.
- Mapa de datos personales.
- Modelo de permisos.
- Reglas de sincronización.
- Reglas de duplicados.
- Reglas de consentimiento.
- Arquitectura técnica.
- Roadmap validado.
- Criterios mínimos RGPD.

## Decisiones clave

- Qué sistema manda sobre cada tipo de dato.
- Qué ocurre si un contacto existe en varias cuentas AgileCRM.
- Qué ocurre si un contacto se da de baja en Brevo.
- Qué ocurre si FactuSOL tiene datos distintos.
- Qué eventos de Brevo se guardan.
- Qué eventos se vuelcan de nuevo a AgileCRM.
- Qué usuarios pueden ver, crear, editar, exportar o borrar datos.
- Cuánto tiempo se conservan logs y eventos.

## Criterio recomendado

```text
Datos comerciales básicos        → App propia
Email marketing                  → Brevo
Estadísticas de email             → App propia + Brevo
Facturación                       → FactuSOL
Soporte                           → Freshdesk
Consentimiento marketing          → App propia + Brevo
Notas y tareas                    → App propia
Historial consolidado             → App propia
```

---

# Fase 1 — Infraestructura base

## Objetivo

Crear la base técnica segura sobre la que se construirá toda la plataforma.

## Stack recomendado

```text
Frontend: React / Next.js
Backend: FastAPI o NestJS
Base de datos: PostgreSQL
Servidor: IONOS VPS / Cloud Server
Proxy: Nginx
SSL: HTTPS obligatorio
Colas: Redis + worker
Repositorio: GitHub privado
Backups: diarios
Logs: aplicación + errores + auditoría
```

## Dominio y subdominio

Ejemplo:

```text
app.tudominio.com
crm.tudominio.com
api.tudominio.com
```

Estructura recomendada:

```text
https://app.tudominio.com             → Interfaz web
https://app.tudominio.com/api         → API propia
https://app.tudominio.com/webhooks    → Webhooks externos
Base de datos PostgreSQL              → No expuesta públicamente
```

## Entregables

- Repositorio GitHub privado.
- Backend inicial.
- Frontend inicial.
- Base de datos PostgreSQL.
- Sistema de login.
- Roles básicos.
- Panel de administración.
- Despliegue en IONOS.
- Certificado SSL.
- Sistema de backups.
- Sistema básico de logs.

## Seguridad mínima

- HTTPS obligatorio.
- Base de datos no pública.
- API keys cifradas.
- Variables de entorno protegidas.
- Firewall.
- Backups automáticos.
- Control de accesos por usuario.
- Registro de accesos.
- Separación entre entorno de pruebas y producción.

---

# Fase 2 — Modelo central de datos

## Objetivo

Crear el modelo interno propio de la aplicación.

La app debe tener su propio modelo de contactos, empresas, eventos, tareas y actividades, sin depender directamente de la estructura de AgileCRM, Brevo, Freshdesk o FactuSOL.

## Entidades principales

```text
Usuarios
Roles
Contactos
Empresas
Fuentes externas
Cuentas AgileCRM
Cuentas Brevo
Cuentas Freshdesk
Cuentas FactuSOL
Campañas
Eventos de email
Tickets
Facturas
Notas
Tareas
Automatizaciones
Consentimientos
Logs de sincronización
Auditoría
```

## Ficha de contacto

Cada contacto debería tener:

```text
Nombre
Apellidos
Empresa
Email
Teléfono
Origen
Tags
Estado comercial
Responsable interno
Consentimiento marketing
Historial de campañas
Eventos Brevo
Tickets Freshdesk
Facturas FactuSOL
Notas internas
Tareas
Historial de sincronización
```

## Entregables

- Modelo de base de datos.
- Migraciones iniciales.
- CRUD interno de contactos.
- CRUD de empresas.
- CRUD de notas.
- CRUD de tareas.
- Sistema de búsqueda.
- Vista básica de ficha de cliente.

---

# Fase 3 — Conector AgileCRM → App propia → Brevo

## Objetivo

Sincronizar contactos desde varias cuentas AgileCRM hacia la app propia y desde ahí hacia Brevo.

## Flujo

```text
AgileCRM
  ↓
Conector AgileCRM
  ↓
Base de datos propia
  ↓
Conector Brevo
  ↓
Brevo
```

## Funcionalidades

- Conectar varias cuentas AgileCRM.
- Leer contactos nuevos.
- Leer contactos modificados.
- Normalizar datos.
- Detectar duplicados.
- Guardar contacto en BDD propia.
- Crear contacto en Brevo.
- Actualizar contacto en Brevo.
- Asignar lista o segmento Brevo.
- Registrar errores.
- Reintentar sincronizaciones fallidas.

## Reglas recomendadas

- El email será el identificador principal.
- Cada contacto podrá tener varios orígenes.
- No se debe crear contacto en Brevo sin email válido.
- No se debe reactivar a un contacto dado de baja.
- Las bajas de Brevo tienen prioridad sobre futuras sincronizaciones.
- Los duplicados se gestionan en la app propia, no directamente en Brevo.

## Entregables

- Panel de cuentas AgileCRM.
- Configuración de API keys.
- Sincronizador AgileCRM.
- Mapeo AgileCRM → contacto propio.
- Sincronizador contacto propio → Brevo.
- Logs de sincronización.
- Panel de errores.
- Sistema de reintentos.

---

# Fase 4 — Webhooks Brevo → App propia

## Objetivo

Recibir toda la actividad de email marketing desde Brevo y guardarla en la app propia.

## Eventos a capturar

```text
Email enviado
Email entregado
Email abierto
Click
Rebote blando
Rebote duro
Baja
Spam
Bloqueo
Contacto actualizado
Contacto añadido a lista
Contacto eliminado
```

## Flujo

```text
Brevo
  ↓
Webhook
  ↓
API propia
  ↓
Base de datos propia
  ↓
Ficha de contacto / estadísticas
```

## Datos a guardar

```text
Email
ID de contacto Brevo
ID de campaña
ID de mensaje
Tipo de evento
Fecha del evento
Asunto
URL clicada
Payload original
Estado de procesamiento
Contacto asociado
```

## Entregables

- Endpoint de webhooks Brevo.
- Validación de payloads.
- Registro de eventos.
- Asociación de eventos a contacto.
- Panel de actividad por contacto.
- Panel de actividad por campaña.
- Estadísticas agregadas.

---

# Fase 5 — Volcado de estadísticas a AgileCRM

## Objetivo

Hacer que en AgileCRM se pueda consultar, al menos parcialmente, el historial de email marketing generado en Brevo.

## Estrategias posibles

### Opción A — Notas o actividades

Crear una nota o actividad en AgileCRM por eventos relevantes.

Ejemplo:

```text
[Brevo] Email abierto
Campaña: Newsletter abril
Asunto: Novedades legales
Fecha: 07/05/2026
Evento: Open
```

### Opción B — Campos agregados

Actualizar campos personalizados en AgileCRM:

```text
Último email enviado
Última apertura
Último click
Total aperturas
Total clicks
Estado Brevo
Baja marketing
Rebote duro
```

### Opción C — Enlace a ficha propia

Guardar en AgileCRM un enlace a la ficha completa dentro de la app propia.

```text
https://app.tudominio.com/contactos/12345
```

## Recomendación

Combinar B + C.

- Campos agregados en AgileCRM para consulta rápida.
- Historial completo en la app propia.

## Entregables

- Mapeo contacto propio → contacto AgileCRM.
- Actualización de campos agregados.
- Creación de notas o actividades, si procede.
- Enlace desde AgileCRM a la ficha propia.
- Logs de sincronización inversa.

---

# Fase 6 — CRM propio mínimo

## Objetivo

Empezar a usar la app propia como CRM central.

## Funcionalidades

- Fichas de clientes.
- Fichas de empresas.
- Notas internas.
- Tareas.
- Estados comerciales.
- Tags.
- Responsable interno.
- Historial de actividad.
- Buscador.
- Filtros.
- Segmentos.
- Exportaciones controladas.
- Permisos básicos.

## Vista de ficha de cliente

```text
Datos generales
Consentimiento
Historial Brevo
Tickets Freshdesk
Facturas FactuSOL
Notas
Tareas
Actividad reciente
Origen del contacto
Logs de sincronización
```

## Entregables

- CRM básico funcional.
- Panel de contactos.
- Panel de empresas.
- Vista detalle de contacto.
- Vista detalle de empresa.
- Notas.
- Tareas.
- Estados.
- Búsqueda.
- Filtros.
- Segmentación básica.

---

# Fase 7 — Email marketing desde la app vía Brevo

## Objetivo

Permitir crear y lanzar campañas de email desde la app propia, usando Brevo como motor de envío.

## Flujo

```text
App propia
  ↓
Segmento de contactos
  ↓
Campaña
  ↓
Brevo
  ↓
Envío
  ↓
Webhooks Brevo
  ↓
Estadísticas en app propia
```

## Funcionalidades

- Crear campaña.
- Elegir segmento.
- Elegir lista.
- Elegir plantilla.
- Definir asunto.
- Definir remitente.
- Enviar prueba.
- Programar envío.
- Lanzar campaña.
- Ver estadísticas.
- Ver eventos por contacto.
- Excluir contactos sin consentimiento.
- Excluir bajas y rebotes duros.

## Recomendación

No construir un sistema propio de envío masivo.

Brevo debe seguir siendo el motor de:

```text
Entregabilidad
Tracking
Bajas
Rebotes
Reputación
Plantillas
Listas
Estadísticas técnicas
```

La app propia debe ser la capa de gestión.

## Entregables

- Constructor básico de campañas.
- Selector de segmentos.
- Integración con plantillas Brevo.
- Envío de pruebas.
- Programación de campaña.
- Lanzamiento vía API Brevo.
- Dashboard de estadísticas.
- Control de consentimiento.

---

# Fase 8 — Integración Freshdesk

## Objetivo

Integrar tickets de soporte e incidencias dentro de la ficha de cliente.

## Flujo

```text
Freshdesk
  ↓
Webhook/API
  ↓
App propia
  ↓
Ficha de cliente
```

## Funcionalidades

- Sincronizar contactos Freshdesk.
- Sincronizar empresas Freshdesk.
- Sincronizar tickets.
- Ver tickets en la ficha de cliente.
- Ver estado del ticket.
- Ver prioridad.
- Ver agente asignado.
- Ver fechas.
- Crear ticket desde la app propia.
- Asociar ticket a contacto.
- Automatizar acciones según tickets.

## Casos de uso

```text
Si se crea un ticket crítico → crear alerta interna.
Si un cliente tiene ticket abierto → excluirlo de campaña comercial.
Si se cierra un ticket → crear tarea de seguimiento.
Si un cliente premium abre ticket → marcar prioridad alta.
```

## Entregables

- Conector Freshdesk.
- Webhooks Freshdesk.
- Tabla de tickets.
- Vista de tickets por contacto.
- Creación básica de ticket desde app propia.
- Reglas simples de automatización.

---

# Fase 9 — Integración FactuSOL / DELSOL

## Objetivo

Conectar facturación y datos comerciales reales con el CRM propio.

## Datos a sincronizar

```text
Clientes
Facturas
Presupuestos
Pedidos
Cobros
Importes pendientes
Productos o servicios
Estado de facturación
Histórico de compras
```

## Flujo

```text
FactuSOL / DELSOL
  ↓
Conector FactuSOL
  ↓
App propia
  ↓
Ficha de cliente / empresa
```

## Funcionalidades

- Sincronizar clientes.
- Asociar cliente FactuSOL con contacto/empresa en CRM.
- Ver facturas en ficha de cliente.
- Ver presupuestos.
- Ver importes pendientes.
- Ver histórico de facturación.
- Calcular valor del cliente.
- Crear alertas comerciales o administrativas.

## Casos de uso

```text
Cliente con factura pendiente → mostrar aviso.
Cliente con alto volumen de facturación → marcar como cliente estratégico.
Cliente con presupuesto enviado y sin respuesta → crear tarea.
Cliente nuevo en FactuSOL → crear ficha en CRM.
```

## Entregables

- Verificación de disponibilidad API según instalación concreta.
- Conector FactuSOL.
- Mapeo cliente FactuSOL → empresa/contacto propio.
- Vista de facturación en ficha de cliente.
- Alertas básicas.
- Logs de sincronización.

---

# Fase 10 — Automatizaciones

## Objetivo

Crear reglas automáticas entre CRM, Brevo, Freshdesk y FactuSOL.

## Automatizaciones simples

Formato:

```text
Si ocurre A → hacer B
```

Ejemplos:

```text
Si contacto abre 3 campañas → crear tarea comercial.
Si contacto hace click en enlace clave → marcar como lead caliente.
Si contacto se da de baja → bloquear marketing.
Si hay rebote duro → marcar email inválido.
Si se crea ticket crítico → avisar a responsable.
Si se emite factura → actualizar valor del cliente.
Si presupuesto lleva 15 días sin respuesta → crear recordatorio.
```

## Automatizaciones avanzadas

Formato:

```text
Evento → condición → espera → acción → bifurcación
```

Ejemplo:

```text
Enviar campaña
  ↓
Esperar 3 días
  ↓
Si abrió email:
    crear tarea comercial
Si no abrió:
    enviar segundo email
Si hizo click:
    marcar lead caliente
```

## Recomendación

Primero reglas simples.  
Después journeys visuales.

## Entregables

- Motor básico de reglas.
- Panel de automatizaciones.
- Activación/desactivación de reglas.
- Logs de ejecución.
- Reintentos.
- Alertas.
- Automatizaciones predefinidas.

---

# Fase 11 — Reporting transversal

## Objetivo

Crear dashboards que crucen marketing, soporte, ventas y facturación.

## Dashboards posibles

### Dashboard comercial

```text
Nuevos contactos
Leads calientes
Tareas pendientes
Clientes sin actividad
Campañas con mejor respuesta
Contactos más activos
```

### Dashboard marketing

```text
Campañas enviadas
Aperturas
Clicks
CTR
Rebotes
Bajas
Mejores segmentos
Mejores asuntos
```

### Dashboard soporte

```text
Tickets abiertos
Tickets críticos
Tiempo medio de resolución
Clientes con más incidencias
Tickets por estado
Tickets por agente
```

### Dashboard facturación

```text
Facturación total
Facturación por cliente
Importes pendientes
Presupuestos abiertos
Clientes de mayor valor
Clientes inactivos
```

### Dashboard cliente 360º

```text
Datos CRM
Actividad Brevo
Tickets Freshdesk
Facturación FactuSOL
Notas
Tareas
Estado comercial
Consentimiento
```

## Entregables

- Dashboard general.
- Dashboard por contacto.
- Dashboard por empresa.
- Métricas de campañas.
- Métricas de soporte.
- Métricas de facturación.
- Exportaciones controladas.

---

# Fase 12 — Seguridad, RGPD y auditoría

## Objetivo

Blindar la plataforma para uso real con datos personales y actividad comercial.

## Medidas mínimas

```text
HTTPS
Login seguro
Roles y permisos
2FA para administradores
API keys cifradas
Backups diarios
Logs de acceso
Logs de cambios
Auditoría de exportaciones
Control de bajas
Control de consentimiento
Política de conservación
Registro de errores
Sistema de restauración
```

## Aspectos RGPD

```text
Base jurídica
Consentimiento marketing
Prueba de origen del contacto
Registro de bajas
Derecho de acceso
Derecho de rectificación
Derecho de supresión
Derecho de oposición
Minimización de datos
Limitación de conservación
Contratos con encargados
Ubicación de datos
Registro de actividad
```

## Reglas especialmente importantes

- Nunca reactivar en Brevo a una persona dada de baja.
- No enviar campañas a contactos sin base jurídica.
- No exponer la base de datos públicamente.
- No guardar API keys en texto plano.
- No permitir exportaciones sin control.
- Registrar quién accede y quién modifica datos relevantes.

## Entregables

- Sistema de permisos.
- Logs de auditoría.
- Panel de consentimientos.
- Panel de bajas.
- Exportaciones controladas.
- Backups verificados.
- Documentación técnica y jurídica.

---

# Fase 13 — Sustitución progresiva de AgileCRM

## Objetivo

Reducir dependencia de AgileCRM si la app propia ya cubre sus funciones principales.

## Funciones a cubrir antes de sustituir AgileCRM

```text
Fichas de clientes
Empresas
Notas
Tareas
Estados comerciales
Historial
Búsqueda
Segmentación
Responsables
Importación/exportación
Permisos
Automatizaciones básicas
```

## Estrategia

```text
1. Mantener AgileCRM como fuente inicial.
2. Sincronizar todo hacia app propia.
3. Empezar a trabajar desde la app propia.
4. Dejar AgileCRM como histórico.
5. Apagar AgileCRM solo cuando la app propia sea suficiente.
```

## Recomendación

No eliminar AgileCRM pronto.  
Primero usar la app propia como capa central.  
Después decidir si AgileCRM sigue aportando valor.

---

# MVP recomendado

## Objetivo del MVP

Tener una primera versión útil, segura y operativa sin intentar construir todo el CRM desde el primer día.

## Alcance MVP

```text
Login
Usuarios
Roles básicos
Contactos
Empresas
Notas
Tareas
Conector AgileCRM
Conector Brevo
Sincronización AgileCRM → Brevo
Webhooks Brevo
Ficha de contacto
Historial de emails
Estadísticas básicas
Logs de sincronización
Panel de errores
Control de bajas
Base de datos PostgreSQL
Despliegue en IONOS
```

## Fuera del MVP

```text
Freshdesk
FactuSOL
Automatizaciones avanzadas
Constructor completo de campañas
Journeys visuales
Sustitución total de AgileCRM
Reporting avanzado
App móvil
```

## MVP visual mínimo

```text
Dashboard
Contactos
Ficha de contacto
Campañas / eventos Brevo
Notas
Tareas
Sincronizaciones
Errores
Configuración
```

---

# Orden recomendado de desarrollo

## Etapa 1

```text
Infraestructura
Login
Base de datos
Contactos
Empresas
Notas
Tareas
```

## Etapa 2

```text
AgileCRM → app propia
App propia → Brevo
Logs
Errores
Reintentos
```

## Etapa 3

```text
Webhooks Brevo
Eventos email
Estadísticas
Ficha de contacto completa
```

## Etapa 4

```text
Campañas desde app vía Brevo
Segmentos
Control de consentimiento
```

## Etapa 5

```text
Freshdesk
Tickets
Soporte en ficha de cliente
```

## Etapa 6

```text
FactuSOL
Facturas
Presupuestos
Cobros
Valor cliente
```

## Etapa 7

```text
Automatizaciones
Reporting avanzado
Sustitución progresiva de AgileCRM
```

---

# Stack técnico sugerido

## Opción recomendada

```text
Frontend: Next.js
Backend: FastAPI
Base de datos: PostgreSQL
ORM: SQLAlchemy / Prisma
Colas: Redis
Workers: Celery / RQ / BullMQ
Servidor: IONOS VPS o Cloud Server
Proxy: Nginx
SSL: Let's Encrypt
Repositorio: GitHub privado
CI/CD: GitHub Actions
Logs: Sentry + logs internos
```

## Alternativa Node.js

```text
Frontend: Next.js
Backend: NestJS
Base de datos: PostgreSQL
ORM: Prisma
Colas: Redis + BullMQ
Servidor: IONOS
```

## Recomendación

Para este proyecto, elegiría:

```text
Next.js + FastAPI + PostgreSQL
```

Motivo:

- Rápido para MVP.
- Muy claro para APIs.
- Bueno para integraciones externas.
- Fácil de mantener.
- Escalable.
- Adecuado para automatizaciones y conectores.

---

# Papel de ChatGPT y Codex

## Qué puede hacer ChatGPT

```text
Definir arquitectura
Preparar especificaciones funcionales
Diseñar modelos de datos
Crear prompts técnicos para Codex
Revisar código
Detectar riesgos
Diseñar pantallas
Preparar documentación
Ayudar con RGPD
Crear tests
Generar ejemplos de API
Ayudar a depurar errores
```

## Qué puede hacer Codex

```text
Crear estructura del proyecto
Implementar endpoints
Crear modelos de base de datos
Crear migraciones
Crear conectores API
Escribir tests
Refactorizar código
Corregir bugs
Implementar pantallas
Revisar pull requests
Documentar funciones
```

## Qué seguiría necesitando supervisión humana

```text
Seguridad real en producción
Gestión del servidor
Backups
Despliegue
Revisión de permisos
Revisión RGPD
Validación de integraciones reales
Pruebas con datos reales
Control de costes
Mantenimiento
```

## Conclusión sobre desarrollo con ChatGPT + Codex

Sí, se puede desarrollar con ChatGPT y Codex, especialmente si se trabaja de forma ordenada:

```text
1. ChatGPT define especificación.
2. Codex implementa tarea concreta.
3. ChatGPT revisa diseño y riesgos.
4. Se prueba en entorno staging.
5. Se corrige.
6. Se documenta.
7. Se despliega.
```

Pero no lo haría sin:

```text
Repositorio GitHub
Entorno de pruebas
Copias de seguridad
Control de versiones
Tests
Logs
Revisión de seguridad
```

---

# Conclusión

La app puede evolucionar en tres niveles:

## Nivel 1 — Middleware

```text
AgileCRM ↔ App propia ↔ Brevo
```

## Nivel 2 — CRM propio

```text
Contactos
Empresas
Notas
Tareas
Historial
Estadísticas
```

## Nivel 3 — Plataforma completa

```text
CRM
Email marketing
Soporte
Facturación
Automatizaciones
Reporting
Auditoría RGPD
```

La recomendación es construir por capas, sin intentar hacer un Salesforce desde el día uno.

Primero:

```text
Integrador sólido + base de datos propia
```

Después:

```text
CRM mínimo
```

Luego:

```text
Brevo desde la app
Freshdesk
FactuSOL
Automatizaciones
Reporting
```

Así se construye una plataforma realista, útil y escalable.
