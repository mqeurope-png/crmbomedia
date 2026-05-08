# Ajustes de integraciones

Este módulo prepara la configuración interna de integraciones sin implementar conectores reales ni llamar a APIs externas.

## Sistemas soportados

- AgileCRM
- Brevo
- Freshdesk
- FactuSOL

## Datos persistidos

La tabla `integration_settings` guarda solo metadatos no secretos:

- `system`
- `display_name`
- `enabled`
- `mode`: `sandbox` o `live`
- `status`: `not_configured`, `configured` o `paused`
- `api_base_url`
- `account_label`
- `credential_status`
- `notes`

No se deben guardar API keys, tokens ni secretos en esta tabla.

## Permisos

- `manager`: puede consultar ajustes.
- `admin`: puede consultar y editar ajustes.

## Endpoints

- `GET /api/integration-settings`
- `GET /api/integration-settings/{system}`
- `PATCH /api/integration-settings/{system}`

## UI

La pantalla administrativa está en `/admin/integrations`. El enlace debe mostrarse en el dashboard solo para usuarios `admin` o `manager`.
