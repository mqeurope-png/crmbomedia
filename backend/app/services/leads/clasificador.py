"""Clasificador de leads: idioma, interés, spam y confianza.

Entra la consulta con su contexto (web y formulario, idioma del formulario,
productos marcados, país, cuenta de Agile, email) y sale una `Clasificacion`.
Es un SERVICIO, no un workflow: aislado detrás de una interfaz
(`Clasificador`), con el proveedor intercambiable y el resultado auditable
(`lead_classifications`).

Dos reglas mandan sobre cualquier proveedor (`clasificar_lead`):

- Si el formulario trae **productos marcados**, el interés sale de las
  etiquetas y no de la IA.
- Si el lead viene de un formulario de BoHub, **el idioma del formulario
  manda** y el proveedor solo lo revisa: ya ha pasado que un alemán rellene
  el formulario francés, así que si el texto está claramente en otro idioma
  gana el texto y queda anotada la discrepancia.

`ClasificadorPalabrasClave` es el proveedor sin IA: palabras clave por
interés, vocabulario por idioma y señales de spam. Es el respaldo cuando no
hay proveedor de IA configurado y lo que usan los tests. El de IA
(`anthropic`) se registra aparte y entra por la misma interfaz.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

#: Intereses, alineados con las plantillas que existen (`plantillas.py`).
INTERES_UV_PEQUENO = "uv_pequeno_mediano"
INTERES_UV_GRANDE = "uv_gran_formato"
INTERES_LASER = "laser_cnc"
INTERES_VENDING = "vending"
INTERES_DISTRIBUCION = "distribucion"
INTERES_CONSUMIBLES = "consumibles"
INTERES_SERVICIO = "servicio_tecnico"
INTERES_REPUESTOS = "repuestos"
INTERES_OTRO = "otro"
INTERESES: tuple[str, ...] = (
    INTERES_UV_PEQUENO, INTERES_UV_GRANDE, INTERES_LASER, INTERES_VENDING,
    INTERES_DISTRIBUCION, INTERES_CONSUMIBLES, INTERES_SERVICIO, INTERES_REPUESTOS,
    INTERES_OTRO,
)
#: Los que son un lead comercial: tienen plantilla de venta. Consumibles,
#: servicio técnico y repuestos se clasifican, se marcan y se crea la tarea,
#: pero no se les prepara plantilla de venta.
INTERESES_COMERCIALES: frozenset[str] = frozenset({
    INTERES_UV_PEQUENO, INTERES_UV_GRANDE, INTERES_LASER, INTERES_VENDING,
    INTERES_DISTRIBUCION,
})
ETIQUETAS_INTERES: dict[str, str] = {
    INTERES_UV_PEQUENO: "UV pequeño-mediano",
    INTERES_UV_GRANDE: "UV gran formato",
    INTERES_LASER: "Láser y CNC",
    INTERES_VENDING: "Vending",
    INTERES_DISTRIBUCION: "Distribución",
    INTERES_CONSUMIBLES: "Consumibles",
    INTERES_SERVICIO: "Servicio técnico",
    INTERES_REPUESTOS: "Repuestos",
    INTERES_OTRO: "Otro",
}

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
    """Lo que sale del clasificador."""

    idioma: str | None
    interes: str
    es_spam: bool
    confianza: float
    motivo: str
    idioma_fuente: str = FUENTE_DESCONOCIDA
    interes_fuente: str = FUENTE_PALABRAS
    #: El formulario decía un idioma y el texto, claramente, otro.
    discrepancia_idioma: bool = False
    proveedor: str = PROVEEDOR_PALABRAS
    modelo: str | None = None

    def como_dict(self) -> dict[str, Any]:
        return {
            "idioma": self.idioma, "interes": self.interes, "es_spam": self.es_spam,
            "confianza": round(float(self.confianza), 2), "motivo": self.motivo,
            "idioma_fuente": self.idioma_fuente, "interes_fuente": self.interes_fuente,
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


# --- interés ----------------------------------------------------------------

#: Palabras clave por interés, sin acentos y en minúsculas (el texto se
#: normaliza igual). Se casan por PALABRA ENTERA (o frase entera): «primer»
#: no es «primera», «corte» no es «cortesía». Una clave acabada en `*` casa
#: como prefijo («grabad*»: grabado, grabador, grabadora).
_PALABRAS_INTERES: dict[str, tuple[str, ...]] = {
    INTERES_SERVICIO: (
        "averia", "averiad*", "no funciona", "no imprime", "no enciende", "no arranca",
        "reparar", "reparacion", "repair", "reparation", "reparatur", "soporte tecnico",
        "asistencia tecnica", "technical support", "technischer support", "defekt", "kaputt",
        "storing", "mantenimiento", "maintenance", "wartung", "onderhoud", "fallo", "fallos",
        "error", "errores", "garantia", "warranty", "garantie", "se ha roto", "broken",
        "incidencia", "estropead*",
    ),
    INTERES_REPUESTOS: (
        "repuesto*", "recambio*", "spare part*", "piece* detachee*", "ersatzteil*",
        "onderdeel", "onderdelen", "cabezal*", "printhead*", "print head*", "druckkopf",
        "druckkopfe", "tete d'impression", "peca de reposicao", "pecas de reposicao",
        "placa base", "lampara uv", "lampe uv", "uv lamp", "uv-lampe",
    ),
    INTERES_CONSUMIBLES: (
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
    INTERES_LASER: (
        "laser*", "cnc", "fresadora*", "router", "co2", "fibra", "fiber", "grabad*", "grabar",
        "engrav*", "gravure*", "decoupe*", "graveer*", "graveren", "cutting", "corte",
        "cortar", "flux", "mbolaser*", "mbo laser",
    ),
    INTERES_UV_GRANDE: (
        "gran formato", "large format", "grand format", "grossformat*", "groot formaat",
        "grande formato", "2513", "2030", "3020", "roll to roll", "roll-to-roll",
        "rollo a rollo", "industrial", "2,5 m", "2.5 m", "2,5m", "2.5m", "paneles", "panels",
        "tableros", "boards", "carteleria", "signage",
    ),
    INTERES_UV_PEQUENO: (
        "uv", "flatbed", "a3", "a4", "a2", "6090", "3060", "4060", "artisjet", "boligrafo*",
        "botella*", "bottle*", "bouteille*", "flasche*", "fles", "flessen", "funda*", "movil",
        "moviles", "phone case*", "regalo*", "gift*", "cadeau*", "geschenk*", "merchandising",
        "promocional*", "personaliza*", "personalis*", "personalize*", "objetos", "objets",
        "gegenstande*", "pens", "glass", "vidrio", "verre", "glas", "madera", "wood", "bois",
        "holz", "impresora* uv", "uv printer*", "imprimante* uv", "uv-drucker", "uv drucker",
        "uv-printer*",
    ),
}
#: Orden de desempate: lo más específico primero; «uv» a secas, lo último.
_ORDEN_INTERES: tuple[str, ...] = (
    INTERES_SERVICIO, INTERES_REPUESTOS, INTERES_CONSUMIBLES, INTERES_DISTRIBUCION,
    INTERES_VENDING, INTERES_LASER, INTERES_UV_GRANDE, INTERES_UV_PEQUENO,
)


def _patron(clave: str) -> re.Pattern[str]:
    """Palabra (o frase) entera; `*` al final de una palabra = prefijo."""
    trozos = [
        re.escape(p[:-1]) + r"\w*" if p.endswith("*") else re.escape(p)
        for p in clave.split(" ")
    ]
    return re.compile(r"(?<!\w)" + r"\s+".join(trozos) + r"(?!\w)")


_PATRONES: dict[str, re.Pattern[str]] = {}


def _casa(texto_normalizado: str, clave: str) -> int:
    patron = _PATRONES.get(clave)
    if patron is None:
        patron = _PATRONES[clave] = _patron(clave)
    return len(patron.findall(texto_normalizado))


def puntuar_intereses(texto: str) -> dict[str, int]:
    normalizado = _normalizar(texto)
    return {
        interes: sum(_casa(normalizado, clave) for clave in claves)
        for interes, claves in _PALABRAS_INTERES.items()
    }


def interes_por_texto(texto: str) -> tuple[str, int]:
    """`(interes, aciertos)`; `otro` con 0 si nada casa."""
    puntos = puntuar_intereses(texto)
    mejor = max(_ORDEN_INTERES, key=lambda i: (puntos.get(i, 0), -_ORDEN_INTERES.index(i)))
    aciertos = puntos.get(mejor, 0)
    return (mejor, aciertos) if aciertos > 0 else (INTERES_OTRO, 0)


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
        idioma, puntos_idioma, _ = detectar_idioma(texto)
        interes, aciertos = interes_por_texto(texto)
        if es_spam:
            confianza = 0.85
            motivo = motivo_spam
        elif aciertos >= 3:
            confianza, motivo = 0.8, f"{aciertos} palabras clave de {ETIQUETAS_INTERES[interes]}"
        elif aciertos == 2:
            confianza, motivo = 0.7, f"2 palabras clave de {ETIQUETAS_INTERES[interes]}"
        elif aciertos == 1:
            confianza, motivo = 0.55, f"1 palabra clave de {ETIQUETAS_INTERES[interes]}"
        else:
            confianza, motivo = 0.3, "sin palabras clave reconocibles"
        return Clasificacion(
            idioma=idioma, interes=interes, es_spam=es_spam, confianza=confianza,
            motivo=motivo,
            idioma_fuente=FUENTE_PALABRAS if idioma else FUENTE_DESCONOCIDA,
            interes_fuente=FUENTE_PALABRAS, proveedor=self.nombre,
        )


#: Proveedores registrados por nombre. El de IA se registra desde su módulo.
_PROVEEDORES: dict[str, type] = {PROVEEDOR_PALABRAS: ClasificadorPalabrasClave}


def registrar_proveedor(nombre: str, clase: type) -> None:
    _PROVEEDORES[nombre] = clase


def proveedor_por_defecto() -> Clasificador:
    """El proveedor de IA si está configurado; si no, palabras clave. La
    elección vive aquí para que el paso del workflow y el modo en seco usen
    el mismo."""
    from app.core.config import get_settings  # noqa: PLC0415

    clase = _PROVEEDORES.get("anthropic")
    if clase is not None and get_settings().ai_features_enabled:
        return clase()
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
) -> Clasificacion:
    """Clasifica aplicando las dos reglas duras encima del proveedor."""
    proveedor = proveedor or proveedor_por_defecto()
    texto = entrada.texto or ""

    # Spam claro por palabras clave: no hace falta IA (ni gastarla).
    spam_claro, motivo_spam = parece_spam(texto, entrada.dominio_email)
    por_etiquetas = interes_por_etiquetas(entrada.productos)

    if spam_claro:
        bruta = Clasificacion(
            idioma=None, interes=INTERES_OTRO, es_spam=True, confianza=0.9,
            motivo=motivo_spam, interes_fuente=FUENTE_PALABRAS, proveedor=PROVEEDOR_PALABRAS,
        )
    elif por_etiquetas is not None and not isinstance(proveedor, ClasificadorPalabrasClave):
        # Con productos marcados, la IA no decide el interés; solo haría
        # falta para el idioma y el spam, y eso lo hace el vocabulario.
        bruta = ClasificadorPalabrasClave().clasificar(entrada)
    else:
        bruta = proveedor.clasificar(entrada)

    # 1. Interés: las etiquetas mandan.
    if por_etiquetas is not None and not bruta.es_spam:
        interes, interes_fuente, confianza = por_etiquetas, FUENTE_ETIQUETAS, 0.95
        motivo = (f"productos marcados en el formulario: "
                  f"{', '.join(entrada.productos)}")
    else:
        interes, interes_fuente, confianza, motivo = (
            bruta.interes, bruta.interes_fuente, bruta.confianza, bruta.motivo,
        )
    if interes not in INTERESES:
        interes = INTERES_OTRO

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
        idioma=idioma, interes=interes, es_spam=bruta.es_spam,
        confianza=max(0.0, min(1.0, float(confianza))), motivo=motivo[:500],
        idioma_fuente=idioma_fuente, interes_fuente=interes_fuente,
        discrepancia_idioma=discrepancia, proveedor=bruta.proveedor, modelo=bruta.modelo,
    )


def etiqueta_interes(interes: str | None) -> str:
    return ETIQUETAS_INTERES.get(interes or "", interes or "—")
