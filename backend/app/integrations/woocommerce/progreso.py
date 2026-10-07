"""Progreso de los trabajos largos de WooCommerce (la puesta al día).

El trabajo de RQ registra un informador con `activar(cb)`; los bucles llaman
a `informar(fase, hechos, total)` sin saber quién escucha. Fuera de un
trabajo (tests, repaso periódico) no hace nada.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar, Token

Informador = Callable[[str, int | None, int | None], None]

_actual: ContextVar[Informador | None] = ContextVar("woo_progreso", default=None)


def activar(cb: Informador) -> Token:
    return _actual.set(cb)


def desactivar(token: Token) -> None:
    _actual.reset(token)


def informar(fase: str, hechos: int | None = None, total: int | None = None) -> None:
    cb = _actual.get()
    if cb is None:
        return
    try:
        cb(fase, hechos, total)
    except Exception:  # noqa: BLE001 — informar nunca tumba el trabajo
        pass
