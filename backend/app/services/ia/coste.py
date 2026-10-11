"""El coste de una llamada a Claude en USD, a partir de los tokens que
devuelve la API y el precio por millón de tokens de cada modelo.

Los precios son los publicados por Anthropic (octubre de 2026) por millón de
tokens, entrada / salida. La caché de prompts cobra lo leído de caché a una
décima parte del precio de entrada (una vigésima en Opus 5.5 y menos aún en
Fable, que publican su propio precio) y lo escrito en caché un cuarto más que
la entrada normal. Un modelo que no esté en la tabla se cobra como el Sonnet
más caro conocido para no quedarse corto; la tabla se amplía a mano cuando
cambie el modelo en Configuración ERP.
"""
from __future__ import annotations

#: modelo (prefijo) → (USD por millón de tokens de entrada, de salida).
PRECIOS_USD_POR_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
}
PRECIO_DESCONOCIDO: tuple[float, float] = (3.0, 15.0)
#: Lectura de caché: una décima parte del precio de entrada, salvo donde
#: Anthropic publica otro precio (USD por millón).
CACHE_LECTURA_USD_POR_MTOK: dict[str, float] = {
    "claude-opus-5-5": 0.20,
    "claude-fable-5-1": 0.25,
    "claude-fable-5": 0.25,
}
FACTOR_CACHE_LECTURA = 0.1
FACTOR_CACHE_ESCRITURA = 1.25


def _por_prefijo(modelo: str | None, tabla: dict[str, object]) -> object | None:
    """La entrada de la tabla cuyo prefijo case con el modelo, el más largo
    primero: `claude-sonnet-4-6-20260301` vale lo mismo que `claude-sonnet-4-6`,
    y `claude-opus-5-5` no se confunde con `claude-opus-5`."""
    nombre = (modelo or "").strip().lower()
    for prefijo in sorted(tabla, key=len, reverse=True):
        if nombre.startswith(prefijo):
            return tabla[prefijo]
    return None


def precio(modelo: str | None) -> tuple[float, float]:
    """`(entrada, salida)` en USD por millón de tokens."""
    tarifa = _por_prefijo(modelo, PRECIOS_USD_POR_MTOK)
    return tarifa if isinstance(tarifa, tuple) else PRECIO_DESCONOCIDO


def precio_cache(modelo: str | None) -> tuple[float, float]:
    """`(lectura de caché, escritura en caché)` en USD por millón de tokens."""
    entrada, _ = precio(modelo)
    lectura = _por_prefijo(modelo, CACHE_LECTURA_USD_POR_MTOK)
    if not isinstance(lectura, float | int):
        lectura = entrada * FACTOR_CACHE_LECTURA
    return float(lectura), entrada * FACTOR_CACHE_ESCRITURA


def coste_usd(
    modelo: str | None, tokens_entrada: int, tokens_salida: int,
    tokens_cache_lectura: int = 0, tokens_cache_escritura: int = 0,
) -> float:
    """Lo que ha costado la llamada: la entrada a precio entero más lo leído y
    escrito en caché a su precio, más la salida."""
    entrada, salida = precio(modelo)
    lectura, escritura = precio_cache(modelo)
    total = (
        max(0, tokens_entrada) * entrada
        + max(0, tokens_cache_lectura) * lectura
        + max(0, tokens_cache_escritura) * escritura
        + max(0, tokens_salida) * salida
    ) / 1_000_000
    return round(total, 6)
