"""BoHub ERP — almacenamiento de documentos (Fase A PR 5)."""
from app.erp.storage.document_storage import (
    DocumentStorage,
    HiDriveDocumentStorage,
    LocalDocumentStorage,
    StoredDocument,
    get_document_storage,
)

__all__ = [
    "DocumentStorage",
    "HiDriveDocumentStorage",
    "LocalDocumentStorage",
    "StoredDocument",
    "get_document_storage",
]
