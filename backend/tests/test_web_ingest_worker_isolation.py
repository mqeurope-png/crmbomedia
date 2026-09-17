"""PR-Fix-Web-Ingest-Starvation — la ingesta web va aislada de AgileCRM.

Incidencia: los webhooks de WooCommerce respondían 200 y el job se encolaba en
`woocommerce:webhooks`, pero `worker-sync` no lo procesaba porque estaba
permanentemente ocupado con `agilecrm:sync_contacts` (RQ drena las colas EN
ORDEN dentro de un worker). Un `rq worker --burst` sobre `woocommerce:*` los
procesaba al instante → starvation.

Invariante fijado aquí (ambos compose): ningún worker escucha a la vez colas de
ingesta web (`woocommerce:*`) y de AgileCRM (`agilecrm:*`); existe un worker
dedicado que cubre las 3 colas de ingesta y no lleva AgileCRM. Así un pedido web
entra en segundos aunque AgileCRM sature su propio worker.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

COMPOSES = ("docker-compose.prod.yml", "docker-compose.yml")
WOO_INGEST_QUEUES = (
    "woocommerce:webhooks",
    "woocommerce:import",
    "woocommerce:backfill",
)
AGILE_QUEUES = (
    "agilecrm:periodic_read",
    "agilecrm:sync_contacts",
    "agilecrm:purge_quota",
)


def _repo_root() -> Path:
    return Path(__file__).parent.parent.parent


def _worker_queues(path: Path) -> dict[str, list[str]]:
    """`{worker_service: [colas]}` para los servicios `worker*` cuyo command
    es `rq worker ...`."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}
    for name, svc in (data.get("services") or {}).items():
        if not name.startswith("worker"):
            continue
        cmd = svc.get("command") or []
        if not (isinstance(cmd, list) and cmd[:2] == ["rq", "worker"]):
            continue
        out[name] = [
            c for c in cmd
            if isinstance(c, str) and ":" in c
            and not c.startswith("redis://") and not c.startswith("mysql")
        ]
    return out


@pytest.mark.parametrize("compose", COMPOSES)
def test_web_ingest_isolated_from_agilecrm(compose: str) -> None:
    workers = _worker_queues(_repo_root() / compose)
    for name, queues in workers.items():
        has_web = any(q.startswith("woocommerce:") for q in queues)
        has_agile = any(q.startswith("agilecrm:") for q in queues)
        assert not (has_web and has_agile), (
            f"{name} en {compose} escucha ingesta web Y AgileCRM a la vez: "
            "AgileCRM volvería a poder matar de hambre a los pedidos web."
        )


@pytest.mark.parametrize("compose", COMPOSES)
def test_dedicated_web_worker_covers_all_ingest_queues(compose: str) -> None:
    workers = _worker_queues(_repo_root() / compose)
    covering = [
        name for name, queues in workers.items()
        if all(q in queues for q in WOO_INGEST_QUEUES)
    ]
    assert covering, (
        f"ningún worker cubre {WOO_INGEST_QUEUES} en {compose} — la ingesta "
        "web necesita un worker dedicado."
    )
    for name in covering:
        assert not any(q.startswith("agilecrm:") for q in workers[name]), (
            f"{name} cubre la ingesta web pero lleva AgileCRM: no está aislado."
        )


@pytest.mark.parametrize("compose", COMPOSES)
def test_agilecrm_queues_still_have_a_worker(compose: str) -> None:
    all_queues = [q for qs in _worker_queues(_repo_root() / compose).values() for q in qs]
    for queue in AGILE_QUEUES:
        assert queue in all_queues, f"{queue} sin worker en {compose}."


@pytest.mark.parametrize("compose", COMPOSES)
def test_woo_webhooks_queue_has_a_worker(compose: str) -> None:
    all_queues = [q for qs in _worker_queues(_repo_root() / compose).values() for q in qs]
    assert "woocommerce:webhooks" in all_queues, (
        f"woocommerce:webhooks sin worker en {compose} — los pedidos web "
        "encolados se quedarían sin procesar."
    )
