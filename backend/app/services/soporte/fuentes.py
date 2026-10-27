"""La lista blanca de centros de soporte (`soporte_fuentes`).

Es lo ÚNICO de Internet de lo que el soporte descarga: cada petición sale
con `hosts_permitidos={dominio}` de su fuente, así que una redirección a otro
sitio se para y un enlace de otro dominio no se pide. Arranca con FLUX (la
siembra la migración 0148) y se edita desde Configuración ERP → Base de
conocimiento (solo administración).

Lo que se valida al guardar es lo que se puede validar sin salir a la red:
dominio con punto, sin IP ni nombres internos, y `base_url` en HTTPS sobre ese
mismo dominio. Que el dominio no resuelva a la red de dentro lo comprueba
`red.obtener` en cada petición (el DNS puede cambiar después de guardar).
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.fuentes_externas import SOPORTE_TIPOS, SoporteCache, SoporteFuente
from app.services.soporte.tipos import SoporteError

_CLAVE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")
#: Un nombre de dominio normal: letras, cifras, guiones y puntos, con al menos
#: un punto (los nombres sin punto son los servicios de Docker).
_DOMINIO_RE = re.compile(r"^(?=.{4,255}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_SUFIJOS_INTERNOS = (".local", ".localhost", ".internal", ".lan", ".home", ".corp",
                     ".localdomain")
#: Nuestro idioma (`es`) → el locale de la fuente (`es-419`).
_IDIOMA_RE = re.compile(r"^[a-z]{2}$")
_LOCALE_RE = re.compile(r"^[A-Za-z0-9_-]{1,15}$")
MAX_LOCALES = 20

#: Campos que se pueden cambiar con PATCH (la clave no: es el id).
_EDITABLES = ("nombre", "dominio", "tipo", "base_url", "marca", "locales", "activo",
              "posicion", "notas")
#: Los que admiten `null` para vaciarse.
_ANULABLES = frozenset({"marca", "notas"})
#: Si cambia alguno de estos, lo comprobado y lo guardado en caché ya no valen.
_CAMBIAN_CONTENIDO = frozenset({"dominio", "tipo", "base_url", "locales"})


def _utc(valor: datetime | None) -> datetime | None:
    """MySQL y SQLite devuelven la fecha sin zona: es UTC."""
    if valor is None:
        return None
    return valor if valor.tzinfo else valor.replace(tzinfo=UTC)


def locales_de(fuente: SoporteFuente) -> dict[str, str]:
    """`locales_json` leído con cuidado: lo que no sea `{idioma: locale}`
    válido se ignora (una fila tocada a mano no rompe la búsqueda)."""
    try:
        datos = json.loads(fuente.locales_json or "{}")
    except ValueError:
        return {}
    if not isinstance(datos, dict):
        return {}
    return {str(k).lower(): str(v) for k, v in datos.items()
            if isinstance(k, str) and isinstance(v, str) and _IDIOMA_RE.match(k.lower())
            and _LOCALE_RE.match(v)}


def fuente_como_dict(f: SoporteFuente) -> dict[str, Any]:
    """La forma `SoporteFuente` de `soporteApi.ts` (con `locales` como objeto)."""
    comprobado = _utc(f.comprobado_en)
    return {
        "clave": f.clave,
        "nombre": f.nombre,
        "dominio": f.dominio,
        "tipo": f.tipo,
        "base_url": f.base_url,
        "marca": f.marca,
        "locales": locales_de(f),
        "activo": bool(f.activo),
        "posicion": int(f.posicion or 0),
        "notas": f.notas,
        "comprobado_en": comprobado.isoformat() if comprobado else None,
        "comprobado_estado": f.comprobado_estado,
        "comprobado_detalle": f.comprobado_detalle,
    }


def listar(session: Session) -> list[SoporteFuente]:
    return list(session.scalars(
        select(SoporteFuente).order_by(SoporteFuente.posicion, SoporteFuente.clave)))


def activas(session: Session) -> list[SoporteFuente]:
    return [f for f in listar(session) if f.activo]


def obtener_activa(session: Session, clave: str) -> SoporteFuente:
    """La fuente si está en la lista blanca Y activa; si no, error y no se
    descarga nada: lo que no está en la lista no se pide."""
    fuente = session.get(SoporteFuente, (clave or "").strip().lower())
    if fuente is None:
        raise SoporteError("fuente_desconocida",
                           f"«{clave}» no está en la lista de centros de soporte.", 404)
    if not fuente.activo:
        raise SoporteError("fuente_inactiva",
                           f"{fuente.nombre} está desactivada en la lista de centros de "
                           "soporte.", 404)
    return fuente


def _invalido(mensaje: str) -> SoporteError:
    return SoporteError("datos_invalidos", mensaje, 400)


def _dominio(valor: Any) -> str:
    """`https://Support.Flux3dp.com/hc` → `support.flux3dp.com`."""
    texto = str(valor or "").strip().lower()
    if "://" in texto:
        texto = urlsplit(texto).hostname or ""
    texto = texto.split("/")[0].rstrip(".")
    if not _DOMINIO_RE.match(texto) or texto.endswith(_SUFIJOS_INTERNOS):
        raise _invalido(f"«{valor}» no es un dominio válido (p. ej. support.flux3dp.com).")
    return texto


def _base_url(valor: Any, dominio: str) -> str:
    texto = str(valor or "").strip().rstrip("/")
    try:
        partes = urlsplit(texto)
        puerto = partes.port
    except ValueError as exc:
        raise _invalido("La dirección base no es válida.") from exc
    if partes.scheme != "https":
        raise _invalido("La dirección base tiene que empezar por https://.")
    if partes.username or partes.password or puerto not in (None, 443):
        raise _invalido("La dirección base no puede llevar usuario, contraseña ni puerto.")
    if (partes.hostname or "").lower() != dominio:
        raise _invalido(f"La dirección base tiene que estar en {dominio}.")
    if partes.query or partes.fragment:
        raise _invalido("La dirección base no puede llevar «?» ni «#».")
    return texto


def _locales(valor: Any) -> str:
    if valor is None:
        return "{}"
    if not isinstance(valor, dict):
        raise _invalido("Los idiomas tienen que ser pares idioma → locale.")
    if len(valor) > MAX_LOCALES:
        raise _invalido(f"Como mucho {MAX_LOCALES} idiomas por fuente.")
    limpio: dict[str, str] = {}
    for idioma, locale in valor.items():
        clave = str(idioma or "").strip().lower()
        loc = str(locale or "").strip()
        if not _IDIOMA_RE.match(clave):
            raise _invalido(f"«{idioma}» no es un código de idioma de dos letras (es, en…).")
        if not _LOCALE_RE.match(loc):
            raise _invalido(f"«{locale}» no es un locale válido (p. ej. es-419, en-us).")
        limpio[clave] = loc
    return json.dumps(limpio, ensure_ascii=False, sort_keys=True)


def _tipo(valor: Any) -> str:
    tipo = str(valor or "").strip().lower()
    if tipo not in SOPORTE_TIPOS:
        raise _invalido(f"El tipo tiene que ser uno de: {', '.join(SOPORTE_TIPOS)}.")
    return tipo


def _texto(valor: Any, *, campo: str, maximo: int, obligatorio: bool = False) -> str | None:
    texto = str(valor).strip() if valor is not None else ""
    if not texto:
        if obligatorio:
            raise _invalido(f"Falta {campo}.")
        return None
    if len(texto) > maximo:
        raise _invalido(f"{campo.capitalize()}: como mucho {maximo} caracteres.")
    return texto


def _dominio_libre(session: Session, dominio: str, *, salvo: str | None = None) -> None:
    otra = session.scalar(select(SoporteFuente).where(SoporteFuente.dominio == dominio))
    if otra is not None and otra.clave != salvo:
        raise SoporteError("ya_existe", f"{dominio} ya está en la lista ({otra.nombre}).", 409)


def crear(session: Session, datos: dict[str, Any]) -> SoporteFuente:
    """Alta en la lista blanca. `dominio` puede faltar si viene `base_url` (se
    saca de ahí), y al revés (`https://{dominio}`)."""
    clave = str(datos.get("clave") or "").strip().lower()
    if not _CLAVE_RE.match(clave):
        raise _invalido("La clave: minúsculas, cifras, «-» o «_», de 2 a 40 (p. ej. artisjet).")
    if session.get(SoporteFuente, clave) is not None:
        raise SoporteError("ya_existe", f"Ya hay una fuente con la clave «{clave}».", 409)
    nombre = _texto(datos.get("nombre"), campo="el nombre", maximo=120, obligatorio=True)
    origen_dominio = datos.get("dominio") or datos.get("base_url")
    if not origen_dominio:
        raise _invalido("Falta el dominio (p. ej. support.flux3dp.com).")
    dominio = _dominio(origen_dominio)
    _dominio_libre(session, dominio)
    base = _base_url(datos.get("base_url") or f"https://{dominio}", dominio)
    posicion = datos.get("posicion")
    if posicion is None:
        posicion = max((f.posicion or 0 for f in listar(session)), default=-1) + 1
    fuente = SoporteFuente(
        clave=clave, nombre=nombre, dominio=dominio,
        tipo=_tipo(datos.get("tipo") or "zendesk"), base_url=base,
        marca=_texto(datos.get("marca"), campo="la marca", maximo=40),
        locales_json=_locales(datos.get("locales")),
        activo=bool(datos.get("activo", True)), posicion=int(posicion),
        notas=_texto(datos.get("notas"), campo="las notas", maximo=2000),
    )
    session.add(fuente)
    session.flush()
    return fuente


def actualizar(session: Session, clave: str, cambios: dict[str, Any]) -> SoporteFuente:
    """Cambia lo que venga. Si cambia de dónde o cómo se lee (dominio, tipo,
    dirección base, idiomas), lo comprobado y la caché de esa fuente se tiran:
    ya no dicen la verdad."""
    fuente = session.get(SoporteFuente, (clave or "").strip().lower())
    if fuente is None:
        raise SoporteError("fuente_desconocida",
                           f"«{clave}» no está en la lista de centros de soporte.", 404)
    cambios = {k: v for k, v in cambios.items() if k in _EDITABLES
               and (v is not None or k in _ANULABLES)}
    if not cambios:
        raise _invalido("No hay nada que cambiar.")
    if "nombre" in cambios:
        fuente.nombre = _texto(cambios["nombre"], campo="el nombre", maximo=120,
                               obligatorio=True) or fuente.nombre
    dominio = fuente.dominio
    if "dominio" in cambios:
        dominio = _dominio(cambios["dominio"])
        _dominio_libre(session, dominio, salvo=fuente.clave)
    if "base_url" in cambios or dominio != fuente.dominio:
        base = cambios.get("base_url")
        if not base:
            # Cambia el dominio y no viene dirección: la misma ruta en el nuevo.
            ruta = urlsplit(fuente.base_url).path if fuente.base_url else ""
            base = f"https://{dominio}{ruta}"
        fuente.base_url = _base_url(base, dominio)
    fuente.dominio = dominio
    if "tipo" in cambios:
        fuente.tipo = _tipo(cambios["tipo"])
    if "locales" in cambios:
        fuente.locales_json = _locales(cambios["locales"])
    if "marca" in cambios:
        fuente.marca = _texto(cambios["marca"], campo="la marca", maximo=40)
    if "notas" in cambios:
        fuente.notas = _texto(cambios["notas"], campo="las notas", maximo=2000)
    if "activo" in cambios:
        fuente.activo = bool(cambios["activo"])
    if "posicion" in cambios:
        fuente.posicion = int(cambios["posicion"])
    if _CAMBIAN_CONTENIDO & set(cambios):
        fuente.comprobado_en = None
        fuente.comprobado_estado = None
        fuente.comprobado_detalle = None
        vaciar_cache(session, fuente.clave)
    session.flush()
    return fuente


def vaciar_cache(session: Session, clave: str) -> None:
    session.execute(delete(SoporteCache).where(SoporteCache.fuente == clave)
                    .execution_options(synchronize_session=False))
