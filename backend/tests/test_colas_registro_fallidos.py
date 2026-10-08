"""Reencolar y vaciar el registro de fallidos de una cola, desde la app.

Los trabajos fallidos guardan sus argumentos, así que se pueden volver a
encolar: es la única forma de recuperar los 1.687 lotes de webhook de Brevo
que murieron por un evento duplicado. Y hacía falta poder vaciar el registro
sin entrar al contenedor, que es lo único que había.

Sin Redis de verdad: se sustituyen las piezas de RQ, como en
`test_cuadre_trabajos_fallidos.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.workers import registro_fallidos


def _trabajo(jid: str, func: str, estado: dict) -> SimpleNamespace:
    job = SimpleNamespace(
        id=jid, func_name=func, args=(), kwargs={},
        ended_at=datetime(2026, 10, 7, tzinfo=UTC),
    )

    def _delete() -> None:
        # Como el `delete` de RQ: se lleva el trabajo Y su sitio en el
        # registro. Contar bien depende de esto.
        estado["ids"] = [i for i in estado["ids"] if i != jid.encode()]
        estado["borrados"].append(jid)

    job.delete = _delete
    return job


@pytest.fixture()
def rq_falso(monkeypatch):
    """Devuelve el estado observable: qué se reencoló y qué se descartó."""
    import rq
    import rq.job
    import rq.registry

    estado: dict = {
        # zrange: del más ANTIGUO al más nuevo, que es como se recupera.
        "ids": [b"j1", b"j2", b"j3"],
        "reencolados": [], "borrados": [], "quitados_del_registro": [],
    }
    estado["trabajos"] = {
        "j1": _trabajo("j1", "app.integrations.brevo.webhooks."
                             "process_brevo_webhook_batch", estado),
        "j2": None,                                   # datos caducados
        "j3": _trabajo("j3", "app.integrations.brevo.push_jobs.push_contact_job",
                       estado),
    }

    class _Conn:
        def zrange(self, _key, start, end):
            """Como Redis: un tramo, con `end` incluido y -1 = hasta el final."""
            ids = estado["ids"]
            return ids[start:] if end == -1 else ids[start : end + 1]

        def zrem(self, _key, job_id):
            antes = len(estado["ids"])
            estado["ids"] = [i for i in estado["ids"] if i != job_id.encode()]
            estado["quitados_del_registro"].append(job_id)
            return antes - len(estado["ids"])

    class _Registro:
        def __init__(self, queue):
            self.key = f"rq:failed:{queue.name}"

        def __len__(self):
            return len(estado["ids"])

        def requeue(self, job_id):
            estado["reencolados"].append(job_id)

        def remove(self, job_id, delete_job=False):
            estado["borrados"].append((job_id, delete_job))

    monkeypatch.setattr(rq, "Queue", lambda nombre, connection=None:
                        SimpleNamespace(name=nombre))
    monkeypatch.setattr(rq.registry, "FailedJobRegistry", _Registro)
    monkeypatch.setattr(rq.job.Job, "fetch_many", classmethod(
        lambda cls, js, connection=None: [estado["trabajos"][j.decode()
                                          if isinstance(j, bytes) else j]
                                          for j in js]))
    estado["conn"] = _Conn()
    return estado


def test_la_vista_previa_no_toca_nada(rq_falso):
    out = registro_fallidos.reencolar(
        "brevo:webhook_process", probar=True, conn=rq_falso["conn"])
    assert out["probar"] is True
    assert out["en_registro"] == 3
    assert out["elegidos"] == 2            # j2 no tiene datos
    assert out["sin_datos"] == 1
    assert out["reencolados"] == 0
    assert rq_falso["reencolados"] == []   # nada tocado
    assert out["ejemplo"]["id"] == "j1"


def test_reencola_solo_la_funcion_pedida(rq_falso):
    out = registro_fallidos.reencolar(
        "brevo:webhook_process", funcion="process_brevo_webhook_batch",
        probar=False, conn=rq_falso["conn"])
    assert out["reencolados"] == 1
    assert rq_falso["reencolados"] == ["j1"]


def test_el_tope_acota_cuantos_se_reencolan(rq_falso):
    out = registro_fallidos.reencolar(
        "brevo:push_contact", limite=1, probar=False, conn=rq_falso["conn"])
    assert out["reencolados"] == 1 and out["tope"] == 1
    assert rq_falso["reencolados"] == ["j1"]


def test_un_fallo_al_reencolar_no_corta_los_demas(rq_falso, monkeypatch):
    import rq.registry

    real = rq.registry.FailedJobRegistry

    class _Roto(real):
        def requeue(self, job_id):
            if job_id == "j1":
                raise RuntimeError("Redis dijo no")
            super().requeue(job_id)

    monkeypatch.setattr(rq.registry, "FailedJobRegistry", _Roto)
    out = registro_fallidos.reencolar(
        "brevo:webhook_process", probar=False, conn=rq_falso["conn"])
    assert out["fallos_al_reencolar"] == 1
    assert out["reencolados"] == 1          # el otro sí
    assert rq_falso["reencolados"] == ["j3"]


def test_vaciar_cuenta_en_vista_previa_y_descarta_al_confirmar(rq_falso):
    previa = registro_fallidos.vaciar("brevo:push_contact", probar=True,
                                      conn=rq_falso["conn"])
    assert previa["en_registro"] == 3 and previa["descartados"] == 0
    assert previa["quedan"] == 3
    assert rq_falso["borrados"] == []

    hecho = registro_fallidos.vaciar("brevo:push_contact", probar=False,
                                     conn=rq_falso["conn"])
    # Dos trabajos con datos (se borran de verdad, traza incluida: si no,
    # seguiría ocupando Redis) y uno que solo era un id en el registro. Lo
    # que NO vale es cantar tres descartados: el recuento es la única prueba
    # de que la memoria se ha liberado.
    assert hecho["descartados"] == 2
    assert hecho["sin_datos"] == 1
    assert hecho["fallos"] == 0
    assert hecho["quedan"] == 0
    assert rq_falso["borrados"] == ["j1", "j3"]
    assert rq_falso["quitados_del_registro"] == ["j2"]


def test_el_filtro_de_funcion_no_casa_por_el_rabo(rq_falso):
    """`endswith` a secas hacía que «job» casara con todos los `*_job` del
    sistema: se reencolaría cualquier cosa."""
    out = registro_fallidos.reencolar(
        "brevo:push_contact", funcion="job", probar=False,
        conn=rq_falso["conn"])
    assert out["elegidos"] == 0 and rq_falso["reencolados"] == []

    out = registro_fallidos.reencolar(
        "brevo:push_contact", funcion="push_contact_job", probar=False,
        conn=rq_falso["conn"])
    assert rq_falso["reencolados"] == ["j3"]


def test_la_previa_dice_hasta_donde_ha_mirado(rq_falso):
    """Con 146.876 entradas no se puede leer el registro entero dentro de una
    petición: se recorre por páginas y se dice cuántas se han revisado."""
    out = registro_fallidos.reencolar(
        "brevo:push_contact", limite=1, probar=True, conn=rq_falso["conn"])
    assert out["elegidos"] == 1
    assert out["revisados"] == 3        # una página, que aquí son los tres
    assert out["ventana_agotada"] is False
