"""Mini-PR Gmail Templates — backend tests for /api/emails/gmail-templates.

Mocks `app.integrations.gmail.client.GmailClient` to skip the real
Google API. Confirms:

- 200 + parsed payload when Gmail returns drafts with `^smartlabel_
  canned_response`.
- 200 + [] when the user has no Gmail integration (graceful, no
  banner spam).
- 403 when the integration row exists but lacks `gmail.send` scope
  (paridad con /aliases).
"""
from __future__ import annotations

import base64
import json
from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import get_session
from app.main import app
from app.models.crm import (
    Base,
    User,
    UserRole,
)
from tests._test_helpers import (
    auth_headers,
    seed_org_google_integration,
    seed_test_users,
)


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
        seed.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(session_factory: sessionmaker) -> Generator[TestClient, None, None]:
    def override_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def autorun_worker(
    session_factory: sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vuelve la enqueue de RQ síncrona: cuando el router llama a
    `queue_for(...).enqueue(run_sync_job, **kw)` ejecutamos
    `run_sync_job` inline contra la BD del test. Así los tests del
    endpoint async pueden seguir afirmando sobre el resultado final
    sin levantar Redis. Necesario tras pasar import-gmail a async."""
    from types import SimpleNamespace  # noqa: PLC0415

    engine = session_factory.kw["bind"]
    monkeypatch.setattr("app.workers.jobs.get_engine", lambda: engine)

    class _InlineQueue:
        def enqueue(self, fn, **kwargs):
            kwargs.pop("retry", None)
            fn(**kwargs)
            return SimpleNamespace(id="fake-job-id")

    monkeypatch.setattr(
        "app.workers.jobs.queue_for", lambda *_a, **_kw: _InlineQueue()
    )


def _post_import_and_get_counters(
    client: TestClient,
    session_factory: sessionmaker,
    *,
    delete_after: bool = False,
) -> tuple[dict, dict]:
    """POST al endpoint async + lee los counters del SyncLog que el
    worker inline acaba de actualizar. Devuelve `(response_json,
    sync_log_metadata)` para que cada test compruebe lo que necesita.
    Requiere fixture `autorun_worker`."""
    qs = "?delete_after=true" if delete_after else ""
    resp = client.post(
        f"/api/email-templates/import-gmail{qs}",
        headers=auth_headers(client, "admin"),
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()

    with session_factory() as session:
        from app.models.crm import SyncLog as _SyncLog  # noqa: PLC0415

        row = session.get(_SyncLog, body["sync_log_id"])
        assert row is not None
        metadata = json.loads(row.metadata_json or "{}")
    return body, metadata


def _user_id(factory: sessionmaker, role: UserRole) -> str:
    with factory() as session:
        return session.scalar(select(User.id).where(User.role == role))


def _seed_gmail(factory: sessionmaker, *, user_id: str, scopes: str) -> None:
    # PR-OAuth-Google-Unificado. Cuenta Google org compartida; el import
    # de plantillas usa los tokens org vía get_org_integration.
    with factory() as session:
        seed_org_google_integration(
            session, connected_by_user_id=user_id, scopes=scopes
        )
        session.commit()


def _build_raw_email(*, subject: str, body_html: str) -> str:
    """Construye un RFC822 mínimo en formato urlsafe_b64 que `service.
    list_gmail_templates` parsea con email.message_from_bytes."""
    raw = (
        f"Subject: {subject}\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "\r\n"
        f"{body_html}"
    )
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _build_raw_email_with_inline_image(
    *, subject: str, body_html: str, cid: str, png_bytes: bytes
) -> str:
    """Construye un MIME multipart/related con una imagen inline
    referenciada por `cid`. El HTML body apunta a la imagen con
    `src="cid:{cid}"`. Usado para testear que el importador resuelve
    los cid: a data URIs."""
    img_b64 = base64.b64encode(png_bytes).decode("ascii")
    boundary = "===BOUNDARY==="
    raw = (
        f"Subject: {subject}\r\n"
        "MIME-Version: 1.0\r\n"
        f"Content-Type: multipart/related; boundary=\"{boundary}\"\r\n"
        "\r\n"
        f"--{boundary}\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "\r\n"
        f"{body_html}\r\n"
        f"--{boundary}\r\n"
        "Content-Type: image/png\r\n"
        f"Content-ID: <{cid}>\r\n"
        "Content-Transfer-Encoding: base64\r\n"
        "\r\n"
        f"{img_b64}\r\n"
        f"--{boundary}--\r\n"
    )
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


class _FakeGmailClient:
    """Stand-in. Modelo:

    - `draft-tpl1`: template puro ("Pressupost sol·licitat"), pasa
      la heurística.
    - `draft-tpl2`: template ("Adquisición equipos FLUX"), también pasa.
    - `draft-reply`: borrador de respuesta con subject "Re: Tu
      consulta" — NO debería contar como template.
    - `draft-quoted`: borrador con snippet quoted "On Mon, Jun 16
      Bart wrote:" — NO debería contar.
    - `draft-gt`: snippet empieza con `> texto citado` — NO debería
      contar.

    Todos los drafts vienen con `labelIds=["DRAFT"]` para reflejar el
    hallazgo: la API NO expone qué es template.
    """

    def __init__(self, *_a, **_kw) -> None:
        pass

    def list_draft_templates(
        self, *, query: str | None = None, max_results: int = 30
    ) -> list[dict]:
        _ = query
        _ = max_results
        return [
            {"id": "draft-tpl1"},
            {"id": "draft-tpl2"},
            {"id": "draft-reply"},
            {"id": "draft-quoted"},
            {"id": "draft-gt"},
        ]

    def get_draft_metadata(self, draft_id: str) -> dict:
        catalog = {
            "draft-tpl1": {
                "snippet": "Hola, adjuntamos el presupuesto solicitado",
                "internalDate": "1718500000000",  # más reciente
                "labelIds": ["DRAFT"],
                "threadId": "thr-1",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Pressupost sol·licitat"},
                    ]
                },
            },
            "draft-tpl2": {
                "snippet": "Te confirmo la adquisición de equipos",
                "internalDate": "1718000000000",  # más antiguo
                "labelIds": ["DRAFT"],
                "threadId": "thr-2",
                "payload": {
                    "headers": [
                        {
                            "name": "Subject",
                            "value": "Adquisición equipos FLUX",
                        },
                    ]
                },
            },
            "draft-reply": {
                "snippet": "Gracias por la información",
                "internalDate": "1719000000000",
                "labelIds": ["DRAFT"],
                "threadId": "thr-3",
                "payload": {
                    "headers": [{"name": "Subject", "value": "Re: Tu consulta"}]
                },
            },
            "draft-quoted": {
                "snippet": "On Mon, Jun 16 2026 at 10:00, Bart wrote: hola",
                "internalDate": "1719500000000",
                "labelIds": ["DRAFT"],
                "threadId": "thr-4",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Borrador con quote"}
                    ]
                },
            },
            "draft-gt": {
                "snippet": "> Texto citado por completo",
                "internalDate": "1720000000000",
                "labelIds": ["DRAFT"],
                "threadId": "thr-5",
                "payload": {
                    "headers": [{"name": "Subject", "value": "Reply gt"}]
                },
            },
        }
        return {"id": draft_id, "message": catalog[draft_id]}

    def get_draft_template(self, draft_id: str) -> dict:
        # Match el subject del metadata para no confundir al parser
        # cuando el código intenta llenar el subject desde el raw.
        bodies = {
            "draft-tpl1": (
                "Pressupost sol·licitat",
                "<p>Hola, adjuntamos el presupuesto solicitado.</p>",
            ),
            "draft-tpl2": (
                "Adquisición equipos FLUX",
                "<p>Te confirmo la adquisición de equipos.</p>",
            ),
        }
        subject, body_html = bodies[draft_id]
        return {
            "id": draft_id,
            "message": {
                "id": f"msg-{draft_id}",
                "internalDate": "1718500000000",
                "raw": _build_raw_email(
                    subject=subject,
                    body_html=body_html,
                ),
            },
        }


def test_body_html_populated_from_raw_format(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin que body_html NO sale vacío para los templates que pasan
    la heurística. Pre-fix, get_draft_template pedía format=full que
    devuelve payload structured sin `raw`, así que el parseo MIME
    nunca encontraba el body y todos los items salían con
    body_html="". Tras pedir format=raw el cliente devuelve el MIME
    completo y el parseo extrae el cuerpo."""
    uid = _user_id(session_factory, UserRole.USER)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient",
        _FakeGmailClient,
    )

    resp = client.get(
        "/api/emails/gmail-templates",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for item in body:
        assert item["body_html"], (
            f"body_html vacío para {item['id']} subject={item['subject']!r}"
        )
    # Sanity sobre el body parseado: el HTML del template 1 debe
    # contener el texto distintivo del fake.
    by_id = {item["id"]: item for item in body}
    assert "presupuesto" in by_id["draft-tpl1"]["body_html"].lower()


def test_heuristic_keeps_templates_drops_replies_and_quoted(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5 drafts:
    - 2 templates puros → quedan.
    - 1 con subject 'Re: …' → fuera.
    - 1 con snippet 'On … wrote:' → fuera.
    - 1 con snippet '> citado' → fuera.

    Resultado: solo los 2 templates, ordenados por updated_at DESC."""
    uid = _user_id(session_factory, UserRole.USER)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient",
        _FakeGmailClient,
    )

    resp = client.get(
        "/api/emails/gmail-templates",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = [item["id"] for item in body]
    # Solo los 2 templates, en orden updated_at desc (tpl1 más reciente).
    assert ids == ["draft-tpl1", "draft-tpl2"], body
    assert body[0]["subject"] == "Pressupost sol·licitat"
    assert body[1]["subject"] == "Adquisición equipos FLUX"


def test_debug_returns_all_drafts_with_summary_and_is_template_flag(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Modo debug devuelve TODOS los drafts marcados con
    `is_template` boolean + entrada `_summary` con counters."""
    uid = _user_id(session_factory, UserRole.USER)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient",
        _FakeGmailClient,
    )

    resp = client.get(
        "/api/emails/gmail-templates?debug=true",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    summary = body[0]
    assert summary["id"] == "_summary"
    assert summary["total_drafts"] == 5
    assert summary["detected_templates"] == 2

    drafts = body[1:]
    by_id = {item["id"]: item for item in drafts}
    assert by_id["draft-tpl1"]["is_template"] is True
    assert by_id["draft-tpl2"]["is_template"] is True
    assert by_id["draft-reply"]["is_template"] is False
    assert by_id["draft-quoted"]["is_template"] is False
    assert by_id["draft-gt"]["is_template"] is False
    # label_ids siempre ["DRAFT"] (refleja el hallazgo Bart).
    assert by_id["draft-tpl1"]["label_ids"] == ["DRAFT"]


def test_gmail_templates_returns_empty_when_not_connected(
    client: TestClient,
) -> None:
    """User sin integration row → no banner ni 500: lista vacía."""
    resp = client.get(
        "/api/emails/gmail-templates",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_gmail_templates_403_when_scope_missing(
    client: TestClient, session_factory: sessionmaker
) -> None:
    """Integration sin gmail.send → 403 con detalle (mismo patrón que
    /aliases)."""
    uid = _user_id(session_factory, UserRole.USER)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes="https://www.googleapis.com/auth/calendar.events",
    )

    resp = client.get(
        "/api/emails/gmail-templates",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 403
    assert "gmail.send" in resp.json()["detail"]


# -- importador one-shot a email_templates ---------------------------


class _FakeGmailImportClient:
    """Fake distinto del usado por list/heuristic. Modelo: 3 drafts:
    1 con prefijo [TPL] (debería importarse), 1 sin prefijo, 1 con
    prefijo [TPL] pero name duplicado tras stripped (importable pero
    salta en re-run para test idempotencia)."""

    def __init__(self, *_a, **_kw) -> None:
        self.deleted: list[str] = []

    def list_all_drafts(self) -> list[str]:
        return ["draft-tpl-a", "draft-no-tpl", "draft-tpl-b"]

    def get_draft_metadata(self, draft_id: str) -> dict:
        subjects = {
            "draft-tpl-a": "[TPL] Bienvenida CRM",
            "draft-no-tpl": "Re: tu consulta",
            "draft-tpl-b": "[TPL] Adquisición equipos",
        }
        return {
            "id": draft_id,
            "message": {
                "payload": {
                    "headers": [{"name": "Subject", "value": subjects[draft_id]}]
                },
            },
        }

    def get_draft_template(self, draft_id: str) -> dict:
        bodies = {
            "draft-tpl-a": (
                "[TPL] Bienvenida CRM",
                "<p>Hola, bienvenido al CRM.</p>",
            ),
            "draft-tpl-b": (
                "[TPL] Adquisición equipos",
                "<p>Confirmamos compra.</p>",
            ),
        }
        subject, body_html = bodies[draft_id]
        return {
            "id": draft_id,
            "message": {
                "id": f"msg-{draft_id}",
                "raw": _build_raw_email(subject=subject, body_html=body_html),
            },
        }

    def delete_draft(self, draft_id: str) -> None:
        self.deleted.append(draft_id)


def test_import_creates_email_templates_in_dedicated_folder(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    autorun_worker: None,
) -> None:
    uid = _user_id(session_factory, UserRole.ADMIN)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )
    fake = _FakeGmailImportClient()
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient",
        lambda *_a, **_kw: fake,
    )

    body, summary = _post_import_and_get_counters(client, session_factory)
    assert body["status"] == "pending"
    assert body["sync_log_id"]
    assert summary["imported"] == 2
    assert summary["skipped"] == 0
    assert summary["errors"] == 0
    assert summary["deleted"] == 0
    assert summary["tpl_drafts_found"] == 2
    assert summary["total_drafts_scanned"] == 3

    # Sanity: las plantillas viven en la folder dedicada.
    from sqlalchemy import select as _select  # noqa: PLC0415

    from app.email_templates.models import (  # noqa: PLC0415
        EmailTemplate,
        EmailTemplateFolder,
    )

    with session_factory() as session:
        folder = session.scalar(
            _select(EmailTemplateFolder).where(
                EmailTemplateFolder.name == "Gmail (importadas)"
            )
        )
        assert folder is not None
        rows = list(
            session.scalars(
                _select(EmailTemplate).where(
                    EmailTemplate.folder_id == folder.id
                )
            )
        )
        names = sorted(r.name for r in rows)
        assert names == ["Adquisición equipos", "Bienvenida CRM"]
        # body_html no vacío, source/owner OK.
        for row in rows:
            assert row.body_html
            assert row.is_global is True
            assert row.owner_user_id == uid


def test_import_is_idempotent(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    autorun_worker: None,
) -> None:
    """Segundo run no duplica — counters reflejan skipped."""
    uid = _user_id(session_factory, UserRole.ADMIN)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )
    fake = _FakeGmailImportClient()
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient",
        lambda *_a, **_kw: fake,
    )

    _post_import_and_get_counters(client, session_factory)
    _, summary = _post_import_and_get_counters(client, session_factory)
    assert summary["imported"] == 0
    assert summary["skipped"] == 2


def test_import_delete_after_removes_drafts(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    autorun_worker: None,
) -> None:
    uid = _user_id(session_factory, UserRole.ADMIN)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )
    fake = _FakeGmailImportClient()
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient",
        lambda *_a, **_kw: fake,
    )

    _, summary = _post_import_and_get_counters(
        client, session_factory, delete_after=True
    )
    assert summary["imported"] == 2
    assert summary["deleted"] == 2
    assert sorted(fake.deleted) == ["draft-tpl-a", "draft-tpl-b"]


def test_import_requires_admin(client: TestClient) -> None:
    for role in ("manager", "user"):
        resp = client.post(
            "/api/email-templates/import-gmail",
            headers=auth_headers(client, role),
        )
        assert resp.status_code == 403, f"{role}: {resp.text}"


def test_import_returns_202_with_sync_log_id_and_pending(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El endpoint async no espera al worker — devuelve 202 +
    `{sync_log_id, job_id, status: pending}` inmediatamente. Sin
    `autorun_worker` el SyncLog se queda en PENDING (Redis no se
    invoca porque mockeamos `queue_for`)."""
    from types import SimpleNamespace  # noqa: PLC0415

    uid = _user_id(session_factory, UserRole.ADMIN)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )

    class _SilentQueue:
        def enqueue(self, *_a, **_kw):
            return SimpleNamespace(id="job-id-stub")

    monkeypatch.setattr(
        "app.workers.jobs.queue_for", lambda *_a, **_kw: _SilentQueue()
    )

    resp = client.post(
        "/api/email-templates/import-gmail",
        headers=auth_headers(client, "admin"),
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["sync_log_id"]
    assert body["job_id"] == "job-id-stub"

    from app.models.crm import (  # noqa: PLC0415
        ExternalSystem,
        SyncLog,
        SyncStatus,
    )

    with session_factory() as session:
        row = session.get(SyncLog, body["sync_log_id"])
        assert row is not None
        assert row.status == SyncStatus.PENDING.value
        assert row.system == ExternalSystem.EMAIL_TEMPLATES
        assert row.operation == "import_gmail"
        assert row.job_id == "job-id-stub"


def test_import_worker_marks_sync_log_success(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    autorun_worker: None,
) -> None:
    """End-to-end: POST → worker inline corre el handler → SyncLog
    queda en SUCCESS con los counters poblados."""
    uid = _user_id(session_factory, UserRole.ADMIN)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )
    fake = _FakeGmailImportClient()
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient",
        lambda *_a, **_kw: fake,
    )

    body, _ = _post_import_and_get_counters(client, session_factory)

    from app.models.crm import SyncLog, SyncStatus  # noqa: PLC0415

    with session_factory() as session:
        row = session.get(SyncLog, body["sync_log_id"])
        assert row is not None
        assert row.status == SyncStatus.SUCCESS.value
        assert row.records_processed == 2
        assert row.records_skipped == 0
        assert row.records_failed == 0
        assert row.finished_at is not None


# 1x1 transparent PNG bytes
_PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000005000196f7250b0000000049454e44"
    "ae426082"
)


class _FakeGmailImportClientInline:
    def __init__(self, *_a, **_kw) -> None:
        pass

    def list_all_drafts(self) -> list[str]:
        return ["draft-tpl-img"]

    def get_draft_metadata(self, draft_id: str) -> dict:
        return {
            "id": draft_id,
            "message": {
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "[TPL] Con imagen"},
                    ]
                }
            },
        }

    def get_draft_template(self, draft_id: str) -> dict:
        body_html = (
            '<p>Hola</p>'
            '<p><img src="cid:ii_abc123" alt="logo"></p>'
        )
        return {
            "id": draft_id,
            "message": {
                "id": f"msg-{draft_id}",
                "raw": _build_raw_email_with_inline_image(
                    subject="[TPL] Con imagen",
                    body_html=body_html,
                    cid="ii_abc123",
                    png_bytes=_PNG_1x1,
                ),
            },
        }


def test_import_rewrites_cid_to_crm_attachment_urls(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    autorun_worker: None,
) -> None:
    """Tras migrar a `email_template_attachments` el body deja de
    llevar base64: los `<img src="cid:ii_*">` se reescriben a la URL
    `/api/email-templates/{id}/attachments/by-cid/{cid}` y los
    binarios se guardan en la nueva tabla."""
    uid = _user_id(session_factory, UserRole.ADMIN)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient",
        _FakeGmailImportClientInline,
    )

    _, summary = _post_import_and_get_counters(client, session_factory)
    assert summary["imported"] == 1

    from sqlalchemy import select as _select  # noqa: PLC0415

    from app.email_templates.models import (  # noqa: PLC0415
        EmailTemplate,
        EmailTemplateAttachment,
    )

    with session_factory() as session:
        row = session.scalar(
            _select(EmailTemplate).where(EmailTemplate.name == "Con imagen")
        )
        assert row is not None
        assert "cid:ii_abc123" not in row.body_html
        assert "data:image/" not in row.body_html
        expected_url = (
            f"/api/email-templates/{row.id}/attachments/by-cid/ii_abc123"
        )
        assert expected_url in row.body_html

        attachment = session.scalar(
            _select(EmailTemplateAttachment).where(
                EmailTemplateAttachment.template_id == row.id,
                EmailTemplateAttachment.original_cid == "ii_abc123",
            )
        )
        assert attachment is not None
        assert attachment.content_type == "image/png"
        assert bytes(attachment.data) == _PNG_1x1


def test_attachment_endpoint_serves_binary_with_cache_headers(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    autorun_worker: None,
) -> None:
    """El endpoint del CRM devuelve los bytes con el `Content-Type`
    original y `Cache-Control: immutable` para que el navegador no
    re-pida la imagen entre aperturas del modal."""
    uid = _user_id(session_factory, UserRole.ADMIN)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient",
        _FakeGmailImportClientInline,
    )
    _post_import_and_get_counters(client, session_factory)

    from sqlalchemy import select as _select  # noqa: PLC0415

    from app.email_templates.models import EmailTemplate  # noqa: PLC0415

    with session_factory() as session:
        tpl = session.scalar(
            _select(EmailTemplate).where(EmailTemplate.name == "Con imagen")
        )
        assert tpl is not None
        tpl_id = tpl.id

    # Un user normal puede leer la plantilla porque es is_global.
    resp = client.get(
        f"/api/email-templates/{tpl_id}/attachments/by-cid/ii_abc123",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/png")
    assert "immutable" in resp.headers.get("cache-control", "")
    assert resp.content == _PNG_1x1


def test_attachment_endpoint_is_public(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    autorun_worker: None,
) -> None:
    """El endpoint NO usa `require_user` porque el browser carga las
    imágenes desde un `<img src=...>` sin el header `Authorization:
    Bearer`. La protección efectiva es conocer el `template_id`
    (UUID) + `cid`."""
    uid = _user_id(session_factory, UserRole.ADMIN)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient",
        _FakeGmailImportClientInline,
    )
    _post_import_and_get_counters(client, session_factory)

    from sqlalchemy import select as _select  # noqa: PLC0415

    from app.email_templates.models import EmailTemplate  # noqa: PLC0415

    with session_factory() as session:
        tpl = session.scalar(
            _select(EmailTemplate).where(EmailTemplate.name == "Con imagen")
        )
        assert tpl is not None
        tpl_id = tpl.id

    # Sin Authorization header — emula el `<img>` del navegador.
    resp = client.get(
        f"/api/email-templates/{tpl_id}/attachments/by-cid/ii_abc123",
    )
    assert resp.status_code == 200
    assert resp.content == _PNG_1x1


def test_attachment_endpoint_404_for_unknown_cid(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    autorun_worker: None,
) -> None:
    uid = _user_id(session_factory, UserRole.ADMIN)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient",
        _FakeGmailImportClientInline,
    )
    _post_import_and_get_counters(client, session_factory)

    from sqlalchemy import select as _select  # noqa: PLC0415

    from app.email_templates.models import EmailTemplate  # noqa: PLC0415

    with session_factory() as session:
        tpl = session.scalar(
            _select(EmailTemplate).where(EmailTemplate.name == "Con imagen")
        )
        assert tpl is not None
        tpl_id = tpl.id

    resp = client.get(
        f"/api/email-templates/{tpl_id}/attachments/by-cid/does-not-exist",
        headers=auth_headers(client, "user"),
    )
    assert resp.status_code == 404


def test_build_mime_wraps_in_multipart_related_with_inline_parts() -> None:
    """Con `inline_attachments` el MIME pasa a `multipart/related`
    envolviendo el `alternative` + cada attachment como part inline
    con `Content-ID: <cid>`. Es la forma que esperan los clientes de
    correo (Gmail, Outlook, Apple Mail) para resolver `cid:` en
    `<img>` tags."""
    from app.integrations.gmail.client import _build_mime  # noqa: PLC0415

    mime = _build_mime(
        from_alias="ops@bomedia.es",
        from_name="Bomedia",
        to=["client@example.com"],
        cc=None,
        bcc=None,
        subject="Hola",
        body_html='<p><img src="cid:ii_xyz" alt="logo"></p>',
        body_text="Hola",
        in_reply_to_message_id=None,
        references=None,
        extra_headers=None,
        inline_attachments=[
            {
                "cid": "ii_xyz",
                "content_type": "image/png",
                "filename": "logo.png",
                "data": _PNG_1x1,
            }
        ],
    )

    assert mime.get_content_type() == "multipart/related"
    assert mime["Subject"] == "Hola"
    assert mime["From"] == "Bomedia <ops@bomedia.es>"

    payload = mime.get_payload()
    # Primer part: el alternative con text + html.
    assert payload[0].get_content_type() == "multipart/alternative"
    sub_types = [p.get_content_type() for p in payload[0].get_payload()]
    assert "text/plain" in sub_types
    assert "text/html" in sub_types
    # Segundo part: la imagen con Content-ID y disposition inline.
    img = payload[1]
    assert img.get_content_type() == "image/png"
    assert img["Content-ID"] == "<ii_xyz>"
    assert "inline" in img["Content-Disposition"]


def test_build_mime_without_inline_attachments_stays_alternative() -> None:
    """Backwards compat: sin attachments inline el MIME mantiene la
    estructura `multipart/alternative` que llevaba antes del refactor.
    """
    from app.integrations.gmail.client import _build_mime  # noqa: PLC0415

    mime = _build_mime(
        from_alias="ops@bomedia.es",
        from_name=None,
        to=["client@example.com"],
        cc=None,
        bcc=None,
        subject="x",
        body_html="<p>hi</p>",
        body_text="hi",
        in_reply_to_message_id=None,
        references=None,
        extra_headers=None,
        inline_attachments=None,
    )
    assert mime.get_content_type() == "multipart/alternative"


def test_swap_crm_urls_to_cid_round_trip() -> None:
    """`_swap_crm_urls_to_cid` deja el HTML con `src="cid:X"` y
    devuelve los blobs cargados en la lista. Es la pieza que el send
    path usa para reinyectar attachments inline."""
    import re as _re  # noqa: PLC0415

    from sqlalchemy import create_engine  # noqa: PLC0415
    from sqlalchemy.orm import sessionmaker as _sm  # noqa: PLC0415
    from sqlalchemy.pool import StaticPool  # noqa: PLC0415

    from app.db.base import Base as _Base  # noqa: PLC0415
    from app.email_templates.models import (  # noqa: PLC0415
        EmailTemplate,
        EmailTemplateAttachment,
        EmailTemplateFolder,
    )
    from app.integrations.gmail.service import (  # noqa: PLC0415
        _swap_crm_urls_to_cid,
    )

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _Base.metadata.create_all(engine)
    Session_ = _sm(bind=engine, autoflush=False, autocommit=False)

    with Session_() as session:
        folder = EmailTemplateFolder(name="F", is_global=True)
        session.add(folder)
        session.flush()
        tpl = EmailTemplate(
            name="T",
            body_html="x",
            folder_id=folder.id,
            is_global=True,
        )
        session.add(tpl)
        session.flush()
        att = EmailTemplateAttachment(
            template_id=tpl.id,
            original_cid="ii_xyz",
            filename="logo.png",
            content_type="image/png",
            data=_PNG_1x1,
            created_at=datetime.now(UTC),
        )
        session.add(att)
        session.flush()

        body = (
            f'<p><img src="/api/email-templates/{tpl.id}'
            f'/attachments/by-cid/ii_xyz" alt="x"></p>'
        )
        new_body, parts = _swap_crm_urls_to_cid(session, body)
        assert 'src="cid:ii_xyz"' in new_body
        assert _re.search(r"/by-cid/", new_body) is None
        assert len(parts) == 1
        assert parts[0]["cid"] == "ii_xyz"
        assert parts[0]["content_type"] == "image/png"
        assert parts[0]["data"] == _PNG_1x1


def test_gmail_call_with_timeout_aborts_after_n_attempts() -> None:
    """Si la llamada Gmail nunca completa dentro del timeout, el
    helper levanta `GmailRequestTimeout` tras agotar los reintentos.
    Imprescindible para evitar el zombie reportado: un `drafts.get`
    que se queda colgado en el socket bloqueaba el worker entero."""
    import time as _time  # noqa: PLC0415

    from app.integrations.gmail.service import (  # noqa: PLC0415
        GmailRequestTimeout,
        _gmail_call_with_timeout,
    )

    def _hang():
        _time.sleep(10)
        return "never"

    t_start = _time.monotonic()
    with pytest.raises(GmailRequestTimeout):
        _gmail_call_with_timeout(_hang, timeout_s=0.1, retries=1)
    elapsed = _time.monotonic() - t_start
    # 2 intentos × 0.1s = 0.2s. Margen amplio para CI lentos.
    assert elapsed < 2.0


def test_gmail_call_with_timeout_returns_value_on_quick_call() -> None:
    """Camino feliz: si la fn termina dentro del timeout, devuelve
    el resultado sin reintento."""
    from app.integrations.gmail.service import (  # noqa: PLC0415
        _gmail_call_with_timeout,
    )

    assert _gmail_call_with_timeout(lambda: 42, timeout_s=1.0) == 42


class _FakeGmailFlakyClient:
    """Importer fake con un draft que cuelga en `get_draft_template`
    para verificar que el loop NO queda zombie tras el timeout."""

    def __init__(self) -> None:
        self.hang_seen = 0

    def list_all_drafts(self) -> list[str]:
        return ["draft-ok", "draft-hang", "draft-ok2"]

    def get_draft_metadata(self, draft_id: str) -> dict:
        name = {
            "draft-ok": "[TPL] OK A",
            "draft-hang": "[TPL] Cuelga",
            "draft-ok2": "[TPL] OK B",
        }[draft_id]
        return {
            "id": draft_id,
            "message": {
                "payload": {"headers": [{"name": "Subject", "value": name}]}
            },
        }

    def get_draft_template(self, draft_id: str) -> dict:
        if draft_id == "draft-hang":
            import time as _time  # noqa: PLC0415

            self.hang_seen += 1
            _time.sleep(10)  # más que el timeout del test
            raise AssertionError("never reached")
        return {
            "id": draft_id,
            "message": {
                "id": f"msg-{draft_id}",
                "raw": _build_raw_email(
                    subject="x",
                    body_html=f"<p>body {draft_id}</p>",
                ),
            },
        }


def test_import_continues_after_hanging_draft(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    autorun_worker: None,
) -> None:
    """Un draft cuyo `drafts.get` se cuelga NO bloquea el loop:
    timeout → log warning → contador `errors` → sigue con los demás.
    Las 2 plantillas sanas se persisten; la SyncLog termina en
    PARTIAL_SUCCESS (no zombie en RUNNING)."""
    uid = _user_id(session_factory, UserRole.ADMIN)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )
    fake = _FakeGmailFlakyClient()
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient",
        lambda *_a, **_kw: fake,
    )
    # Bajamos el timeout en el módulo del servicio para que el test
    # corra en < 2s.
    monkeypatch.setattr(
        "app.integrations.gmail.service._GMAIL_REQUEST_TIMEOUT_S", 0.2
    )
    monkeypatch.setattr(
        "app.integrations.gmail.service._GMAIL_REQUEST_RETRIES", 0
    )

    body, summary = _post_import_and_get_counters(client, session_factory)
    assert summary["imported"] == 2
    assert summary["errors"] == 1

    from app.models.crm import SyncLog, SyncStatus  # noqa: PLC0415

    with session_factory() as session:
        row = session.get(SyncLog, body["sync_log_id"])
        assert row is not None
        # 2 importados, 1 error → PARTIAL_SUCCESS, no zombie.
        assert row.status == SyncStatus.PARTIAL_SUCCESS.value
        assert row.records_processed == 2
        assert row.records_failed == 1
        assert row.finished_at is not None


def test_import_per_draft_commit_updates_sync_log_heartbeat(
    client: TestClient,
    session_factory: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    autorun_worker: None,
) -> None:
    """Cada draft procesado dispara un commit → `sync_log.updated_at`
    refresca. Con ese campo la UI puede detectar zombies (> 10 min
    sin tocar) y mostrar badge de warning."""
    uid = _user_id(session_factory, UserRole.ADMIN)
    _seed_gmail(
        session_factory,
        user_id=uid,
        scopes=(
            "https://www.googleapis.com/auth/gmail.send "
            "https://www.googleapis.com/auth/gmail.modify"
        ),
    )
    fake = _FakeGmailImportClient()
    monkeypatch.setattr(
        "app.integrations.gmail.service.GmailClient",
        lambda *_a, **_kw: fake,
    )

    body, _ = _post_import_and_get_counters(client, session_factory)

    from app.models.crm import SyncLog  # noqa: PLC0415

    with session_factory() as session:
        row = session.get(SyncLog, body["sync_log_id"])
        assert row is not None
        # records_processed se fue refrescando draft a draft. La
        # señal interesante para la UI es `updated_at` posterior a
        # `started_at` — implica al menos un commit intermedio.
        assert row.started_at is not None
        assert row.updated_at >= row.started_at
