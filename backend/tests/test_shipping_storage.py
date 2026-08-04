"""BoHub ERP Fase D · PR D-1 — storage local de ficheros de expedición."""
from __future__ import annotations

import pytest

from app.storage.base import StorageError
from app.storage.hidrive import HiDriveShippingStorage
from app.storage.local import LocalShippingStorage


def test_local_shipping_storage_roundtrip(tmp_path):
    st = LocalShippingStorage(base_dir=str(tmp_path))
    path = st.save("order-1", "albaran", "mi_albaran.pdf", b"%PDF-1.4 data")
    # Ruta relativa {order}/{kind}/{uuid}_{filename}.
    assert path.startswith("order-1/albaran/")
    assert path.endswith("_mi_albaran.pdf")
    # El fichero está en disco y se lee igual.
    assert (tmp_path / path).exists()
    assert st.read(path) == b"%PDF-1.4 data"
    # Delete borra e idempotente.
    st.delete(path)
    assert not (tmp_path / path).exists()
    st.delete(path)  # no lanza aunque ya no exista


def test_local_shipping_storage_sanitises_filename(tmp_path):
    st = LocalShippingStorage(base_dir=str(tmp_path))
    path = st.save("o", "etiqueta", "../../etc/passwd", b"x")
    # El nombre se sanea: sin separadores de ruta ni «..».
    assert ".." not in path
    assert path.startswith("o/etiqueta/")
    assert st.read(path) == b"x"


def test_local_shipping_storage_rejects_path_traversal_on_read(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (tmp_path / "secret.txt").write_bytes(b"top secret")
    st = LocalShippingStorage(base_dir=str(base))
    with pytest.raises(StorageError):
        st.read("../secret.txt")


def test_hidrive_stub_raises_not_implemented():
    st = HiDriveShippingStorage()
    with pytest.raises(NotImplementedError):
        st.save("o", "albaran", "f.pdf", b"x")
    with pytest.raises(NotImplementedError):
        st.read("whatever")


def test_generate_albaran_pdf_produces_valid_pdf():
    from app.erp.albaran_pdf import generate_albaran_pdf

    pdf = generate_albaran_pdf({
        "number": "BOP-123",
        "shipping": {"first_name": "Ana", "last_name": "Pi",
                     "address_1": "C Aribau 171", "city": "Barcelona"},
        "line_items": [{"sku": "A1", "name": "Artículo 1", "quantity": 2}],
    })
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 500  # no es un PDF vacío


def test_generate_albaran_pdf_handles_missing_fields():
    from app.erp.albaran_pdf import generate_albaran_pdf

    # Sin líneas ni dirección no debe petar.
    pdf = generate_albaran_pdf({"id": 5})
    assert pdf[:5] == b"%PDF-"
