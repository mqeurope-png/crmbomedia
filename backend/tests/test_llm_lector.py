"""`llm._invoke_claude` lee el PRIMER bloque de TEXTO de la respuesta, no el
primer bloque a secas: los modelos de la generación 5 razonan antes de
contestar y en los casos difíciles devuelven un bloque `thinking` delante
del texto. El 09/10/2026, al pasar a claude-sonnet-5-5, 6 de 12
clasificaciones de leads cayeron al respaldo por palabras clave con
«Provider returned non-text content». Lo comparten la clasificación de
leads, las reglas de segmento, la explicación de segmentos y la propuesta de
pipeline."""
from __future__ import annotations

import logging
import sys
import types
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from app.services import llm


def _texto(t: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=t)


def _pensamiento(t: str = "razonando…") -> SimpleNamespace:
    """Un bloque de pensamiento: tiene `thinking`, no `text`."""
    return SimpleNamespace(type="thinking", thinking=t)


@pytest.fixture()
def respuesta(monkeypatch) -> Callable[[list[SimpleNamespace]], dict]:
    """Un `anthropic` falso cuyo cliente devuelve los bloques que se le den.
    Nada sale a la red y no hace falta el paquete real."""
    estado: dict = {}

    class _RateLimitError(Exception):
        pass

    class _APIError(Exception):
        pass

    def _fabrica(api_key: str):
        estado["api_key"] = api_key

        def _create(**kwargs):
            estado["kwargs"] = kwargs
            return SimpleNamespace(content=estado["contenido"])

        return SimpleNamespace(messages=SimpleNamespace(create=_create))

    modulo = types.ModuleType("anthropic")
    modulo.Anthropic = _fabrica  # type: ignore[attr-defined]
    modulo.RateLimitError = _RateLimitError  # type: ignore[attr-defined]
    modulo.APIError = _APIError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", modulo)

    def _con(contenido: list[SimpleNamespace]) -> dict:
        estado["contenido"] = contenido
        return estado

    return _con


def _llamar() -> str:
    return llm._invoke_claude(api_key="clave-de-prueba", model="claude-sonnet-5-5",
                              system_prompt="sistema", user_prompt="consulta")


def test_un_solo_bloque_de_texto_se_lee_igual_que_antes(respuesta) -> None:
    estado = respuesta([_texto('{"idioma": "es"}')])
    assert _llamar() == '{"idioma": "es"}'
    assert estado["api_key"] == "clave-de-prueba"
    assert estado["kwargs"]["model"] == "claude-sonnet-5-5"
    assert estado["kwargs"]["max_tokens"] == llm.MAX_TOKENS_OUTPUT


def test_el_bloque_de_pensamiento_delante_no_rompe(respuesta, caplog) -> None:
    respuesta([_pensamiento(), _texto('{"idioma": "de"}')])
    with caplog.at_level(logging.WARNING, logger="app.services.llm"):
        assert _llamar() == '{"idioma": "de"}'
    assert not [r for r in caplog.records if "non_text_response" in r.getMessage()]


def test_varios_bloques_de_texto_se_concatenan(respuesta) -> None:
    respuesta([_texto('{"idioma": '), _pensamiento(), _texto('"fr"}')])
    assert _llamar() == '{"idioma": "fr"}'


def test_sin_ningun_bloque_de_texto_el_error_de_siempre_y_el_log_dice_que_llego(
    respuesta, caplog,
) -> None:
    respuesta([_pensamiento(), SimpleNamespace(type="tool_use", name="x")])
    with caplog.at_level(logging.WARNING, logger="app.services.llm"), \
            pytest.raises(llm.LLMUpstreamError) as info:
        _llamar()
    assert "Provider returned non-text content" in str(info.value)
    assert "thinking,tool_use" in str(info.value)
    aviso = [r for r in caplog.records if "llm.non_text_response" in r.getMessage()]
    assert aviso and "blocks=thinking,tool_use" in aviso[0].getMessage()
    # Un bloque de texto vacío tampoco es texto.
    respuesta([_texto("")])
    with pytest.raises(llm.LLMUpstreamError):
        _llamar()


def test_sin_contenido_sigue_siendo_respuesta_vacia(respuesta) -> None:
    respuesta([])
    with pytest.raises(llm.LLMUpstreamError, match="Empty response"):
        _llamar()
