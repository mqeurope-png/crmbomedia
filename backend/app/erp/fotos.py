"""ERP · fotos (y documentos) del embalaje: se guardan como `shipment_files`.

Antes (`POST /orders/{id}/attach-document`) iban a `erp_uploads_dir` —dentro
del contenedor `api`, SIN volumen— con la referencia en
`packing_json.documents` y `url: null`: cada despliegue las borraba
(07/10/2026). Ahora van por el mismo camino que el albarán y la etiqueta:
tabla `shipment_files` (`kind = foto`, `source = manual_upload`) y el bind
mount `/opt/crmbo/uploads/erp-shipping`, que persiste. Varias fotos conviven
(no se reemplazan unas a otras).

Formatos: lo que de verdad sacan los móviles. HEIC/HEIF (iPhone) y WebP
(bastantes Android) —y cualquier otra imagen que Pillow sepa leer— se
convierten a JPEG al guardar, para que se vean en cualquier navegador. JPEG y
PNG se guardan tal cual; PDF también (es «foto / documento»).
"""
from __future__ import annotations

import io
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

#: Tope por fichero (el mismo que tenía `attach-document`). El navegador
#: reduce las fotos grandes antes de subirlas, así que rara vez se alcanza.
MAX_FOTO_BYTES = 15 * 1024 * 1024
#: Lado mayor de la miniatura (tarjeta de la Cola SAT y ficha).
THUMB_PX = 320
#: Carpeta (dentro del volumen de expedición) donde se puede dejar, antes de
#: desplegar, lo que había en `erp_uploads_dir` para que la migración lo
#: recupere (ver `docs/erp/fotos-embalaje.md`).
RESCATE_DIRNAME = "_rescate"

MIME_PDF = "application/pdf"


class FotoError(ValueError):
    """El fichero no se puede guardar como foto/documento (formato)."""


@dataclass(frozen=True)
class FotoNormalizada:
    filename: str
    mime_type: str
    data: bytes
    convertida: bool = False


def _heif_disponible() -> bool:
    try:
        import pillow_heif  # noqa: PLC0415

        pillow_heif.register_heif_opener()
        return True
    except Exception:  # noqa: BLE001 — sin la librería, HEIC no se lee
        return False


def _stem(filename: str | None) -> str:
    base = os.path.basename(filename or "").strip() or "foto"
    stem, _ext = os.path.splitext(base)
    return stem or "foto"


def normalizar(filename: str | None, content_type: str | None, data: bytes) -> FotoNormalizada:
    """Deja la foto en un formato que cualquier navegador enseña: JPEG/PNG tal
    cual, PDF tal cual, y el resto de imágenes (HEIC/HEIF, WebP, TIFF…) a
    JPEG. Lanza `FotoError` si no es una imagen ni un PDF."""
    ctype = (content_type or "").lower()
    if data[:5] == b"%PDF-" or ctype == MIME_PDF:
        if data[:5] != b"%PDF-":
            raise FotoError("El PDF está dañado o no es un PDF.")
        return FotoNormalizada(f"{_stem(filename)}.pdf", MIME_PDF, data)

    from PIL import Image, ImageOps, UnidentifiedImageError  # noqa: PLC0415

    _heif_disponible()
    try:
        img = Image.open(io.BytesIO(data))
        formato = (img.format or "").upper()
        img.load()
    except Image.DecompressionBombError as exc:
        # Cabecera que dice tener muchísimos píxeles (fichero dañado o
        # malicioso): no se decodifica.
        raise FotoError("La imagen es demasiado grande o está dañada.") from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise FotoError(
            "No se reconoce el formato. Sube una foto (JPG, PNG, HEIC o WebP) o un PDF."
        ) from exc
    if formato in ("JPEG", "MPO"):          # MPO = JPEG de algunas cámaras
        return FotoNormalizada(f"{_stem(filename)}.jpg", "image/jpeg", data)
    if formato == "PNG":
        return FotoNormalizada(f"{_stem(filename)}.png", "image/png", data)
    # HEIC/HEIF, WebP, TIFF, BMP, GIF… → JPEG (orientación de la cámara aplicada).
    img = ImageOps.exif_transpose(img).convert("RGB")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88, optimize=True)
    return FotoNormalizada(f"{_stem(filename)}.jpg", "image/jpeg", out.getvalue(), True)


def miniatura(data: bytes, mime_type: str | None) -> bytes | None:
    """JPEG pequeño de una foto (None si no es una imagen legible)."""
    if not (mime_type or "").startswith("image/"):
        return None
    from PIL import Image, ImageOps  # noqa: PLC0415

    _heif_disponible()
    try:
        img = Image.open(io.BytesIO(data))
        # JPEG: decodifica ya reducido (mucho más rápido y con menos memoria que
        # la foto entera de 12 MP); en otros formatos no hace nada.
        img.draft("RGB", (THUMB_PX * 2, THUMB_PX * 2))
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((THUMB_PX, THUMB_PX))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=80)
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return None


def guardar_foto(
    session: Any, order: Any, *, filename: str | None, content_type: str | None,
    data: bytes, actor_id: str | None,
) -> Any:
    """Normaliza y guarda la foto como `shipment_files` (`kind = foto`). No
    reemplaza las anteriores: un pedido puede tener varias. Devuelve la fila
    (sin commit). Lanza `FotoError` (formato) o la excepción del almacén."""
    return guardar_normalizada(session, order, normalizar(filename, content_type, data),
                               actor_id=actor_id)


def guardar_normalizada(session: Any, order: Any, foto: FotoNormalizada, *,
                        actor_id: str | None) -> Any:
    """Guarda una foto ya normalizada (ver `guardar_foto`)."""
    from app.erp.api.shipping import _store_new_file  # noqa: PLC0415
    from app.erp.models.shipping import KIND_FOTO, SOURCE_MANUAL_UPLOAD  # noqa: PLC0415

    return _store_new_file(
        session, order, kind=KIND_FOTO, source=SOURCE_MANUAL_UPLOAD,
        filename=foto.filename, mime_type=foto.mime_type, data=foto.data,
        actor_id=actor_id, replace_previous=False,
    )


# --- migración de `packing_json.documents` ---------------------------------------------


def _candidatos(storage_key: str, uploads_dir: str, rescate_dir: str) -> list[Path]:
    rel = Path(storage_key)
    if rel.is_absolute() or ".." in rel.parts:
        return []
    return [Path(uploads_dir) / rel, Path(rescate_dir) / rel]


def migrar_documentos(
    conn: Any, *, uploads_dir: str, shipping_dir: str, storage: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Pasa las referencias de `packing_json.documents` a `shipment_files`.

    - Si el archivo existe todavía (en `uploads_dir` o en la carpeta de
      rescate del volumen de expedición) se copia al almacén de expedición y se
      registra como `kind = foto`.
    - Si se perdió (despliegue), NO se deja una referencia rota: se quita de
      `documents` y queda solo constancia en `packing_json.fotos_perdidas`
      (nombre y fecha de subida, sin ruta), para que la ficha avise.

    Trabaja con SQL de Core (no con el ORM de la app): la uso desde una
    migración de Alembic. Idempotente: sin `documents`, no hace nada. Un
    documento que falla se anota como perdido (con log) y no tumba la
    migración (ni el arranque del api). Escribe en el almacén LOCAL de
    expedición (`STORAGE_BACKEND=local`, el de producción)."""
    import sqlalchemy as sa  # noqa: PLC0415

    from app.storage.local import LocalShippingStorage  # noqa: PLC0415

    storage = storage or LocalShippingStorage(base_dir=shipping_dir)
    rescate_dir = str(Path(shipping_dir) / RESCATE_DIRNAME)
    ahora = now or datetime.now(UTC)
    movidas = 0
    perdidas = 0
    pedidos = 0
    filas = conn.execute(sa.text(
        "SELECT id, packing_json FROM orders WHERE packing_json LIKE :p"
    ), {"p": '%"documents"%'}).fetchall()
    for order_id, packing_json in filas:
        try:
            packing = json.loads(packing_json or "{}")
        except (TypeError, ValueError):
            continue
        docs = packing.get("documents") if isinstance(packing, dict) else None
        if not isinstance(docs, list):
            continue
        pedidos += 1
        perdidas_aqui = list(packing.get("fotos_perdidas") or [])
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            try:
                if _migrar_uno(conn, order_id, doc, uploads_dir, rescate_dir, storage, ahora):
                    movidas += 1
                    continue
                motivo = ("El archivo se perdió en un despliegue (se guardaba dentro "
                          "del contenedor). Hay que volver a subirlo.")
            except Exception as exc:  # noqa: BLE001 — uno no tumba el arranque del api
                logger.warning("fotos: no se pudo migrar %s del pedido %s: %s",
                               doc.get("filename"), order_id, exc)
                motivo = "No se pudo recuperar el archivo. Hay que volver a subirlo."
            perdidas += 1
            perdidas_aqui.append({
                "filename": doc.get("filename"), "uploaded_at": doc.get("uploaded_at"),
                "size_bytes": doc.get("size_bytes"), "motivo": motivo,
            })
        packing.pop("documents", None)
        if perdidas_aqui:
            packing["fotos_perdidas"] = perdidas_aqui
        conn.execute(sa.text("UPDATE orders SET packing_json = :j WHERE id = :id"),
                     {"j": json.dumps(packing, default=str), "id": order_id})
    if pedidos:
        logger.info("fotos: %d pedidos con documentos antiguos · %d movidas a shipment_files · "
                    "%d perdidas (sin referencia rota)", pedidos, movidas, perdidas)
    return {"pedidos": pedidos, "movidas": movidas, "perdidas": perdidas}


def _migrar_uno(conn: Any, order_id: str, doc: dict[str, Any], uploads_dir: str,
                rescate_dir: str, storage: Any, ahora: datetime) -> bool:
    """Mueve un documento al almacén de expedición y lo registra. False si el
    archivo ya no existe."""
    import sqlalchemy as sa  # noqa: PLC0415

    from app.erp.models.shipping import KIND_FOTO  # noqa: PLC0415

    key = str(doc.get("storage_key") or "")
    data = None
    if doc.get("backend", "local") == "local" and key:
        for path in _candidatos(key, uploads_dir, rescate_dir):
            if path.is_file():
                data = path.read_bytes()
                break
    if not data:
        return False
    try:
        foto = normalizar(doc.get("filename"), doc.get("content_type"), data)
    except FotoError:
        foto = FotoNormalizada(str(doc.get("filename") or "documento"),
                               str(doc.get("content_type") or "application/octet-stream"),
                               data)
    path = storage.save(order_id, KIND_FOTO, foto.filename, foto.data)
    subida = doc.get("uploaded_at") or ahora.isoformat()
    autor = doc.get("uploaded_by_user_id")
    if autor and conn.execute(sa.text("SELECT 1 FROM users WHERE id = :u"),
                              {"u": autor}).first() is None:
        autor = None                    # usuario borrado: sin autor
    conn.execute(sa.text(
        "INSERT INTO shipment_files (id, order_id, kind, source, filename, mime_type, "
        "size_bytes, storage_path, uploaded_by_user_id, uploaded_at, replaced_at) "
        "VALUES (:id, :o, :k, :s, :f, :m, :n, :p, :u, :t, NULL)"
    ), {"id": str(uuid4()), "o": order_id, "k": KIND_FOTO, "s": "manual_upload",
        "f": foto.filename, "m": foto.mime_type, "n": len(foto.data), "p": path,
        "u": autor, "t": _fecha(subida, ahora)})
    return True


def _fecha(valor: Any, defecto: datetime) -> datetime:
    try:
        dt = datetime.fromisoformat(str(valor))
    except ValueError:
        return defecto
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
