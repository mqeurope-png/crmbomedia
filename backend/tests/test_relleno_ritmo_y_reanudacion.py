"""El relleno universal de Gmail va a su ritmo y no se cae por cuota (10/10/2026).

En producción el recorrido moría con «403 rateLimitExceeded … Units per minute
per user» a los 194 y 330 mensajes: la excepción subía hasta arriba, sin
informe, y el seco no avanzaba nunca porque cada intento empezaba igual.

Aquí Gmail está simulado (nada sale a la red) y nadie espera de verdad: el
`Ritmo` lleva reloj y «dormir» falsos.
"""
from __future__ import annotations

import base64
import json
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httplib2
import pytest
from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main  # noqa: F401
from app.db.session import get_session
from app.integrations import gmail_watch
from app.integrations.gmail import backfill_universal as bu_module
from app.integrations.gmail import service as gmail_service
from app.integrations.gmail.backfill_universal import (
    run_backfill_universal,
    run_universal_job,
)
from app.integrations.gmail.ritmo import (
    CuotaGmailAgotada,
    Ritmo,
    es_error_de_cuota,
    es_error_transitorio,
    segundos_retry_after,
)
from app.main import app
from app.models.crm import (
    Base,
    EmailMessage,
    GmailBackfillJob,
    GmailBackfillMode,
    GmailBackfillStatus,
    User,
    UserRole,
)
from tests._test_helpers import (
    auth_headers,
    seed_org_google_integration,
    seed_test_users,
)

BART = "bart@artisjet-printers.eu"
MARC = "marc.moll@moll.team"
DESDE = datetime(2026, 10, 1).date()
HASTA = datetime(2026, 10, 10).date()


# --- infraestructura -----------------------------------------------------------


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
        admin = seed.scalar(select(User.id).where(User.role == UserRole.ADMIN))
        seed_org_google_integration(seed, connected_by_user_id=admin)
        seed.commit()
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def http(session_factory: sessionmaker) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_session] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _buzon(session: Session) -> str:
    uid = session.scalar(select(User.id).where(User.role == UserRole.ADMIN))
    assert uid
    return uid


def _cuantos(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(EmailMessage)))


def _raw(mid: str, tid: str, *, dia: str = "2026-10-08", to: str = MARC) -> dict[str, Any]:
    fecha = datetime.fromisoformat(dia).replace(hour=11, tzinfo=UTC)
    return {
        "id": mid, "threadId": tid, "labelIds": ["SENT"], "snippet": "Gerne…",
        "internalDate": str(int(fecha.timestamp() * 1000)),
        "payload": {
            "headers": [
                {"name": "From", "value": BART},
                {"name": "To", "value": to},
                {"name": "Subject", "value": f"Angebot {mid}"},
                {"name": "Date", "value": fecha.strftime("%a, %d %b %Y %H:%M:%S +0000")},
            ],
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(b"Angebot").decode()},
        },
    }


#: Tres páginas de SENT: la primera del 08/10, la segunda del 05/10, la última
#: del 01/10 (Gmail lista de más nuevo a más viejo).
PAGINAS: list[list[dict[str, Any]]] = [
    [_raw("s-1", "t-1"), _raw("s-2", "t-2")],
    [_raw("s-3", "t-3", dia="2026-10-05"), _raw("s-4", "t-4", dia="2026-10-05")],
    [_raw("s-5", "t-5", dia="2026-10-01")],
]


def _http_error(
    status: int, *, reason: str | None = None, retry_after: str | None = None,
    mensaje: str = "",
) -> HttpError:
    """Un `HttpError` de googleapiclient de verdad, como el que llega del
    transporte: `resp.status`, cabeceras en minúsculas y cuerpo JSON de Google."""
    cabeceras: dict[str, Any] = {"status": status, "content-type": "application/json"}
    if retry_after is not None:
        cabeceras["retry-after"] = retry_after
    errores = [{"message": mensaje, "domain": "usageLimits", "reason": reason}] if reason else []
    cuerpo = {"error": {"code": status, "message": mensaje or f"HTTP {status}", "errors": errores}}
    return HttpError(
        httplib2.Response(cabeceras), json.dumps(cuerpo).encode(),
        uri="https://gmail.googleapis.com/gmail/v1/users/me/messages?q=after",
    )


def _cuota() -> HttpError:
    return _http_error(
        403, reason="rateLimitExceeded",
        mensaje="Quota exceeded for quota metric 'Total Query Cost' and limit "
        "'Units per minute per user' of service 'gmail.googleapis.com'",
    )


class _GmailPaginado:
    """Un buzón con `PAGINAS` de SENT y tokens «p2», «p3»… `fallos_list[token]`
    y `fallos_get[mid]` son listas de excepciones que se lanzan, una por
    llamada, antes de contestar bien. Apunta cada llamada en `llamadas`."""

    def __init__(
        self, paginas: list[list[dict[str, Any]]] = PAGINAS, *,
        fallos_list: dict[str | None, list[Exception]] | None = None,
        fallos_get: dict[str, list[Exception]] | None = None,
    ) -> None:
        self.paginas = paginas
        self.mensajes = {m["id"]: m for pagina in paginas for m in pagina}
        self.fallos_list = dict(fallos_list or {})
        self.fallos_get = dict(fallos_get or {})
        self.llamadas: list[tuple[Any, ...]] = []

    def list_messages(self, *, query: str, page_size: int = 100,
                      page_token: str | None = None,
                      label_ids: list[str] | None = None) -> dict[str, Any]:
        self.llamadas.append(("list", page_token, query))
        pendientes = self.fallos_list.get(page_token)
        if pendientes:
            raise pendientes.pop(0)
        indice = 0 if page_token is None else int(page_token[1:]) - 1
        pagina = self.paginas[indice]
        siguiente = f"p{indice + 2}" if indice + 1 < len(self.paginas) else None
        return {
            "messages": [{"id": m["id"], "threadId": m["threadId"]} for m in pagina],
            "nextPageToken": siguiente,
        }

    def get_message(self, mid: str) -> dict[str, Any]:
        self.llamadas.append(("get", mid))
        pendientes = self.fallos_get.get(mid)
        if pendientes:
            raise pendientes.pop(0)
        return self.mensajes[mid]

    def pedidos(self) -> list[str]:
        return [mid for tipo, mid, *_ in self.llamadas if tipo == "get"]


class _Reloj:
    """Tiempo simulado: «dormir» adelanta el reloj y apunta cuánto."""

    def __init__(self) -> None:
        self.t = 0.0
        self.esperas: list[float] = []

    def ahora(self) -> float:
        return self.t

    def dormir(self, segundos: float) -> None:
        self.esperas.append(segundos)
        self.t += segundos


def _ritmo(reloj: _Reloj, *, rps: float | None = None, max_reintentos: int = 3) -> Ritmo:
    # `azar=1.0` → la espera es exactamente la base (1 s, 2 s, 4 s…).
    return Ritmo(peticiones_por_segundo=rps, max_reintentos=max_reintentos,
                 reloj=reloj.ahora, dormir=reloj.dormir, azar=lambda: 1.0)


def _correr(session: Session, falso: Any, ritmo: Ritmo, **kw: Any) -> tuple[Any, list[dict]]:
    puntos: list[dict[str, Any]] = []
    with patch.object(gmail_service, "_client_for", return_value=falso):
        informe = run_backfill_universal(
            session, user_id=_buzon(session), since=DESDE, until=HASTA, labels=("SENT",),
            ritmo=ritmo, on_checkpoint=lambda cp, _i: puntos.append(cp), **kw,
        )
    session.commit()
    return informe, puntos


# --- el ritmo y la clasificación de errores -------------------------------------


def test_distingue_la_cuota_de_los_errores_de_verdad() -> None:
    assert es_error_de_cuota(_cuota()) is True
    assert es_error_transitorio(_cuota()) is True
    assert es_error_de_cuota(_http_error(429)) is True
    assert es_error_transitorio(_http_error(503)) is True
    # El límite diario no se arregla esperando un minuto; un 404 o un 400, tampoco.
    assert es_error_de_cuota(_http_error(403, reason="dailyLimitExceeded")) is False
    assert es_error_transitorio(_http_error(403, reason="insufficientPermissions")) is False
    assert es_error_transitorio(_http_error(404)) is False
    assert es_error_transitorio(_http_error(400)) is False
    assert segundos_retry_after(_http_error(429, retry_after="3")) == 3.0
    assert segundos_retry_after(_http_error(429)) is None


def test_el_ritmo_no_deja_pasar_mas_peticiones_por_segundo_de_las_configuradas() -> None:
    reloj = _Reloj()
    ritmo = _ritmo(reloj, rps=2)
    marcas: list[float] = []
    for _ in range(5):
        ritmo.llamar(lambda: marcas.append(reloj.ahora()))
    # Cinco peticiones a 2/s: una cada medio segundo, dos segundos en total.
    assert marcas == [0.0, 0.5, 1.0, 1.5, 2.0]
    assert ritmo.peticiones == 5 and ritmo.esperas == 0
    # Sin límite no se duerme nunca.
    libre = _ritmo(_Reloj(), rps=None)
    for _ in range(3):
        libre.llamar(lambda: None)
    assert libre.peticiones == 3 and reloj.esperas == [0.5, 0.5, 0.5, 0.5]


def test_cuando_google_dice_que_pares_se_espera_creciente_y_se_reintenta() -> None:
    reloj = _Reloj()
    ritmo = _ritmo(reloj, max_reintentos=3)
    intentos = iter([_cuota(), _http_error(503), _http_error(429, retry_after="5"), "ok"])

    def _gmail() -> str:
        valor = next(intentos)
        if isinstance(valor, Exception):
            raise valor
        return valor

    assert ritmo.llamar(_gmail, etiqueta="get x") == "ok"
    # 1 s, 2 s y el Retry-After de 5 s (manda sobre los 4 s del backoff).
    assert reloj.esperas == [1.0, 2.0, 5.0]
    assert ritmo.esperas == 3 and ritmo.segundos_esperando == 8.0

    # Si sigue negándose, CuotaGmailAgotada tras max_reintentos + 1 intentos.
    terco = _ritmo(_Reloj(), max_reintentos=2)
    with pytest.raises(CuotaGmailAgotada) as info:
        terco.llamar(lambda: (_ for _ in ()).throw(_cuota()), etiqueta="list SENT p2")
    assert info.value.intentos == 3 and "rateLimitExceeded" in info.value.detalle
    # Un error que no es «espera» sube a la primera, sin reintentar.
    with pytest.raises(HttpError):
        _ritmo(_Reloj()).llamar(lambda: (_ for _ in ()).throw(_http_error(400)))


# --- el recorrido ---------------------------------------------------------------


def test_un_403_de_cuota_se_espera_y_el_recorrido_termina_sin_propagar_nada(
    session_factory: sessionmaker,
) -> None:
    reloj = _Reloj()
    falso = _GmailPaginado(
        fallos_list={None: [_cuota()]},                        # la primera lista, una vez
        fallos_get={"s-3": [_http_error(429, retry_after="2")]},  # un full, una vez
    )
    with session_factory() as s:
        informe, _ = _correr(s, falso, _ritmo(reloj))
        assert informe.terminado and not informe.incompleto
        assert informe.outbound == 5 and informe.errors == 0
        assert informe.esperas == 2 and reloj.esperas == [1.0, 2.0]
        assert informe.peticiones == 3 + 5 + 2                 # listas + fulls + reintentos
        assert _cuantos(s) == 5


def test_si_gmail_sigue_negandose_se_para_con_informe_incompleto_y_punto_de_reanudacion(
    session_factory: sessionmaker,
) -> None:
    reloj = _Reloj()
    falso = _GmailPaginado(fallos_list={"p2": [_cuota() for _ in range(50)]})
    with session_factory() as s:
        informe, puntos = _correr(s, falso, _ritmo(reloj, max_reintentos=3))
        assert informe.incompleto and not informe.terminado and not informe.cancelado
        assert "cuota" in informe.motivo_parada.lower() and "4 intentos" in informe.motivo_parada
        assert "rateLimitExceeded" in informe.motivo_parada
        assert informe.hasta_donde == "etiqueta SENT, página 2; mensajes hasta el 2026-10-08"
        assert reloj.esperas == [1.0, 2.0, 4.0]
        # Lo procesado antes del corte está en el informe y en la BD.
        assert informe.outbound == 2 and _cuantos(s) == 2
        assert "INCOMPLETO" in informe.render() and "Llegó hasta" in informe.render()
        # El punto de reanudación apunta a la página que no se pudo listar.
        ultimo = puntos[-1]
        assert (ultimo["label"], ultimo["page_token"], ultimo["pagina"]) == ("SENT", "p2", 2)
        assert ultimo["hechos_en_pagina"] == [] and ultimo["informe"]["outbound"] == 2
        assert ultimo["fecha_mas_antigua"] == "2026-10-08"


def test_un_get_que_falla_se_cuenta_se_lista_y_el_recorrido_sigue(
    session_factory: sessionmaker,
) -> None:
    reloj = _Reloj()
    falso = _GmailPaginado(fallos_get={
        "s-2": [_http_error(400, mensaje="Bad Request")],       # no transitorio: a la primera
        "s-4": [_http_error(500) for _ in range(10)],           # 5xx persistente: tras reintentos
    })
    with session_factory() as s:
        informe, _ = _correr(s, falso, _ritmo(reloj, max_reintentos=2))
        assert informe.terminado
        assert informe.errors == 2 and informe.outbound == 3
        assert informe.fallos == [{"id": "s-2", "motivo": "HTTP 400"},
                                  {"id": "s-4", "motivo": "HTTP 500"}]
        assert reloj.esperas == [1.0, 2.0]                     # solo por el 500
        assert "Mensajes con error (2)" in informe.render()
        assert _cuantos(s) == 3


def test_reanudar_continua_donde_se_quedo_sin_recontar_ni_duplicar(
    session_factory: sessionmaker,
) -> None:
    # En seco: antes no avanzaba nunca porque cada intento empezaba igual.
    with session_factory() as s:
        roto = _GmailPaginado(fallos_list={"p2": [_cuota() for _ in range(50)]})
        parcial, puntos = _correr(s, roto, _ritmo(_Reloj(), max_reintentos=1), dry_run=True)
        assert parcial.incompleto and parcial.outbound == 2 and _cuantos(s) == 0
        sano = _GmailPaginado()
        entero, _ = _correr(s, sano, _ritmo(_Reloj()), dry_run=True, reanudar=puntos[-1])
        assert entero.terminado and entero.reanudado
        assert sano.llamadas[0][:2] == ("list", "p2")            # empieza donde se quedó
        assert sano.pedidos() == ["s-3", "s-4", "s-5"]           # los de la página 1, ni se piden
        assert entero.outbound == 5 and entero.skipped_dedupe == 0
        assert entero.enviados_por_remitente == {BART: 5}
        assert "(reanudado)" in entero.render() and _cuantos(s) == 0

    # De verdad: lo guardado antes del corte no se vuelve a pedir ni a guardar.
    with session_factory() as s:
        roto = _GmailPaginado(fallos_list={"p2": [_cuota() for _ in range(50)]})
        parcial, puntos = _correr(s, roto, _ritmo(_Reloj(), max_reintentos=1))
        assert parcial.incompleto and _cuantos(s) == 2
        sano = _GmailPaginado()
        entero, _ = _correr(s, sano, _ritmo(_Reloj()), reanudar=puntos[-1])
        assert entero.terminado and entero.outbound == 5 and _cuantos(s) == 5
        # Relanzar el tramo entero desde cero sigue sin duplicar.
        otra, _ = _correr(s, _GmailPaginado(), _ritmo(_Reloj()))
        assert otra.skipped_dedupe == 5 and otra.outbound == 0 and _cuantos(s) == 5


def test_si_el_token_guardado_ya_no_vale_la_etiqueta_sigue_por_fecha(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        roto = _GmailPaginado(fallos_list={"p2": [_cuota() for _ in range(50)]})
        _, puntos = _correr(s, roto, _ritmo(_Reloj(), max_reintentos=1))
        assert _cuantos(s) == 2
        # Gmail rechaza el pageToken guardado (400): se sigue por fecha desde el
        # día más antiguo visto (08/10 → before:2026/10/09), sin token.
        caduco = _GmailPaginado(fallos_list={"p2": [_http_error(400, mensaje="Invalid pageToken")]})
        entero, _ = _correr(s, caduco, _ritmo(_Reloj()), reanudar=puntos[-1])
        assert entero.terminado
        assert caduco.llamadas[0][:2] == ("list", "p2")
        assert caduco.llamadas[1][1] is None and "before:2026/10/09" in caduco.llamadas[1][2]
        # El solape del día 08/10 lo absorbe el dedupe; el resto entra una vez. El
        # informe acumula: 2 de la primera pasada + 3 de esta.
        assert entero.skipped_dedupe == 2 and entero.outbound == 5 and _cuantos(s) == 5


def test_el_tope_del_seco_deja_punto_de_reanudacion_y_se_sigue_a_trozos(
    session_factory: sessionmaker,
) -> None:
    with session_factory() as s:
        primero = _GmailPaginado()
        parcial, puntos = _correr(s, primero, _ritmo(_Reloj()), dry_run=True, dry_run_limit=3)
        assert parcial.incompleto and parcial.tope_seco_alcanzado
        assert "tope del seco" in parcial.motivo_parada and parcial.outbound == 3
        ultimo = puntos[-1]
        assert (ultimo["page_token"], ultimo["hechos_en_pagina"]) == ("p2", ["s-3"])
        segundo = _GmailPaginado()
        resto, _ = _correr(s, segundo, _ritmo(_Reloj()), dry_run=True, dry_run_limit=3,
                           reanudar=ultimo)
        assert segundo.pedidos() == ["s-4", "s-5"]              # ni s-3 otra vez
        assert resto.terminado and resto.outbound == 5 and _cuantos(s) == 0


# --- el job de worker-gmail y su reanudación --------------------------------------


def _job(session: Session, *, dry_run: bool = False) -> GmailBackfillJob:
    job = GmailBackfillJob(
        mode=GmailBackfillMode.UNIVERSAL.value, status=GmailBackfillStatus.QUEUED.value,
        config_json=json.dumps({"since": DESDE.isoformat(), "until": HASTA.isoformat(),
                                "labels": ["SENT"], "dry_run": dry_run}),
    )
    session.add(job)
    session.commit()
    return job


def test_el_job_sin_cuota_queda_fallido_pero_reanudable_y_resume_lo_termina(
    session_factory: sessionmaker, http: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reloj = _Reloj()
    monkeypatch.setattr(bu_module, "ritmo_desde_ajustes",
                        lambda *_a, **_k: _ritmo(reloj, max_reintentos=2))
    roto = _GmailPaginado(fallos_list={"p2": [_cuota() for _ in range(50)]})
    with session_factory() as s, patch.object(gmail_service, "_client_for", return_value=roto):
        job = _job(s)
        run_universal_job(s, job)
        s.refresh(job)
        assert job.status == "failed"
        assert "cuota" in job.error_summary.lower() and f"/{job.id}/resume" in job.error_summary
        resultado = json.loads(job.result_json)
        assert resultado["incompleto"] is True and resultado["terminado"] is False
        assert resultado["checkpoint"]["page_token"] == "p2"
        assert job.total_imported == 2 and _cuantos(s) == 2
        job_id = job.id

    # Reanudar es cosa de admin; vuelve a la cola con su punto de reanudación.
    assert http.post(f"/api/admin/gmail/backfill/{job_id}/resume",
                     headers=auth_headers(http, "user")).status_code == 403
    r = http.post(f"/api/admin/gmail/backfill/{job_id}/resume", headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued" and r.json()["error_summary"] is None

    sano = _GmailPaginado()
    with session_factory() as s, patch.object(gmail_service, "_client_for", return_value=sano):
        job = s.get(GmailBackfillJob, job_id)
        run_universal_job(s, job)
        s.refresh(job)
        assert job.status == "completed", job.error_summary
        assert sano.pedidos() == ["s-3", "s-4", "s-5"]
        resultado = json.loads(job.result_json)
        assert resultado["reanudado"] is True and resultado["terminado"] is True
        assert "checkpoint" not in resultado
        assert resultado["outbound"] == 5 and job.total_imported == 5 and _cuantos(s) == 5

    # Terminó entero: ya no hay nada que reanudar.
    r = http.post(f"/api/admin/gmail/backfill/{job_id}/resume", headers=auth_headers(http, "admin"))
    assert r.status_code == 409 and "punto de reanudación" in r.json()["detail"]


def test_resume_rechaza_jobs_que_no_son_universal_o_siguen_en_marcha(
    session_factory: sessionmaker, http: TestClient,
) -> None:
    with session_factory() as s:
        estimate = GmailBackfillJob(mode="estimate", status="failed", config_json="{}")
        en_marcha = _job(s)
        en_marcha.status = "running"
        s.add(estimate)
        s.commit()
        ids = (estimate.id, en_marcha.id)
    for job_id in ids:
        r = http.post(f"/api/admin/gmail/backfill/{job_id}/resume",
                      headers=auth_headers(http, "admin"))
        assert r.status_code == 409, r.text
    assert http.post("/api/admin/gmail/backfill/no-existe/resume",
                     headers=auth_headers(http, "admin")).status_code == 404


def test_el_endpoint_universal_acepta_un_ritmo_propio(http: TestClient) -> None:
    r = http.post("/api/admin/gmail/backfill/universal",
                  json={"since": "2026-07-15", "until": "2026-10-01", "labels": ["SENT"],
                        "dry_run": True, "rps": 1.5},
                  headers=auth_headers(http, "admin"))
    assert r.status_code == 200, r.text
    assert r.json()["config"]["rps"] == 1.5
    assert http.post("/api/admin/gmail/backfill/universal",
                     json={"since": "2026-07-15", "rps": 0},
                     headers=auth_headers(http, "admin")).status_code == 422


# --- la CLI: informe aunque se pare, y relanzar continúa ----------------------------


def test_la_cli_imprime_el_informe_aunque_se_pare_y_relanzar_continua(
    session_factory: sessionmaker, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(gmail_watch, "get_engine", lambda: session_factory.kw["bind"])
    monkeypatch.setattr("app.integrations.gmail.ritmo.ritmo_desde_ajustes",
                        lambda *_a, **_k: _ritmo(_Reloj(), max_reintentos=1))
    checkpoint = tmp_path / "relleno.json"
    argv = ["--since", DESDE.isoformat(), "--until", HASTA.isoformat(), "--labels", "SENT",
            "--yes", "--checkpoint", str(checkpoint)]

    roto = _GmailPaginado(fallos_list={"p2": [_cuota() for _ in range(50)]})
    with (
        patch.object(gmail_service, "_client_for", return_value=roto),
        pytest.raises(SystemExit) as salida,
    ):
        gmail_watch.backfill_universal(argv)
    assert salida.value.code == 2
    texto = capsys.readouterr().out
    assert "INCOMPLETO" in texto and "Se paró antes de tiempo" in texto
    assert "Enviados (outbound):            2" in texto
    assert f"Punto de reanudación guardado en {checkpoint}" in texto
    assert "relanza el MISMO comando" in texto
    assert json.loads(checkpoint.read_text())["page_token"] == "p2"

    sano = _GmailPaginado()
    with patch.object(gmail_service, "_client_for", return_value=sano):
        gmail_watch.backfill_universal(argv)
    texto = capsys.readouterr().out
    assert "Se reanuda desde el punto guardado" in texto
    assert "Backfill completo (reanudado)" in texto
    assert "Enviados (outbound):            5" in texto
    assert sano.pedidos() == ["s-3", "s-4", "s-5"]
    assert not checkpoint.exists()                             # terminó entero: se borra
    with session_factory() as s:
        assert _cuantos(s) == 5
