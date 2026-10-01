"""Fallos de los jobs RQ (emitir factura, proformas): la pantalla recibe solo
el mensaje de la excepción; la traza se queda en el log del servidor."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.erp.job_errors import estado_fallido, mensaje_de_fallo

TRAZA_NO_EN_FACTUSOL = (
    "Traceback (most recent call last):\n"
    '  File "/app/app/integrations/factusol/jobs.py", line 88, in emit_invoice_job\n'
    "    result = emit_invoice(session, order, ...)\n"
    '  File "/app/app/integrations/factusol/service.py", line 861, in emit_invoice\n'
    "    raise FactusolError(\n"
    "app.integrations.factusol.client.FactusolError: Este pedido (FLUXLA-5790) aún no "
    "está en FACTUSOL. La app WooCommerce→FACTUSOL todavía no lo ha importado.\n"
)


def test_mensaje_sin_traza_ni_clase() -> None:
    msg = mensaje_de_fallo(TRAZA_NO_EN_FACTUSOL, "falló")
    assert msg.startswith("Este pedido (FLUXLA-5790) aún no está en FACTUSOL.")
    assert "Traceback" not in msg and "File " not in msg and "FactusolError" not in msg
    # Sin traza → el mensaje por defecto; una línea suelta se deja tal cual.
    assert mensaje_de_fallo(None, "falló") == "falló"
    assert mensaje_de_fallo("  \n ", "falló") == "falló"
    assert mensaje_de_fallo("Algo raro", "falló") == "Algo raro"
    assert len(mensaje_de_fallo("x.Error: " + "a" * 1000, "falló")) == 400


def test_estado_fallido_marca_el_pedido_que_aun_no_esta() -> None:
    assert estado_fallido(TRAZA_NO_EN_FACTUSOL, "falló") == {
        "status": "failed", "code": "pedido_no_en_factusol",
        "error": mensaje_de_fallo(TRAZA_NO_EN_FACTUSOL, "falló"),
    }
    otro = "Traceback...\napp.integrations.factusol.client.FactusolError: BDEscribirRegistroError"
    assert estado_fallido(otro, "falló") == {"status": "failed", "error": "BDEscribirRegistroError"}


def test_mensaje_de_varias_lineas_y_excepciones_encadenadas() -> None:
    """El cuerpo HTML/JSON de un 502 que FACTUSOL mete en el error no se queda
    en su última línea («</html>»); con excepciones encadenadas, la última."""
    html = (
        "Traceback (most recent call last):\n"
        '  File "/app/app/integrations/factusol/client.py", line 415, in _request\n'
        "    raise FactusolError(\n"
        "    ^^^^^^^^^^^^^^^^^^^\n"
        "app.integrations.factusol.client.FactusolError: POST /api/CargaTabla → 502: "
        "<html>\r\n<head><title>502 Bad Gateway</title></head>\r\n</html>\r\n"
    )
    msg = mensaje_de_fallo(html, "falló")
    assert msg.startswith("POST /api/CargaTabla → 502: <html>")
    assert "502 Bad Gateway" in msg and "\n" not in msg and "Traceback" not in msg
    encadenada = (
        "Traceback (most recent call last):\n"
        '  File "a.py", line 1, in f\n'
        "    x()\n"
        "KeyError: 'CODPCL'\n\n"
        "During handling of the above exception, another exception occurred:\n\n"
        "Traceback (most recent call last):\n"
        '  File "b.py", line 2, in g\n'
        "    raise FactusolError(msg)\n"
        "app.integrations.factusol.client.FactusolError: Detalle: BDEscribirRegistroError\n"
    )
    assert mensaje_de_fallo(encadenada, "falló") == "Detalle: BDEscribirRegistroError"
    sin_mensaje = 'Traceback (most recent call last):\n  File "c.py", line 3, in h\n' \
                  "    raise X()\napp.erp.Boom\n"
    assert mensaje_de_fallo(sin_mensaje, "falló") == "falló"


def test_estado_del_job_de_emision_sin_traza() -> None:
    """`GET …/factusol-invoice-status` con el job RQ fallido: mensaje limpio y
    el `code` que ofrece «Volver a comprobar»."""
    from app.erp.api.orders import _rq_job_status

    job = MagicMock()
    job.get_status.return_value = "failed"
    job.exc_info = TRAZA_NO_EN_FACTUSOL
    with patch("redis.Redis.from_url"), patch("rq.job.Job.fetch", return_value=job):
        info = _rq_job_status("job-1")
    assert info is not None
    assert info["status"] == "failed"
    assert info["code"] == "pedido_no_en_factusol"
    assert "Traceback" not in info["error"]
