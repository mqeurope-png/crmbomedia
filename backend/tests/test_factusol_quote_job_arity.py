"""Los `enqueue_*` de proformas encolan con argumentos que CASAN con la firma
del job que ejecuta el worker.

El worker de RQ llama al job con lo que puso el `enqueue`. Si la firma del job
y la llamada se desincronizan —lo que pasó al añadir `serie` a la creación:
`create_quote_job() takes from 2 to 6 positional arguments but 7 were given`—,
la acción revienta en producción y aquí no se veía, porque los tests de
endpoint solo comprueban que el `enqueue` recibió el dato, no que el job sepa
recibirlo.

Este test reproduce EN FRÍO lo que hace el worker: captura lo que el `enqueue`
manda a la cola (`args` + `kwargs`) y lo enlaza contra la firma real del job
con `inspect.Signature.bind`. Un desajuste de aridad o de nombre falla aquí,
sin Redis ni FACTUSOL.
"""
from __future__ import annotations

import importlib
import inspect
from typing import Any
from unittest.mock import patch

import pytest

from app.integrations.factusol import jobs

#: (helper de encolado, kwargs de ejemplo con los que lo llama el endpoint).
#: Se listan TODOS los `enqueue_*` de proformas: si mañana se añade un
#: parámetro a un job y no a su llamada (o al revés), el `bind` lo caza.
_QUOTE_ENQUEUES: list[tuple[str, dict[str, Any]]] = [
    ("enqueue_create_quote", {
        "customer": {"codcli": "1"}, "lines": [], "referencia": "R",
        "fecha": "2026-09-24", "fopfac": "011", "portes": 0.0, "serie": 2,
    }),
    ("enqueue_update_quote", {
        "codpre": "585", "customer": {"codcli": "1"}, "lines": [],
        "referencia": "R", "force": True, "portes": 0.0, "serie": 2,
    }),
    ("enqueue_duplicate_quote", {"codpre": "585", "fecha": None, "serie": 5}),
    ("enqueue_convert_quote_to_order", {
        "codpre": "585", "actor_user_id": "u-1", "payment": None,
        "create_albaran": True, "serie": 2,
    }),
]


@pytest.mark.parametrize(("helper_name", "sample"), _QUOTE_ENQUEUES)
def test_el_enqueue_de_proforma_casa_con_la_firma_del_job(helper_name, sample):
    captura: dict[str, Any] = {}

    def _fake_enqueue(func_path: str, *args: Any, **kwargs: Any) -> str:
        captura["func_path"] = func_path
        captura["args"] = args
        captura["kwargs"] = kwargs
        return "job-test"

    helper = getattr(jobs, helper_name)
    with patch.object(jobs, "_enqueue", _fake_enqueue):
        helper(**sample)

    # Resolver el job por su ruta, tal como hace el worker de RQ.
    module_path, _, func_name = captura["func_path"].rpartition(".")
    job = getattr(importlib.import_module(module_path), func_name)

    # El worker llama job(*args, **kwargs): si no enlaza, es el TypeError de
    # producción. `bind` lo levanta aquí, en frío.
    inspect.signature(job).bind(*captura["args"], **captura["kwargs"])


def test_create_quote_se_encola_por_nombre_con_la_serie():
    """La creación pasa `serie` POR NOMBRE: un parámetro nuevo en el futuro no
    puede volver a desplazarla por posición (la regresión que rompió #455)."""
    captura: dict[str, Any] = {}

    def _fake_enqueue(func_path: str, *args: Any, **kwargs: Any) -> str:
        captura.update(func_path=func_path, args=args, kwargs=kwargs)
        return "job-test"

    with patch.object(jobs, "_enqueue", _fake_enqueue):
        jobs.enqueue_create_quote({"codcli": "1"}, [], serie=4)

    assert captura["func_path"].endswith("create_quote_job")
    assert captura["args"] == ()          # nada posicional: todo por nombre
    assert captura["kwargs"]["serie"] == 4
