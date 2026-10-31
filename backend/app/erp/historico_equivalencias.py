"""Códigos de artículo equivalentes: el mismo modelo con dos códigos (1/11/2026).

El código interno de FACTUSOL y el de la web conviven (FLUX BEAMBOX 40W es
`60995` y `34`). `EQUART` no sirve: vacío en los diez de Flux y en el 93 %
del histórico. Así que es una tabla editable (`historico_equivalencias`):
cada código apunta a su canónico, y el canónico más los que apuntan a él son
un grupo.

Para qué sirve:

- **emparejar** un albarán con un código y su factura con el otro (si no,
  salen como dos máquinas de dos ventas);
- **el parque**: el mismo modelo, una marca de máquina vale para el grupo,
  y el aviso de «mismo modelo con otro código» ya no salta para un par
  declarado (sigue para los no declarados);
- **«Quién compró qué»**: buscar un código encuentra también las ventas del
  equivalente.

Los códigos se comparan sin mayúsculas ni tildes (como la colación de MySQL).
Solo se escribe aquí; FACTUSOL no se toca.
"""
from __future__ import annotations

import unicodedata
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.erp.models.historico import HistoricoArticulo, HistoricoEquivalencia

#: Los cinco pares de Flux que siembra la 0163 (canónico ← equivalente).
PARES_FLUX = (
    ("60995", "34"),       # Beambox 40W
    ("61000", "234"),      # Beambox Pro 50W
    ("60997", "1398"),     # Beamo 30W
    ("BT2EYB", "4173"),    # Beambox II 55W
    ("61403", "4174"),     # Flux Hexa 60W
)


def clave(codart: str | None) -> str:
    t = unicodedata.normalize("NFKD", codart or "").encode("ascii", "ignore").decode()
    return t.strip().upper()


class Equivalencias:
    """Los grupos, leídos una vez. `canonico(c)` es la clave del grupo (en
    mayúsculas); `grupo(c)` los códigos del grupo tal como se guardaron."""

    def __init__(self, filas: list[tuple[str, str]]) -> None:
        self._canon: dict[str, str] = {}
        self._grupos: dict[str, set[str]] = {}
        for codart, canonico in filas:
            cc = clave(canonico)
            self._canon[clave(codart)] = cc
            self._grupos.setdefault(cc, {canonico}).add(codart)

    def canonico(self, codart: str | None) -> str:
        k = clave(codart)
        return self._canon.get(k, k)

    def grupo(self, codart: str | None) -> set[str]:
        """Todos los códigos del grupo (incluido el propio, tal cual)."""
        g = self._grupos.get(self.canonico(codart))
        return ({codart} | g) if g else ({codart} if codart else set())

    def declarado(self, a: str | None, b: str | None) -> bool:
        return self.canonico(a) == self.canonico(b)

    def __bool__(self) -> bool:
        return bool(self._canon)


def cargar(session: Session) -> Equivalencias:
    return Equivalencias([(e.codart, e.canonico)
                          for e in session.scalars(select(HistoricoEquivalencia))])


def _descripciones(session: Session, codigos: set[str]) -> dict[str, str | None]:
    if not codigos:
        return {}
    out: dict[str, str | None] = {}
    for a in session.scalars(select(HistoricoArticulo).where(
            func.upper(HistoricoArticulo.codart).in_(sorted(clave(c) for c in codigos)))
            .order_by(HistoricoArticulo.ejercicio)):
        out[clave(a.codart)] = a.descripcion       # la del catálogo más reciente
    return out


def listar(session: Session) -> list[dict[str, Any]]:
    filas = list(session.scalars(select(HistoricoEquivalencia)
                                 .order_by(HistoricoEquivalencia.canonico,
                                           HistoricoEquivalencia.codart)))
    desc = _descripciones(session, {f.codart for f in filas} | {f.canonico for f in filas})
    return [{"codart": f.codart, "descripcion": desc.get(clave(f.codart)),
             "canonico": f.canonico, "canonico_descripcion": desc.get(clave(f.canonico)),
             "por": f.por, "at": f"{f.at.isoformat()}Z" if f.at else None} for f in filas]


def _existe(session: Session, codart: str) -> str | None:
    """El código tal como está en el catálogo (o None si no está)."""
    return session.scalar(select(HistoricoArticulo.codart).where(
        func.upper(HistoricoArticulo.codart) == clave(codart)).limit(1))


def declarar(session: Session, canonico: str, codart: str, *, por: str) -> None:
    """`codart` es el mismo modelo que `canonico`. ValueError si son el
    mismo, si alguno no está en ningún catálogo o si `codart` ya va con
    otro grupo (se quita antes)."""
    a, b = (canonico or "").strip(), (codart or "").strip()
    if not a or not b or clave(a) == clave(b):
        raise ValueError("mismo_codigo")
    a_cat, b_cat = _existe(session, a), _existe(session, b)
    if a_cat is None or b_cat is None:
        raise ValueError("sin_catalogo")
    eq = cargar(session)
    if eq.declarado(a_cat, b_cat):
        raise ValueError("ya_declarado")
    if clave(b_cat) in eq._canon or clave(b_cat) in eq._grupos:
        raise ValueError("ya_en_otro_grupo")
    # Si el canónico elegido ya apunta a otro, va al canónico de su grupo.
    destino = next((c for c in eq._grupos.get(eq.canonico(a_cat), ())
                    if clave(c) == eq.canonico(a_cat)), a_cat)
    session.add(HistoricoEquivalencia(codart=b_cat, canonico=destino, por=por,
                                      at=datetime.now(UTC).replace(tzinfo=None)))
    session.commit()


def quitar(session: Session, codart: str) -> None:
    """Quita la equivalencia de `codart`. Si `codart` es el canónico de un
    grupo (no tiene fila propia), deshace el grupo entero."""
    todas = list(session.scalars(select(HistoricoEquivalencia)))
    filas = [e for e in todas if clave(e.codart) == clave(codart)] or \
            [e for e in todas if clave(e.canonico) == clave(codart)]
    if not filas:
        raise LookupError("equivalencia")
    for f in filas:
        session.delete(f)
    session.commit()
