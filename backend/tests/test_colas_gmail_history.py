"""La cola `gmail:process_history` y sus 944 fallos.

Cuatro causas, todas de la misma familia: un fallo que no se puede arreglar
reintentando y que se reintentaba igual, cada vez que llegaba un push.

- `HttpError 404` en `history?startHistoryId=`: el cursor guardado es más
  viejo de lo que Gmail conserva. Reintentar con el mismo cursor falla para
  siempre; la salida es recolocarlo y recuperar el hueco.
- `RefreshError: invalid_grant`: google-auth refresca el token DENTRO del
  `execute()`, así que el error no pasaba por el envoltorio que lo convierte
  en `GoogleAuthExpiredError` y se escapaba crudo, sin marcar la cuenta.
- `Lock wait timeout (1205)`: dos pasadas a la vez sobre la misma cuenta (el
  push y el sondeo de respaldo) escribiendo los mismos mensajes.
- `PendingRollbackError`: el error de verdad enterrado por el siguiente.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.crm import (
    AuditLog,
    GmailPubsubWatch,
    OrgGoogleIntegration,
    User,
    UserRole,
)
from tests._test_helpers import seed_org_google_integration, seed_test_users


@pytest.fixture()
def factory() -> Generator[sessionmaker, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with f() as seed:
        seed_test_users(seed)
        dueno = seed.scalar(select(User.id).where(User.role == UserRole.ADMIN))
        seed.add(GmailPubsubWatch(
            user_id=dueno, history_id=1000,
            watch_expires_at=datetime.now(UTC) + timedelta(days=6),
            last_renewed_at=datetime.now(UTC),
            topic_name="projects/x/topics/y",
        ))
        seed_org_google_integration(seed, connected_by_user_id=dueno)
        seed.commit()
    yield f
    Base.metadata.drop_all(engine)


def _dueno(f: sessionmaker) -> str:
    with f() as s:
        return s.scalar(select(User.id).where(User.role == UserRole.ADMIN))


class _Error404(Exception):
    """Como el `HttpError` de googleapiclient: lo que se mira es
    `resp.status`."""

    def __init__(self) -> None:
        super().__init__("history not found")
        self.resp = type("R", (), {"status": 404})()


# --- 404: recolocar el cursor y recuperar el hueco --------------------------


def test_un_cursor_caducado_recoloca_y_encola_la_recuperacion(
    factory, monkeypatch, caplog
):
    """Antes esto fallaba cada vez que llegaba un push, con el mismo cursor
    caducado. Los 944 fallos de la cola son esto."""
    from app.integrations.gmail import service as gmail_service

    dueno = _dueno(factory)
    encolados: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "app.integrations.gmail.jobs.enqueue_recuperar_hueco",
        lambda *, user_id, dias: encolados.append((user_id, dias)),
    )

    class _Cliente:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def list_history(self, _start: int, *_a: Any, **_k: Any) -> dict:
            raise _Error404()

        def get_profile(self) -> dict:
            return {"emailAddress": "bart@bomedia.net", "historyId": "99999"}

    monkeypatch.setattr(gmail_service, "GmailClient", _Cliente)
    monkeypatch.setattr(gmail_service, "HttpError", _Error404, raising=False)

    with factory() as s, caplog.at_level(logging.WARNING):
        # El `except HttpError` de `process_history` tiene que atrapar el
        # error de la librería real; aquí se sustituye por el nuestro.
        monkeypatch.setitem(
            __import__("googleapiclient.errors", fromlist=["errors"]).__dict__,
            "HttpError", _Error404,
        )
        traidos = gmail_service.process_history(s, user_id=dueno,
                                               new_history_id=1001)
        s.commit()

    assert traidos == 0
    with factory() as s:
        # El cursor salta al de ahora: el push vuelve a funcionar en el acto.
        assert s.scalar(select(GmailPubsubWatch.history_id)) == 99999
        # Y queda escrito el hueco, que es lo que luego se recupera.
        fila = s.scalar(select(AuditLog).where(
            AuditLog.action == "gmail.history_cursor_reset"))
        assert fila is not None
        assert "1000" in (fila.metadata_json or "")
        assert "99999" in (fila.metadata_json or "")
    # Y se encola el repaso acotado que trae lo de en medio.
    # Diez días como mínimo; si el cursor llevaba más sin avanzar, los que
    # lleve (hasta el tope).
    assert len(encolados) == 1
    assert encolados[0][0] == dueno
    assert encolados[0][1] >= gmail_service.DIAS_DE_RECUPERACION
    assert "cursor_caducado" in caplog.text


def test_un_error_que_no_es_404_sigue_subiendo(factory, monkeypatch):
    """Un 500 de Gmail SÍ se arregla reintentando: no se toca el cursor."""
    from app.integrations.gmail import service as gmail_service

    class _Error500(Exception):
        def __init__(self) -> None:
            super().__init__("backend error")
            self.resp = type("R", (), {"status": 500})()

    class _Cliente:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def list_history(self, _start: int, *_a: Any, **_k: Any) -> dict:
            raise _Error500()

    monkeypatch.setattr(gmail_service, "GmailClient", _Cliente)
    monkeypatch.setitem(
        __import__("googleapiclient.errors", fromlist=["errors"]).__dict__,
        "HttpError", _Error500,
    )
    with factory() as s, pytest.raises(_Error500):
        gmail_service.process_history(s, user_id=_dueno(factory),
                                      new_history_id=1001)
    with factory() as s:
        assert s.scalar(select(GmailPubsubWatch.history_id)) == 1000


# --- invalid_grant: marcar, avisar y dejar de encolar -----------------------


def test_un_refresh_error_crudo_marca_la_cuenta_y_no_se_reintenta(
    factory, monkeypatch, caplog
):
    """`RefreshError` se escapaba sin pasar por `GoogleAuthExpiredError`,
    porque google-auth refresca el token dentro del `execute()`."""
    from google.auth.exceptions import RefreshError

    from app.integrations.gmail import jobs as gmail_jobs

    dueno = _dueno(factory)
    monkeypatch.setattr(gmail_jobs, "_tomar_cerrojo", lambda _u: (None, True))
    monkeypatch.setattr(
        "app.db.session.get_engine",
        lambda: factory.kw["bind"],
    )
    monkeypatch.setattr(gmail_jobs, "Session", lambda _e: factory())
    monkeypatch.setattr(
        "app.integrations.gmail.service.process_history",
        lambda *_a, **_k: (_ for _ in ()).throw(
            RefreshError("invalid_grant: Token has been expired or revoked.")),
    )

    with caplog.at_level(logging.WARNING):
        # No se relanza: reintentar no arregla un token revocado, y eso es lo
        # que hacía que la cola acumulara fallos.
        assert gmail_jobs.process_history_job(dueno, 1001) == 0

    with factory() as s:
        org = s.scalar(select(OrgGoogleIntegration))
        assert org.status == "needs_reconnect"
        assert "invalid_grant" in (org.last_refresh_error or "")
    assert "marcada para reconectar" in caplog.text


def test_una_cuenta_marcada_deja_de_trabajar(factory, monkeypatch):
    """«Dejar de encolar» sale gratis con la marca puesta: el webhook exige
    `status=active`, el sondeo de respaldo también, y el propio cliente se
    niega a usar unos tokens que ya no valen. Lo que faltaba era PONER la
    marca, no respetarla."""
    from app.integrations.gmail import jobs as gmail_jobs
    from app.integrations.gmail import service as gmail_service

    with factory() as s:
        org = s.scalar(select(OrgGoogleIntegration))
        org.status = "needs_reconnect"
        s.commit()

    with factory() as s, pytest.raises(gmail_service.GmailNotConnectedError):
        gmail_service._client_for(s, _dueno(factory))  # noqa: SLF001

    # Y el sondeo periódico no llega ni a mirar el historial.
    llamadas: list[str] = []
    monkeypatch.setattr(gmail_service, "process_history",
                        lambda *_a, **_k: llamadas.append("corrió") or 0)
    monkeypatch.setattr(gmail_jobs, "Session", lambda _e: factory())
    monkeypatch.setattr("app.db.session.get_engine", lambda: factory.kw["bind"])
    assert gmail_jobs.poll_history_fallback_job() == 0
    assert llamadas == []


# --- 1205: una sola pasada por cuenta a la vez ------------------------------


def test_dos_pasadas_a_la_vez_sobre_la_misma_cuenta_no_se_pisan(
    factory, monkeypatch, caplog
):
    """El push y el sondeo de respaldo caían juntos sobre la misma cuenta y
    escribían los mismos mensajes: de ahí los `Lock wait timeout (1205)`."""
    from app.integrations.gmail import jobs as gmail_jobs

    monkeypatch.setattr(gmail_jobs, "_tomar_cerrojo", lambda _u: (None, False))
    llamadas: list[str] = []
    monkeypatch.setattr(
        "app.integrations.gmail.service.process_history",
        lambda *_a, **_k: llamadas.append("corrió") or 0,
    )
    with caplog.at_level(logging.INFO):
        assert gmail_jobs.process_history_job(_dueno(factory), 1001) == 0
    # No ha corrido: la pasada que ya estaba en marcha lee el cursor de la
    # base de datos, así que se llevará también lo que acaba de llegar.
    assert llamadas == []
    assert "ya hay una pasada en marcha" in caplog.text


def test_sin_redis_se_sigue_adelante(factory, monkeypatch):
    """Fail-open: mejor arriesgar un choque que dejar de capturar correo."""
    from app.integrations.gmail import jobs as gmail_jobs
    from app.workers import queues

    def _sin_redis(*_a: Any, **_k: Any):
        raise RuntimeError("Connection refused")

    monkeypatch.setattr(queues, "redis_connection", _sin_redis)
    conn, se_puede = gmail_jobs._tomar_cerrojo("quien-sea")  # noqa: SLF001
    assert conn is None and se_puede is True


def test_un_429_del_endpoint_de_tokens_no_marca_la_cuenta(factory, monkeypatch):
    """`RefreshError` no siempre es `invalid_grant`: un 429 o un 500 del
    endpoint de tokens también llega así, y ese SÍ se arregla reintentando.
    Marcar la cuenta por eso pararía todo el correo hasta que alguien la
    reconectara a mano."""
    from google.auth.exceptions import RefreshError

    from app.integrations.gmail import jobs as gmail_jobs

    monkeypatch.setattr(gmail_jobs, "_tomar_cerrojo", lambda _u: (None, True))
    monkeypatch.setattr(gmail_jobs, "Session", lambda _e: factory())
    monkeypatch.setattr("app.db.session.get_engine", lambda: factory.kw["bind"])
    monkeypatch.setattr(
        "app.integrations.gmail.service.process_history",
        lambda *_a, **_k: (_ for _ in ()).throw(
            RefreshError("('Too Many Requests', '429')")),
    )
    # Sube para que RQ lo reintente, y la cuenta sigue activa.
    with pytest.raises(RefreshError):
        gmail_jobs.process_history_job(_dueno(factory), 1001)
    with factory() as s:
        assert s.scalar(select(OrgGoogleIntegration)).status == "active"


def test_el_sondeo_de_respaldo_tambien_toma_el_cerrojo(factory, monkeypatch):
    """El choque que provoca los 1205 es push CONTRA sondeo. Con el cerrojo
    solo en el push, seguía vivo."""
    from app.integrations.gmail import jobs as gmail_jobs
    from app.integrations.gmail import service as gmail_service

    llamadas: list[str] = []
    monkeypatch.setattr(gmail_service, "process_history",
                        lambda *_a, **_k: llamadas.append("corrió") or 0)
    monkeypatch.setattr(gmail_jobs, "Session", lambda _e: factory())
    monkeypatch.setattr("app.db.session.get_engine", lambda: factory.kw["bind"])
    monkeypatch.setattr(gmail_jobs, "_tomar_cerrojo", lambda _u: (None, False))
    assert gmail_jobs.poll_history_fallback_job() == 0
    assert llamadas == []


def test_la_recuperacion_va_dia_a_dia_y_un_dia_malo_no_corta_los_demas(
    factory, monkeypatch
):
    """Diez días en una sola transacción se perdían enteros si el trabajo se
    agotaba, y el cursor ya estaba movido: el hueco quedaba irrecuperable."""
    from app.integrations.gmail import jobs as gmail_jobs

    dias_pedidos: list[tuple] = []

    def _repaso(session, *, user_id, since, until, **_k):
        dias_pedidos.append((since, until))
        if len(dias_pedidos) == 2:
            raise RuntimeError("Gmail dijo no justo ese día")
        return SimpleNamespace(imported_linked=1, imported_orphan=0)

    monkeypatch.setattr(gmail_jobs, "_tomar_cerrojo", lambda _u: (None, True))
    monkeypatch.setattr(gmail_jobs, "Session", lambda _e: factory())
    monkeypatch.setattr("app.db.session.get_engine", lambda: factory.kw["bind"])
    monkeypatch.setattr(
        "app.integrations.gmail.backfill_universal.run_backfill_universal", _repaso)

    recuperados = gmail_jobs.recuperar_hueco_job(_dueno(factory), 4)
    assert len(dias_pedidos) == 4                 # un día por vuelta
    assert all(desde == hasta for desde, hasta in dias_pedidos)
    assert recuperados == 3                       # el día malo no corta el resto
