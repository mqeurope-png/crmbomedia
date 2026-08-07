# Perfiles y permisos — perfil del comercial y panel de administración

> **Sprint CRM-PERFIL.** Complementa a [`permissions.md`](./permissions.md)
> (matriz general de roles) con el detalle de **qué puede tocar cada rol de su
> propio perfil** y **qué queda reservado al administrador**.

## Resumen

A partir de CRM-PERFIL el perfil del **comercial** (rol `user`) es de **solo
lectura salvo la firma de email**. Todo lo demás —contraseña, preferencias de
envío, alias, calendario, carpeta de plantillas por defecto— lo gestiona el
**administrador** desde `/admin/users`. El autoservicio público de «olvidé mi
contraseña» queda **retirado**: si un comercial olvida su contraseña, el admin
se la resetea.

El **2FA** sigue siendo autoservicio para todos los roles (es una medida de
seguridad de la propia cuenta, no una preferencia de perfil).

## Matriz de perfil propio (`/account`)

| Elemento del perfil | Admin (sobre el suyo) | Comercial (`user`) | Endpoint |
|---|---|---|---|
| Ver perfil (`GET /me`) | ✓ | ✓ | `GET /api/auth/me` |
| Editar **firma** de email | ✓ | ✓ | `GET/POST/PUT/DELETE /api/email-signatures` |
| Configurar 2FA | ✓ | ✓ | `/api/auth/2fa/*` |
| Cambiar su contraseña | ✓ | ✗ (403 `requires_admin`) | `POST /api/auth/change-password` |
| Preferencia «incluir baja por defecto» | ✓ | ✗ (403 `requires_admin`) | `PUT /api/users/me/preferences` |
| Seleccionar calendario Google | ✓ | ✗ (403 `requires_admin`) | `PATCH /api/integrations/google/calendar` |
| Carpeta de plantillas por defecto | ✓ | ✗ (403 `requires_admin`) | `PUT /api/users/me/default-template-folder` |
| Preferencias de alias Send-As | ✓ | ✓ (auto-sync desde Gmail)¹ | `PUT /api/emails/aliases/preferences` |

¹ Las **preferencias Send-As** (`/api/emails/aliases/preferences`) son
per-`/me` y se auto-sincronizan desde Gmail; no son un vector de escalada
porque la **propiedad** del alias entrante (`user_email_aliases`) sí es
admin-only (ver más abajo). La UI de composición de alias no se muestra en el
`/account` de solo lectura del comercial.

## Endpoints admin-only sobre otros usuarios (`/admin/users`)

| Acción | Endpoint | Guard |
|---|---|---|
| Listar usuarios | `GET /api/users` | `require_viewer` |
| Crear usuario | `POST /api/users` | `require_admin` |
| Editar perfil de cualquiera (nombre, rol, activo) | `PATCH /api/users/{id}` | `require_admin` |
| Fijar contraseña concreta | `PATCH /api/users/{id}/password` | `require_admin` |
| **Resetear contraseña** (genera una aleatoria, se muestra una vez) | `POST /api/users/{id}/reset-password` | `require_admin` |
| Desactivar / reactivar | `PATCH /api/users/{id}/deactivate` · `/reactivate` | `require_admin` |
| CRUD propiedad de alias entrante | `POST/PATCH/DELETE /api/users/{id}/aliases` | `require_admin` |

> No hay **impersonación**: el admin nunca «entra como» el comercial. Edita el
> perfil y resetea la contraseña, nada más.

### Reset de contraseña por admin

`POST /api/users/{id}/reset-password` genera una contraseña aleatoria que
cumple la política (mayúscula + minúscula + dígito, ≥ 12 chars), la persiste
como hash y la **devuelve una sola vez** en la respuesta:

```json
{ "password": "…", "message": "Contraseña reseteada. Cópiala y comunícasela al usuario: no se volverá a mostrar." }
```

La UI (`ResetPasswordModal`) la muestra con un botón «Copiar» y el aviso de que
no se volverá a mostrar. El reset queda registrado en auditoría como
`user.password_set_by_admin` con `metadata.method = "admin_reset_generated"`.

## Flujo público «olvidé contraseña» — retirado

Los dos endpoints públicos siguen existiendo pero responden **403** con
`code = password_reset_disabled` y **registran el intento** en auditoría
(`auth.password_reset_requested`, `target_id = "password-reset-disabled"`) para
poder detectar abuso. No hay email de reset ni token público.

- `POST /api/auth/password-reset/request` → 403
- `POST /api/auth/password-reset/confirm` → 403

En el frontend se elimina la página `/password-reset` y el enlace «¿Has
olvidado la contraseña?» del login se sustituye por la nota «Pide al
administrador que la resetee».

## Contrato de error

Los bloqueos por rol devuelven un cuerpo estructurado y homogéneo:

```json
{ "detail": { "code": "requires_admin",
              "detail": "Este cambio solo puede hacerlo un administrador. Contacta con soporte interno." } }
```

El frontend traduce `code` a un toast rojo (reutilizando `formatFastApiDetail`).

## Fuera de alcance / backlog

- **Firma:** el admin no edita la firma de un comercial desde `/admin/users`
  (el comercial la autogestiona). Diferido a backlog por bajo valor.
- **Sanitizador HTML de firmas/plantillas:** no se añade en este sprint; se
  mantiene la paridad de HTML crudo con el resto del CRM. Anotado en backlog.
- **Calendario / Send-As por-comercial desde el panel admin:** no se añade
  (son ajustes org-scoped; el admin ya controla la conexión Google org-wide).
- Sin impersonación, sin MFA/2FA obligatorio, sin tocar permisos de
  PEDIDOS/SAT del ERP.
