"""Los dominios que no emparejan (1/11/2026): la lista que dice qué dominios
de correo NO identifican a una empresa.

Un contacto con un dominio de la lista nunca se vincula a una empresa por el
dominio (ni por el de su correo ni por la web que declare). La lista es un
dato, en `dominios_compartidos`: se edita en Administración → Dominios
compartidos, sin tocar código.

Un subdominio cuenta como su dominio: si `xtec.cat` está en la lista,
`alumnes.xtec.cat` también. Lo prudente: un subdominio de una consejería no
identifica un centro con seguridad, y el coste de no emparejar es solo que el
contacto queda sin empresa hasta que alguien se la ponga.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.dominios import TIPO_COMPARTIDO, TIPO_CORREO, TIPOS, DominioCompartido

_CACHE = "dominios_compartidos"
_DOMINIO_RE = re.compile(r"^(?=.{3,255}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


def normaliza(valor: str | None) -> str:
    """Minúsculas, sin espacios, sin `@` delante ni `www.`."""
    d = (valor or "").strip().lower().lstrip("@")
    return d[4:] if d.startswith("www.") else d


def dominio_de_correo(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return normaliza(email.rsplit("@", 1)[1]) or None


@dataclass(frozen=True)
class Lista:
    """La lista leída una vez: dominio → tipo."""

    tipos: dict[str, str] = field(default_factory=dict)

    def de_la_lista(self, dominio: str | None) -> str | None:
        """El dominio de la lista que cubre a `dominio` (él mismo o uno del
        que es subdominio), o None."""
        d = normaliza(dominio)
        while d:
            if d in self.tipos:
                return d
            if "." not in d:
                return None
            d = d.split(".", 1)[1]
        return None

    def contiene(self, dominio: str | None) -> bool:
        return self.de_la_lista(dominio) is not None

    def tipo(self, dominio: str | None) -> str | None:
        d = self.de_la_lista(dominio)
        return self.tipos[d] if d else None

    def compartido(self, dominio: str | None) -> bool:
        """De una organización compartida (no un proveedor de correo)."""
        return self.tipo(dominio) == TIPO_COMPARTIDO

    def __len__(self) -> int:
        return len(self.tipos)


def cargar(session: Session, *, refrescar: bool = False) -> Lista:
    """La lista, leída una vez por sesión (una sincronización de Brevo
    resuelve miles de contactos con la misma)."""
    if not refrescar and isinstance(session.info.get(_CACHE), Lista):
        return session.info[_CACHE]
    filas = session.scalars(select(DominioCompartido))
    lista = Lista({normaliza(d.dominio): d.tipo for d in filas})
    session.info[_CACHE] = lista
    return lista


def _olvidar(session: Session) -> None:
    session.info.pop(_CACHE, None)


def listar(session: Session, *, emails: Iterable[str | None] | None = None) -> list[dict[str, Any]]:
    """Las filas de la lista. Con `emails`, cuántos contactos caen en cada
    dominio (para la pantalla)."""
    lista = cargar(session, refrescar=True)
    cuenta: dict[str, int] = {}
    for e in emails or ():
        if (d := lista.de_la_lista(dominio_de_correo(e))) is not None:
            cuenta[d] = cuenta.get(d, 0) + 1
    filas = session.scalars(select(DominioCompartido).order_by(
        DominioCompartido.tipo.desc(), DominioCompartido.dominio))
    return [{"dominio": f.dominio, "tipo": f.tipo, "nota": f.nota, "por": f.por,
             "at": f"{f.at.isoformat()}Z" if f.at else None,
             "contactos": cuenta.get(normaliza(f.dominio), 0) if emails is not None else None}
            for f in filas]


def anadir(session: Session, dominio: str, *, tipo: str = TIPO_COMPARTIDO,
           nota: str | None = None, por: str | None = None) -> dict[str, Any]:
    """Añade un dominio. ValueError si no es un dominio, si el tipo no vale o
    si ya está (él o el dominio del que es subdominio)."""
    d = normaliza(dominio)
    if not _DOMINIO_RE.match(d):
        raise ValueError("dominio_no_valido")
    if tipo not in TIPOS:
        raise ValueError("tipo_no_valido")
    if (ya := cargar(session, refrescar=True).de_la_lista(d)) is not None:
        raise ValueError("ya_esta" if ya == d else "ya_lo_cubre")
    fila = DominioCompartido(dominio=d, tipo=tipo, nota=(nota or "").strip()[:255] or None,
                             por=por, at=datetime.now(UTC).replace(tzinfo=None))
    session.add(fila)
    session.commit()
    _olvidar(session)
    return {"dominio": d, "tipo": tipo}


def quitar(session: Session, dominio: str) -> bool:
    fila = session.get(DominioCompartido, normaliza(dominio))
    if fila is None:
        return False
    session.delete(fila)
    session.commit()
    _olvidar(session)
    return True


__all__ = ["TIPO_COMPARTIDO", "TIPO_CORREO", "Lista", "anadir", "cargar", "dominio_de_correo",
           "listar", "normaliza", "quitar"]
