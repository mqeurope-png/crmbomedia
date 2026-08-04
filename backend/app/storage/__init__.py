"""Storage abstracto de ficheros de expedición (Fase D).

Los albaranes y etiquetas se guardan fuera de la BD. La interfaz
`ShippingStorage` desacopla el backend concreto: hoy disco local en el VPS
(`LocalShippingStorage`); mañana HiDrive/S3 sin tocar los endpoints — basta con
implementar la interfaz y cambiar el env `STORAGE_BACKEND`.
"""
from __future__ import annotations

from functools import lru_cache

from app.storage.base import ShippingStorage
from app.storage.hidrive import HiDriveShippingStorage
from app.storage.local import LocalShippingStorage

__all__ = [
    "HiDriveShippingStorage",
    "LocalShippingStorage",
    "ShippingStorage",
    "get_shipping_storage",
]


@lru_cache(maxsize=1)
def get_shipping_storage() -> ShippingStorage:
    """Backend de storage según `STORAGE_BACKEND` (default `local`)."""
    from app.core.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    backend = (settings.storage_backend or "local").lower()
    if backend == "hidrive":
        return HiDriveShippingStorage()
    if backend != "local":
        # Desconocido → local, con aviso implícito (el default es seguro).
        pass
    return LocalShippingStorage(base_dir=settings.local_shipping_storage_dir)
