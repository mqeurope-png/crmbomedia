"""Storage en disco local del VPS (Fase D).

Estructura: `{base_dir}/{order_id}/{kind}/{uuid}_{filename_saneado}`.
La `storage_path` que se persiste es **relativa** a `base_dir` (portable si el
directorio base cambia). `read`/`delete` la resuelven contra `base_dir` y
verifican que no se escapa del árbol (defensa ante `..`).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import uuid4

from app.storage.base import StorageError

#: Solo se conservan estos caracteres del nombre original; el resto → "_".
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(filename: str) -> str:
    base = os.path.basename(filename or "").strip() or "archivo"
    cleaned = _SAFE_CHARS.sub("_", base).lstrip(".") or "archivo"
    return cleaned[:200]


class LocalShippingStorage:
    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir).resolve()

    def _abs(self, storage_path: str) -> Path:
        # Resuelve la ruta relativa y garantiza que queda dentro de base_dir.
        target = (self.base_dir / storage_path).resolve()
        if self.base_dir not in target.parents and target != self.base_dir:
            raise StorageError(f"Ruta fuera del storage: {storage_path!r}")
        return target

    def save(self, order_id: str, kind: str, filename: str, content: bytes) -> str:
        safe_order = _safe_filename(order_id)
        safe_kind = _safe_filename(kind)
        rel = f"{safe_order}/{safe_kind}/{uuid4().hex}_{_safe_filename(filename)}"
        target = self._abs(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_bytes(content)
        except OSError as exc:  # disco lleno, permisos…
            raise StorageError(f"No se pudo guardar el fichero: {exc}") from exc
        return rel

    def read(self, storage_path: str) -> bytes:
        target = self._abs(storage_path)
        try:
            return target.read_bytes()
        except FileNotFoundError as exc:
            raise StorageError(f"Fichero no encontrado: {storage_path!r}") from exc
        except OSError as exc:
            raise StorageError(f"No se pudo leer el fichero: {exc}") from exc

    def delete(self, storage_path: str) -> None:
        target = self._abs(storage_path)
        try:
            target.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(f"No se pudo borrar el fichero: {exc}") from exc
