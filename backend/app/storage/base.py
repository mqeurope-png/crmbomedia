"""Interfaz de storage de ficheros de expedición (Fase D)."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


class StorageError(RuntimeError):
    """Fallo de storage (I/O, backend no implementado, ruta inválida…)."""


@runtime_checkable
class ShippingStorage(Protocol):
    """Guarda/lee/borra los bytes de un albarán o etiqueta.

    `save` devuelve una **ruta relativa** opaca (`storage_path`) que se
    persiste en `shipment_files`; `read`/`delete` la reciben de vuelta. El
    formato de la ruta es asunto del backend concreto — la capa de API no lo
    interpreta.
    """

    def save(self, order_id: str, kind: str, filename: str, content: bytes) -> str:
        """Persiste `content` y devuelve la `storage_path` relativa."""
        ...

    def read(self, storage_path: str) -> bytes:
        """Devuelve los bytes guardados en `storage_path`."""
        ...

    def delete(self, storage_path: str) -> None:
        """Borra el fichero en `storage_path` (idempotente)."""
        ...
