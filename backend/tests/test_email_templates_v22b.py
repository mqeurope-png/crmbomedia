"""Sprint Email v2.2b — proxy + upload + merge-vars tests."""
from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.email_templates import services as et_services
from app.email_templates.services import (
    has_merge_tokens,
    replace_merge_vars,
    reset_composer_cache,
)
from app.main import app
from app.models.crm import Base, Company, Contact
from tests._test_helpers import auth_headers, seed_test_users


@pytest.fixture()
def session_factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as seed:
        seed_test_users(seed)
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(
    session_factory: sessionmaker, tmp_path: Path
) -> Generator[TestClient, None, None]:
    # Override settings so the image-upload tests write into a tmpdir.
    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    base_settings = get_settings()
    overridden = Settings(
        **{
            **base_settings.model_dump(),
            "email_image_upload_dir": str(tmp_path / "imgs"),
            "email_image_max_bytes": 1024,
        }
    )
    app.dependency_overrides[get_session] = override_session
    with patch.object(et_services, "get_settings", return_value=overridden):
        with patch(
            "app.email_templates.router.get_settings",
            return_value=overridden,
        ):
            reset_composer_cache()
            with TestClient(app) as test_client:
                yield test_client
    app.dependency_overrides.clear()
    reset_composer_cache()


# ───────────────────────────────────────────────────────────────────
# Merge variables — helpers
# ───────────────────────────────────────────────────────────────────


def _make_contact(
    *, first_name: str = "Ana", email: str = "ana@example.com", company_name: str | None = None
) -> Contact:
    company = Company(id="c1", name=company_name) if company_name else None
    contact = Contact(
        id="ct1",
        first_name=first_name,
        email=email,
        tags="",
    )
    if company is not None:
        contact.company = company
    return contact


def test_replace_merge_vars_substitutes_all_three() -> None:
    contact = _make_contact(
        first_name="Bart", email="bart@bomedia.net", company_name="Bomedia"
    )
    html = "<p>Hola {nombre}, escribo a {email} desde {empresa}.</p>"
    out = replace_merge_vars(html, contact)
    assert out == "<p>Hola Bart, escribo a bart@bomedia.net desde Bomedia.</p>"


def test_replace_merge_vars_returns_none_for_none() -> None:
    assert replace_merge_vars(None, _make_contact()) is None


def test_replace_merge_vars_passthrough_when_no_contact() -> None:
    text = "Hola {nombre}"
    assert replace_merge_vars(text, None) == text


def test_replace_merge_vars_empty_strings_for_missing_company() -> None:
    contact = _make_contact()
    assert replace_merge_vars("desde {empresa}", contact) == "desde "


def test_has_merge_tokens_detects_each_placeholder() -> None:
    assert has_merge_tokens("Hola {nombre}") is True
    assert has_merge_tokens("Tu {empresa}") is True
    assert has_merge_tokens("Tu {email}") is True
    assert has_merge_tokens("Sin tokens") is False
    assert has_merge_tokens(None) is False


# ───────────────────────────────────────────────────────────────────
# Composer-source proxy
# ───────────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload: list[dict] | dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> list[dict] | dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "boom", request=None, response=None  # type: ignore[arg-type]
            )


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def get(self, *_: object, **__: object) -> _FakeResponse:
        return self._response


def test_composer_source_returns_not_configured_when_unset(
    client: TestClient,
) -> None:
    headers = auth_headers(client, role="user")
    response = client.get(
        "/api/emails/composer-source", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert "no está configurado" in body["error"].lower()


def test_composer_source_normalises_templates(client: TestClient) -> None:
    headers = auth_headers(client, role="user")
    # composer.bomedia.net's current shape: state lives under `data`.
    payload = [
        {
            "id": "main",
            "data": {
                "templates": [
                    {
                        "id": "tpl-1",
                        "name": "Pimpam Hero",
                        "brand": "pimpam",
                        "blocks": [
                            {"type": "hero"},
                            {"type": "product_single"},
                        ],
                    },
                    {"id": "tpl-2", "compositorBlocks": []},
                    {"name": "no id, skipped"},
                ],
            },
        }
    ]
    with patch.object(
        et_services, "get_settings",
        return_value=Settings(
            **{
                **get_settings().model_dump(),
                "supabase_composer_url": "https://supa.example",
                "supabase_composer_key": "secret",
            }
        ),
    ), patch.object(
        et_services.httpx, "Client",
        return_value=_FakeClient(_FakeResponse(payload)),
    ):
        response = client.get(
            "/api/emails/composer-source", headers=headers
        )
    assert response.status_code == 200
    body = response.json()
    assert body["error"] is None
    assert len(body["items"]) == 2
    first = body["items"][0]
    assert first["id"] == "tpl-1"
    assert first["name"] == "Pimpam Hero"
    assert first["brand"] == "pimpam"
    assert first["blocks_count"] == 2
    assert first["open_url"].endswith("?template=tpl-1")
    # No-name fallback works.
    assert body["items"][1]["name"] == "Sin nombre"


def test_composer_source_supports_legacy_root_shape(
    client: TestClient,
) -> None:
    """Older snapshots stored the state at the row root, not inside
    `data`. The proxy must keep working until the Composer migration
    is fully rolled out."""
    headers = auth_headers(client, role="user")
    payload = [
        {
            "id": "main",
            "templates": [
                {"id": "legacy-1", "name": "Old hero", "blocks": []}
            ],
        }
    ]
    with patch.object(
        et_services, "get_settings",
        return_value=Settings(
            **{
                **get_settings().model_dump(),
                "supabase_composer_url": "https://supa.example",
                "supabase_composer_key": "secret",
            }
        ),
    ), patch.object(
        et_services.httpx, "Client",
        return_value=_FakeClient(_FakeResponse(payload)),
    ):
        response = client.get(
            "/api/emails/composer-source", headers=headers
        )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == "legacy-1"


def test_composer_source_handles_supabase_failure(client: TestClient) -> None:
    headers = auth_headers(client, role="user")

    class _RaisingClient:
        def __enter__(self) -> _RaisingClient:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def get(self, *_: object, **__: object) -> _FakeResponse:
            raise httpx.ConnectError("boom")

    with patch.object(
        et_services, "get_settings",
        return_value=Settings(
            **{
                **get_settings().model_dump(),
                "supabase_composer_url": "https://supa.example",
                "supabase_composer_key": "secret",
            }
        ),
    ), patch.object(et_services.httpx, "Client", return_value=_RaisingClient()):
        response = client.get(
            "/api/emails/composer-source", headers=headers
        )
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert "composer.bomedia.net" in body["error"]


# ───────────────────────────────────────────────────────────────────
# Image upload
# ───────────────────────────────────────────────────────────────────


def test_upload_image_accepts_png(client: TestClient) -> None:
    headers = auth_headers(client, role="user")
    response = client.post(
        "/api/emails/upload-image",
        files={"file": ("x.png", b"\x89PNG fake bytes", "image/png")},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["url"].startswith("/uploads/email_images/")
    assert body["url"].endswith(".png")
    assert body["content_type"] == "image/png"
    assert body["size_bytes"] == len(b"\x89PNG fake bytes")


def test_upload_image_rejects_svg(client: TestClient) -> None:
    headers = auth_headers(client, role="user")
    response = client.post(
        "/api/emails/upload-image",
        files={"file": ("evil.svg", b"<svg/>", "image/svg+xml")},
        headers=headers,
    )
    assert response.status_code == 415


def test_upload_image_rejects_oversized(client: TestClient) -> None:
    headers = auth_headers(client, role="user")
    # The fixture caps email_image_max_bytes at 1024.
    response = client.post(
        "/api/emails/upload-image",
        files={"file": ("big.png", b"0" * 2048, "image/png")},
        headers=headers,
    )
    assert response.status_code == 413
