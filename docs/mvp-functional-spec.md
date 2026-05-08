# Especificación funcional del MVP

## Objetivo

Construir una primera versión útil y segura del CRM propio que centralice contactos, empresas, notas, tareas, consentimiento marketing y trazabilidad básica de sincronizaciones.

## Alcance inicial

1. **Infraestructura base**: backend FastAPI, frontend Next.js, PostgreSQL y Redis preparados en Docker Compose.
2. **Migraciones**: Alembic gestiona el esquema de base de datos.
3. **Modelo central**: contactos, empresas, notas, tareas, referencias externas y logs de sincronización.
4. **Principio de conectores**: AgileCRM, Brevo, Freshdesk y FactuSOL se representan como sistemas externos, nunca como modelo interno dominante.
5. **Preparación RGPD**: estado de consentimiento y validez de email en contacto; logs para futuras auditorías.
6. **Seguridad mínima**: login JWT, usuario actual, roles `admin`, `manager`, `user` y `viewer`.
7. **UI mínima**: login, dashboard protegido, listado de contactos, ficha editable y formulario de creación de contacto.

## Reglas de datos del MVP

- El email normalizado en minúsculas es el identificador principal de contacto.
- No se aceptan contactos duplicados con el mismo email.
- Las bajas de marketing se modelan como estado de consentimiento `unsubscribed` para evitar reactivaciones futuras.
- Las referencias externas permiten vincular un mismo contacto a varias cuentas o herramientas.
- Las notas y tareas pertenecen a la ficha interna del contacto.
- Las notas y tareas no pueden crearse para contactos inexistentes.
- Viewers solo pueden leer; users crean notas/tareas; managers editan CRM; admins gestionan usuarios y auditoría.
- Contactos y empresas se desactivan mediante soft-delete.

## Fuera de esta primera entrega

- Autenticación completa con 2FA.
- Despliegue IONOS real.
- Conectores reales con credenciales de AgileCRM/Brevo/Freshdesk/FactuSOL.
- Webhooks de Brevo en producción.
- Automatizaciones avanzadas y journeys visuales.
