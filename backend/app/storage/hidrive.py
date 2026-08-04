"""Storage HiDrive (WebDAV) — STUB (Fase D).

Reservado para cuando HiDrive tenga espacio disponible. Reusará el skeleton
WebDAV de Fase A (`HIDRIVE_WEBDAV_URL` + `HIDRIVE_USERNAME` +
`HIDRIVE_PASSWORD_ENCRYPTED` cifrado con Fernet). Hoy no está implementado:
cualquier operación lanza `NotImplementedError` con un mensaje claro para que
el operador sepa que debe seguir en `local` hasta migrar.
"""
from __future__ import annotations

_NOT_READY = (
    "HiDriveShippingStorage aún no está implementado (HiDrive sin espacio). "
    "Usa STORAGE_BACKEND=local hasta que se habilite HiDrive."
)


class HiDriveShippingStorage:
    def __init__(self) -> None:
        # No falla al construir para no romper el arranque si el env queda mal
        # puesto; falla en la primera operación real con un mensaje claro.
        pass

    def save(self, order_id: str, kind: str, filename: str, content: bytes) -> str:
        raise NotImplementedError(_NOT_READY)

    def read(self, storage_path: str) -> bytes:
        raise NotImplementedError(_NOT_READY)

    def delete(self, storage_path: str) -> None:
        raise NotImplementedError(_NOT_READY)
