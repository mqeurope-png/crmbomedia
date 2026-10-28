"""Búsqueda en el índice de las webs (lo que usa el redactor).

- En MySQL: `MATCH(titulo, texto) AGAINST(… IN BOOLEAN MODE)` con las MISMAS
  columnas del índice FULLTEXT `ft_web_indice_titulo_texto` (InnoDB exige que
  coincidan). La consulta se sanea (fuera los operadores `+-<>()~*"@`) y cada
  palabra de 3 letras o más va con `*` al final (prefijo: «impresor» encuentra
  «impresora» e «impresoras»).
- Las palabras de 1 o 2 letras («UV», «A3», «IA») no están en el índice
  FULLTEXT (`innodb_ft_min_token_size` es 3 por defecto y no se toca la
  configuración del servidor): van por `LIKE`.
- En cualquier otra base (SQLite en los tests) todo va por `LIKE`, con una
  puntuación sencilla en Python (el título pesa más que el texto).
- Primero se buscan las que tienen TODAS las palabras; si no hay ninguna, las
  que tienen alguna.
- Solo lo vivo (`ido_en IS NULL`). Lo que vuelve son fragmentos recortados
  (los párrafos que tienen que ver con la búsqueda) con su URL: nunca la
  página entera.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session

from app.models.fuentes_externas import WebIndiceEntrada
from app.services.externo import html_texto

#: Palabras que no ayudan a buscar (las más comunes de nuestros idiomas y la
#: lista de palabras vacías que InnoDB trae de serie: con `+palabra` una de
#: ellas dejaría la búsqueda sin resultados).
PALABRAS_VACIAS = frozenset("""
a o y e u de del la las el los lo un una unos unas al en por para con sin que se su sus es
como mas más muy ya le les du des et ou pour par avec sur dans une est qui au aux ce cette
der die das den dem ein eine und oder mit von für fur zu im ist auf het een van voor met op
te om the an and or of to in on for with from by at is are was be as it this that what when
where who how will com www about i da do dos das em um uma não nao os ao
""".split())
#: Palabras de la consulta que se usan como mucho.
MAX_PALABRAS = 8
#: Candidatos que se puntúan antes de quedarse con los mejores.
MAX_CANDIDATOS = 200

_OPERADORES_RE = re.compile(r"[+\-<>()~*\"@]")
_PALABRA_RE = re.compile(r"[^\W_]+")


@dataclass
class Fragmento:
    """Un trozo del índice para el redactor (forma `WebFragmento`)."""

    id: str
    sitio: str
    dominio: str
    tipo: str
    idioma: str
    titulo: str
    url: str
    fragmento: str
    imagen: str | None = None
    galeria: list[str] = field(default_factory=list)
    categorias: list[str] = field(default_factory=list)
    #: Precio de la WEB (lo que dice la web). Nunca es precio de oferta.
    precio_web: float | None = None
    precio_web_texto: str | None = None
    precio_iva: str | None = None
    moneda: str | None = None
    #: Id de WordPress: con el sitio, la clave de su ficha (`webs.fichas`).
    wp_id: int | None = None


def palabras_de(consulta: str | None) -> list[str]:
    """Las palabras útiles de la consulta, saneadas (sin operadores de MySQL),
    en minúsculas y sin repetir."""
    limpio = _OPERADORES_RE.sub(" ", consulta or "")
    salida: list[str] = []
    for palabra in _PALABRA_RE.findall(limpio.lower()):
        if palabra in PALABRAS_VACIAS or palabra in salida:
            continue
        salida.append(palabra)
    return salida[:MAX_PALABRAS]


def _lista(raw: str | None) -> list[str]:
    try:
        datos = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(x) for x in datos if x] if isinstance(datos, list) else []


def fragmento_de(fila: WebIndiceEntrada, palabras: list[str], *, max_chars: int) -> Fragmento:
    return Fragmento(
        id=fila.id, sitio=fila.sitio, dominio=fila.dominio, tipo=fila.tipo, idioma=fila.idioma,
        titulo=fila.titulo, url=fila.url,
        fragmento=html_texto.fragmentos(fila.texto or "", palabras, max_chars=max_chars),
        imagen=fila.imagen, galeria=_lista(fila.galeria_json),
        categorias=_lista(fila.categorias_json),
        precio_web=float(fila.precio_web) if fila.precio_web is not None else None,
        precio_web_texto=fila.precio_web_texto, precio_iva=fila.precio_iva, moneda=fila.moneda,
        wp_id=fila.wp_id,
    )


def fragmento_como_dict(f: Fragmento) -> dict[str, Any]:
    """La forma `WebFragmento` de `websApi.ts`."""
    return asdict(f)


def _filtros(idioma: str | None, sitio: str | None, tipo: str | None) -> list[Any]:
    filtros: list[Any] = [WebIndiceEntrada.ido_en.is_(None)]
    if idioma:
        filtros.append(WebIndiceEntrada.idioma == idioma)
    if sitio:
        filtros.append(WebIndiceEntrada.sitio == sitio)
    if tipo:
        filtros.append(WebIndiceEntrada.tipo == tipo)
    return filtros


def _like(palabra: str) -> Any:
    patron = f"%{palabra}%"
    return or_(func.lower(WebIndiceEntrada.titulo).like(patron),
               func.lower(WebIndiceEntrada.texto).like(patron))


def consulta_mysql(palabras: list[str], filtros: list[Any], *, todas: bool,
                   limite: int = MAX_CANDIDATOS) -> Select[Any]:
    """La consulta de MySQL: FULLTEXT para las palabras de 3 letras o más y
    `LIKE` para las cortas. Con `todas`, cada palabra es obligatoria."""
    largas = [p for p in palabras if len(p) >= 3]
    cortas = [p for p in palabras if len(p) < 3]
    consulta = select(WebIndiceEntrada).where(*filtros)
    if largas:
        contra = " ".join(f"{'+' if todas else ''}{p}*" for p in largas)
        coincide = mysql.match(WebIndiceEntrada.titulo, WebIndiceEntrada.texto,
                               against=contra).in_boolean_mode()
        consulta = consulta.where(coincide).order_by(coincide.desc())
    if cortas and (todas or not largas):
        condiciones = [_like(p) for p in cortas]
        consulta = consulta.where(and_(*condiciones) if todas else or_(*condiciones))
    return consulta.limit(limite)


def consulta_like(palabras: list[str], filtros: list[Any], *, todas: bool,
                  limite: int = MAX_CANDIDATOS) -> Select[Any]:
    """La consulta de cualquier otra base: `LIKE` con AND (o con OR) de
    palabras."""
    condiciones = [_like(p) for p in palabras]
    junta = and_(*condiciones) if todas else or_(*condiciones)
    return select(WebIndiceEntrada).where(*filtros, junta).limit(limite)


#: Veces que cuenta, como mucho, una palabra repetida en el texto (una página
#: de tienda que lista veinte accesorios «… Beambox II» no gana a la máquina).
MAX_REPETICIONES_TEXTO = 5


def _puntos(fila: WebIndiceEntrada, palabras: list[str], orden: int) -> tuple[float, int]:
    """Puntuación sencilla: cada palabra en el título vale más que en el texto;
    el título que trae la búsqueda entera y seguida, más; y cuanto más del
    título es la búsqueda, más (la «Beambox II 55W» antes que el «Extension
    Pocket Add-on – Beambox II»). El orden de la base (la relevancia de MySQL)
    desempata."""
    titulo = html_texto.plano(fila.titulo or "")
    texto = html_texto.plano(fila.texto or "")
    claves = [html_texto.plano(p) for p in palabras]
    puntos = 0.0
    for clave in claves:
        puntos += 5 * titulo.count(clave) + min(texto.count(clave), MAX_REPETICIONES_TEXTO)
    if len(claves) > 1 and " ".join(claves) in titulo:
        puntos += 15
    cubierto = sum(len(c) for c in claves if c in titulo)
    puntos += 10 * cubierto / max(len(titulo.replace(" ", "")), 1)
    return -puntos, orden


def _candidatos(session: Session, palabras: list[str], filtros: list[Any], *,
                todas: bool) -> list[WebIndiceEntrada]:
    if session.get_bind().dialect.name == "mysql":
        consulta = consulta_mysql(palabras, filtros, todas=todas)
    else:
        consulta = consulta_like(palabras, filtros, todas=todas)
    return list(session.scalars(consulta))


def buscar(session: Session, consulta: str, *, idioma: str | None = None,
           sitio: str | None = None, tipo: str | None = None, limite: int = 8,
           max_chars: int = 700) -> list[Fragmento]:
    """Los fragmentos del índice que mejor casan con la consulta (solo lo
    vivo)."""
    palabras = palabras_de(consulta)
    if not palabras or limite <= 0:
        return []
    filtros = _filtros(idioma, sitio, tipo)
    filas = _candidatos(session, palabras, filtros, todas=True)
    if not filas and len(palabras) > 1:
        filas = _candidatos(session, palabras, filtros, todas=False)
    ordenadas = sorted(enumerate(filas), key=lambda par: _puntos(par[1], palabras, par[0]))
    return [fragmento_de(fila, palabras, max_chars=max_chars)
            for _, fila in ordenadas[:limite]]


def fragmentos_de(session: Session, ids: list[str], *, consulta: str | None = None,
                  max_chars: int = 900) -> list[Fragmento]:
    """Los fragmentos de las filas que eligió el usuario (en ese orden), con los
    párrafos que tienen que ver con la consulta si la hay. Lo que ya se fue de
    la web no vuelve."""
    limpios = [str(i) for i in dict.fromkeys(ids or []) if i]
    if not limpios:
        return []
    filas = {f.id: f for f in session.scalars(select(WebIndiceEntrada).where(
        WebIndiceEntrada.id.in_(limpios), WebIndiceEntrada.ido_en.is_(None)))}
    palabras = palabras_de(consulta)
    return [fragmento_de(filas[i], palabras, max_chars=max_chars) for i in limpios if i in filas]
