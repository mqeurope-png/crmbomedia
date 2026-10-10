"""Respuesta a leads · el clasificador sin IA y las dos reglas que mandan
sobre cualquier proveedor (el texto manda sobre las etiquetas, que solo
deciden sin texto; idioma del formulario → idioma, salvo que el texto lo
contradiga claramente)."""
from __future__ import annotations

from app.services.leads.clasificador import (
    CONFIANZA_CONTRADICCION,
    CONFIANZA_ETIQUETAS_COHERENTES,
    CONFIANZA_SOLO_ETIQUETAS,
    INTERES_CORTE_LASER,
    INTERES_DISTRIBUCION,
    INTERES_DTF,
    INTERES_GRABADO_LASER,
    INTERES_OTRO,
    INTERES_SOPORTE,
    INTERES_TIENDA,
    INTERES_UV_MEDIANO,
    INTERES_UV_PEQUENO,
    INTERES_VENDING,
    INTERESES_COMERCIALES,
    Clasificacion,
    ClasificadorPalabrasClave,
    EntradaLead,
    clasificar_lead,
    codigo_idioma,
    detectar_idioma,
    dominio_sospechoso,
    interes_por_etiquetas,
    interes_por_texto,
    intereses_por_texto,
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
    assert interes_por_texto(ALEMAN)[0] == INTERES_TIENDA
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


class _ProveedorQueApunta:
    """Simula la IA y apunta cada llamada: devuelve lo que se le diga."""

    nombre = "ia_falsa"

    def __init__(self, interes: str, confianza: float = 0.9, motivo: str = "lo dice el texto"):
        self.interes, self.confianza, self.motivo = interes, confianza, motivo
        self.entradas: list[EntradaLead] = []

    def clasificar(self, entrada: EntradaLead) -> Clasificacion:
        self.entradas.append(entrada)
        return Clasificacion(idioma="en", interes=self.interes, es_spam=False,
                             confianza=self.confianza, motivo=self.motivo, idioma_fuente="ia",
                             interes_fuente="ia", proveedor=self.nombre, modelo="falso")


# Los dos leads de la simulación en seco del 09/10/2026 que habrían recibido el
# catálogo con precios teniendo la máquina averiada. Textos reales.
JOVICA = ("We purchased an Artist 5000U printer from you in 2023. Unfortunately, we have "
          "been experiencing nothing but problems with the unit for the past year…")
JOVICA_PRODUCTOS = ["Impresión UV Led", "Impresión Textil"]
TONI = ("Wie Haben seit sechs Monaten viele Probleme mit unserem Drucker. Druckkopf wurde "
        "getauscht. Dampfer wurden getauscht. Es wurde auf das neue Tanksystem umgestellt…")
TONI_PRODUCTOS = ["artisJet Passion", "Artisjet 5000U UV Led"]
# Mickael DUROS preguntaba por la PRO V6 en francés (el texto literal no está en
# el repositorio; lo que se comprueba es lo que dijo la simulación: UV pequeño,
# francés, leyendo el texto).
MICKAEL = "Bonjour, je souhaite un devis pour l'imprimante PRO V6. Merci"


def test_la_consulta_se_lee_siempre_aunque_haya_productos_marcados() -> None:
    """Las etiquetas ya no cortocircuitan al proveedor: se llama con la
    consulta, y las etiquetas le llegan como contexto."""
    ia = _ProveedorQueApunta(INTERES_VENDING)
    entrada = _entrada(CASTELLANO, idioma_formulario="es", productos=["Máquina de vending"])
    out = clasificar_lead(entrada, ia)
    assert len(ia.entradas) == 1 and ia.entradas[0].productos == ["Máquina de vending"]
    assert (out.interes, out.interes_fuente) == (INTERES_VENDING, "ia")
    # Texto y etiquetas coinciden: la confianza no baja.
    assert out.confianza >= CONFIANZA_ETIQUETAS_COHERENTES
    assert "coincide con los productos marcados" in out.motivo
    assert (out.idioma, out.idioma_fuente, out.discrepancia_idioma) == ("es", "formulario", False)


def test_jovica_soporte_postventa_aunque_marcara_impresion_uv() -> None:
    """Cliente con la máquina averiada desde hace un año y «Impresión UV Led»
    marcado: soporte postventa, sin plantilla de venta."""
    out = clasificar_lead(_entrada(JOVICA, idioma_formulario="en", productos=JOVICA_PRODUCTOS),
                          ClasificadorPalabrasClave())
    assert out.interes == INTERES_SOPORTE
    assert out.interes not in INTERESES_COMERCIALES          # sin plantilla de venta
    assert out.idioma == "en"
    # La contradicción (máquina marcada, texto de avería) se nota en la
    # confianza y el motivo cuenta qué dice el texto y qué se marcó.
    assert out.confianza <= CONFIANZA_CONTRADICCION
    assert "Soporte postventa" in out.motivo and "problem" in out.motivo
    assert "Impresión UV Led" in out.motivo and "manda lo que pide el texto" in out.motivo


def test_toni_soporte_postventa_en_aleman_aunque_marcara_dos_artisjet() -> None:
    """Seis meses de problemas, cabezal y dampers cambiados, dos máquinas
    marcadas: soporte postventa, idioma de (el formulario era francés)."""
    out = clasificar_lead(_entrada(TONI, idioma_formulario="fr", productos=TONI_PRODUCTOS),
                          ClasificadorPalabrasClave())
    assert out.interes == INTERES_SOPORTE
    assert out.interes not in INTERESES_COMERCIALES
    assert (out.idioma, out.idioma_fuente, out.discrepancia_idioma) == ("de", "texto", True)
    assert out.confianza <= CONFIANZA_CONTRADICCION


def test_mickael_sigue_siendo_uv_pequeno_en_frances() -> None:
    out = clasificar_lead(_entrada(MICKAEL, idioma_formulario="fr"), ClasificadorPalabrasClave())
    assert (out.interes, out.idioma) == (INTERES_UV_PEQUENO, "fr")
    assert out.interes in INTERESES_COMERCIALES


def test_con_la_consulta_en_blanco_deciden_las_etiquetas_con_menos_confianza() -> None:
    entrada = _entrada("   ", idioma_formulario="es", productos=["Impresora UV A3"])
    out = clasificar_lead(entrada, _ProveedorQueNoSePuedeLlamar())   # sin texto no se llama
    assert (out.interes, out.interes_fuente) == (INTERES_UV_PEQUENO, "etiquetas")
    assert out.confianza == CONFIANZA_SOLO_ETIQUETAS < CONFIANZA_ETIQUETAS_COHERENTES
    assert "sin consulta" in out.motivo and "Impresora UV A3" in out.motivo
    assert (out.idioma, out.idioma_fuente) == ("es", "formulario")
    # Sin consulta ni etiquetas no hay nada que clasificar.
    vacio = clasificar_lead(_entrada("", idioma_formulario="es"), _ProveedorQueNoSePuedeLlamar())
    assert (vacio.interes, vacio.confianza) == (INTERES_OTRO, 0.2)


def test_maquina_nueva_con_etiquetas_coherentes_tiene_confianza_alta() -> None:
    """La regla no penaliza el caso normal: texto que pide una impresora UV y
    etiqueta de impresora UV."""
    out = clasificar_lead(_entrada(FRANCES, idioma_formulario="fr", productos=["Impresora UV A3"]),
                          ClasificadorPalabrasClave())
    assert (out.interes, out.idioma) == (INTERES_UV_PEQUENO, "fr")
    assert out.confianza >= CONFIANZA_ETIQUETAS_COHERENTES


def test_el_texto_manda_tambien_entre_dos_maquinas_y_otro_de_la_ia_es_una_decision() -> None:
    # Texto de láser con etiqueta de vending: láser, con la contradicción anotada.
    out = clasificar_lead(_entrada("Busco una grabadora láser para madera", idioma_formulario="es",
                                   productos=["Máquina de vending"]), ClasificadorPalabrasClave())
    assert out.interes == INTERES_GRABADO_LASER and out.confianza <= CONFIANZA_CONTRADICCION
    assert "Vending" in out.motivo
    # La IA dice «otro» (una gestión: factura, transferencia) con máquinas
    # marcadas: se queda en «otro», que gana a cualquier etiqueta.
    ia = _ProveedorQueApunta(INTERES_OTRO, confianza=0.8, motivo="pide la factura del pedido")
    out = clasificar_lead(_entrada("I need the invoice for order 99942, thanks",
                                   idioma_formulario="en", productos=["Impresión UV Led"]), ia)
    assert (out.interes, out.interes_fuente) == (INTERES_OTRO, "ia")
    assert out.confianza <= CONFIANZA_CONTRADICCION and "pide la factura" in out.motivo
    # Sin IA, «otro» solo significa «ni una palabra clave»: ahí sí deciden las
    # etiquetas, con menos confianza.
    out = clasificar_lead(_entrada("Buenas tardes, ¿me podéis llamar?", idioma_formulario="es",
                                   productos=["Impresora UV A3"]), ClasificadorPalabrasClave())
    assert (out.interes, out.interes_fuente) == (INTERES_UV_PEQUENO, "etiquetas")
    assert out.confianza == CONFIANZA_SOLO_ETIQUETAS and "no dice qué quiere" in out.motivo


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
    # «un laser», sin más, es corte (el más pedido); «grabadora láser», grabado.
    assert interes_por_texto("Hola, es la primera vez que contacto, me interesa un laser")[0] \
        == INTERES_CORTE_LASER
    assert interes_por_texto("Por cortesía, ¿me llamáis?") == (INTERES_OTRO, 0)
    # Y los prefijos marcados sí: «grabadora», «personalización», «distribuidores».
    assert interes_por_texto("Busco una grabadora láser")[0] == INTERES_GRABADO_LASER
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
    assert out.interes == INTERES_TIENDA
    assert out.proveedor == "palabras_clave"
    assert 0.0 < out.confianza < 1.0


# --- varios intereses por lead (10/10/2026) -----------------------------------

#: torracollons@elbarquito.net: placas de metal Y camisetas, con la 3000U PRO
#: y la 5000U marcadas. Dos intereses: UV (principal) y DTF; las etiquetas
#: coinciden con el principal, así que la confianza sube.
TORRACOLLONS = "Ich möchte auf Metallplatten sowie auf T-Shirts drucken"
TORRACOLLONS_PRODUCTOS = ["Artisjet 3000U PRO", "5000U UV Led"]


def test_placas_de_metal_y_camisetas_son_uv_y_dtf_en_ese_orden() -> None:
    lista = intereses_por_texto(TORRACOLLONS)
    assert [i for i, _ in lista] == [INTERES_UV_PEQUENO, INTERES_DTF]
    out = clasificar_lead(_entrada(TORRACOLLONS, idioma_formulario="de",
                                   productos=TORRACOLLONS_PRODUCTOS), ClasificadorPalabrasClave())
    assert out.intereses == [INTERES_UV_PEQUENO, INTERES_DTF]
    assert out.interes == INTERES_UV_PEQUENO                 # el principal es el primero
    assert out.idioma == "de"
    # Las etiquetas (dos impresoras UV) coinciden con el principal: sube.
    assert out.confianza >= CONFIANZA_ETIQUETAS_COHERENTES
    assert "DTF" in out.motivo and "coincide con los productos marcados" in out.motivo
    assert out.como_dict()["intereses"] == [INTERES_UV_PEQUENO, INTERES_DTF]


def test_un_lead_lleva_una_sola_talla_de_uv_y_lo_que_pide_primero_va_primero() -> None:
    # «impresora UV» puntúa para las tres tallas: se queda una (la pequeña, a
    # igualdad); y «A2» la hace mediana.
    assert [i for i, _ in intereses_por_texto("Quiero una impresora UV")] == [INTERES_UV_PEQUENO]
    assert [i for i, _ in intereses_por_texto("Quiero una impresora UV A2")] == [INTERES_UV_MEDIANO]
    # A igualdad de palabras clave, lo que se pide antes en el texto es lo principal.
    assert [i for i, _ in intereses_por_texto("Busco una máquina de vending y una impresora UV")] \
        == [INTERES_VENDING, INTERES_UV_PEQUENO]
    assert [i for i, _ in intereses_por_texto("Busco una impresora UV y una máquina de vending")] \
        == [INTERES_UV_PEQUENO, INTERES_VENDING]


def test_los_materiales_y_laser_a_secas_refuerzan_pero_no_crean_un_interes() -> None:
    """«Grabar logos en madera y metal con láser» es grabado láser y nada
    más: madera y metal no lo hacen UV, «láser» no lo hace también corte."""
    assert [i for i, _ in intereses_por_texto("Quiero grabar logos en madera y metal con láser")] \
        == [INTERES_GRABADO_LASER]
    # Solo palabras débiles: una conjetura, no dos («láser» a secas es corte).
    assert [i for i, _ in intereses_por_texto("Busco una máquina láser")] == [INTERES_CORTE_LASER]
    assert [i for i, _ in intereses_por_texto("Impresión sobre madera y vidrio")] \
        == [INTERES_UV_PEQUENO]
    # Cortar Y grabar sí son dos cosas.
    assert [i for i, _ in intereses_por_texto("Quiero cortar y grabar madera con láser")] \
        == [INTERES_CORTE_LASER, INTERES_GRABADO_LASER]
    # Con «Impresora UV» marcada y un texto de grabado, es contradicción (UV
    # no es un secundario espurio por «madera»), no coherencia.
    out = clasificar_lead(_entrada("Quiero grabar logos en madera y metal con láser",
                                   idioma_formulario="es", productos=["Impresora UV A3"]),
                          ClasificadorPalabrasClave())
    assert out.intereses == [INTERES_GRABADO_LASER]
    assert out.confianza <= CONFIANZA_CONTRADICCION and "manda lo que pide el texto" in out.motivo


def test_la_clasificacion_se_construye_con_lista_o_con_un_solo_interes() -> None:
    una = Clasificacion(idioma="es", interes=INTERES_VENDING)
    assert una.intereses == [INTERES_VENDING]
    varias = Clasificacion(idioma="es", intereses=[INTERES_UV_MEDIANO, INTERES_DTF])
    assert varias.interes == INTERES_UV_MEDIANO
    ninguna = Clasificacion(idioma=None)
    assert (ninguna.interes, ninguna.intereses) == (INTERES_OTRO, [INTERES_OTRO])


def test_un_proveedor_que_devuelve_varios_intereses_los_conserva_en_su_orden() -> None:
    class _IA:
        nombre = "ia_falsa"

        def clasificar(self, entrada: EntradaLead) -> Clasificacion:
            return Clasificacion(idioma="de", intereses=["dtf", "uv_mediano", "cohetes", "dtf"],
                                 confianza=0.9, motivo="camisetas y placas", idioma_fuente="ia",
                                 interes_fuente="ia", proveedor=self.nombre)

    out = clasificar_lead(_entrada(TORRACOLLONS, idioma_formulario="de"), _IA())
    # Lo que no está en el catálogo se tira, lo repetido una vez, el orden se respeta.
    assert out.intereses == ["dtf", "uv_mediano"] and out.interes == "dtf"
