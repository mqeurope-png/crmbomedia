"""Bomedia Email Composer — CRM integration module.

Sprint Composer Fase 1 ports the existing standalone Composer
SPA (`composer.bomedia.net`) into the CRM as the `/composer`
module:

- `models` mirrors the eleven `composer_*` tables.
- `schemas` carries the Pydantic in/out shapes.
- `routers` exposes the REST surface under `/api/composer/*`.
- `services` holds the business logic (template snapshot FIFO,
  asset deduping, AI proxy).
- `permissions` centralises the role → capability checks the
  spec calls for.
"""
