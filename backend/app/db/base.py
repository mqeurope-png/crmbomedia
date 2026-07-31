"""Aggregate model registry.

Importing this module guarantees every ORM model is registered on
`Base.metadata` — required by `Base.metadata.create_all` in tests and
by Alembic's env. Add new model modules here as they appear.
"""
from app.erp import models as _erp_models  # noqa: F401
from app.models import brevo as _brevo  # noqa: F401
from app.models import integration_settings as _integration_settings  # noqa: F401
from app.models import web_forms as _web_forms  # noqa: F401
from app.models import workflows as _workflows  # noqa: F401
from app.models.crm import Base

__all__ = ["Base"]
