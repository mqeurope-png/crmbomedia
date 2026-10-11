"""De qué web viene un lead, dicho en claro; y qué dominios son del grupo.

Con 25 formularios en ocho webs y seis idiomas, «web_form» no dice nada. El
origen de cada lead lleva la WEB y el idioma del formulario
(`web_form:<sitio>:<idioma>`), y aquí se traduce a algo legible: «Nuevo lead
desde mboprinters.com (alemán)».

La web NO se deduce de `web_forms.brand`: una misma marca puede estar en dos
webs distintas (`artisjet` lo está en artisjet-spain.es y en
artisjet-printers.eu). Se deduce del **slug**, que es la clave pública y
única de cada formulario: lo que va antes de `-contacto`
(`artisjet-es-contacto-de` → `artisjet-es`).

Desde el 11/10/2026 la lista de sitios vive en la tabla `sitios_web`
(Configuración ERP) y aquí se LEE: `registro()` la carga con una caché de un
minuto (y `invalidar()` la tira al guardar) y `SITIOS_DE_PARTIDA` es la lista
con la que se siembra y la que vale cuando la tabla está vacía (una base
recién creada, los tests). Las funciones de siempre (`web_de_sitio`,
`marca_de_sitio`, `remitente_de_sitio`) siguen igual y no necesitan sesión.
`WEBS`, `MARCAS` y `REMITENTES` son la lista de partida, para quien la quiera
como constante (filtros de segmentos); lo vivo está en el registro.

La misma tabla dice qué **dominios son del grupo** (`dominios_del_grupo`),
que es lo que decide la captura universal de correo: si un mensaje toca uno
de nuestros dominios, se guarda. Los alias de `user_email_aliases` ya no son
la puerta de la entrada, solo atribuyen.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.models import sitios as _modelo_sitios  # noqa: F401 — registra la tabla en Base
from app.models.sitios import PLUGIN_QTRANSLATE, PLUGIN_WPML, TIPO_CORREO, TIPO_WEB
from app.services.web_forms.textos import nombre_idioma, normalizar_idioma

logger = logging.getLogger(__name__)

__all__ = ["PLUGIN_QTRANSLATE", "PLUGIN_WPML", "TIPO_CORREO", "TIPO_WEB"]


@dataclass(frozen=True)
class Sitio:
    """Una web (o un dominio de correo) del grupo."""

    clave: str
    dominio: str
    marca: str
    empresa: str
    idioma_defecto: str = "es"
    idiomas: tuple[str, ...] = ("es",)
    host: str | None = None
    dominios_extra: tuple[str, ...] = ()
    plugin: str | None = None
    prefijo_idioma_defecto: bool = False
    slugs_traducidos: bool = False
    soporte_propio: bool = False
    remitente_defecto: str | None = None
    tipo: str = TIPO_WEB
    posicion: int = 0
    activo: bool = True

    @property
    def web(self) -> str:
        """El host con el que se enseña y se construyen URLs."""
        return self.host or self.dominio

    @property
    def dominios(self) -> tuple[str, ...]:
        return (self.dominio, *self.dominios_extra)

    def url_base(self, idioma: str | None = None) -> str:
        """`https://host/` o `https://host/de/` según el idioma pedido y el
        plugin: el idioma por defecto va sin prefijo (salvo que la web lo
        lleve); un idioma que no está publicado cae al por defecto."""
        idioma = normalizar_idioma(idioma) if idioma else self.idioma_defecto
        if idioma not in self.idiomas:
            idioma = self.idioma_defecto
        base = f"https://{self.web}/"
        if idioma == self.idioma_defecto and not self.prefijo_idioma_defecto:
            return base
        return f"{base}{idioma}/"

    def como_dict(self) -> dict[str, Any]:
        return {
            "clave": self.clave, "dominio": self.dominio, "host": self.web,
            "dominios_extra": list(self.dominios_extra), "marca": self.marca,
            "empresa": self.empresa, "tipo": self.tipo, "idioma_defecto": self.idioma_defecto,
            "idiomas": list(self.idiomas), "plugin_traduccion": self.plugin,
            "prefijo_idioma_defecto": self.prefijo_idioma_defecto,
            "slugs_traducidos": self.slugs_traducidos, "soporte_propio": self.soporte_propio,
            "remitente_defecto": self.remitente_defecto, "posicion": self.posicion,
            "activo": self.activo,
        }


#: La lista de partida (comprobada el 11/10/2026): las ocho webs y los dos
#: dominios de correo del grupo. Siembra la migración 0135 y vale con la
#: tabla vacía. Lo que se cambie de verdad se cambia en la tabla.
SITIOS_DE_PARTIDA: tuple[Sitio, ...] = (
    Sitio(clave="boprint", dominio="boprint.net", marca="Boprint", empresa="Streamtec",
          idioma_defecto="es", idiomas=("es",), remitente_defecto="info@boprint.net",
          posicion=0),
    Sitio(clave="artisjet-es", dominio="artisjet-spain.es", marca="Artisjet España",
          empresa="Streamtec", idioma_defecto="es", idiomas=("es",),
          remitente_defecto="info@artisjet-spain.es", posicion=1),
    Sitio(clave="artisjet-eu", dominio="artisjet-printers.eu", marca="Artisjet Europe",
          empresa="MQ Europe", idioma_defecto="fr", idiomas=("fr", "de", "en", "pt"),
          plugin=PLUGIN_QTRANSLATE, remitente_defecto="info@artisjet-printers.eu", posicion=2),
    Sitio(clave="mboprinters", dominio="mboprinters.com", marca="MBO Printers",
          empresa="Streamtec", idioma_defecto="es", idiomas=("es", "de", "en", "fr"),
          plugin=PLUGIN_QTRANSLATE, remitente_defecto="info@mboprinters.com", posicion=3),
    # mqeurope sirve con www: sin www redirige.
    Sitio(clave="mqeurope", dominio="mqeurope.com", host="www.mqeurope.com", marca="MQ Europe",
          empresa="MQ Europe", idioma_defecto="en", idiomas=("en", "fr", "nl"),
          plugin=PLUGIN_QTRANSLATE, remitente_defecto="sales@mqeurope.com", posicion=4),
    Sitio(clave="mbolasers", dominio="mbolasers.com", marca="MBO Lasers", empresa="Streamtec",
          idioma_defecto="es", idiomas=("es", "en", "de", "fr", "nl"), plugin=PLUGIN_WPML,
          slugs_traducidos=True, soporte_propio=True,
          remitente_defecto="info@mbolasers.com", posicion=5),
    # fluxlasers.es y fluxlasers.eu son el MISMO WordPress: dos puertas.
    Sitio(clave="fluxlasers", dominio="fluxlasers.es", dominios_extra=("fluxlasers.eu",),
          marca="Fluxlasers", empresa="Streamtec", idioma_defecto="es",
          idiomas=("es", "en", "de", "fr", "nl"), plugin=PLUGIN_WPML, soporte_propio=True,
          remitente_defecto="info@fluxlasers.es", posicion=6),
    Sitio(clave="pimpam", dominio="pimpam-vending.com", marca="Pim Pam Vending",
          empresa="Streamtec", idioma_defecto="es",
          idiomas=("es", "en", "fr", "de", "nl", "pt"), plugin=PLUGIN_WPML, soporte_propio=True,
          remitente_defecto="info@pimpam-vending.com", posicion=7),
    # Dominios de correo del grupo sin web: solo cuentan para la captura.
    Sitio(clave="bomedia", dominio="bomedia.net", marca="Bomedia", empresa="Bomedia",
          idiomas=(), tipo=TIPO_CORREO, posicion=8),
    Sitio(clave="streamtec", dominio="streamtec.es", marca="Streamtec", empresa="Streamtec",
          idiomas=(), tipo=TIPO_CORREO, posicion=9),
)

#: Compatibilidad: la lista de partida como los diccionarios de siempre
#: (clave → web, clave → marca, clave → remitente). Lo VIVO (la tabla) se
#: pregunta con las funciones de abajo.
WEBS: dict[str, str] = {s.clave: s.web for s in SITIOS_DE_PARTIDA if s.tipo == TIPO_WEB}
MARCAS: dict[str, str] = {s.clave: s.marca for s in SITIOS_DE_PARTIDA if s.tipo == TIPO_WEB}
REMITENTES: dict[str, str] = {
    s.clave: s.remitente_defecto
    for s in SITIOS_DE_PARTIDA if s.tipo == TIPO_WEB and s.remitente_defecto
}

#: Separador del slug: `<sitio>-contacto[-<idioma>]`.
SEPARADOR = "-contacto"

#: Prefijo del origen de un lead de formulario: `web_form:<sitio>:<idioma>`.
ORIGEN_PREFIJO = "web_form"

#: Lo que se lee en «Origen del lead» de la ficha. El prefijo es estable
#: (una regla o un workflow puede buscar «Formulario web»).
ORIGEN_LEGIBLE_PREFIJO = "Formulario web"

#: Cuánto vive la copia en memoria del registro (segundos).
CACHE_SEGUNDOS = 60.0


# --- el registro --------------------------------------------------------------


@dataclass
class Registro:
    """Los sitios, con las preguntas que les hacen la captura, los acuses,
    los leads y la base de conocimiento."""

    sitios: tuple[Sitio, ...]
    #: True si viene de la tabla; False si es la lista de partida.
    de_la_base: bool = False
    _por_clave: dict[str, Sitio] = field(default_factory=dict, repr=False)
    _por_dominio: dict[str, Sitio] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.sitios = tuple(sorted(self.sitios, key=lambda s: (s.posicion, s.clave)))
        self._por_clave = {s.clave: s for s in self.sitios}
        self._por_dominio = {}
        for s in self.sitios:
            for d in s.dominios:
                self._por_dominio.setdefault(_dominio_limpio(d), s)
            self._por_dominio.setdefault(_dominio_limpio(s.web), s)

    @property
    def webs(self) -> list[Sitio]:
        """Las webs activas, en orden."""
        return [s for s in self.sitios if s.tipo == TIPO_WEB and s.activo]

    def sitio(self, clave: str | None) -> Sitio | None:
        return self._por_clave.get((clave or "").strip())

    def por_dominio(self, dominio_o_direccion: str | None) -> Sitio | None:
        """El sitio al que pertenece un dominio o una dirección de correo (el
        dominio exacto o un subdominio suyo)."""
        dominio = _dominio_de(dominio_o_direccion)
        if not dominio:
            return None
        while dominio:
            sitio = self._por_dominio.get(dominio)
            if sitio is not None:
                return sitio
            if "." not in dominio:
                return None
            dominio = dominio.split(".", 1)[1]
        return None

    def dominios(self) -> set[str]:
        """Todos los dominios activos del grupo (webs y correo), en minúsculas."""
        salida: set[str] = set()
        for s in self.sitios:
            if not s.activo:
                continue
            salida.update(_dominio_limpio(d) for d in s.dominios)
            salida.add(_dominio_limpio(s.web))
        return {d for d in salida if d}

    def como_lista(self) -> list[dict[str, Any]]:
        return [s.como_dict() for s in self.sitios]


def _dominio_limpio(dominio: str) -> str:
    d = (dominio or "").strip().lower().rstrip(".")
    return d[4:] if d.startswith("www.") else d


def _dominio_de(valor: str | None) -> str:
    """El dominio de una dirección de correo o de un dominio ya pelado."""
    texto = (valor or "").strip().lower()
    if "@" in texto:
        texto = texto.rsplit("@", 1)[-1]
    return _dominio_limpio(texto.strip("<> "))


def _de_partida() -> Registro:
    return Registro(sitios=SITIOS_DE_PARTIDA, de_la_base=False)


def _desde_fila(fila: Any) -> Sitio:
    def _lista(raw: str | None) -> tuple[str, ...]:
        try:
            datos = json.loads(raw or "[]")
        except (TypeError, ValueError):
            return ()
        return tuple(str(x) for x in datos if str(x).strip()) if isinstance(datos, list) else ()

    return Sitio(
        clave=fila.clave, dominio=fila.dominio, host=fila.host or None, marca=fila.marca,
        empresa=fila.empresa, idioma_defecto=fila.idioma_defecto or "es",
        idiomas=_lista(fila.idiomas_json), dominios_extra=_lista(fila.dominios_extra_json),
        plugin=fila.plugin_traduccion or None,
        prefijo_idioma_defecto=bool(fila.prefijo_idioma_defecto),
        slugs_traducidos=bool(fila.slugs_traducidos), soporte_propio=bool(fila.soporte_propio),
        remitente_defecto=fila.remitente_defecto or None, tipo=fila.tipo or TIPO_WEB,
        posicion=int(fila.posicion or 0), activo=bool(fila.activo),
    )


def cargar(session: Any) -> Registro:
    """El registro de la tabla `sitios_web`; con la tabla vacía (o aún sin
    crear: un worker que arranca antes de que el api migre), el de partida.
    Para quien tiene sesión (la captura, la API)."""
    from sqlalchemy import select  # noqa: PLC0415
    from sqlalchemy.exc import OperationalError, ProgrammingError  # noqa: PLC0415

    from app.models.sitios import SitioWeb  # noqa: PLC0415

    try:
        filas = list(
            session.scalars(select(SitioWeb).order_by(SitioWeb.posicion, SitioWeb.clave)))
    except (OperationalError, ProgrammingError) as exc:
        logger.warning("sitios: la tabla sitios_web no responde, lista de partida (%s)", exc)
        return _de_partida()
    if not filas:
        return _de_partida()
    return Registro(sitios=tuple(_desde_fila(f) for f in filas), de_la_base=True)


_cache: tuple[float, Registro] | None = None
_fijado: Registro | None = None


def registro() -> Registro:
    """El registro para quien NO tiene sesión (las funciones de siempre):
    una copia en memoria que se renueva cada minuto. Si la base no responde
    o la tabla no existe todavía, la lista de partida: nunca se rompe por
    esto."""
    global _cache
    if _fijado is not None:
        return _fijado
    ahora = time.monotonic()
    if _cache is not None and ahora - _cache[0] < CACHE_SEGUNDOS:
        return _cache[1]
    try:
        from sqlalchemy.orm import Session  # noqa: PLC0415

        from app.db.session import get_engine  # noqa: PLC0415

        with Session(get_engine()) as session:
            reg = cargar(session)
    except Exception as exc:  # noqa: BLE001 — sin base, la lista de partida
        logger.debug("sitios: registro de partida (%s: %s)", type(exc).__name__, exc)
        reg = _de_partida()
    _cache = (ahora, reg)
    return reg


def invalidar() -> None:
    """Tira la copia en memoria (al guardar la tabla desde la pantalla)."""
    global _cache
    _cache = None


def fijar_para_tests(reg: Registro | None) -> None:
    """Los tests fijan un registro (o `None` para volver a lo normal)."""
    global _fijado
    _fijado = reg
    invalidar()


# --- lo que preguntan los demás ------------------------------------------------


def clave_de_sitio(slug: str | None) -> str:
    """La web a la que pertenece un formulario, a partir de su slug.

    `artisjet-es-contacto-de` → `artisjet-es`; `boprint-contacto` →
    `boprint`. Un slug que no siga el convenio es su propio sitio (se
    enseñará con su nombre, sin dominio inventado)."""
    slug = (slug or "").strip().lower()
    if not slug:
        return "sin-web"
    return slug.split(SEPARADOR, 1)[0] or slug


def sitio_de(clave: str | None) -> Sitio | None:
    return registro().sitio(clave)


def web_de_sitio(clave: str | None) -> str:
    """La web de un sitio, o su clave si no está en la lista."""
    clave = (clave or "").strip()
    sitio = registro().sitio(clave)
    return (sitio.web if sitio is not None else clave) or "web sin identificar"


def web_de_formulario(slug: str | None) -> str:
    return web_de_sitio(clave_de_sitio(slug))


def marca_de_sitio(clave: str | None, respaldo: str | None = None) -> str:
    """El nombre comercial de un sitio. Si la web no está en la lista, el
    `respaldo` que dé el llamador (la marca del formulario) y, en último
    término, su web: la clave del slug («webnueva») no es un nombre que se
    pueda enseñar a un cliente."""
    clave = (clave or "").strip()
    sitio = registro().sitio(clave)
    if sitio is not None and sitio.marca:
        return sitio.marca
    return (respaldo or "").strip() or web_de_sitio(clave)


def marca_de_formulario(slug: str | None, respaldo: str | None = None) -> str:
    return marca_de_sitio(clave_de_sitio(slug), respaldo)


def remitente_de_sitio(clave: str | None) -> str | None:
    """El remitente por defecto de un sitio. `None` si la web no está en la
    lista: inventarse un `info@` de un dominio que no firma nos mandaría
    derecho a la carpeta de spam."""
    sitio = registro().sitio((clave or "").strip())
    return (sitio.remitente_defecto or None) if sitio is not None else None


def remitente_de_formulario(slug: str | None) -> str | None:
    return remitente_de_sitio(clave_de_sitio(slug))


def webs_conocidas() -> dict[str, str]:
    """Clave → web de las webs activas (lo vivo, para validar y para pintar)."""
    return {s.clave: s.web for s in registro().webs}


def origen_de(slug: str | None, idioma: str | None) -> str:
    """El `origin_account_id` de un formulario: identifica la web y el
    idioma, y se puede filtrar por cualquiera de los dos."""
    return f"{ORIGEN_PREFIJO}:{clave_de_sitio(slug)}:{normalizar_idioma(idioma)}"


def origen_legible(slug: str | None, idioma: str | None) -> str:
    """`Contact.origin` de un lead: «Formulario web · mboprinters.com
    (alemán)». Cabe en los 120 caracteres de la columna."""
    # Se normaliza igual que en `origen_de` y que en `contact.language`: si
    # no, un formulario con un idioma que no tenemos diría «(italiano)» en
    # la ficha mientras el filtro y el correo lo tratan como castellano.
    texto = (f"{ORIGEN_LEGIBLE_PREFIJO} · {web_de_formulario(slug)} "
             f"({nombre_idioma(normalizar_idioma(idioma))})")
    return texto[:120]


def partes_de_origen(origen: str | None) -> tuple[str, str] | None:
    """`web_form:mbolasers:nl` → `("mbolasers", "nl")`. None si no es uno de
    estos orígenes (incluye el formato antiguo `web_form:<slug>`)."""
    trozos = (origen or "").split(":")
    if len(trozos) != 3 or trozos[0] != ORIGEN_PREFIJO:
        return None
    return trozos[1], trozos[2]


def etiqueta_de_origen(origen: str | None) -> str | None:
    """Lo que se lee en la ficha: «mboprinters.com (alemán)». None si el
    origen no es de un formulario con web e idioma."""
    partes = partes_de_origen(origen)
    if partes is None:
        return None
    sitio, idioma = partes
    return f"{web_de_sitio(sitio)} ({nombre_idioma(idioma)})"


# --- los dominios del grupo (la captura de correo) ---------------------------------


def dominios_del_grupo(session: Any) -> set[str]:
    """Los dominios que son nuestros: los de la tabla de sitios (webs y
    correo) más los de los alias registrados en `user_email_aliases` (una
    dirección registrada es nuestra aunque su dominio no sea una web). En
    minúsculas y sin `www.`. Es lo que decide si un correo se captura."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.crm import UserEmailAlias  # noqa: PLC0415

    dominios = cargar(session).dominios()
    for alias in session.scalars(select(UserEmailAlias.alias_email)):
        dominio = _dominio_de(alias)
        if dominio:
            dominios.add(dominio)
    return dominios


def es_del_grupo(direccion: str | None, dominios: Iterable[str]) -> bool:
    """La dirección pertenece a uno de esos dominios (o a un subdominio)."""
    dominio = _dominio_de(direccion)
    if not dominio:
        return False
    conjunto = set(dominios)
    while dominio:
        if dominio in conjunto:
            return True
        if "." not in dominio:
            return False
        dominio = dominio.split(".", 1)[1]
    return False


# --- alta y edición (Configuración ERP → Base de conocimiento) ----------------------

_CLAVE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
_DOMINIO_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")


def sembrar_si_vacio(session: Any) -> int:
    """Escribe la lista de partida si la tabla está vacía (antes de la primera
    edición desde la pantalla). Devuelve cuántos sembró."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.sitios import SitioWeb  # noqa: PLC0415

    if session.scalar(select(SitioWeb.clave).limit(1)) is not None:
        return 0
    for s in SITIOS_DE_PARTIDA:
        session.add(SitioWeb(
            clave=s.clave, dominio=_dominio_limpio(s.dominio), host=s.web,
            dominios_extra_json=json.dumps(list(s.dominios_extra)), marca=s.marca,
            empresa=s.empresa, tipo=s.tipo, idioma_defecto=s.idioma_defecto,
            idiomas_json=json.dumps(list(s.idiomas)), plugin_traduccion=s.plugin,
            prefijo_idioma_defecto=s.prefijo_idioma_defecto, slugs_traducidos=s.slugs_traducidos,
            soporte_propio=s.soporte_propio, remitente_defecto=s.remitente_defecto,
            posicion=s.posicion, activo=s.activo,
        ))
    session.flush()
    invalidar()
    return len(SITIOS_DE_PARTIDA)


def _validar_dominio(valor: Any) -> str:
    dominio = _dominio_limpio(str(valor or ""))
    if not _DOMINIO_RE.match(dominio):
        raise ValueError(f"Dominio no válido: {valor!r} (p. ej. fluxlasers.es).")
    return dominio


def _validar_idiomas(valores: Any, defecto: str) -> list[str]:
    if not isinstance(valores, list):
        raise ValueError("Los idiomas publicados tienen que ser una lista de códigos.")
    salida: list[str] = []
    for v in valores:
        codigo = normalizar_idioma(str(v)) if str(v).strip() else ""
        if not codigo:
            raise ValueError(f"Idioma no válido: {v!r}.")
        if codigo not in salida:
            salida.append(codigo)
    if defecto not in salida:
        salida.insert(0, defecto)
    return salida


def _validar_plugin(valor: Any) -> str | None:
    plugin = str(valor or "").strip().lower() or None
    if plugin is not None and plugin not in (PLUGIN_QTRANSLATE, PLUGIN_WPML):
        raise ValueError(f"Plugin de traducción desconocido: {valor!r} (qtranslate o wpml).")
    return plugin


def _validar_remitente(valor: Any) -> str | None:
    texto = str(valor or "").strip().lower()
    if texto and ("@" not in texto or " " in texto):
        raise ValueError(f"Remitente no válido: {valor!r}.")
    return texto or None


def crear(
    session: Any, *, clave: str, dominio: str, marca: str, empresa: str, host: str | None = None,
    dominios_extra: list[str] | None = None, tipo: str = TIPO_WEB, idioma_defecto: str = "es",
    idiomas: list[str] | None = None, plugin_traduccion: str | None = None,
    prefijo_idioma_defecto: bool = False, slugs_traducidos: bool = False,
    soporte_propio: bool = False, remitente_defecto: str | None = None,
    posicion: int | None = None,
) -> Any:
    """Una web o dominio de correo nuevo. Desde ese momento su correo se
    captura (dominio del grupo) y sus formularios y rutas la conocen."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.sitios import SitioWeb  # noqa: PLC0415

    sembrar_si_vacio(session)
    clave = str(clave or "").strip().lower()
    if not _CLAVE_RE.match(clave):
        raise ValueError(
            f"Clave no válida: {clave!r} (minúsculas, números y guiones, p. ej. «mbolasers»).")
    if session.get(SitioWeb, clave) is not None:
        raise ValueError(f"Ya hay un sitio con la clave «{clave}».")
    if tipo not in (TIPO_WEB, TIPO_CORREO):
        raise ValueError(f"Tipo de sitio desconocido: {tipo!r} (web o correo).")
    dominio_ok = _validar_dominio(dominio)
    if session.scalar(select(SitioWeb.clave).where(SitioWeb.dominio == dominio_ok)) is not None:
        raise ValueError(f"El dominio {dominio_ok} ya está dado de alta.")
    defecto = normalizar_idioma(idioma_defecto)
    fila = SitioWeb(
        clave=clave, dominio=dominio_ok, host=_dominio_limpio(str(host or "")) or dominio_ok,
        dominios_extra_json=json.dumps([_validar_dominio(d) for d in (dominios_extra or [])]),
        marca=str(marca).strip(), empresa=str(empresa).strip(), tipo=tipo,
        idioma_defecto=defecto, idiomas_json=json.dumps(_validar_idiomas(idiomas or [defecto],
                                                                         defecto)),
        plugin_traduccion=_validar_plugin(plugin_traduccion),
        prefijo_idioma_defecto=bool(prefijo_idioma_defecto),
        slugs_traducidos=bool(slugs_traducidos), soporte_propio=bool(soporte_propio),
        remitente_defecto=_validar_remitente(remitente_defecto),
        posicion=int(posicion) if posicion is not None else len(SITIOS_DE_PARTIDA) + 1,
        activo=True,
    )
    if not fila.marca or not fila.empresa:
        raise ValueError("El sitio necesita marca y empresa.")
    # El host se guarda tal cual (con `www.` si lo lleva): sirve para construir URLs.
    fila.host = str(host or "").strip().lower().rstrip("/") or dominio_ok
    session.add(fila)
    session.flush()
    invalidar()
    return fila


def actualizar(session: Any, clave: str, cambios: dict[str, Any]) -> Any:
    """Todo menos la clave (es lo que llevan los orígenes de los contactos y
    las rutas). Desactivar saca el sitio de la captura y de los desplegables."""
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.sitios import SitioWeb  # noqa: PLC0415

    sembrar_si_vacio(session)
    fila = session.get(SitioWeb, (clave or "").strip().lower())
    if fila is None:
        raise LookupError("Ese sitio no existe.")
    if "dominio" in cambios:
        dominio_ok = _validar_dominio(cambios["dominio"])
        otro = session.scalar(select(SitioWeb.clave).where(
            SitioWeb.dominio == dominio_ok, SitioWeb.clave != fila.clave))
        if otro is not None:
            raise ValueError(f"El dominio {dominio_ok} ya es de «{otro}».")
        fila.dominio = dominio_ok
    if "host" in cambios:
        fila.host = str(cambios["host"] or "").strip().lower().rstrip("/") or fila.dominio
    if "dominios_extra" in cambios:
        fila.dominios_extra_json = json.dumps(
            [_validar_dominio(d) for d in (cambios["dominios_extra"] or [])])
    for campo in ("marca", "empresa"):
        if campo in cambios:
            valor = str(cambios[campo] or "").strip()
            if not valor:
                raise ValueError(f"El sitio necesita {campo}.")
            setattr(fila, campo, valor)
    if "tipo" in cambios:
        if cambios["tipo"] not in (TIPO_WEB, TIPO_CORREO):
            raise ValueError(f"Tipo de sitio desconocido: {cambios['tipo']!r}.")
        fila.tipo = cambios["tipo"]
    if "idioma_defecto" in cambios:
        fila.idioma_defecto = normalizar_idioma(str(cambios["idioma_defecto"]))
    if "idiomas" in cambios or "idioma_defecto" in cambios:
        actuales = json.loads(fila.idiomas_json or "[]")
        fila.idiomas_json = json.dumps(
            _validar_idiomas(cambios.get("idiomas", actuales), fila.idioma_defecto))
    if "plugin_traduccion" in cambios:
        fila.plugin_traduccion = _validar_plugin(cambios["plugin_traduccion"])
    for campo in ("prefijo_idioma_defecto", "slugs_traducidos", "soporte_propio", "activo"):
        if campo in cambios:
            setattr(fila, campo, bool(cambios[campo]))
    if "remitente_defecto" in cambios:
        fila.remitente_defecto = _validar_remitente(cambios["remitente_defecto"])
    if "posicion" in cambios and cambios["posicion"] is not None:
        fila.posicion = int(cambios["posicion"])
    session.flush()
    invalidar()
    return fila
