"""Contexto de una pasada del Cuadre: sesión, «ahora», umbrales y las lecturas
compartidas entre comprobaciones (cada tabla de FACTUSOL se lee UNA vez por
pasada y del ejercicio en curso; los pedidos, una vez)."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.erp.cuadre.config import check_dias, cuadre_config


class FactusolNoDisponible(RuntimeError):
    """La pasada de FACTUSOL no tiene cliente (sin configurar / sin conexión)."""


class Contexto:
    def __init__(
        self,
        session: Session,
        *,
        ahora: datetime | None = None,
        config: dict[str, Any] | None = None,
        client: Any = None,
        ejercicio: str | None = None,
    ) -> None:
        self.session = session
        self.ahora = ahora or datetime.now(UTC)
        self.config = config if config is not None else cuadre_config(session)
        self.client = client
        self.ejercicio = ejercicio
        self._cache: dict[str, Any] = {}

    # -- umbrales ------------------------------------------------------------------

    def dias(self, check_id: str, defecto: int) -> int:
        """Umbral en días de la comprobación (Configuración ERP o su defecto)."""
        valor = check_dias(self.config, check_id)
        return int(valor) if valor else defecto

    def dias_desde(self, momento: datetime | None) -> int | None:
        """Días completos transcurridos desde `momento` (None si no hay fecha)."""
        if momento is None:
            return None
        if momento.tzinfo is None:
            momento = momento.replace(tzinfo=UTC)
        return (self.ahora - momento).days

    # -- lecturas compartidas -----------------------------------------------------

    def cached(self, clave: str, fn: Callable[[], Any]) -> Any:
        if clave not in self._cache:
            self._cache[clave] = fn()
        return self._cache[clave]

    def pedidos(self) -> list[Any]:
        """Todos los pedidos con su historial de estados (una sola carga)."""
        from app.erp.models import Order  # noqa: PLC0415

        return self.cached("pedidos", lambda: list(self.session.scalars(
            select(Order).options(selectinload(Order.status_history))
        )))

    def olvidar_orm(self) -> None:
        """Tras un rollback los objetos de la sesión caducan: se olvidan las
        lecturas de la BD (las de FACTUSOL se conservan: no se releen)."""
        for clave in [k for k in self._cache if not k.startswith("tabla:")]:
            self._cache.pop(clave, None)

    def tabla(self, nombre: str) -> list[dict[str, Any]]:
        """Filas de una tabla de FACTUSOL del ejercicio en curso, leídas UNA vez
        por pasada (`1=1`: filtrar por una columna inexistente devuelve `[]` en
        silencio). Solo lectura.

        Una tabla VACÍA se trata como lectura rota (`FactusolNoDisponible`): si
        no, las comprobaciones darían por resueltos (o por nuevos) todos sus
        descuadres. Un fallo también se recuerda en la pasada, para no volver a
        cargar a DELSOL desde cada comprobación."""
        if self.client is None or not self.ejercicio:
            raise FactusolNoDisponible("FACTUSOL no está disponible para esta pasada.")
        clave = f"tabla:{nombre}"
        if clave not in self._cache:
            try:
                filas = list(self.client.load_table(nombre, filtro="1=1", ejercicio=self.ejercicio))
            except Exception as exc:  # noqa: BLE001 — se recuerda y se relanza
                self._cache[clave] = FactusolNoDisponible(
                    f"No se pudo leer {nombre} de FACTUSOL ({type(exc).__name__})."
                )
            else:
                self._cache[clave] = filas if filas else FactusolNoDisponible(
                    f"{nombre} ha llegado vacía de FACTUSOL (ejercicio {self.ejercicio}): "
                    "no se juzga para no dar por resuelto lo que no se ha podido leer."
                )
        valor = self._cache[clave]
        if isinstance(valor, FactusolNoDisponible):
            raise valor
        return valor
