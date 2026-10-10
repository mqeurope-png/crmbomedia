"""El proveedor de IA del clasificador: Anthropic, por la misma interfaz que
el de palabras clave (`Clasificador`).

Usa el cliente que ya tiene BoHub (`app.services.llm._invoke_claude`,
`ANTHROPIC_API_KEY` y `ANTHROPIC_MODEL`). La consulta del cliente SÍ viaja al
proveedor: es lo que se clasifica. Ni el prompt ni la respuesta se guardan en
la base de datos; en el log, el `llm` apunta tamaños y, si la respuesta no es
JSON, sus primeros 200 caracteres (`_parse_segment_json`). Lo que queda es la
clasificación, en `lead_classifications`.

Los intereses posibles van en las instrucciones con su código, su etiqueta y
la descripción de cuándo aplica, sacados del catálogo (Configuración ERP):
un interés nuevo o una descripción afinada desde la pantalla llega al modelo
en la siguiente clasificación, sin desplegar nada. El modelo devuelve una
LISTA ordenada por relevancia: un lead puede pedir varias cosas.

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
    Clasificacion,
    ClasificadorPalabrasClave,
    EntradaLead,
    codigo_idioma,
    registrar_proveedor,
)
from app.services.leads.intereses import Catalogo

logger = logging.getLogger(__name__)

NOMBRE = "anthropic"
MAX_TEXTO = 4000


def prompt_sistema(catalogo: Catalogo) -> str:
    """Las instrucciones, con la lista de intereses del catálogo."""
    return (
        "Clasificas consultas comerciales que llegan a un grupo de empresas de maquinaria "
        "de impresión y personalización: impresoras UV LED (pequeño, mediano y gran "
        "formato), impresión textil DTF, corte y grabado láser, CNC, packaging, máquinas "
        "de vending y su tienda de consumibles y repuestos; además busca distribuidores. "
        "Las consultas llegan por formularios web y por AgileCRM, en varios idiomas.\n\n"
        "Los intereses posibles, con su código y cuándo aplica, son:\n"
        f"{catalogo.para_prompt()}\n\n"
        "Devuelves SIEMPRE un único JSON, sin texto alrededor ni markdown:\n"
        "{\n"
        '  "idioma": "es" | "en" | "fr" | "de" | "nl" | "pt" | "ca" | "it" | null,\n'
        '  "intereses": ["código", ...],\n'
        '  "es_spam": true | false,\n'
        '  "confianza": número entre 0 y 1,\n'
        '  "motivo": "una frase en español"\n'
        "}\n\n"
        "Reglas:\n"
        "- `idioma` es el idioma en que está ESCRITO el texto de la consulta (no el país ni "
        "el idioma del formulario). Si el texto es demasiado corto para saberlo, null.\n"
        "- `intereses` es una LISTA de códigos de la lista de arriba, ordenada por "
        "relevancia: el primero es el principal (lo que pide con más claridad). Pon varios "
        "SOLO si el texto pide claramente varias cosas distintas (por ejemplo, imprimir "
        "sobre placas de metal Y sobre camisetas es UV y DTF); lo normal es uno. Pedir "
        "varias cosas NO baja la confianza: baja cuando dudas de lo que pide.\n"
        "- `intereses` sale de lo que PIDE EL TEXTO. Los «productos marcados en el "
        "formulario» del contexto dicen qué máquina tiene o mira el cliente, no lo que "
        "quiere: úsalos solo para afinar el modelo concreto (la talla de UV, láser, "
        "vending) cuando el texto pide una máquina o no dice cuál. Prioridad escrita: lo "
        "que pide el texto manda sobre lo que marca el formulario.\n"
        "- Hay intenciones que ganan a cualquier producto marcado. Si el texto indica una "
        "de estas, el interés es esa aunque el formulario traiga tres máquinas marcadas: "
        "soporte postventa (cliente que YA tiene la máquina y algo no va: averías, piezas "
        "cambiadas, calidad de impresión, calibración, «llevamos meses con problemas»), "
        "tienda (tintas, películas, repuestos, un cabezal) y «otro» (gestiones: facturas, "
        "transferencias, pedidos ya hechos, correos mal escritos). Ninguna es un lead de "
        "venta de máquinas.\n"
        "- `es_spam`: venta de bases de datos o de listas de correo, generación de leads, "
        "SEO y backlinks, agencias ofreciendo servicios (diseño web, apps, marketing, "
        "redes sociales), ofertas que no piden nada a la empresa. Un cliente que pide "
        "información o precio NUNCA es spam.\n"
        "- `confianza`: alta solo si el texto lo dice claramente y los productos marcados "
        "no lo contradicen. Si el texto y los productos marcados se contradicen (máquina "
        "marcada, texto de avería), clasifica por el texto y BAJA la confianza. Si dudas "
        "entre dos intereses para la MISMA cosa, elige el más probable y baja la "
        "confianza.\n"
        "- `motivo`: di SIEMPRE qué pide el texto (qué quiere el cliente), no solo qué marcó "
        "en el formulario.\n"
        "- No inventes datos que no estén en el texto."
    )


#: Compatibilidad: las instrucciones con la lista de partida.
SYSTEM_PROMPT = prompt_sistema(Catalogo.de_partida())


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


def _lista_intereses(data: dict[str, Any]) -> list[str]:
    """`intereses` (lista) o, si el modelo contesta a la antigua, `interes`."""
    crudo = data.get("intereses")
    if isinstance(crudo, str):
        crudo = [crudo]
    if not isinstance(crudo, list) or not crudo:
        crudo = [data.get("interes")] if data.get("interes") else []
    return [str(i).strip().lower() for i in crudo if str(i or "").strip()]


class ClasificadorAnthropic:
    nombre = NOMBRE

    def __init__(self, catalogo: Catalogo | None = None) -> None:
        self.catalogo = catalogo or Catalogo.de_partida()

    def clasificar(self, entrada: EntradaLead) -> Clasificacion:
        from app.core.config import get_settings  # noqa: PLC0415
        from app.services import llm  # noqa: PLC0415

        settings = get_settings()
        if not settings.anthropic_api_key:
            return self._respaldo(entrada, "sin clave de API")
        try:
            raw = llm._invoke_claude(
                api_key=settings.anthropic_api_key, model=settings.anthropic_model,
                system_prompt=prompt_sistema(self.catalogo), user_prompt=_prompt(entrada),
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
        # Códigos que no están en el catálogo se tiran; sin ninguno válido, «otro».
        intereses = self.catalogo.limpiar(_lista_intereses(data))
        motivo = str(data.get("motivo") or "").strip()[:500] or "clasificado por IA"
        return Clasificacion(
            idioma=idioma, intereses=intereses, es_spam=_booleano(data.get("es_spam")),
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
