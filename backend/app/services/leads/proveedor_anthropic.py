"""El proveedor de IA del clasificador: Anthropic, por la misma interfaz que
el de palabras clave (`Clasificador`).

Usa el cliente que ya tiene BoHub (`app.services.llm._invoke_claude`,
`ANTHROPIC_API_KEY` y `ANTHROPIC_MODEL`). La consulta del cliente SÍ viaja al
proveedor: es lo que se clasifica. Ni el prompt ni la respuesta se guardan en
la base de datos; en el log, el `llm` apunta tamaños y, si la respuesta no es
JSON, sus primeros 200 caracteres (`_parse_segment_json`). Lo que queda es la
clasificación, en `lead_classifications`.

Si la IA no está disponible (sin clave, cuota, caída o respuesta ilegible) se
cae al proveedor de palabras clave y el motivo lo dice: un lead nunca se queda
sin clasificar por la IA. Las dos reglas duras (el texto manda sobre las
etiquetas, el idioma del formulario manda sobre el texto salvo contradicción
clara) las aplica `clasificar_lead`, no este módulo; aquí las etiquetas van
al modelo como lo que son, contexto, y la instrucción de prioridad está
escrita en el prompt.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.services.leads.clasificador import (
    FUENTE_DESCONOCIDA,
    FUENTE_IA,
    IDIOMAS,
    INTERESES,
    Clasificacion,
    ClasificadorPalabrasClave,
    EntradaLead,
    codigo_idioma,
    registrar_proveedor,
)

logger = logging.getLogger(__name__)

NOMBRE = "anthropic"
MAX_TEXTO = 4000

SYSTEM_PROMPT = (
    "Clasificas consultas comerciales que llegan a un grupo de empresas que vende "
    "impresoras UV (pequeño y mediano formato: objetos, botellas, bolígrafos, fundas, "
    "regalos, madera, vidrio; gran formato: paneles, cartelería, rollo a rollo, "
    "industrial), máquinas de láser y CNC (grabado y corte), máquinas de vending "
    "(expendedoras personalizadas) y que busca distribuidores. Las consultas llegan por "
    "formularios web y por AgileCRM, en varios idiomas.\n\n"
    "Devuelves SIEMPRE un único JSON, sin texto alrededor ni markdown:\n"
    "{\n"
    '  "idioma": "es" | "en" | "fr" | "de" | "nl" | "pt" | "ca" | "it" | null,\n'
    '  "interes": "uv_pequeno_mediano" | "uv_gran_formato" | "laser_cnc" | "vending" | '
    '"distribucion" | "consumibles" | "servicio_tecnico" | "repuestos" | "otro",\n'
    '  "es_spam": true | false,\n'
    '  "confianza": número entre 0 y 1,\n'
    '  "motivo": "una frase en español"\n'
    "}\n\n"
    "Reglas:\n"
    "- `idioma` es el idioma en que está ESCRITO el texto de la consulta (no el país ni "
    "el idioma del formulario). Si el texto es demasiado corto para saberlo, null.\n"
    "- `interes` sale de lo que PIDE EL TEXTO. Los «productos marcados en el "
    "formulario» del contexto dicen qué máquina tiene o mira el cliente, no lo que "
    "quiere: úsalos solo para afinar el modelo concreto (UV pequeño o gran formato, "
    "láser, vending) cuando el texto pide una máquina o no dice cuál. Prioridad "
    "escrita: lo que pide el texto manda sobre lo que marca el formulario.\n"
    "- Hay intenciones que ganan a cualquier producto marcado. Si el texto indica una "
    "de estas, el interés es esa aunque el formulario traiga tres máquinas marcadas:\n"
    "  · `servicio_tecnico`: cliente que YA tiene la máquina y algo no va — averías, "
    "piezas cambiadas (cabezal, dampers), calidad de impresión, calibración, errores, "
    "«llevamos meses con problemas», «el cabezal», «no imprime bien».\n"
    "  · `consumibles`: tintas, películas de transferencia, barnices, materiales.\n"
    "  · `repuestos`: piezas sueltas, presupuesto de un cabezal.\n"
    "  · `otro`: gestiones administrativas — facturas, datos de transferencia, correos "
    "mal escritos, pedidos ya hechos.\n"
    "  Ninguno de los cuatro es un lead de venta de máquinas.\n"
    "- `es_spam`: venta de bases de datos o de listas de correo, generación de leads, "
    "SEO y backlinks, agencias ofreciendo servicios (diseño web, apps, marketing, "
    "redes sociales), ofertas que no piden nada a la empresa. Un cliente que pide "
    "información o precio NUNCA es spam.\n"
    "- `confianza`: alta solo si el texto lo dice claramente y los productos marcados "
    "no lo contradicen. Si el texto y los productos marcados se contradicen (máquina "
    "marcada, texto de avería), clasifica por el texto y BAJA la confianza. Si dudas "
    "entre intereses, elige el más probable y baja la confianza.\n"
    "- `motivo`: di SIEMPRE qué pide el texto (qué quiere el cliente), no solo qué marcó "
    "en el formulario.\n"
    "- No inventes datos que no estén en el texto."
)


def _prompt(entrada: EntradaLead) -> str:
    contexto: list[str] = []
    if entrada.sitio:
        contexto.append(f"Web por la que entró: {entrada.sitio}")
    if entrada.idioma_formulario:
        contexto.append(f"Idioma del formulario: {entrada.idioma_formulario}")
    if entrada.productos:
        contexto.append(
            "Productos marcados en el formulario (lo que el cliente marcó: la máquina que "
            "tiene o mira, NO lo que pide; afina el modelo, no decide la intención): "
            + ", ".join(entrada.productos)
        )
    if entrada.pais:
        contexto.append(f"País del contacto: {entrada.pais}")
    if entrada.cuenta_agile:
        contexto.append(f"Cuenta de AgileCRM: {entrada.cuenta_agile}")
    if entrada.dominio_email:
        contexto.append(f"Dominio del correo: {entrada.dominio_email}")
    texto = (entrada.texto or "").strip()[:MAX_TEXTO] or "(sin texto)"
    cabecera = "\n".join(contexto) if contexto else "(sin contexto)"
    return f"Contexto:\n{cabecera}\n\nConsulta:\n{texto}"


def _confianza(raw: Any) -> float:
    try:
        valor = float(raw)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, valor))


class ClasificadorAnthropic:
    nombre = NOMBRE

    def clasificar(self, entrada: EntradaLead) -> Clasificacion:
        from app.core.config import get_settings  # noqa: PLC0415
        from app.services import llm  # noqa: PLC0415

        settings = get_settings()
        if not settings.anthropic_api_key:
            return self._respaldo(entrada, "sin clave de API")
        try:
            raw = llm._invoke_claude(
                api_key=settings.anthropic_api_key, model=settings.anthropic_model,
                system_prompt=SYSTEM_PROMPT, user_prompt=_prompt(entrada),
            )
            data = llm._parse_segment_json(raw)
        except llm.LLMError as exc:
            # El mensaje, no solo la clase: `LLMUpstreamError` se lanza por
            # seis motivos distintos y el 09/10/2026 hubo que ir al log del
            # contenedor para saber cuál era.
            logger.warning("leads.ia: respaldo por palabras clave (%s: %s)",
                           type(exc).__name__, exc)
            return self._respaldo(entrada, f"IA no disponible: {type(exc).__name__}: {exc}")
        idioma = codigo_idioma(data.get("idioma")) if data.get("idioma") else None
        if idioma not in IDIOMAS:
            idioma = None
        interes = str(data.get("interes") or "otro").strip()
        if interes not in INTERESES:
            interes = "otro"
        motivo = str(data.get("motivo") or "").strip()[:500] or "clasificado por IA"
        return Clasificacion(
            idioma=idioma, interes=interes, es_spam=_booleano(data.get("es_spam")),
            confianza=_confianza(data.get("confianza")), motivo=motivo,
            idioma_fuente=FUENTE_IA if idioma else FUENTE_DESCONOCIDA,
            interes_fuente=FUENTE_IA, proveedor=self.nombre, modelo=settings.anthropic_model,
        )

    @staticmethod
    def _respaldo(entrada: EntradaLead, motivo: str) -> Clasificacion:
        out = ClasificadorPalabrasClave().clasificar(entrada)
        out.motivo = f"{out.motivo} (respaldo por palabras clave: {motivo})"[:500]
        return out


_SI = {"true", "yes", "si", "sí", "1", "spam"}
_NO = {"false", "no", "0", "", "none", "null"}


def _booleano(valor: Any) -> bool:
    """`es_spam` como lo devuelva el modelo: un booleano, un número o un
    texto («false», «no»). Un texto no vacío NO es «sí» por defecto: en caso
    de duda, no es spam (el lead sigue su camino y una persona lo ve)."""
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, int | float):
        return valor != 0
    texto = str(valor or "").strip().lower()
    if texto in _SI:
        return True
    if texto in _NO:
        return False
    return False


def _json_de_prueba(clasificacion: dict[str, Any]) -> str:
    """Para los tests: lo que devolvería el modelo."""
    return json.dumps(clasificacion, ensure_ascii=False)


registrar_proveedor(NOMBRE, ClasificadorAnthropic)
