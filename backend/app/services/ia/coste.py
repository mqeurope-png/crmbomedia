"""El coste de una llamada a Claude en USD, a partir de los tokens que
devuelve la API y el precio por millón de tokens de cada modelo.

Los precios son los publicados por Anthropic (octubre de 2026) por millón de
tokens, entrada / salida. Un modelo que no esté en la tabla se cobra como el
Sonnet más caro conocido para no quedarse corto; la tabla se amplía a mano
cuando cambie el modelo de `ANTHROPIC_MODEL`.
"""
from __future__ import annotations

#: modelo (prefijo) → (USD por millón de tokens de entrada, de salida).
PRECIOS_USD_POR_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5-1": (4.0, 20.0),
}
PRECIO_DESCONOCIDO: tuple[float, float] = (3.0, 15.0)


def precio(modelo: str | None) -> tuple[float, float]:
    """`(entrada, salida)` en USD por millón de tokens. Casa por prefijo para
    que `claude-sonnet-4-6-20260301` valga lo mismo que `claude-sonnet-4-6`."""
    nombre = (modelo or "").strip().lower()
    for prefijo, tarifa in sorted(PRECIOS_USD_POR_MTOK.items(), key=lambda kv: -len(kv[0])):
        if nombre.startswith(prefijo):
            return tarifa
    return PRECIO_DESCONOCIDO


def coste_usd(modelo: str | None, tokens_entrada: int, tokens_salida: int) -> float:
    entrada, salida = precio(modelo)
    total = (max(0, tokens_entrada) * entrada + max(0, tokens_salida) * salida) / 1_000_000
    return round(total, 6)
