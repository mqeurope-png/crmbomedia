"""Clasificador de leads: idioma, intereses, spam y confianza.

Entra la consulta con su contexto (web y formulario, idioma del formulario,
productos marcados, país, cuenta de Agile, email) y sale una `Clasificacion`.
Es un SERVICIO, no un workflow: aislado detrás de una interfaz
(`Clasificador`), con el proveedor intercambiable y el resultado auditable
(`lead_classifications`).

Un lead puede querer VARIAS cosas: «placas de metal y camisetas» es UV y DTF a
la vez (10/10/2026). El clasificador devuelve una **lista de intereses ordenada
por relevancia** (`Clasificacion.intereses`); el primero es el principal
(`interes`). La lista de intereses posibles vive en datos
(`app.services.leads.intereses`, Configuración ERP), no aquí: este módulo
solo conoce las palabras clave de los de partida para el proveedor sin IA.

Dos reglas mandan sobre cualquier proveedor (`clasificar_lead`):

- **La consulta se lee siempre y lo que pide el texto manda.** Los productos
  marcados en el formulario dicen qué máquina tiene o mira el cliente, no lo
  que quiere: entran como contexto (al proveedor se le dice lo que son) y
  solo deciden el interés cuando el texto no dice nada (consulta en blanco, o
  sin una sola palabra clave con el proveedor sin IA), y entonces con menos
  confianza. Si el texto es de soporte postventa, de tienda (consumibles,
  repuestos) o una gestión («otro»), ese es el interés aunque el formulario
  traiga tres máquinas marcadas. El 09/10/2026 el atajo «con etiquetas no se
  lee el texto» habría mandado el catálogo con precios a dos clientes con la
  máquina averiada.
- Si el lead viene de un formulario de BoHub, **el idioma del formulario
  manda** y el proveedor solo lo revisa: ya ha pasado que un alemán rellene
  el formulario francés, así que si el texto está claramente en otro idioma
  gana el texto y queda anotada la discrepancia.

La confianza dice algo: etiquetas que coinciden con alguno de los intereses
del texto la suben; etiquetas que lo contradicen (máquina marcada, texto de
avería) la bajan y el motivo lo cuenta; etiquetas sin texto, baja.

`ClasificadorPalabrasClave` es el proveedor sin IA: palabras clave por
interés, vocabulario por idioma y señales de spam. Es el respaldo cuando no
hay proveedor de IA configurado y lo que usan los tests. El de IA
(`anthropic`) se registra aparte y entra por la misma interfaz, con el
catálogo (códigos y descripciones) en sus instrucciones.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.services.leads.intereses import (
    FAMILIA_UNICA,
    OTRO,
    Catalogo,
)

#: Códigos de los intereses de partida (los que tienen palabras clave aquí).
INTERES_UV_PEQUENO = "uv_pequeno"
INTERES_UV_MEDIANO = "uv_mediano"
INTERES_UV_GRANDE = "uv_grande"
INTERES_DTF = "dtf"
INTERES_CORTE_LASER = "corte_laser"
INTERES_GRABADO_LASER = "grabado_laser"
INTERES_CNC = "cnc"
INTERES_PACKAGING = "packaging"
INTERES_VENDING = "vending"
INTERES_DISTRIBUCION = "distribucion"
INTERES_SOPORTE = "soporte_postventa"
INTERES_TIENDA = "tienda"
INTERES_OTRO = OTRO

_DE_PARTIDA = Catalogo.de_partida()
#: Los códigos de partida, en orden de pantalla (compatibilidad: lo que manda
#: es el catálogo de la base, `intereses.cargar`).
INTERESES: tuple[str, ...] = _DE_PARTIDA.codigos
INTERESES_COMERCIALES: frozenset[str] = _DE_PARTIDA.comerciales
ETIQUETAS_INTERES: dict[str, str] = {i.codigo: i.etiqueta for i in _DE_PARTIDA.todos}

#: Idiomas que se reconocen. Los seis primeros tienen plantilla.
IDIOMAS: tuple[str, ...] = ("es", "en", "fr", "de", "nl", "pt", "ca", "it")

#: De dónde salió cada dato.
FUENTE_FORMULARIO = "formulario"
FUENTE_ETIQUETAS = "etiquetas"
FUENTE_TEXTO = "texto"
FUENTE_IA = "ia"
FUENTE_PALABRAS = "palabras_clave"
FUENTE_DESCONOCIDA = "desconocido"

PROVEEDOR_PALABRAS = "palabras_clave"

#: Intereses que no son una venta de máquina: si el texto dice uno de estos,
#: gana a cualquier etiqueta marcada en el formulario.
INTERESES_NO_COMERCIALES: frozenset[str] = frozenset({INTERES_SOPORTE, INTERES_TIENDA, OTRO})
#: Confianza cuando deciden las etiquetas porque el texto no dice nada.
CONFIANZA_SOLO_ETIQUETAS = 0.6
#: Confianza mínima cuando el texto y las etiquetas coinciden.
CONFIANZA_ETIQUETAS_COHERENTES = 0.85
#: Confianza máxima cuando el texto contradice a las etiquetas: manda el
#: texto, pero la contradicción se nota.
CONFIANZA_CONTRADICCION = 0.75
#: Sin consulta ni etiquetas reconocibles no hay nada que clasificar.
CONFIANZA_SIN_NADA = 0.2


@dataclass
class EntradaLead:
    """Lo que se le da al clasificador."""

    texto: str
    #: web_form | agilecrm | manual
    fuente: str
    #: Id del envío o de la nota.
    referencia: str
    #: La fecha real del lead.
    lead_at: datetime | None = None
    #: La clave de la web (lo que va antes de `-contacto` en el slug).
    sitio: str | None = None
    formulario: str | None = None
    idioma_formulario: str | None = None
    #: Nombres de las etiquetas (productos) que marcó en el formulario.
    productos: list[str] = field(default_factory=list)
    pais: str | None = None
    cuenta_agile: str | None = None
    email: str | None = None

    @property
    def dominio_email(self) -> str:
        correo = (self.email or "").strip().lower()
        return correo.rsplit("@", 1)[-1] if "@" in correo else ""

    def contexto(self) -> dict[str, Any]:
        """Lo que se guarda en `lead_classifications.input_json`."""
        return {
            "fuente": self.fuente, "referencia": self.referencia,
            "sitio": self.sitio, "formulario": self.formulario,
            "idioma_formulario": self.idioma_formulario,
            "productos": list(self.productos), "pais": self.pais,
            "cuenta_agile": self.cuenta_agile, "dominio_email": self.dominio_email,
        }


@dataclass
class Clasificacion:
    """Lo que sale del clasificador. `intereses` es la lista ordenada por
    relevancia; `interes` es el principal (el primero). Se puede construir
    con cualquiera de los dos: el otro se deduce."""

    idioma: str | None
    interes: str = OTRO
    es_spam: bool = False
    confianza: float = 0.0
    motivo: str = ""
    idioma_fuente: str = FUENTE_DESCONOCIDA
    interes_fuente: str = FUENTE_PALABRAS
    #: El formulario decía un idioma y el texto, claramente, otro.
    discrepancia_idioma: bool = False
    proveedor: str = PROVEEDOR_PALABRAS
    modelo: str | None = None
    intereses: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.intereses:
            self.intereses = [str(i) for i in self.intereses]
            self.interes = self.intereses[0]
        else:
            self.intereses = [self.interes or OTRO]
            self.interes = self.intereses[0]

    def como_dict(self) -> dict[str, Any]:
        return {
            "idioma": self.idioma, "interes": self.interes, "intereses": list(self.intereses),
            "es_spam": self.es_spam, "confianza": round(float(self.confianza), 2),
            "motivo": self.motivo, "idioma_fuente": self.idioma_fuente,
            "interes_fuente": self.interes_fuente,
            "discrepancia_idioma": self.discrepancia_idioma,
            "proveedor": self.proveedor, "modelo": self.modelo,
        }


class Clasificador(Protocol):
    """La interfaz del proveedor: mira el texto y el contexto y propone. Las
    reglas duras (etiquetas, idioma del formulario) las aplica
    `clasificar_lead`, no el proveedor."""

    nombre: str

    def clasificar(self, entrada: EntradaLead) -> Clasificacion: ...


# --- normalización ----------------------------------------------------------


def _sin_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )


def _normalizar(texto: str) -> str:
    """Minúsculas, sin acentos, espacios simples: para las palabras clave."""
    return re.sub(r"\s+", " ", _sin_acentos((texto or "").lower())).strip()


_PALABRA_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _palabras(texto: str) -> list[str]:
    """Las palabras del texto, en minúsculas y CON acentos (el vocabulario
    por idioma los necesita: «für» no es «fur»)."""
    return _PALABRA_RE.findall((texto or "").lower())


# --- idioma -----------------------------------------------------------------

#: Palabras distintivas por idioma. Nada de artículos cortos compartidos («la»,
#: «de», «un») que están en media Europa: cuentan las que delatan el idioma.
_VOCABULARIO: dict[str, tuple[str, ...]] = {
    "es": (
        "hola", "gracias", "quiero", "quisiera", "informacion", "información", "precio",
        "presupuesto", "saludos", "necesito", "interesado", "interesada", "buenos", "buenas",
        "máquina", "maquina", "impresora", "tienen", "estamos", "somos", "ustedes", "nuestra",
        "empresa", "cuánto", "cuanto", "envío", "envio", "también", "tambien", "pero", "muy",
        "hemos", "estoy", "podrían", "podrian", "quería", "queria", "atentamente",
    ),
    "en": (
        "the", "hello", "hi", "please", "thanks", "thank", "would", "price", "quote",
        "quotation", "information", "printer", "regards", "looking", "interested", "company",
        "we", "you", "our", "your", "with", "and", "about", "could", "need", "machine",
        "kindly", "sincerely", "best",
    ),
    "fr": (
        "bonjour", "merci", "nous", "vous", "devis", "prix", "imprimante", "cordialement",
        "souhaite", "souhaitons", "souhaiterais", "votre", "notre", "sommes", "avons", "est",
        "pour", "avec", "une", "des", "les", "machine", "renseignements", "madame", "monsieur",
        "bouteilles", "verre", "impression",
    ),
    "de": (
        "und", "ich", "wir", "sie", "für", "mit", "nicht", "eine", "ein", "hallo", "danke",
        "preis", "angebot", "drucker", "maschine", "grüße", "grüsse", "möchte", "möchten",
        "bitte", "haben", "sind", "uns", "ihnen", "freundlichen", "sehr", "geehrte", "geehrter",
        "bin", "auch", "über", "unsere", "ihre", "wie", "können", "benötigen", "brauchen",
        "auf", "sowie", "drucken",
    ),
    "nl": (
        "wij", "graag", "offerte", "prijs", "bedankt", "groeten", "willen", "hebben", "zijn",
        "onze", "informatie", "over", "het", "een", "voor", "met", "naar", "kunnen",
        "vriendelijke", "ik", "u", "jullie", "ook", "geïnteresseerd", "printer", "machine",
    ),
    "pt": (
        "olá", "ola", "obrigado", "obrigada", "gostaria", "preço", "preco", "orçamento",
        "orcamento", "impressora", "cumprimentos", "informação", "informacao", "somos",
        "temos", "nossa", "você", "vocês", "não", "nao", "também", "tambem", "estamos",
        "muito", "saudações", "máquina", "maquina",
    ),
    "ca": (
        "gràcies", "gracies", "voldria", "preu", "pressupost", "impressora", "salutacions",
        "aquest", "aquesta", "som", "tenim", "nostra", "amb", "més", "també", "estem",
        "podeu", "vostè", "hem", "màquina", "bon", "dia",
    ),
    "it": (
        "ciao", "buongiorno", "grazie", "vorrei", "prezzo", "preventivo", "stampante",
        "saluti", "siamo", "abbiamo", "nostra", "della", "sono", "che", "anche", "vostro",
        "informazioni", "macchina", "gentile", "cordiali",
    ),
}

#: Caracteres que solo (o casi solo) usa un idioma.
_SENALES: tuple[tuple[str, str, int], ...] = (
    ("es", "ñ", 2), ("es", "¿", 3), ("es", "¡", 3),
    ("de", "ß", 3), ("de", "ä", 1), ("de", "ö", 1), ("de", "ü", 1),
    ("pt", "ã", 2), ("pt", "õ", 2),
)


def puntuar_idiomas(texto: str) -> dict[str, int]:
    """Puntos por idioma: palabras distintivas (cada una hasta 3 veces) y
    caracteres que lo delatan."""
    palabras = Counter(_palabras(texto))
    puntos: dict[str, int] = {}
    for idioma, vocabulario in _VOCABULARIO.items():
        total = 0
        for palabra in vocabulario:
            total += min(palabras.get(palabra, 0), 3)
        puntos[idioma] = total
    bajo = (texto or "").lower()
    for idioma, senal, peso in _SENALES:
        if senal in bajo:
            puntos[idioma] = puntos.get(idioma, 0) + peso
    return puntos


def detectar_idioma(texto: str) -> tuple[str | None, int, dict[str, int]]:
    """`(idioma, puntos, puntos_por_idioma)`. `None` si el texto no delata
    ninguno (muy corto o sin vocabulario reconocible)."""
    puntos = puntuar_idiomas(texto)
    if not puntos:
        return None, 0, puntos
    orden = sorted(puntos.items(), key=lambda kv: kv[1], reverse=True)
    mejor, mejor_puntos = orden[0]
    segundo_puntos = orden[1][1] if len(orden) > 1 else 0
    if mejor_puntos < 2 or mejor_puntos == segundo_puntos:
        return None, mejor_puntos, puntos
    return mejor, mejor_puntos, puntos


def texto_claramente_en(texto: str, idioma: str) -> bool:
    """El texto está CLARAMENTE en `idioma`: puntúa al menos 3 y el doble
    que cualquier otro. Es lo que hace falta para contradecir al
    formulario."""
    puntos = puntuar_idiomas(texto)
    propio = puntos.get(idioma, 0)
    if propio < 3:
        return False
    resto = max((p for i, p in puntos.items() if i != idioma), default=0)
    return propio >= 2 * resto


# --- intereses --------------------------------------------------------------

#: Palabras clave por interés, sin acentos y en minúsculas (el texto se
#: normaliza igual). Se casan por PALABRA ENTERA (o frase entera): «primer»
#: no es «primera», «corte» no es «cortesía». Una clave acabada en `*` casa
#: como prefijo («grabad*»: grabado, grabador, grabadora).
#:
#: Las tres tallas de UV comparten las palabras de la familia (`_UV_COMUN`):
#: «impresora UV» puntúa para las tres y la talla la decide lo específico
#: (A3, 3000U → pequeño; 6090, 5000U → mediano; 2,5 m, paneles → grande); a
#: igualdad, la pequeña. Un lead lleva una sola talla (`FAMILIA_UNICA`).
_UV_COMUN: tuple[str, ...] = (
    "uv", "uv-led", "led uv", "impresora* uv", "uv printer*", "imprimante* uv", "uv-drucker",
    "uv drucker", "uv-printer*", "uv-druck*", "artisjet", "personaliza*", "personalis*",
    "personalize*", "glass", "vidrio", "verre", "glas", "madera", "wood", "bois", "holz",
    "metal", "metall*", "placa*", "plate*", "platten", "acrilico", "metacrilato", "acrylic",
    "acryl",
)
_PALABRAS_INTERES: dict[str, tuple[str, ...]] = {
    INTERES_SOPORTE: (
        "averia", "averiad*", "no funciona", "no imprime", "no enciende", "no arranca",
        "reparar", "reparacion", "repair", "reparation", "reparatur", "soporte tecnico",
        "asistencia tecnica", "technical support", "technischer support", "defekt", "kaputt",
        "storing", "mantenimiento", "maintenance", "wartung", "onderhoud", "fallo", "fallos",
        "error", "errores", "garantia", "warranty", "garantie", "se ha roto", "broken",
        "incidencia", "estropead*",
        # Cliente que ya tiene la máquina y algo no va: «llevamos meses con
        # problemas», piezas cambiadas, calidad de impresión, calibración.
        "problem*", "nothing but problems", "getauscht", "ausgetauscht", "replaced",
        "print quality", "calidad de impresion", "druckqualitat", "qualite d'impression",
        "calibra*", "kalibrier*", "banding", "clogged", "nozzle*", "dusen", "no imprime bien",
        "imprime mal", "not printing", "doesn't print", "does not print", "druckt nicht",
        "funktioniert nicht", "ne fonctionne pas", "ne marche pas", "werkt niet",
        "nao funciona",
    ),
    INTERES_TIENDA: (
        # Repuestos.
        "repuesto*", "recambio*", "spare part*", "piece* detachee*", "ersatzteil*",
        "onderdeel", "onderdelen", "cabezal*", "printhead*", "print head*", "druckkopf",
        "druckkopfe", "tete d'impression", "peca de reposicao", "pecas de reposicao",
        "placa base", "lampara uv", "lampe uv", "uv lamp", "uv-lampe",
        # Consumibles.
        "tinta", "tintas", "ink", "inks", "encre", "encres", "tinte", "tinten", "inkt",
        "consumible*", "consumable*", "verbrauchsmaterial*", "primer", "primers", "barniz",
        "barnices", "varnish", "vernis", "pelicula* de transferencia", "transfer film*",
        "transferfolie*", "film* de transfert", "folie", "folien", "lamina", "laminas",
        "cleaning", "limpiador*", "solucion de limpieza", "imprimacion",
    ),
    INTERES_DISTRIBUCION: (
        "distribuidor*", "distribucion", "distributor*", "distribution", "reseller*",
        "revendedor*", "revendeur*", "dealer*", "handler", "vertrieb*", "wholesale*",
        "mayorista*", "partner*", "representar", "representacion", "importar", "importador*",
        "importer*", "exclusiv*", "agente comercial", "distribuir",
    ),
    INTERES_VENDING: (
        "vending", "expendedor*", "distributeur* automatique*", "verkaufsautomat*",
        "automaten", "snackautomaat", "automaat", "pimpam", "pim pam", "maquina* de snacks",
    ),
    INTERES_PACKAGING: (
        "packaging", "embalaje*", "caja", "cajas", "estuche*", "boxes", "carton", "cartones",
        "verpackung*", "emballage*", "verpakking*", "cardboard", "faltschachtel*",
    ),
    INTERES_CNC: (
        "cnc", "fresadora*", "fresado", "router", "mecanizado", "milling", "fraisage",
        "frasen", "frase", "frasmaschine", "freesmachine",
    ),
    INTERES_CORTE_LASER: (
        "laser*", "co2", "fibra", "fiber", "decoupe*", "cutting", "corte", "cortar", "cut",
        "schneiden", "snijden", "flux", "mbolaser*", "mbo laser",
    ),
    INTERES_GRABADO_LASER: (
        "laser*", "grabad*", "grabar", "engrav*", "gravure*", "graver", "graveer*", "graveren",
        "gravier*", "marcaje", "marcado laser", "marquage",
    ),
    INTERES_DTF: (
        # «shirt*» ya casa «t-shirts» y «tshirt*» («t-» no es letra): una
        # palabra del texto cuenta una vez, no una por cada clave que la pille.
        "dtf", "textil", "textile*", "camiseta*", "tshirt*", "shirt*",
        "sudadera*", "hoodie*", "gorra*", "prenda*", "garment*", "ropa", "tela", "tejido*",
        "tissu*", "stoff*", "kleidung", "sublimacion", "sublimation", "dtg", "polvo dtf",
        "film dtf", "textildruck*",
    ),
    INTERES_UV_GRANDE: _UV_COMUN + (
        "gran formato", "large format", "grand format", "grossformat*", "groot formaat",
        "grande formato", "2513", "2030", "3020", "roll to roll", "roll-to-roll",
        "rollo a rollo", "industrial", "2,5 m", "2.5 m", "2,5m", "2.5m", "paneles", "panels",
        "tableros", "boards", "carteleria", "signage",
    ),
    INTERES_UV_MEDIANO: _UV_COMUN + (
        "a2", "a1", "6090", "4060", "5000u", "6000u", "flatbed", "mediano formato",
        "medium format", "format moyen", "mittelformat", "60x90", "60 x 90",
    ),
    INTERES_UV_PEQUENO: _UV_COMUN + (
        "a3", "a4", "3060", "3000u", "1500u", "pro v6", "boligrafo*", "funda*", "movil",
        "moviles", "phone case*", "regalo*", "gift*", "cadeau*", "geschenk*", "merchandising",
        "promocional*", "pens", "pequeno formato", "small format", "petit format",
        "kleinformat", "botella*", "bottle*", "bouteille*", "flasche*", "fles", "flessen",
        "objetos", "objets", "gegenstande*",
    ),
}
#: Orden de desempate a igualdad de aciertos y de posición en el texto: lo
#: más específico primero; las tallas de UV, de menor a mayor, lo último.
_ORDEN_INTERES: tuple[str, ...] = (
    INTERES_SOPORTE, INTERES_TIENDA, INTERES_DISTRIBUCION, INTERES_VENDING, INTERES_PACKAGING,
    INTERES_CNC, INTERES_CORTE_LASER, INTERES_GRABADO_LASER, INTERES_DTF, INTERES_UV_PEQUENO,
    INTERES_UV_MEDIANO, INTERES_UV_GRANDE,
)


def _patron(clave: str) -> re.Pattern[str]:
    """Palabra (o frase) entera; `*` al final de una palabra = prefijo."""
    trozos = [
        re.escape(p[:-1]) + r"\w*" if p.endswith("*") else re.escape(p)
        for p in clave.split(" ")
    ]
    return re.compile(r"(?<!\w)" + r"\s+".join(trozos) + r"(?!\w)")


_PATRONES: dict[str, re.Pattern[str]] = {}


def _compilado(clave: str) -> re.Pattern[str]:
    patron = _PATRONES.get(clave)
    if patron is None:
        patron = _PATRONES[clave] = _patron(clave)
    return patron


def _casa(texto_normalizado: str, clave: str) -> int:
    return sum(1 for _ in _compilado(clave).finditer(texto_normalizado))


def _tramos(texto_normalizado: str, claves: tuple[str, ...]) -> list[tuple[int, int]]:
    """Los trozos del texto que casan con alguna de las claves, fusionando
    los que se solapan: «impresora uv» casa con `impresora* uv` y con `uv`,
    y «uv-drucker» con `uv`, `uv-drucker` y `uv-druck*`, pero cada uno es UNA
    cosa que dice el texto, no tres. Lo que cuenta son las cosas distintas
    que pide."""
    posiciones: list[tuple[int, int]] = []
    for clave in claves:
        posiciones.extend(
            (m.start(), m.end()) for m in _compilado(clave).finditer(texto_normalizado)
        )
    fusionados: list[tuple[int, int]] = []
    for inicio, fin in sorted(posiciones):
        if fusionados and inicio < fusionados[-1][1]:
            fusionados[-1] = (fusionados[-1][0], max(fin, fusionados[-1][1]))
        else:
            fusionados.append((inicio, fin))
    return fusionados


def puntuar_intereses(texto: str) -> dict[str, int]:
    """Por interés, cuántas cosas distintas del texto lo dicen."""
    normalizado = _normalizar(texto)
    return {
        interes: len(_tramos(normalizado, claves))
        for interes, claves in _PALABRAS_INTERES.items()
    }


def intereses_por_texto(texto: str) -> list[tuple[str, int]]:
    """Los intereses que dice el texto, de más a menos relevante:
    `[(interes, aciertos), ...]`. El soporte postventa va siempre primero si
    aparece (quien cuenta una avería menciona la máquina que TIENE: nunca es
    una venta). Después, más cosas distintas del texto primero; a igualdad,
    el que aparece antes (lo que se pide primero es lo que más se quiere) y
    luego el orden de desempate. Una sola talla de UV. Vacío si nada casa."""
    normalizado = _normalizar(texto)
    puntuados: list[tuple[int, int, int, int, str]] = []
    for interes, claves in _PALABRAS_INTERES.items():
        tramos = _tramos(normalizado, claves)
        if tramos:
            puntuados.append((
                0 if interes == INTERES_SOPORTE else 1, -len(tramos), tramos[0][0],
                _ORDEN_INTERES.index(interes), interes,
            ))
    puntuados.sort()
    salida: list[tuple[str, int]] = []
    familias: set[str] = set()
    for _soporte, negativo, _primera, _orden, interes in puntuados:
        familia = FAMILIA_UNICA.get(interes)
        if familia and familia in familias:
            continue
        if familia:
            familias.add(familia)
        salida.append((interes, -negativo))
    return salida


def interes_por_texto(texto: str) -> tuple[str, int]:
    """`(interes principal, aciertos)`; `otro` con 0 si nada casa."""
    lista = intereses_por_texto(texto)
    return lista[0] if lista else (INTERES_OTRO, 0)


def palabras_que_casan(texto: str, interes: str, maximo: int = 4) -> list[str]:
    """Las palabras clave de `interes` que aparecen en el texto (para que el
    motivo diga qué dice el texto, no solo cuántas veces)."""
    normalizado = _normalizar(texto)
    vistas: list[str] = []
    for clave in _PALABRAS_INTERES.get(interes, ()):
        if _casa(normalizado, clave):
            vistas.append(clave.rstrip("*"))
            if len(vistas) >= maximo:
                break
    return vistas


def interes_por_etiquetas(productos: list[str] | None) -> str | None:
    """El interés que dicen las etiquetas marcadas en el formulario (por
    mayoría). `None` si no hay etiquetas o ninguna se reconoce: entonces
    decide el proveedor."""
    votos: Counter[str] = Counter()
    for nombre in productos or []:
        interes, aciertos = interes_por_texto(nombre)
        if aciertos:
            votos[interes] += 1
    if not votos:
        return None
    return max(_ORDEN_INTERES, key=lambda i: (votos.get(i, 0), -_ORDEN_INTERES.index(i)))


# --- spam -------------------------------------------------------------------

_PALABRAS_SPAM: tuple[str, ...] = (
    "lead generation", "leadgeneration", "generacion de leads", "generación de leads",
    "b2b leads", "verified leads", "qualified leads", "seo", "backlink", "backlinks",
    "link building", "linkbuilding", "guest post", "guest posting", "base de datos de",
    "bases de datos", "database of", "email list", "mailing list", "lista de correos",
    "ranking", "posicionamiento web", "diseno web", "web design", "website design",
    "we design", "app development", "mobile app", "software development", "agencia",
    "agency", "marketing digital", "digital marketing", "social media management",
    "we can help you", "help you grow", "boost your", "increase your", "grow your business",
    "outsourcing", "virtual assistant", "data entry", "bulk sms", "whatsapp marketing",
    "cold email", "appointment setting", "lista de empresas", "compra de base de datos",
    "promote your", "advertising services", "content writing", "copywriting services",
    "ai chatbot for your", "chatbot services", "unsubscribe here", "click here",
)
#: Trozos que delatan un dominio: los largos en cualquier parte
#: («blastleadgeneration.com»); los cortos solo como etiqueta entera
#: («seo-agency.com» sí; «seoane.es» o «museodelvidrio.com» no).
_DOMINIOS_SPAM_DENTRO: tuple[str, ...] = (
    "leadgen", "leadgeneration", "lead-gen", "linkbuilding", "backlink", "growthhack",
)
_DOMINIOS_SPAM_ETIQUETA: tuple[str, ...] = ("seo", "leads", "outreach")
_ETIQUETA_DOMINIO_RE = re.compile(r"[a-z0-9]+")


def dominio_sospechoso(dominio_email: str | None) -> bool:
    dominio = _normalizar(dominio_email or "")
    if not dominio:
        return False
    if any(trozo in dominio for trozo in _DOMINIOS_SPAM_DENTRO):
        return True
    etiquetas = set(_ETIQUETA_DOMINIO_RE.findall(dominio))
    return any(token in etiquetas for token in _DOMINIOS_SPAM_ETIQUETA)


def puntuar_spam(texto: str, dominio_email: str | None = None) -> tuple[int, bool]:
    """`(aciertos_en_texto, dominio_sospechoso)`."""
    normalizado = _normalizar(texto)
    aciertos = sum(_casa(normalizado, clave) for clave in _PALABRAS_SPAM)
    return aciertos, dominio_sospechoso(dominio_email)


def parece_spam(texto: str, dominio_email: str | None = None) -> tuple[bool, str]:
    """Venta de bases de datos, generación de leads, SEO, agencias ofreciendo
    servicios. Un solo acierto en el texto no basta (un cliente puede escribir
    «agencia»); dos sí, y el dominio delata por sí solo."""
    aciertos, dominio_sospechoso = puntuar_spam(texto, dominio_email)
    if dominio_sospechoso:
        return True, f"dominio del correo sospechoso ({dominio_email})"
    if aciertos >= 2:
        return True, f"{aciertos} señales de venta de servicios en el texto"
    return False, ""


# --- proveedor sin IA -------------------------------------------------------


class ClasificadorPalabrasClave:
    """Proveedor sin IA: palabras clave por interés, vocabulario por idioma y
    señales de spam. Respaldo cuando no hay IA configurada."""

    nombre = PROVEEDOR_PALABRAS

    def clasificar(self, entrada: EntradaLead) -> Clasificacion:
        texto = entrada.texto or ""
        es_spam, motivo_spam = parece_spam(texto, entrada.dominio_email)
        idioma, _puntos_idioma, _ = detectar_idioma(texto)
        lista = intereses_por_texto(texto)
        intereses = [i for i, _ in lista] or [INTERES_OTRO]
        aciertos = lista[0][1] if lista else 0
        principal = intereses[0]
        vistas = ", ".join(palabras_que_casan(texto, principal)) if aciertos else ""
        etiqueta = ETIQUETAS_INTERES.get(principal, principal)
        if es_spam:
            confianza = 0.85
            motivo = motivo_spam
        elif aciertos >= 3:
            confianza = 0.8
            motivo = f"el texto dice {etiqueta}: {aciertos} palabras clave ({vistas})"
        elif aciertos == 2:
            confianza = 0.7
            motivo = f"el texto dice {etiqueta}: 2 palabras clave ({vistas})"
        elif aciertos == 1:
            confianza = 0.55
            motivo = f"el texto apunta a {etiqueta}: 1 palabra clave ({vistas})"
        else:
            confianza, motivo = 0.3, "el texto no dice qué quiere (sin palabras clave reconocibles)"
        if not es_spam and len(intereses) > 1:
            otros = ", ".join(ETIQUETAS_INTERES.get(i, i) for i in intereses[1:])
            motivo = f"{motivo}; además pide {otros}"
        return Clasificacion(
            idioma=idioma, intereses=intereses, es_spam=es_spam, confianza=confianza,
            motivo=motivo,
            idioma_fuente=FUENTE_PALABRAS if idioma else FUENTE_DESCONOCIDA,
            interes_fuente=FUENTE_PALABRAS, proveedor=self.nombre,
        )


#: Proveedores registrados por nombre. El de IA se registra desde su módulo.
_PROVEEDORES: dict[str, type] = {PROVEEDOR_PALABRAS: ClasificadorPalabrasClave}


def registrar_proveedor(nombre: str, clase: type) -> None:
    _PROVEEDORES[nombre] = clase


def proveedor_por_defecto(catalogo: Catalogo | None = None) -> Clasificador:
    """El proveedor de IA si está configurado (con el catálogo de intereses en
    sus instrucciones); si no, palabras clave. La elección vive aquí para que
    el paso del workflow y el modo en seco usen el mismo."""
    from app.core.config import get_settings  # noqa: PLC0415

    clase = _PROVEEDORES.get("anthropic")
    if clase is not None and get_settings().ai_features_enabled:
        return clase(catalogo=catalogo)
    return ClasificadorPalabrasClave()


# --- las reglas que mandan sobre el proveedor -------------------------------


def codigo_idioma(raw: str | None) -> str | None:
    """`de-DE`, `DE`, `pt_BR` → `de` / `pt`; lo que no sea uno de los idiomas
    que se reconocen → `None` (no se inventa ni se cuela un código largo en
    una columna de cinco caracteres)."""
    codigo = (raw or "").strip().lower().replace("_", "-").split("-")[0]
    return codigo if codigo in IDIOMAS else None


def clasificar_lead(
    entrada: EntradaLead, proveedor: Clasificador | None = None,
    catalogo: Catalogo | None = None,
) -> Clasificacion:
    """Clasifica aplicando las dos reglas duras encima del proveedor. El
    catálogo (`intereses.cargar(session)`) dice qué códigos existen; sin él,
    el de partida."""
    catalogo = catalogo or _DE_PARTIDA
    proveedor = proveedor or proveedor_por_defecto(catalogo)
    texto = (entrada.texto or "").strip()

    # Spam claro por palabras clave: no hace falta IA (ni gastarla).
    spam_claro, motivo_spam = parece_spam(texto, entrada.dominio_email)
    por_etiquetas = interes_por_etiquetas(entrada.productos)
    if por_etiquetas is not None and not catalogo.activo(por_etiquetas):
        por_etiquetas = None
    etiquetas = ", ".join(entrada.productos)

    if spam_claro:
        bruta = Clasificacion(
            idioma=None, interes=INTERES_OTRO, es_spam=True, confianza=0.9,
            motivo=motivo_spam, interes_fuente=FUENTE_PALABRAS, proveedor=PROVEEDOR_PALABRAS,
        )
    elif not texto:
        # Consulta en blanco (pasa): no hay nada que leer, no se llama al
        # proveedor. Decidirán las etiquetas, si las hay.
        bruta = Clasificacion(
            idioma=None, interes=INTERES_OTRO, es_spam=False, confianza=CONFIANZA_SIN_NADA,
            motivo="formulario sin consulta", interes_fuente=FUENTE_DESCONOCIDA,
            proveedor=PROVEEDOR_PALABRAS,
        )
    else:
        # La consulta se lee SIEMPRE, con etiquetas o sin ellas.
        bruta = proveedor.clasificar(entrada)

    # 1. Intereses: lo que pide el texto, en su orden. Las etiquetas solo
    #    deciden cuando el texto no dice nada; si coinciden con alguno de los
    #    intereses del texto suben la confianza y si lo contradicen (máquina
    #    marcada, texto de avería) manda el texto y la confianza baja. Las
    #    intenciones que no son venta (soporte, tienda, «otro») ganan a
    #    cualquier etiqueta.
    intereses = catalogo.limpiar(bruta.intereses)
    interes_fuente, confianza, motivo = bruta.interes_fuente, bruta.confianza, bruta.motivo
    if por_etiquetas is not None and not bruta.es_spam:
        # «otro» de la IA es una decisión (gestión, factura, pedido hecho);
        # «otro» del proveedor sin IA es «ni una palabra clave».
        texto_sin_senal = not texto or (
            intereses == [INTERES_OTRO] and bruta.interes_fuente != FUENTE_IA
        )
        if texto_sin_senal:
            intereses, interes_fuente = [por_etiquetas], FUENTE_ETIQUETAS
            confianza = CONFIANZA_SOLO_ETIQUETAS
            que_dice = ("sin consulta en el formulario" if not texto
                        else "el texto no dice qué quiere")
            motivo = f"{que_dice}; interés por los productos marcados ({etiquetas})"
        elif por_etiquetas in intereses:
            confianza = max(confianza, CONFIANZA_ETIQUETAS_COHERENTES)
            motivo = f"{motivo}; coincide con los productos marcados ({etiquetas})"
        else:
            confianza = min(confianza, CONFIANZA_CONTRADICCION)
            motivo = (f"{motivo}; el formulario marcaba {catalogo.etiqueta(por_etiquetas)} "
                      f"({etiquetas}), pero manda lo que pide el texto")

    # 2. Idioma: el del formulario manda; el texto solo si lo contradice
    #    claramente.
    idioma = bruta.idioma if bruta.idioma in IDIOMAS else None
    idioma_fuente = bruta.idioma_fuente if idioma else FUENTE_DESCONOCIDA
    discrepancia = False
    formulario = codigo_idioma(entrada.idioma_formulario)
    if formulario:
        detectado, _puntos, _ = detectar_idioma(texto)
        if (detectado and detectado != formulario
                and texto_claramente_en(texto, detectado)):
            idioma, idioma_fuente, discrepancia = detectado, FUENTE_TEXTO, True
            motivo = f"{motivo}; el formulario es {formulario} pero el texto está en {detectado}"
        else:
            idioma, idioma_fuente = formulario, FUENTE_FORMULARIO

    return Clasificacion(
        idioma=idioma, intereses=intereses, es_spam=bruta.es_spam,
        confianza=max(0.0, min(1.0, float(confianza))), motivo=motivo[:500],
        idioma_fuente=idioma_fuente, interes_fuente=interes_fuente,
        discrepancia_idioma=discrepancia, proveedor=bruta.proveedor, modelo=bruta.modelo,
    )


def etiqueta_interes(interes: str | None, catalogo: Catalogo | None = None) -> str:
    """La etiqueta de un código; con el catálogo de la base si se pasa, si
    no con la lista de partida."""
    if catalogo is not None:
        return catalogo.etiqueta(interes)
    return ETIQUETAS_INTERES.get(interes or "", interes or "—")
