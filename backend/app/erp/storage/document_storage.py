"""BoHub ERP — abstracción de almacenamiento de documentos (Fase A PR 5).

Decisión cerrada nº3: el storage de documentos es HiDrive (ya contratado),
pero detrás de una interfaz `DocumentStorage` para poder cambiar a S3/otro
sin tocar los endpoints.

Selección en `get_document_storage()`:
  - Si hay credenciales HiDrive (WebDAV) → `HiDriveDocumentStorage`.
  - Si no → `LocalDocumentStorage` (guarda en `erp_uploads_dir` — funciona
    en dev y como fallback si HiDrive no está configurado todavía).

La implementación real de HiDrive (subida WebDAV) se completa en Fase C
cuando Bart dé las credenciales; el esqueleto queda aquí para no cambiar
los endpoints después.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class StoredDocument:
    """Referencia a un documento persistido (se serializa en el pedido)."""

    storage_key: str          # ruta/clave interna (WebDAV path o disco)
    url: str | None           # URL de descarga si el backend la expone
    backend: str              # "local" | "hidrive"
    filename: str
    content_type: str | None
    size_bytes: int
    uploaded_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "storage_key": self.storage_key,
            "url": self.url,
            "backend": self.backend,
            "filename": self.filename,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "uploaded_at": self.uploaded_at,
        }


def _safe_filename(name: str) -> str:
    cleaned = _SAFE_NAME.sub("_", name).strip("._") or "archivo"
    return cleaned[:120]


class DocumentStorage(Protocol):
    def save(
        self, *, order_id: str, filename: str, content_type: str | None, data: bytes
    ) -> StoredDocument:
        ...


class LocalDocumentStorage:
    """Guarda en disco local bajo `{base_dir}/{order_id}/{uuid}_{name}`."""

    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)

    def save(
        self, *, order_id: str, filename: str, content_type: str | None, data: bytes
    ) -> StoredDocument:
        safe = _safe_filename(filename)
        rel = f"{order_id}/{uuid4().hex}_{safe}"
        dest = self.base_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return StoredDocument(
            storage_key=str(rel),
            url=None,  # dev: sin URL pública; el operador lo ve en el server
            backend="local",
            filename=safe,
            content_type=content_type,
            size_bytes=len(data),
            uploaded_at=datetime.now(UTC).isoformat(),
        )


class HiDriveDocumentStorage:
    """Esqueleto WebDAV de HiDrive. La subida real (PUT WebDAV con auth
    básica) se implementa en Fase C con las credenciales de Bomedia; hasta
    entonces `get_document_storage()` no lo selecciona (no hay creds)."""

    def __init__(self, webdav_url: str, user: str, password: str):
        self.webdav_url = webdav_url.rstrip("/")
        self.user = user
        self.password = password

    def save(
        self, *, order_id: str, filename: str, content_type: str | None, data: bytes
    ) -> StoredDocument:  # pragma: no cover — pendiente de credenciales
        raise NotImplementedError(
            "HiDriveDocumentStorage.save se implementa en Fase C (WebDAV PUT). "
            "Sin credenciales HiDrive el sistema usa LocalDocumentStorage."
        )


def get_document_storage() -> DocumentStorage:
    settings = get_settings()
    if settings.hidrive_webdav_url and settings.hidrive_user and settings.hidrive_password:
        return HiDriveDocumentStorage(
            settings.hidrive_webdav_url, settings.hidrive_user, settings.hidrive_password
        )
    return LocalDocumentStorage(settings.erp_uploads_dir)
