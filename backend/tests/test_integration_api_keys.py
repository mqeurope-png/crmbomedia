"""Tests for encrypted API key storage on integration_settings."""
from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import crypto
from app.core.security import hash_password
from app.db.session import get_session
from app.main import app
from app.models.crm import Base, ExternalSystem, User, UserRole
from app.models.integration_settings import IntegrationSetting


@dataclass
class Stack:
    client: TestClient
    engine: Engine


@pytest.fixture()
def stack() -> Generator[Stack, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with testing_session() as seed_session:
        for role in UserRole:
            seed_session.add(
                User(
                    email=f"{role.value}@example.com",
                    full_name=f"{role.value.title()} User",
                    password_hash=hash_password("password123"),
                    role=role,
                    is_active=True,
                )
            )
        seed_session.commit()

    def override_session() -> Generator[Session, None, None]:
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield Stack(client=test_client, engine=engine)
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(stack: Stack) -> TestClient:
    return stack.client


def auth_headers(client: TestClient, role: str = "admin") -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"email": f"{role}@example.com", "password": "password123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_encrypt_decrypt_roundtrips_arbitrary_secret():
    crypto._fernet.cache_clear()
    plaintext = "sk_live_abc123-XYZ.,!?#$%&*"
    ciphertext = crypto.encrypt(plaintext)

    assert ciphertext != plaintext
    assert crypto.decrypt(ciphertext) == plaintext


def test_encrypt_rejects_blank_plaintext():
    with pytest.raises(ValueError):
        crypto.encrypt("")


def test_decrypt_with_wrong_key_raises_decryption_error(monkeypatch):
    crypto._fernet.cache_clear()
    other_key = Fernet.generate_key().decode()
    foreign_ciphertext = Fernet(other_key.encode()).encrypt(b"hello").decode()

    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(foreign_ciphertext)


def test_get_integration_setting_never_returns_plaintext(client: TestClient):
    headers = auth_headers(client, "admin")
    client.put(
        "/api/integration-settings/brevo/api-key",
        json={"api_key": "secret-brevo-key-xyz"},
        headers=headers,
    )

    response = client.get("/api/integration-settings/brevo", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["has_api_key"] is True
    assert body["api_key_set_at"] is not None
    assert "api_key" not in body
    assert "api_key_encrypted" not in body
    assert "secret-brevo-key-xyz" not in response.text


def test_list_integration_settings_never_returns_plaintext(client: TestClient):
    headers = auth_headers(client, "admin")
    client.put(
        "/api/integration-settings/agilecrm/api-key",
        json={"api_key": "secret-agilecrm-key-zzz"},
        headers=headers,
    )

    response = client.get("/api/integration-settings", headers=headers)

    assert response.status_code == 200
    assert "secret-agilecrm-key-zzz" not in response.text
    by_system = {item["system"]: item for item in response.json()}
    assert by_system["agilecrm"]["has_api_key"] is True
    assert "api_key_encrypted" not in by_system["agilecrm"]


def test_put_api_key_requires_admin(client: TestClient):
    response = client.put(
        "/api/integration-settings/brevo/api-key",
        json={"api_key": "secret-brevo-key"},
        headers=auth_headers(client, "manager"),
    )

    assert response.status_code == 403


def test_delete_api_key_requires_admin(client: TestClient):
    response = client.delete(
        "/api/integration-settings/brevo/api-key",
        headers=auth_headers(client, "manager"),
    )

    assert response.status_code == 403


def test_put_api_key_persists_ciphertext_not_plaintext(stack: Stack):
    headers = auth_headers(stack.client, "admin")
    stack.client.put(
        "/api/integration-settings/freshdesk/api-key",
        json={"api_key": "secret-freshdesk-key"},
        headers=headers,
    )

    with Session(stack.engine) as session:
        setting = session.query(IntegrationSetting).filter_by(system=ExternalSystem.FRESHDESK).one()
        assert setting.api_key_encrypted is not None
        assert setting.api_key_encrypted != "secret-freshdesk-key"
        assert setting.api_key_set_at is not None
        assert setting.credential_status == "configured"
        assert crypto.decrypt(setting.api_key_encrypted) == "secret-freshdesk-key"


def test_delete_api_key_clears_state(client: TestClient):
    headers = auth_headers(client, "admin")
    client.put(
        "/api/integration-settings/factusol/api-key",
        json={"api_key": "secret-factusol-key"},
        headers=headers,
    )

    deleted = client.delete(
        "/api/integration-settings/factusol/api-key", headers=headers
    )

    assert deleted.status_code == 200
    body = deleted.json()
    assert body["has_api_key"] is False
    assert body["api_key_set_at"] is None
    assert body["credential_status"] == "not_configured"


def test_audit_log_records_set_and_delete_without_secret(client: TestClient):
    headers = auth_headers(client, "admin")
    client.put(
        "/api/integration-settings/brevo/api-key",
        json={"api_key": "must-not-leak-secret"},
        headers=headers,
    )
    client.delete("/api/integration-settings/brevo/api-key", headers=headers)

    response = client.get("/api/audit-logs", headers=headers)

    assert response.status_code == 200
    actions = {entry["action"] for entry in response.json()}
    assert "set_integration_api_key" in actions
    assert "delete_integration_api_key" in actions
    assert "must-not-leak-secret" not in response.text


def test_put_api_key_rejects_blank(client: TestClient):
    headers = auth_headers(client, "admin")

    response = client.put(
        "/api/integration-settings/brevo/api-key",
        json={"api_key": "   "},
        headers=headers,
    )

    assert response.status_code == 422


def test_get_decrypted_api_key_helper_updates_last_used(monkeypatch):
    """The connector helper must return plaintext and bump api_key_last_used_at."""
    from app.integrations.credentials import get_decrypted_api_key

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    monkeypatch.setattr("app.integrations.credentials.get_engine", lambda: engine)

    with Session(engine) as setup:
        setting = IntegrationSetting(
            system=ExternalSystem.AGILECRM,
            display_name="AgileCRM",
            api_key_encrypted=crypto.encrypt("plain-agilecrm"),
        )
        setup.add(setting)
        setup.commit()
        assert setting.api_key_last_used_at is None

    plaintext = get_decrypted_api_key(ExternalSystem.AGILECRM)
    assert plaintext == "plain-agilecrm"

    with Session(engine) as check:
        refreshed = check.query(IntegrationSetting).filter_by(
            system=ExternalSystem.AGILECRM
        ).one()
        assert refreshed.api_key_last_used_at is not None


def test_get_decrypted_api_key_returns_none_when_missing(monkeypatch):
    from app.integrations.credentials import get_decrypted_api_key

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr("app.integrations.credentials.get_engine", lambda: engine)

    with Session(engine) as setup:
        setup.add(IntegrationSetting(system=ExternalSystem.BREVO, display_name="Brevo"))
        setup.commit()

    assert get_decrypted_api_key(ExternalSystem.BREVO) is None


def test_settings_fail_fast_when_integration_secrets_key_missing(monkeypatch):
    """Without INTEGRATION_SECRETS_KEY the app must refuse to construct Settings."""
    from pydantic import ValidationError

    from app.core.config import Settings

    monkeypatch.delenv("INTEGRATION_SECRETS_KEY", raising=False)

    with pytest.raises(ValidationError):
        # _env_file=None bypasses the .env file fallback so we test the env-only path.
        Settings(_env_file=None)


def test_settings_rejects_invalid_fernet_key(monkeypatch):
    from pydantic import ValidationError

    from app.core.config import Settings

    monkeypatch.setenv("INTEGRATION_SECRETS_KEY", "this-is-not-a-fernet-key")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_accepts_valid_fernet_key():
    from app.core.config import Settings

    valid_key = Fernet.generate_key().decode()
    settings = Settings(_env_file=None, integration_secrets_key=valid_key)
    assert settings.integration_secrets_key == valid_key


