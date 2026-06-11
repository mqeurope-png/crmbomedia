# Roles y permisos

Mini-PR C Fase 3 reordena la tabla de roles. Mantenemos cuatro:

| Rol | Para qué |
|---|---|
| **Admin** | Acceso total. Configura integraciones, gestiona usuarios. |
| **Manager** | Gestiona equipo y datos. NO toca credenciales de integraciones ni usuarios. |
| **User** (comercial) | Trabaja sus contactos, tareas, segmentos. Por defecto ve "solo asignados a mí" en `/contacts`. |
| **Viewer** | Solo lectura. Útil para auditorías externas o equipo de soporte. |

Si tu instalación no necesita `viewer`, el rol puede dejarse sin
asignar — sigue existiendo en la BD para no romper foreign keys
pero no aparece en el form de crear usuario salvo que un admin lo
seleccione explícitamente.

## Visibilidad por rol (sidebar + dashboard)

| Sección | Admin | Manager | User | Viewer |
|---|---|---|---|---|
| Dashboard | ✓ | ✓ | ✓ | ✓ |
| Contactos | ✓ | ✓ | ✓ (filtrado por owner) | ✓ (read) |
| Empresas | ✓ | ✓ | ✓ | ✓ (read) |
| Tareas | ✓ | ✓ | ✓ | ✓ (read) |
| Pipelines | ✓ | ✓ | ✓ | ✓ (read) |
| Segmentos | ✓ | ✓ | ✓ | ✓ (read) |
| Marketing | ✓ | ✓ | ✓ | ✓ (read) |
| Tags | ✓ | ✓ | ✓ | ✓ (read) |
| Integraciones | ✓ | ✗ | ✗ | ✗ |
| Usuarios | ✓ | ✗ | ✗ | ✗ |
| Ajustes | ✓ | ✗ | ✗ | ✗ |
| Botón "OpenAPI" en dashboard | ✓ | ✗ | ✗ | ✗ |

## Mutaciones por rol

| Acción | Admin | Manager | User | Viewer |
|---|---|---|---|---|
| Crear/editar contactos | ✓ | ✓ | ✓ | ✗ |
| Crear/editar tareas | ✓ | ✓ | ✓ | ✗ |
| Crear/editar pipelines | ✓ | ✓ | ✗ | ✗ |
| Crear/editar segmentos | ✓ | ✓ | ✓ (los propios) | ✗ |
| Crear/editar tags | ✓ | ✓ | ✓ | ✗ |
| Bulk: asignar owner | ✓ | ✓ | ✗ | ✗ |
| Bulk: cambiar estado / tag | ✓ | ✓ | ✓ | ✗ |
| Bulk: desactivar contactos | ✓ | ✗ | ✗ | ✗ |
| Conectar Google Calendar | ✓ | ✓ | ✓ | ✓ |
| Acceder a `/api/integrations/*` | ✓ | ✗ | ✗ | ✗ |
| Crear / editar / desactivar usuarios | ✓ | ✗ | ✗ | ✗ |
