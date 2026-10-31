"""Cómo se COMPARAN los códigos del histórico, sin reescribir lo guardado
(1/11/2026). Los códigos de FACTUSOL son los que son; aquí se decide con qué
casa cada uno.

**Familias.**

- **Sin distinguir mayúsculas**: `bob` es `BOB` (como el código de artículo).
- **El fantasma del cero**: una familia corta (`19`) se funde en su gemela
  larga (`019`) SOLO si la corta no tiene descripción, ni clase, ni gama: es
  la que creó la carga a partir de un artículo con el cero perdido. Si las
  dos tienen descripción o clase propia, son familias distintas (`014
  CELOFÁN PRECORTADO` y `14 Recambios Speed Wrapper`) y no se tocan nunca.
  Si la corta no tiene descripción pero sí una clase que puso Bart, no se
  funde: se avisa.

**Códigos de artículo de las líneas** (`historico_alias_articulo`, 0164):
una línea tecleada como `053332` es el artículo `53332` del catálogo. Lo
decide Bart caso a caso (un `1` puede ser el disco o «Transporte»); la 0164
siembra los nueve que ya se miraron uno a uno.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.erp.models.historico import HistoricoArticulo, HistoricoFamilia


def _k(codigo: str | None) -> str:
    return (codigo or "").strip().upper()


@dataclass
class AliasFamilias:
    #: código tal como lo usan los artículos → código de la familia que manda.
    mapa: dict[str, str] = field(default_factory=dict)
    #: fantasmas fundidos: corta → larga.
    fundidas: dict[str, str] = field(default_factory=dict)
    #: gemelas que NO se funden y hay que mirar.
    avisos: list[dict[str, Any]] = field(default_factory=list)
    #: códigos de la tabla en mayúsculas → el código guardado.
    _tabla: dict[str, str] = field(default_factory=dict)

    def resuelve(self, codigo: str | None) -> str | None:
        if codigo is None:
            return None
        if codigo in self.mapa:
            return self.mapa[codigo]
        return self._tabla.get(_k(codigo), codigo)

    def expr(self, columna: Any) -> Any:
        """La columna de familia de un artículo, resuelta (para un JOIN)."""
        return case(self.mapa, value=columna, else_=columna) if self.mapa else columna


def familias(session: Session) -> AliasFamilias:
    tabla = list(session.scalars(select(HistoricoFamilia)))
    out = AliasFamilias(_tabla={_k(f.codigo): f.codigo for f in tabla})
    por_numero: dict[int, list[HistoricoFamilia]] = {}
    for f in tabla:
        c = (f.codigo or "").strip()
        if c.isdigit():
            por_numero.setdefault(int(c), []).append(f)
    for _n, grupo in por_numero.items():
        if len(grupo) < 2:
            continue
        cortas = [f for f in grupo
                  if not f.codigo.strip().startswith("0") or f.codigo.strip() == "0"]
        largas = [f for f in grupo if f not in cortas]
        for corta in cortas:
            if corta.descripcion:
                continue                # las dos con nombre: familias distintas
            con_nombre = [f for f in largas if f.descripcion]
            if corta.clase or corta.gama:
                out.avisos.append({"corta": corta.codigo,
                                   "largas": [f.codigo for f in con_nombre],
                                   "motivo": "clase_puesta"})
                continue
            if len(con_nombre) != 1:
                if con_nombre:
                    out.avisos.append({"corta": corta.codigo,
                                       "largas": [f.codigo for f in con_nombre],
                                       "motivo": "varias"})
                continue
            out.fundidas[corta.codigo] = con_nombre[0].codigo
    # Lo que usan los artículos: el fantasma va a su larga; otra caja, a la
    # de la tabla.
    usadas = set(session.scalars(select(HistoricoArticulo.familia).distinct()
                                 .where(HistoricoArticulo.familia.is_not(None))))
    for u in usadas:
        destino = out._tabla.get(_k(u))
        if destino in out.fundidas:
            destino = out.fundidas[destino]
        if destino and destino != u:
            out.mapa[u] = destino
    return out


# --- códigos de artículo de las líneas --------------------------------------------------


@dataclass
class AliasArticulos:
    #: código de la línea (en mayúsculas) → código del catálogo.
    mapa: dict[str, str] = field(default_factory=dict)
    #: el código de la línea tal como se guardó → código del catálogo.
    crudo: dict[str, str] = field(default_factory=dict)

    def resuelve(self, codigo: str | None) -> str | None:
        if codigo is None:
            return None
        return self.mapa.get(_k(codigo), codigo)

    def expr(self, columna: Any) -> Any:
        """La columna `codart` de una línea, resuelta al catálogo (JOIN)."""
        return case(self.crudo, value=columna, else_=columna) if self.crudo else columna


def articulos(session: Session) -> AliasArticulos:
    from app.erp.models.historico import HistoricoAliasArticulo  # noqa: PLC0415

    out = AliasArticulos()
    for a in session.scalars(select(HistoricoAliasArticulo)):
        out.mapa[_k(a.codigo)] = a.codart
        out.crudo[a.codigo] = a.codart
    return out


def listar_articulos(session: Session) -> list[dict[str, Any]]:
    from app.erp.models.historico import HistoricoAliasArticulo  # noqa: PLC0415

    return [{"codigo": a.codigo, "codart": a.codart, "por": a.por}
            for a in session.scalars(select(HistoricoAliasArticulo)
                                     .order_by(HistoricoAliasArticulo.codigo))]


def declarar_articulo(session: Session, codigo: str, codart: str, *, por: str) -> None:
    """La línea tecleada como `codigo` es el artículo `codart` del catálogo.
    ValueError si `codart` no está en el catálogo, si `codigo` sí está (no es
    un código perdido) o si ya tiene alias."""
    from datetime import UTC, datetime  # noqa: PLC0415

    from app.erp.models.historico import HistoricoAliasArticulo  # noqa: PLC0415

    codigo, codart = (codigo or "").strip(), (codart or "").strip()
    if not codigo or not codart or _k(codigo) == _k(codart):
        raise ValueError("mismo_codigo")
    en_catalogo = {_k(c) for c in session.scalars(select(HistoricoArticulo.codart).where(
        HistoricoArticulo.codart.in_([codigo, codart])))}
    destino = next((c for c in session.scalars(select(HistoricoArticulo.codart).where(
        HistoricoArticulo.codart == codart).limit(1))), None)
    if destino is None:
        raise ValueError("sin_catalogo")
    if _k(codigo) in en_catalogo:
        raise ValueError("codigo_en_catalogo")
    if _k(codigo) in articulos(session).mapa:
        raise ValueError("ya_tiene_alias")
    session.add(HistoricoAliasArticulo(codigo=codigo, codart=destino, por=por,
                                       at=datetime.now(UTC).replace(tzinfo=None)))
    session.commit()


def quitar_articulo(session: Session, codigo: str) -> None:
    from app.erp.models.historico import HistoricoAliasArticulo  # noqa: PLC0415

    fila = next((a for a in session.scalars(select(HistoricoAliasArticulo))
                 if _k(a.codigo) == _k(codigo)), None)
    if fila is None:
        raise LookupError("alias")
    session.delete(fila)
    session.commit()
