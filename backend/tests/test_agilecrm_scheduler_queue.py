"""PR-Fix-Scheduler-Agile-Roto.

El polling automático de AgileCRM llevaba 2 días caído porque el
worker NO escuchaba la queue `agilecrm:periodic_read` donde el
scheduler encola sus heartbeats self-rescheduling. El scheduler
armaba el primer tick correctamente, pero `enqueue_in` lo dejaba en
una queue sin consumidor → silencio para siempre.

Estos tests pinean los dos invariantes:

  1. La función `enqueue_in` del scheduler usa el queue name
     `agilecrm:periodic_read` (no otra cosa).
  2. La worker config del compose lista esa queue — si alguien la
     borra en una refactor futura, este test la atrapa.

PR-Fix-Worker-Dedicado-Workflows: el servicio worker único se
separó en `worker-sync` (sync + housekeeping) y `worker-workflows`.
Estos tests buscan las queues a lo largo de los dos servicios:
da igual cuál las escuche mientras alguien lo haga.
"""
from __future__ import annotations

from pathlib import Path

import yaml

_AGILE_SCHEDULER_QUEUES_REQUIRED: tuple[str, ...] = (
    "agilecrm:periodic_read",
    "agilecrm:sync_contacts",
)

#: Nombres de servicios que pueden ser workers RQ. La búsqueda recorre
#: los servicios cuyo `command` empieza con `rq worker` y agrega todas
#: las queues. Cubre tanto el split actual como una posible vuelta a
#: un worker único sin tener que tocar este test.
_WORKER_SERVICE_PREFIXES: tuple[str, ...] = ("worker",)


def _all_worker_queues_from_compose(compose_path: Path) -> list[str]:
    """Devuelve la unión de queues escuchadas por TODOS los servicios
    cuyo nombre empieza por `worker` y cuyo command empieza por
    `rq worker`. Tolerante al split worker-sync / worker-workflows
    introducido en PR-Fix-Worker-Dedicado-Workflows."""
    with open(compose_path) as f:
        compose = yaml.safe_load(f)
    queues: list[str] = []
    for service_name, service in (compose.get("services") or {}).items():
        if not any(
            service_name == p or service_name.startswith(f"{p}-")
            for p in _WORKER_SERVICE_PREFIXES
        ):
            continue
        command = service.get("command") or []
        if not (
            isinstance(command, list)
            and len(command) >= 2
            and command[0] == "rq"
            and command[1] == "worker"
        ):
            continue
        queues.extend(command)
    return queues


def test_dev_compose_worker_listens_on_agilecrm_periodic_read() -> None:
    """`docker-compose.yml` debe listar `agilecrm:periodic_read` en
    el comando de algún worker — antes faltaba y el scheduler dejaba
    jobs huérfanos en esa queue."""
    repo_root = Path(__file__).parent.parent.parent
    queues = _all_worker_queues_from_compose(repo_root / "docker-compose.yml")
    for queue in _AGILE_SCHEDULER_QUEUES_REQUIRED:
        assert queue in queues, (
            f"Queue {queue!r} no está en ningún worker.* de "
            f"docker-compose.yml — el scheduler encola heartbeats ahí "
            f"pero ningún worker los procesa."
        )


def test_prod_compose_worker_listens_on_agilecrm_periodic_read() -> None:
    """Mismo invariante en `docker-compose.prod.yml`."""
    repo_root = Path(__file__).parent.parent.parent
    queues = _all_worker_queues_from_compose(
        repo_root / "docker-compose.prod.yml"
    )
    for queue in _AGILE_SCHEDULER_QUEUES_REQUIRED:
        assert queue in queues, (
            f"Queue {queue!r} no está en ningún worker.* de "
            f"docker-compose.prod.yml."
        )


def test_brevo_scheduler_queues_still_present_in_dev_compose() -> None:
    """No tocar Brevo: este PR sólo cierra el hueco de Agile."""
    repo_root = Path(__file__).parent.parent.parent
    queues = _all_worker_queues_from_compose(repo_root / "docker-compose.yml")
    for queue in ("brevo:periodic_read", "brevo:periodic_segments"):
        assert queue in queues


def test_workflows_scheduler_queue_still_present_in_prod_compose() -> None:
    """PR-Fix-Worker-Dedicado-Workflows: workflows:scheduler vive en
    `worker-workflows` ahora, pero el invariante "algún worker la
    procesa" sigue siendo el contrato."""
    repo_root = Path(__file__).parent.parent.parent
    queues = _all_worker_queues_from_compose(
        repo_root / "docker-compose.prod.yml"
    )
    assert "workflows:scheduler" in queues


def test_scheduler_module_uses_agilecrm_periodic_read_queue_name() -> None:
    """El nombre canónico de la queue está en la cabecera del módulo —
    cualquier cambio de nombre rompería este invariante."""
    from app.integrations.agilecrm import scheduler
    from app.workers.queues import queue_name

    # Reconstrucción local del nombre para confirmar la convención.
    expected = queue_name("agilecrm", "periodic_read")
    assert expected == "agilecrm:periodic_read"

    # Y el lock key viaja con el mismo prefijo.
    assert scheduler.READ_LOCK_KEY.startswith("agilecrm:periodic_read:")


def test_schedule_periodic_read_enqueues_to_correct_queue(monkeypatch) -> None:
    """`schedule_periodic_read` debe llamar a enqueue_in en la queue
    `agilecrm:periodic_read` (no en otra como sync_contacts)."""
    from app.integrations.agilecrm import scheduler

    captured: dict[str, object] = {}

    class _FakeConn:
        def set(self, key, value, nx, ex):
            captured["lock_key"] = key
            captured["lock_ttl"] = ex
            return True

        def delete(self, key):
            pass

    class _FakeQueue:
        def __init__(self, name, connection):
            captured["queue_name"] = name

        def enqueue_in(self, interval, fn):
            captured["enqueue_interval"] = interval
            captured["enqueue_fn"] = fn

    monkeypatch.setattr(
        scheduler, "redis_connection", lambda: _FakeConn()
    )
    # Patch the Queue class import inside _arm via the rq module.

    import rq

    monkeypatch.setattr(rq, "Queue", _FakeQueue)

    scheduler.schedule_periodic_read()
    assert captured["queue_name"] == "agilecrm:periodic_read"
    assert captured["lock_key"] == "agilecrm:periodic_read:heartbeat"
    assert captured["enqueue_fn"].__name__ == "_periodic_read_runner"
