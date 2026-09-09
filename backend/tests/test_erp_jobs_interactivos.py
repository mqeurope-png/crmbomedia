"""ERP · los trabajos interactivos van a la cola interactiva, no a la batch.

- La reconciliación Woo se encola en `erp:interactive` (worker-factusol, ocioso),
  NO en `woocommerce:backfill` (worker-sync, saturado con los batch horarios).
- El CLI `scripts.erp_reconcile_woo` saca el recuento reutilizando el mismo
  núcleo, sin pasar por la cola.
- El compose de producción configura worker-factusol para escuchar esa cola.
"""
from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import scripts.erp_reconcile_woo as cli
from app.integrations.woocommerce.jobs import (
    ERP_INTERACTIVE_QUEUE,
    WOO_QUEUE_BACKFILL,
    enqueue_woo_reconcile,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

_SUMMARY = {
    "ok": True, "preview": True, "scanned": 42, "woo_calls": 9,
    "removed_total": 5, "to_cancel": 3, "to_fail": 1, "to_refund_out": 1,
    "to_trash": 0, "to_refund_kept": 2, "unchanged": 35, "errors": [],
    "capped": False,
}


# --- Parte A: cola interactiva, no batch -------------------------------------------


def test_reconcile_job_enqueued_on_interactive_queue() -> None:
    captured: dict = {}

    class _FakeQueue:
        def __init__(self, name, connection=None):
            captured["queue"] = name

        def enqueue(self, *a, **k):
            job = MagicMock()
            job.id = "job-int-1"
            return job

    with patch("app.workers.queues.redis_connection", return_value=MagicMock()), \
         patch("rq.Queue", _FakeQueue):
        job_id = enqueue_woo_reconcile(dry_run=True)

    assert job_id == "job-int-1"
    # Va a la cola INTERACTIVA, nunca a la batch saturada.
    assert captured["queue"] == ERP_INTERACTIVE_QUEUE == "erp:interactive"
    assert captured["queue"] != WOO_QUEUE_BACKFILL


# --- Parte B: CLI -------------------------------------------------------------------


def test_reconcile_cli_dry_run_prints_counts(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_run", lambda dry_run, store: dict(_SUMMARY))
    monkeypatch.setattr("sys.argv", ["erp_reconcile_woo", "--dry-run"])
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.main()
    out = buf.getvalue()
    assert "PREVISUALIZACIÓN" in out
    assert "cancelados:              3" in out
    assert "fallidos:                1" in out
    assert "reembolsos no cumplidos: 1" in out
    assert "ya cumplidos" in out and "2" in out
    assert "9 llamadas a WooCommerce" in out


def test_reconcile_cli_reuses_core_logic(monkeypatch) -> None:
    calls: dict = {}

    def fake_core(session, *, dry_run, store_account_id):
        calls["dry_run"] = dry_run
        calls["store"] = store_account_id
        return dict(_SUMMARY)

    fake_session = MagicMock()
    fake_session.__enter__ = MagicMock(return_value=fake_session)
    fake_session.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(
        "app.integrations.woocommerce.reconcile.reconcile_open_order_statuses",
        fake_core,
    )
    # `_run` construye la sesión con sessionmaker(bind=get_engine()); se finge
    # la factoría para no tocar la base (el núcleo va parcheado).
    monkeypatch.setattr("sqlalchemy.orm.sessionmaker", lambda **k: (lambda: fake_session))
    monkeypatch.setattr("app.db.session.get_engine", lambda: MagicMock())

    result = cli._run(dry_run=True, store="boprint")
    assert result["to_cancel"] == 3          # mismo núcleo, mismo resumen
    assert calls == {"dry_run": True, "store": "boprint"}


def test_reconcile_cli_apply_needs_confirmation(monkeypatch) -> None:
    # --apply sin --yes pide confirmación; si se dice que no, no aplica.
    monkeypatch.setattr(cli, "_run", lambda dry_run, store: dict(_SUMMARY))
    monkeypatch.setattr("sys.argv", ["erp_reconcile_woo", "--apply"])
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    buf = io.StringIO()
    with redirect_stdout(buf):
        try:
            cli.main()
        except SystemExit:
            pass
    assert "Cancelado" in buf.getvalue()


# --- compose: worker-factusol escucha la cola interactiva --------------------------


def test_worker_factusol_listens_interactive_queue() -> None:
    text = (REPO_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    start = text.index("worker-factusol:")
    # El bloque hasta el siguiente servicio de primer nivel.
    rest = text[start + 1:]
    end = start + 1 + min(
        (rest.index(m) for m in ("\n  worker-", "\n  frontend:", "\n  nginx")
         if m in rest),
        default=len(rest),
    )
    block = text[start:end]
    assert "erp:interactive" in block, "worker-factusol debe escuchar erp:interactive"
    # Prioridad: antes que factusol:writes.
    assert block.index("erp:interactive") < block.index("factusol:writes")
