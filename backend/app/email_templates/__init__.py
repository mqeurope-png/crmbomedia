"""Sprint Email v2.2 — CRM-owned email templates with folder tree.

Layout:

- `models.py` — `EmailTemplate` + `EmailTemplateFolder`.
- `schemas.py` — Pydantic in/out shapes.
- `services.py` — text extraction + folder depth check.
- `router.py` — REST surface (templates CRUD, folders CRUD, picker).
"""
