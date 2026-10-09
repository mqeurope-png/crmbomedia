"""Respuesta a leads · el clasificador sin IA y las dos reglas que mandan
sobre cualquier proveedor (etiquetas → interés; idioma del formulario → idioma,
salvo que el texto lo contradiga claramente)."""
from __future__ import annotations

from app.services.leads.clasificador import (
    INTERES_CONSUMIBLES,
    INTERES_DISTRIBUCION,
    INTERES_LASER,
    INTERES_OTRO,
    INTERES_UV_PEQUENO,
    INTERES_VENDING,
    Clasificacion,
    ClasificadorPalabrasClave,
    EntradaLead,
    clasificar_lead,
    codigo_idioma,
    detectar_idioma,
    dominio_sospechoso,
    interes_por_etiquetas,
    interes_por_texto,
    parece_spam,
    texto_claramente_en,
)

ALEMAN = ("Hallo, wir möchten Transferfolie für unseren UV-Drucker bestellen. "
          "Welche Folie haben Sie? Danke und Grüße")
FRANCES = ("Bonjour, nous souhaitons imprimer sur des bouteilles en verre. Pouvez-vous "
           "nous envoyer un devis pour une imprimante UV ? Merci")
CASTELLANO = ("Hola, quiero información y precio de una máquina de vending con pantalla "
              "táctil. Gracias, saludos")


def _entrada(texto: str, **over) -> EntradaLead:
    base = {"fuente": "web_form", "referencia": "envio-1"}
    base.update(over)
    return EntradaLead(texto=texto, **base)


def test_detecta_el_idioma_por_vocabulario() -> None:
    assert detectar_idioma(ALEMAN)[0] == "de"
    assert detectar_idioma(FRANCES)[0] == "fr"
    assert detectar_idioma(CASTELLANO)[0] == "es"
    assert detectar_idioma("")[0] is None
    assert detectar_idioma("ok")[0] is None               # sin vocabulario: no se inventa
    assert texto_claramente_en(ALEMAN, "de")
    assert not texto_claramente_en(ALEMAN, "fr")


def test_interes_por_palabras_clave() -> None:
    assert interes_por_texto(FRANCES)[0] == INTERES_UV_PEQUENO
    assert interes_por_texto(ALEMAN)[0] == INTERES_CONSUMIBLES
    assert interes_por_texto(CASTELLANO)[0] == INTERES_VENDING
    assert interes_por_texto("Somos distribuidores en Portugal y queremos representar "
                             "vuestra marca")[0] == INTERES_DISTRIBUCION
    assert interes_por_texto("Buenas tardes, ¿me podéis llamar?") == (INTERES_OTRO, 0)
    # «uv» solo cuenta como palabra entera: «nouveau» no es una impresora UV.
    assert interes_por_texto("Nouveau client, merci de me rappeler") == (INTERES_OTRO, 0)


def test_interes_por_etiquetas_marcadas() -> None:
    assert interes_por_etiquetas(["Máquina de vending", "Vending con pantalla"]) == INTERES_VENDING
    assert interes_por_etiquetas(["Impresora UV A3"]) == INTERES_UV_PEQUENO
    assert interes_por_etiquetas([]) is None
    assert interes_por_etiquetas(["Catálogo"]) is None     # no se reconoce: decide el proveedor


def test_spam_por_dominio_o_por_dos_senales() -> None:
    assert parece_spam("Hi, we offer qualified leads", "blastleadgeneration.com")[0]
    assert parece_spam("We do SEO and build backlinks to boost your ranking", "gmail.com")[0]
    # Una sola señal no basta: un cliente puede escribir «agencia».
    assert not parece_spam("Somos una agencia de regalos y queremos una impresora UV",
                           "regalos.es")[0]
    assert not parece_spam(CASTELLANO, "glowbtl.mx")[0]


class _ProveedorQueNoSePuedeLlamar:
    """Simula la IA: si se la llama, el test falla."""

    nombre = "ia_falsa"

    def clasificar(self, entrada: EntradaLead) -> Clasificacion:  # pragma: no cover
        raise AssertionError("la IA no debía intervenir")


def test_con_productos_marcados_el_interes_sale_de_las_etiquetas_sin_ia() -> None:
    entrada = _entrada(CASTELLANO, idioma_formulario="es", productos=["Máquina de vending"])
    out = clasificar_lead(entrada, _ProveedorQueNoSePuedeLlamar())
    assert out.interes == INTERES_VENDING
    assert out.interes_fuente == "etiquetas"
    assert out.confianza >= 0.9
    assert (out.idioma, out.idioma_fuente, out.discrepancia_idioma) == ("es", "formulario", False)


def test_el_spam_claro_tampoco_llama_a_la_ia() -> None:
    entrada = _entrada("Hello, we sell B2B leads", email="hello@blastleadgeneration.com")
    out = clasificar_lead(entrada, _ProveedorQueNoSePuedeLlamar())
    assert out.es_spam is True
    assert out.interes == INTERES_OTRO


def test_el_idioma_del_formulario_manda_salvo_contradiccion_clara() -> None:
    proveedor = ClasificadorPalabrasClave()
    # Formulario francés y texto en francés: francés, sin discrepancia.
    out = clasificar_lead(_entrada(FRANCES, idioma_formulario="fr"), proveedor)
    assert (out.idioma, out.idioma_fuente, out.discrepancia_idioma) == ("fr", "formulario", False)
    assert out.interes == INTERES_UV_PEQUENO
    # Formulario francés y texto claramente en alemán: gana el alemán y queda anotado.
    out = clasificar_lead(_entrada(ALEMAN, idioma_formulario="fr"), proveedor)
    assert (out.idioma, out.idioma_fuente, out.discrepancia_idioma) == ("de", "texto", True)
    assert "fr" in out.motivo and "de" in out.motivo
    # Texto corto que no delata nada: se queda el del formulario.
    out = clasificar_lead(_entrada("ok", idioma_formulario="nl"), proveedor)
    assert (out.idioma, out.idioma_fuente) == ("nl", "formulario")


def test_las_palabras_clave_casan_por_palabra_entera() -> None:
    # «primera vez» no es el consumible «primer»; «cortesía» no es «corte».
    assert interes_por_texto("Hola, es la primera vez que contacto, me interesa un laser")[0] \
        == INTERES_LASER
    assert interes_por_texto("Por cortesía, ¿me llamáis?") == (INTERES_OTRO, 0)
    # Y los prefijos marcados sí: «grabadora», «personalización», «distribuidores».
    assert interes_por_texto("Busco una grabadora láser")[0] == INTERES_LASER
    assert interes_por_texto("Personalización de botellas")[0] == INTERES_UV_PEQUENO
    assert interes_por_texto("Somos distribuidores")[0] == INTERES_DISTRIBUCION


def test_el_dominio_solo_delata_con_etiquetas_enteras_o_trozos_largos() -> None:
    assert not dominio_sospechoso("seoane.es")              # «seo» dentro de un apellido
    assert not dominio_sospechoso("museodelvidrio.com")
    assert dominio_sospechoso("seo-agency.com")
    assert dominio_sospechoso("blastleadgeneration.com")
    assert dominio_sospechoso("best-backlinks.io")
    assert not parece_spam("Hola, quiero una impresora UV", "pedro@seoane.es")[0]


def test_el_idioma_del_formulario_se_normaliza_a_dos_letras() -> None:
    assert codigo_idioma("de-DE") == "de"
    assert codigo_idioma("EN") == "en"
    assert codigo_idioma("pt_BR") == "pt"
    assert codigo_idioma("zh-Hant") is None
    assert codigo_idioma(None) is None
    out = clasificar_lead(_entrada("ok", idioma_formulario="en-GB"), ClasificadorPalabrasClave())
    assert (out.idioma, out.idioma_fuente) == ("en", "formulario")
    out = clasificar_lead(_entrada(ALEMAN, idioma_formulario="zh-Hant"),
                          ClasificadorPalabrasClave())
    assert (out.idioma, out.idioma_fuente) == ("de", "palabras_clave")


def test_sin_formulario_el_idioma_sale_del_texto() -> None:
    out = clasificar_lead(_entrada(ALEMAN, fuente="agilecrm", referencia="nota-1"),
                          ClasificadorPalabrasClave())
    assert (out.idioma, out.idioma_fuente) == ("de", "palabras_clave")
    assert out.interes == INTERES_CONSUMIBLES
    assert out.proveedor == "palabras_clave"
    assert 0.0 < out.confianza < 1.0
