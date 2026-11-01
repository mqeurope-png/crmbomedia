"""La IA del análisis del parque (1/11/2026): lee los hechos calculados y
escribe dos cosas, nada más:

- una **lectura** corta (dos o tres frases) que junta lo calculado;
- **por dónde entrar**: una frase concreta para una llamada o un correo.

Cada frase tiene que citar los hechos en los que se apoya, por su id (D3,
D7…). Lo que no cita nada, o cita un id que no existe, se DESCARTA: un
resumen que no se puede verificar no se usa dos veces. «Por dónde entrar»
tiene que apoyarse en un dato calculado (no en un aviso); si no puede, no se
escribe: una frase de venta genérica es peor que ninguna.

Producto: solo se puede proponer uno de la lista (P1, P2…), que son los
consumibles de la gama de sus máquinas que nunca ha comprado. Una frase que
nombre un código de artículo que no es suyo ni de la lista se descarta.

Qué sale hacia el modelo (API de Anthropic): los textos de los hechos
(descripciones y códigos de artículo, gamas, fechas, cuántas veces) y la lista
de productos. Ni nombres, ni NIF, ni correos, ni direcciones, ni el nombre de la
empresa, ni los números de documento (el enlace lo pone el servidor por el id).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.services import llm
from app.services.analisis_parque.hechos import ANCLAS, Hechos

MAX_LECTURA = 4

SISTEMA = """Eres un analista comercial de Bomedia (máquinas láser, UV, DTF y sus consumibles). \
Te paso HECHOS ya calculados sobre el parque y las compras de un cliente, cada uno con su id \
(D1, D2…), y una lista de PRODUCTOS (P1, P2…) que se le pueden proponer, ORDENADA POR ENCAJE \
con ese cliente: primero lo que compraba y ha dejado de comprar; después lo que nunca ha \
comprado pero compran clientes como él (que compran sus mismos consumibles o tienen su misma \
máquina). No está ordenada por lo más vendido.

Escribe en castellano, para un comercial que va a llamar o escribir al cliente:

1. "lectura": de 1 a 3 frases cortas que junten lo importante de los hechos. Cada frase cita \
en "citas" los ids de los hechos en los que se apoya. No añadas nada que no esté en los hechos: \
ni datos, ni fechas, ni cifras, ni suposiciones.
2. "entrada": UNA frase concreta de «por dónde entrar» (qué decirle o qué ofrecerle). Tiene que \
apoyarse en un hueco, un cambio o un dato concreto de los hechos, y citarlo en "citas". Si \
propone un producto, solo uno de la lista PRODUCTOS, y pon su id en "productos". Si no hay un \
dato en el que apoyarla, devuelve "entrada": null. Nada de frases de venta genéricas. Si propones \
producto, el PRIMERO de la lista que tenga sentido con los hechos; mejor recuperar lo que ya \
compraba que ofrecer algo nuevo.

Reglas:
- Si un hecho dice que algo no se sabe (por ejemplo, que una máquina puede no estar en \
servicio), no lo des por cierto.
- No inventes productos, tintas, consumibles ni modelos; no propongas nada de otra gama.
- No escribas nombres de personas ni de empresas.

Devuelve SOLO un JSON:
{"lectura": [{"texto": "...", "citas": ["D1"]}], \
"entrada": {"texto": "...", "citas": ["D3"], "productos": ["P2"]} | null}"""


@dataclass
class Resultado:
    lectura: list[dict[str, Any]] = field(default_factory=list)
    entrada: dict[str, Any] | None = None
    #: Lo que se tiró y por qué (se enseña: no se esconde que se descartó algo).
    descartado: list[str] = field(default_factory=list)


def peticion(h: Hechos) -> str:
    datos = {
        "hechos": [{"id": x.id, "bloque": x.bloque, "texto": x.texto} for x in h.hechos],
        "productos": [{"id": p.id, "codigo": p.codart, "descripcion": p.descripcion,
                       "gama": p.gama_nombre} for p in h.productos],
    }
    return "HECHOS Y PRODUCTOS:\n" + json.dumps(datos, ensure_ascii=False, indent=1)


_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-/]{2,}")


def _codigos_ajenos(texto: str, permitidos: set[str], todos: set[str],
                    numeros_de_hechos: set[str]) -> list[str]:
    """Los códigos de artículo del catálogo que nombra el texto y no son ni del
    cliente ni de la lista de productos. Cuenta lo que parece un código: letras
    y cifras, o solo cifras (de 4 o más; muchos códigos de FACTUSOL son
    números) que existan en el catálogo y no salgan en ningún hecho (así un
    año o una cantidad de los hechos no se toman por un código)."""
    out = []
    for t in _TOKEN.findall(texto or ""):
        k = t.strip(".-/").upper()
        digitos = any(c.isdigit() for c in k)
        letras = any(c.isalpha() for c in k)
        if digitos and letras:
            if k in todos and k not in permitidos:
                out.append(t)
        elif digitos and k.isdigit() and len(k) >= 4:
            if k in todos and k not in permitidos and k not in numeros_de_hechos:
                out.append(t)
    return out


def validar(data: Any, h: Hechos, *, codigos_catalogo: set[str]) -> Resultado:
    """Lo que devolvió el modelo, filtrado: solo frases que citan hechos que
    existen, una «entrada» anclada en un dato calculado y productos de la lista."""
    r = Resultado()
    if not isinstance(data, dict):
        r.descartado.append("La respuesta no tenía el formato pedido.")
        return r
    ids = {x.id: x for x in h.hechos}
    productos = {p.id: p for p in h.productos}
    permitidos = {p.codart.upper() for p in h.productos}
    permitidos |= {m.upper() for x in h.hechos for m in re.findall(r"\(([^()\s]+)\)", x.texto)}
    numeros = {n for x in h.hechos for n in re.findall(r"\d+", x.texto)}

    def citas_de(raw: Any) -> list[str] | None:
        """Las citas; None si falta alguna o alguna no existe (la frase entera
        se descarta: un dato inventado entre otros reales no se salva)."""
        if not isinstance(raw, list) or not raw:
            return None
        if not all(isinstance(c, str) and c in ids for c in raw):
            return None
        return list(dict.fromkeys(raw))

    lectura = data.get("lectura")
    for item in (lectura if isinstance(lectura, list) else [])[:MAX_LECTURA]:
        texto = str(item.get("texto") or "").strip() if isinstance(item, dict) else ""
        if not texto:
            continue
        citas = citas_de(item.get("citas"))
        if not citas:
            r.descartado.append(f"Una frase de la lectura no citaba bien sus datos (sin cita o con "
                                f"una que no existe): «{texto[:80]}»")
            continue
        if ajenos := _codigos_ajenos(texto, permitidos, codigos_catalogo, numeros):
            r.descartado.append(f"Una frase nombraba un artículo que no es suyo ni de la lista "
                                f"({', '.join(ajenos)}).")
            continue
        r.lectura.append({"texto": texto, "citas": citas})

    e = data.get("entrada")
    if isinstance(e, dict) and str(e.get("texto") or "").strip():
        texto = str(e["texto"]).strip()
        citas = citas_de(e.get("citas")) or []
        anclas = [c for c in citas if ids[c].bloque in ANCLAS]
        brutos = e.get("productos")
        pedidos = [p for p in (brutos if isinstance(brutos, list) else []) if isinstance(p, str)]
        if not anclas:
            r.descartado.append("«Por dónde entrar» no se apoyaba en ningún dato calculado: "
                                "no se escribe.")
        elif any(p not in productos for p in pedidos):
            r.descartado.append("«Por dónde entrar» proponía un producto que no está en la "
                                "lista del catálogo: no se escribe.")
        elif ajenos := _codigos_ajenos(texto, permitidos, codigos_catalogo, numeros):
            r.descartado.append(f"«Por dónde entrar» nombraba un artículo que no es suyo ni de "
                                f"la lista ({', '.join(ajenos)}): no se escribe.")
        else:
            r.entrada = {"texto": texto, "citas": citas,
                         "productos": [{"id": p, "codart": productos[p].codart,
                                        "descripcion": productos[p].descripcion}
                                       for p in pedidos]}
    return r


def llamar(h: Hechos, *, modelo: str, api_key: str | None) -> tuple[Any, llm.Uso | None]:
    """Llama al modelo. Devuelve (el JSON, o None si no devolvió JSON; el uso).
    LLMNotConfiguredError sin clave; LLMError si falla la llamada."""
    if not api_key:
        raise llm.LLMNotConfiguredError("Falta la clave de la API de Anthropic.")
    raw = llm._invoke_claude(api_key=api_key, model=modelo, system_prompt=SISTEMA,
                             user_prompt=peticion(h), max_tokens=1200, timeout=90,
                             max_retries=1)
    uso = llm.tomar_ultimo_uso()
    try:
        return llm._parse_segment_json(raw), uso
    except (ValueError, llm.LLMError):
        return None, uso


def generar(h: Hechos, *, modelo: str, api_key: str | None,
            codigos_catalogo: set[str]) -> tuple[Resultado, llm.Uso | None]:
    """Llama y valida (para el script de comparar). Si no devolvió JSON,
    LLMUpstreamError: no hay nada que enseñar."""
    data, uso = llamar(h, modelo=modelo, api_key=api_key)
    if data is None:
        raise llm.LLMUpstreamError("La respuesta de la IA no era JSON.")
    return validar(data, h, codigos_catalogo=codigos_catalogo), uso
