"""Test bootstrap.

Pydantic-settings now requires INTEGRATION_SECRETS_KEY at startup. We seed
a stable Fernet key into the environment before any application module is
imported so the rest of the test suite can import `app.main` without
contacting the real environment. Tests that exercise the fail-fast path
must use monkeypatch.delenv on this variable explicitly.
"""
import os

import pytest
from cryptography.fernet import Fernet

os.environ.setdefault("INTEGRATION_SECRETS_KEY", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def _clear_factusol_chain_caches():
    """ERP-E3-B: los caches en proceso del módulo `chain` (allowlists de
    columnas vivas, índice del ciclo) sobrevivirían de un test a otro y
    envenenarían cualquier test que monte tablas FACTUSOL distintas con el
    mismo ejercicio."""
    from app.integrations.factusol import chain

    chain._LIVE_COLUMNS_CACHE.clear()
    chain._CHAIN_INDEX_CACHE.clear()
    yield
    chain._LIVE_COLUMNS_CACHE.clear()
    chain._CHAIN_INDEX_CACHE.clear()
