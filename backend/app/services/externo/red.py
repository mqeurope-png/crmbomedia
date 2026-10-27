"""Peticiones a la red de fuera con cerrojos.

Todo lo que el backend va a buscar a una URL que no es suya pasa por aquí
(`obtener`): la imagen que pega Bart, el centro de soporte de un tercero y
nuestras propias webs. Las reglas:

- **Solo HTTPS** (salvo que quien llama lo permita a propósito) y solo los
  puertos de siempre (443, y 80 si se permite HTTP). Sin usuario ni
  contraseña en la URL.
- **Nunca la red de dentro**: el nombre se resuelve y TODAS sus direcciones
  tienen que ser públicas. Fuera `localhost`, `127.0.0.0/8`, los rangos
  privados (10/8, 172.16/12 —la red de Docker—, 192.168/16), enlace local
  (169.254/16, donde vive el metadato de las nubes), CGNAT (100.64/10),
  multidifusión, reservadas, sus equivalentes IPv6 y las IPv4 metidas en
  IPv6. También los nombres sin punto (los servicios de Docker: `api`,
  `redis`, `db`) y los `.local` / `.internal` / `.localhost`.
- **Las redirecciones se siguen a mano** y cada salto se vuelve a comprobar:
  una URL pública que redirige a `http://127.0.0.1/` se para ahí.
- **Tiempo máximo** por petición y **tamaño máximo**: el cuerpo se lee a
  trozos y se corta al pasar del tope (error, o truncado si quien llama lo
  acepta).

Queda una ventana pequeña: el nombre se resuelve aquí y httpx lo vuelve a
resolver al conectar (un DNS hostil podría cambiar de respuesta entre las dos,
«DNS rebinding»). Para cerrarla del todo habría que conectar a la IP
comprobada; con lo que se descarga (imágenes, artículos y webs públicas, nunca
con credenciales) no compensa.
"""
from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

#: Lo que se manda como agente de usuario a las webs de fuera. Solo ASCII:
#: httpx codifica las cabeceras en ASCII y una tilde rompe el cliente.
USER_AGENT = "BoHub/1.0 (+lectura de webs publicas; no escribe nada)"
#: Saltos de redirección que se siguen como mucho.
MAX_REDIRECCIONES = 4
#: Sufijos de nombre que nunca son de fuera.
_SUFIJOS_INTERNOS = (".local", ".localhost", ".internal", ".lan", ".home", ".corp",
                     ".localdomain")
#: CGNAT (100.64.0.0/10): `is_global` ya lo da por no global, pero se deja
#: explícito por si cambia la biblioteca.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")

Resolutor = Callable[..., list[Any]]


class RedError(Exception):
    """Una petición de fuera que no se hace o no sale bien. `codigo` es
    estable (para la API y los tests); el mensaje se puede enseñar tal cual."""

    def __init__(self, codigo: str, mensaje: str, *, status: int | None = None):
        super().__init__(mensaje)
        self.codigo = codigo
        self.status = status


class UrlNoPermitida(RedError):
    """La URL no se puede pedir (esquema, puerto, red interna…)."""


@dataclass
class Respuesta:
    status: int
    url: str
    cabeceras: dict[str, str]
    contenido: bytes
    #: Se cortó al llegar al tope (solo si quien llama lo aceptó).
    truncado: bool = False
    #: Bytes que anunció el servidor (`Content-Length`), si los anunció.
    longitud_anunciada: int | None = None
    saltos: list[str] = field(default_factory=list)

    @property
    def tipo(self) -> str:
        """`Content-Type` sin parámetros, en minúsculas."""
        return (self.cabeceras.get("content-type") or "").split(";")[0].strip().lower()

    def texto(self) -> str:
        charset = "utf-8"
        for parte in (self.cabeceras.get("content-type") or "").split(";")[1:]:
            clave, _, valor = parte.strip().partition("=")
            if clave.lower() == "charset" and valor:
                charset = valor.strip().strip('"')
        try:
            return self.contenido.decode(charset, errors="replace")
        except LookupError:
            return self.contenido.decode("utf-8", errors="replace")

    def json(self) -> Any:
        import json  # noqa: PLC0415

        try:
            return json.loads(self.texto())
        except ValueError as exc:
            raise RedError("respuesta_ilegible", "La respuesta no es JSON válido.") from exc


def ip_prohibida(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """¿Es una dirección a la que el backend no debe ir nunca?"""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip_prohibida(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            return ip_prohibida(ip.sixtofour)
        if ip.teredo is not None:
            return True
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT:
        return True
    return (not ip.is_global or ip.is_multicast or ip.is_private or ip.is_loopback
            or ip.is_link_local or ip.is_reserved or ip.is_unspecified)


def _host_de(url: str, *, permitir_http: bool) -> tuple[str, int, str]:
    try:
        partes = urlsplit(url.strip())
    except ValueError as exc:
        raise UrlNoPermitida("url_invalida", "La dirección no es válida.") from exc
    esquema = (partes.scheme or "").lower()
    if esquema not in ("https", "http") or (esquema == "http" and not permitir_http):
        if esquema == "http":
            raise UrlNoPermitida(
                "no_https",
                "Solo se admiten direcciones https://: una imagen en http:// la bloquean "
                "los clientes de correo.")
        raise UrlNoPermitida("url_invalida", "La dirección tiene que empezar por https://.")
    if partes.username or partes.password:
        raise UrlNoPermitida("url_invalida", "La dirección no puede llevar usuario ni contraseña.")
    host = (partes.hostname or "").strip().rstrip(".").lower()
    if not host:
        raise UrlNoPermitida("url_invalida", "La dirección no tiene dominio.")
    try:
        puerto = partes.port or (443 if esquema == "https" else 80)
    except ValueError as exc:
        raise UrlNoPermitida("url_invalida", "El puerto de la dirección no es válido.") from exc
    if puerto not in ((443, 80) if permitir_http else (443,)):
        raise UrlNoPermitida("puerto_no_permitido",
                             f"No se admiten direcciones con el puerto {puerto}.")
    return host, puerto, esquema


def comprobar_url(url: str, *, permitir_http: bool = False,
                  resolver: Resolutor | None = None) -> str:
    """Comprueba que la URL se puede pedir y devuelve su host. Lanza
    `UrlNoPermitida` si no: esquema, puerto, nombre interno o alguna de sus
    direcciones en una red privada."""
    host, puerto, _ = _host_de(url, permitir_http=permitir_http)
    if host == "localhost" or host.endswith(_SUFIJOS_INTERNOS):
        raise UrlNoPermitida("red_interna", "No se admiten direcciones de la red interna.")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if ip_prohibida(literal):
            raise UrlNoPermitida("red_interna", "No se admiten direcciones de la red interna.")
        return host
    if "." not in host:
        # `api`, `redis`, `db`: los nombres de los servicios de Docker.
        raise UrlNoPermitida("red_interna", "No se admiten direcciones de la red interna.")
    try:
        direcciones = (resolver or socket.getaddrinfo)(host, puerto, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise UrlNoPermitida("no_resuelve", f"No se encuentra el dominio {host}.") from exc
    ips = {info[4][0] for info in direcciones if info and len(info) > 4 and info[4]}
    if not ips:
        raise UrlNoPermitida("no_resuelve", f"No se encuentra el dominio {host}.")
    for valor in ips:
        try:
            ip = ipaddress.ip_address(str(valor).split("%")[0])
        except ValueError as exc:
            raise UrlNoPermitida("red_interna", "El dominio no resuelve a una dirección válida.") \
                from exc
        if ip_prohibida(ip):
            raise UrlNoPermitida("red_interna",
                                 f"El dominio {host} apunta a la red interna: no se admite.")
    return host


def _cliente(timeout: float) -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)),
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
    )


def obtener(
    url: str, *, max_bytes: int, timeout: float = 10.0, metodo: str = "GET",
    cabeceras: dict[str, str] | None = None, permitir_http: bool = False,
    aceptar_truncado: bool = False, max_redirecciones: int = MAX_REDIRECCIONES,
    hosts_permitidos: set[str] | None = None, resolver: Resolutor | None = None,
    cliente: httpx.Client | None = None,
) -> Respuesta:
    """GET (o HEAD) con los cerrojos de arriba.

    - `max_bytes`: tope del cuerpo. Pasado, `RedError("demasiado_grande")`,
      o se corta y se marca `truncado` si `aceptar_truncado`.
    - `timeout`: segundos para TODA la petición, redirecciones incluidas.
    - `hosts_permitidos`: si viene, cada salto tiene que ir a uno de esos
      hosts (la lista blanca del soporte): una redirección fuera se para.
    - `cliente`: para los tests (un `httpx.Client` con `MockTransport`).
    """
    limite = time.monotonic() + timeout
    propio = cliente is None
    http = cliente or _cliente(timeout)
    actual = url.strip()
    saltos: list[str] = []
    try:
        for _ in range(max_redirecciones + 1):
            host = comprobar_url(actual, permitir_http=permitir_http, resolver=resolver)
            if hosts_permitidos is not None and host not in hosts_permitidos:
                raise UrlNoPermitida("dominio_no_permitido",
                                     f"{host} no está en la lista de dominios permitidos.")
            restante = limite - time.monotonic()
            if restante <= 0:
                raise RedError("tiempo_agotado", "La web ha tardado demasiado en responder.")
            peticion = http.build_request(
                metodo, actual, headers=cabeceras or {},
                extensions={"timeout": httpx.Timeout(restante, connect=min(restante, 10.0))
                            .as_dict()},
            )
            try:
                respuesta = http.send(peticion, stream=True)
            except httpx.TimeoutException as exc:
                raise RedError("tiempo_agotado", "La web ha tardado demasiado en responder.") \
                    from exc
            except httpx.HTTPError as exc:
                raise RedError("sin_conexion",
                               f"No se ha podido conectar ({type(exc).__name__}).") from exc
            try:
                if respuesta.is_redirect and respuesta.headers.get("location"):
                    saltos.append(actual)
                    actual = urljoin(actual, respuesta.headers["location"])
                    continue
                return _leer(respuesta, actual, max_bytes=max_bytes, limite=limite,
                             aceptar_truncado=aceptar_truncado, saltos=saltos,
                             metodo=metodo)
            finally:
                respuesta.close()
        raise RedError("demasiadas_redirecciones", "La dirección redirige demasiadas veces.")
    finally:
        if propio:
            http.close()


def _leer(respuesta: httpx.Response, url: str, *, max_bytes: int, limite: float,
          aceptar_truncado: bool, saltos: list[str], metodo: str) -> Respuesta:
    cabeceras = {k.lower(): v for k, v in respuesta.headers.items()}
    try:
        anunciada = int(cabeceras.get("content-length") or "") if cabeceras.get(
            "content-length") else None
    except ValueError:
        anunciada = None
    trozos: list[bytes] = []
    leidos = 0
    truncado = False
    if metodo.upper() != "HEAD":
        if anunciada is not None and anunciada > max_bytes and not aceptar_truncado:
            raise RedError("demasiado_grande",
                           f"El contenido pesa {anunciada} bytes; el máximo es {max_bytes}.",
                           status=respuesta.status_code)
        try:
            for trozo in respuesta.iter_bytes():
                if time.monotonic() > limite:
                    raise RedError("tiempo_agotado", "La web ha tardado demasiado en responder.")
                if leidos + len(trozo) > max_bytes:
                    if not aceptar_truncado:
                        raise RedError("demasiado_grande",
                                       f"El contenido pasa de {max_bytes} bytes.",
                                       status=respuesta.status_code)
                    trozos.append(trozo[: max_bytes - leidos])
                    leidos = max_bytes
                    truncado = True
                    break
                trozos.append(trozo)
                leidos += len(trozo)
        except httpx.TimeoutException as exc:
            raise RedError("tiempo_agotado", "La web ha tardado demasiado en responder.") from exc
        except httpx.HTTPError as exc:
            raise RedError("sin_conexion",
                           f"Se cortó la descarga ({type(exc).__name__}).") from exc
    return Respuesta(status=respuesta.status_code, url=url, cabeceras=cabeceras,
                     contenido=b"".join(trozos), truncado=truncado,
                     longitud_anunciada=anunciada, saltos=saltos)
