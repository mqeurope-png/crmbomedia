"""Test bootstrap.

Pydantic-settings now requires INTEGRATION_SECRETS_KEY at startup. We seed
a stable Fernet key into the environment before any application module is
imported so the rest of the test suite can import `app.main` without
contacting the real environment. Tests that exercise the fail-fast path
must use monkeypatch.delenv on this variable explicitly.
"""
import os

from cryptography.fernet import Fernet

os.environ.setdefault("INTEGRATION_SECRETS_KEY", Fernet.generate_key().decode())
