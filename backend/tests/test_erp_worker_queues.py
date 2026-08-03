"""BoHub ERP Fase B (hotfix B-2-fix) — invariante de colas del worker.

Bug de producción: el sync-backfill de WooCommerce encolaba en
`woocommerce:backfill` pero el `rq worker` del worker-sync no listaba
ninguna cola `woocommerce:*` → el job se quedó eternamente en Redis
(mismo patrón que el bug histórico de `agilecrm:periodic_read`).

Estos tests fijan el contrato: TODA cola que el código ERP usa para
encolar debe estar en la lista de arranque de algún worker de ambos
compose. Se incluyen ya las de Genei (PR B-4) para no volver a tocar el
compose en ese PR.
"""
from __future__ import annotations

from pathlib import Path

import yaml

#: Colas de las integraciones live del ERP (Fase B). `woocommerce:import`
#: la consume el webhook receiver (PR B-3); `woocommerce:backfill` el
#: sync manual desde la UI admin (PR B-2); las `genei:*` llegan con el
#: adaptador Genei (PR B-4) pero se reservan aquí.
ERP_INTEGRATION_QUEUES = (
    "woocommerce:import",
    "woocommerce:backfill",
    "genei:shipments",
    "genei:webhooks",
)


def _all_worker_queues_from_compose(path: Path) -> list[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    queues: list[str] = []
    for name, service in (data.get("services") or {}).items():
        if not name.startswith("worker"):
            continue
        command = service.get("command") or []
        if not isinstance(command, list):
            continue
        queues.extend(str(item) for item in command)
    return queues


def _repo_root() -> Path:
    return Path(__file__).parent.parent.parent


def test_prod_compose_worker_listens_on_erp_integration_queues() -> None:
    queues = _all_worker_queues_from_compose(_repo_root() / "docker-compose.prod.yml")
    for queue in ERP_INTEGRATION_QUEUES:
        assert queue in queues, (
            f"Queue {queue!r} no está en ningún worker.* de "
            f"docker-compose.prod.yml — los jobs encolados ahí se quedan "
            f"eternamente sin procesar (bug B-2 del backfill Woo)."
        )


def test_dev_compose_worker_listens_on_erp_integration_queues() -> None:
    queues = _all_worker_queues_from_compose(_repo_root() / "docker-compose.yml")
    for queue in ERP_INTEGRATION_QUEUES:
        assert queue in queues, (
            f"Queue {queue!r} no está en ningún worker.* de docker-compose.yml."
        )


def test_woo_job_queue_constants_match_compose_names() -> None:
    """Los nombres que usa el código para encolar son EXACTAMENTE los que
    el worker escucha — si alguien renombra la constante sin tocar el
    compose (o viceversa), esto rompe."""
    from app.integrations.woocommerce.jobs import (
        WOO_QUEUE_BACKFILL,
        WOO_QUEUE_IMPORT,
    )

    assert WOO_QUEUE_IMPORT == "woocommerce:import"
    assert WOO_QUEUE_BACKFILL == "woocommerce:backfill"
    assert WOO_QUEUE_IMPORT in ERP_INTEGRATION_QUEUES
    assert WOO_QUEUE_BACKFILL in ERP_INTEGRATION_QUEUES
