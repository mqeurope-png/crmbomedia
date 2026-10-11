"""Pedirle al modelo el BORRADOR de la respuesta y guardarlo con su coste.

Las reglas van en el prompt de sistema: redacta, no aporta hechos; precios,
plazos y disponibilidad solo si están en los datos (y un precio viejo no es
el de hoy); sin promesas; enlaces solo de la lista, tal cual; en el idioma del
mensaje; breve y con el tono de las plantillas del equipo; y que diga lo que
no puede responder. Reutiliza el cliente de Claude de BoHub (`llm._invoke_claude`,
que ya sabe saltar los bloques de razonamiento) y anota la llamada en
`ia_llamadas`. Aquí NO se envía nada.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.crm import EmailThread, User
from app.models.ia import (
    ALCANCE_CONTACTO,
    ALCANCES,
    ESTADO_GENERADO,
    ESTADO_REGENERADO,
    PROPOSITO_ASISTENTE,
    BorradorAsistente,
)
from app.services import llm
from app.services.asistente import contexto as ctx_mod
from app.services.asistente.contexto import Contexto
from app.services.ia.coste import coste_usd
from app.services.ia.uso import registrar_llamada

logger = logging.getLogger(__name__)

MAX_TOKENS_RESPUESTA = 1500

NOMBRES_IDIOMA = {
    "es": "español", "en": "inglés", "de": "alemán", "fr": "francés", "nl": "neerlandés",
    "pt": "portugués", "it": "italiano", "ca": "catalán",
}

PROMPT_SISTEMA = (
    "Eres el asistente de respuesta del equipo comercial de BoHub (Bomedia, MQ Europe, "
    "Streamtec: impresoras UV, DTF, láser FLUX, vending y sus consumibles). Redactas el "
    "BORRADOR de la respuesta a un correo de un cliente. Una persona lo revisa, lo corrige si "
    "hace falta y lo envía; tú no envías nada.\n\n"
    "Reglas, por este orden:\n"
    "1. Redactas; no aportas hechos. Precios, plazos, disponibilidad, condiciones y datos "
    "técnicos solo si están en los DATOS que te paso. Nunca de memoria ni por sentido común. "
    "Un precio o plazo que aparezca en un pedido o correo antiguo es histórico: si lo citas, "
    "di de cuándo es; no lo des como vigente.\n"
    "2. Sin promesas: ni fechas de entrega, ni descuentos, ni compromisos que no estén en los "
    "datos.\n"
    "3. Enlaces: solo los de la lista ENLACES, copiados tal cual y solo si ayudan a lo que "
    "pregunta. Ningún otro, ninguno inventado.\n"
    "4. Idioma: el que te indico en IDIOMA (el del último mensaje del cliente), aunque la "
    "ficha o el hilo digan otro.\n"
    "5. Tono y longitud como las plantillas del equipo: cercano y profesional, saludo breve, "
    "respuesta directa a lo que pregunta, siguiente paso claro, despedida corta. Entre 60 y "
    "180 palabras. Párrafos cortos separados por una línea en blanco.\n"
    "6. Sin bloque de firma (se añade después). Cierra con el nombre de pila de quien envía "
    "si te lo doy.\n"
    "7. Si algo no se puede responder con los datos, dilo con naturalidad en el texto («lo "
    "compruebo y te lo confirmo») y anótalo en «pendiente». No rellenes huecos.\n"
    "8. No repitas lo que el cliente ya sabe, no resumas el histórico, no te disculpes de "
    "más.\n\n"
    "Devuelve SOLO un JSON válido, sin texto alrededor ni markdown:\n"
    '{"asunto": "asunto de la respuesta", "cuerpo": "el borrador en texto plano, párrafos '
    'separados por una línea en blanco", "idioma": "código de dos letras del idioma del '
    'cuerpo", "pendiente": ["lo que no has podido responder con los datos"], '
    '"enlaces_usados": ["las URL de la lista que has puesto en el cuerpo"]}'
)


def _fecha(valor: str | None) -> str:
    return (valor or "")[:16].replace("T", " ") or "sin fecha"


def _bloque_ficha(ctx: Contexto) -> str:
    if ctx.contacto is None:
        return (
            "FICHA: remitente desconocido. No hay ficha de contacto en BoHub: no sabes quién es, "
            "qué compró ni qué se le ofreció. Trabaja solo con el hilo y no supongas nada.\n"
        )
    c = ctx.contacto
    lineas = [
        "FICHA DEL CONTACTO (datos de BoHub):",
        f"- Nombre: {c.get('nombre') or '—'} · Email: {c.get('email') or '—'}"
        + (f" · Cargo: {c['cargo']}" if c.get("cargo") else ""),
        f"- Empresa: {c.get('empresa') or '— (sin empresa asignada)'}"
        + (f" · País: {c['pais']}" if c.get("pais") else ""),
        f"- Idioma de la ficha: {c.get('idioma') or '—'} · Propietario: "
        f"{c.get('propietario') or '—'} · Estado comercial: {c.get('estado_comercial') or '—'}",
    ]
    if ctx.etapa:
        lineas.append(f"- Pipeline: {ctx.etapa}")
    if ctx.clasificacion:
        cl = ctx.clasificacion
        intereses = ", ".join(cl.get("intereses") or []) or "—"
        lineas.append(
            f"- Clasificación de la IA del lead: interés {intereses}"
            f" · idioma {cl.get('idioma') or '—'} · confianza {cl.get('confianza')}"
        )
    if ctx.pedidos:
        lineas.append("- Pedidos (del más reciente al más antiguo):")
        for p in ctx.pedidos:
            productos = "; ".join(
                f"{ln['descripcion']} ×{ln['cantidad']:g}" for ln in p["lineas"]) or "sin líneas"
            estado = "CANCELADO" if p["cancelado"] else f"pago {p['pago']}"
            lineas.append(
                f"  · Pedido {p['numero']} ({p['fecha'] or 'sin fecha'}, {p['total']:.2f} "
                f"{p['moneda']}, {estado}): {productos}"
            )
    else:
        lineas.append("- Pedidos: ninguno en BoHub.")
    if ctx.maquina:
        lineas.append(f"- Máquina / producto principal según sus pedidos: {ctx.maquina}"
                      + (f" (modelo FLUX: {ctx.modelo_flux})" if ctx.modelo_flux else ""))
    if ctx.facturas_pendientes:
        lineas.append("- Pendiente de cobro: " + "; ".join(
            f"pedido {f['numero']} ({f['fecha']}, {f['total']:.2f} {f['moneda']}, {f['pago']})"
            for f in ctx.facturas_pendientes))
    else:
        lineas.append("- Pendiente de cobro: nada.")
    if ctx.asuntos_recientes:
        lineas.append("- Últimos asuntos tratados con este contacto (solo títulos):")
        for a in ctx.asuntos_recientes:
            quien = "nosotros" if a["lado"] == "nosotros" else "el cliente"
            lineas.append(
                f"  · {a['fecha'] or 'sin fecha'} · último mensaje de {quien} · {a['asunto']}")
    return "\n".join(lineas) + "\n"


def _bloque_enlaces(ctx: Contexto) -> str:
    lista = ctx.enlaces_lista()
    if not lista:
        return "ENLACES: ninguno disponible. No pongas ningún enlace.\n"
    lineas = ["ENLACES (los únicos que puedes usar, tal cual):"]
    for e in lista:
        lineas.append(f"- {e['url']} — {e['etiqueta']}")
    bloque = ctx.enlaces.get("flux") or {}
    if bloque:
        intencion = bloque.get("intencion")
        if intencion == "kb":
            lineas.append("  (Cliente de FLUX que pregunta cómo se hace algo: el enlace que "
                          "toca es la KB de FLUX.)")
        elif intencion == "soporte":
            lineas.append("  (Cliente de FLUX con una avería, repuesto o garantía: el enlace "
                          "que toca es nuestro soporte, no la KB.)")
        else:
            lineas.append("  (Cliente de FLUX; no está claro qué pide: si hace falta un "
                          "enlace, el de nuestro soporte.)")
    return "\n".join(lineas) + "\n"


def _bloque_hilo(ctx: Contexto) -> str:
    lineas = [f"HILO ({len(ctx.mensajes)} mensajes, del más antiguo al más reciente; "
              "«nosotros» es el equipo, «cliente» la otra parte):"]
    for i, m in enumerate(ctx.mensajes, 1):
        lineas.append(
            f"--- Mensaje {i} · {_fecha(m.fecha.isoformat() if m.fecha else None)} · "
            f"{m.lado.upper()} · de {m.de} · para {', '.join(m.para) or '—'} · "
            f"asunto: {m.asunto}"
        )
        lineas.append(m.texto or "(sin texto)")
    if ctx.ultimo_del_cliente is None:
        lineas.append(
            "(No hay ningún mensaje del cliente en el hilo: responde al último mensaje.)")
    return "\n".join(lineas) + "\n"


def _bloque_historico(ctx: Contexto) -> str:
    if not ctx.historico and not ctx.empresa_asuntos:
        return ""
    lineas = []
    if ctx.historico:
        lineas.append(
            f"CORREOS ANTERIORES DEL CONTACTO QUE VIENEN A CUENTO ({len(ctx.historico)}):")
        for h in ctx.historico:
            lineas.append(
                f"--- {_fecha(h.get('fecha'))} · {str(h.get('lado', '')).upper()} · asunto: "
                f"{h.get('asunto') or '(sin asunto)'} · por qué entra: {h.get('motivo')}")
            lineas.append(h.get("texto") or "(sin texto)")
    if ctx.empresa_asuntos:
        lineas.append(
            "CORREOS DE OTRAS PERSONAS DE LA MISMA EMPRESA (no de quien te escribe hoy; no les "
            "atribuyas nada de esto, y no repitas precios ni condiciones dadas a otra persona):")
        for e in ctx.empresa_asuntos:
            quien = e.get("quien") or "otra persona de la empresa"
            lineas.append(
                f"- {_fecha(e.get('fecha'))} · lo escribió {quien} · asunto: "
                f"{e.get('asunto') or '(sin asunto)'}")
            if e.get("texto"):
                lineas.append(e["texto"])
    return "\n".join(lineas) + "\n"


def prompt_usuario(ctx: Contexto) -> str:
    idioma = ctx.idioma
    partes = [
        f"IDIOMA DE LA RESPUESTA: {idioma} ({NOMBRES_IDIOMA.get(idioma, idioma)}); "
        f"detectado en {ctx.idioma_fuente}. Responde en ese idioma.",
        f"QUIÉN ENVÍA: {ctx.usuario.get('nombre') or '—'} (nombre de pila: "
        f"{ctx.usuario.get('nombre_pila') or '—'}), desde "
        f"{ctx.usuario.get('remitente') or '—'}.",
        f"DESTINATARIO: {ctx.destinatario or '—'}. "
        f"ASUNTO PROPUESTO: {ctx.asunto_respuesta or '—'}.",
        f"WEB POR LA QUE LLEGÓ: {ctx.sitio or 'desconocida'}.",
        "",
        _bloque_ficha(ctx),
        _bloque_enlaces(ctx),
    ]
    if ctx.ejemplo_estilo:
        partes.append("EJEMPLO DEL TONO Y LA LONGITUD DEL EQUIPO (una plantilla; no la copies, "
                      "no uses sus datos):\n" + ctx.ejemplo_estilo + "\n")
    historico = _bloque_historico(ctx)
    if historico:
        partes.append(historico)
    partes.append(_bloque_hilo(ctx))
    partes.append("Redacta ahora el borrador de la respuesta al último mensaje del cliente.")
    return "\n".join(partes)


def _parsear(raw: str, ctx: Contexto) -> dict[str, Any]:
    """El JSON del modelo; si no devolvió JSON, el texto entero es el cuerpo."""
    try:
        data = llm._parse_segment_json(raw)
    except (ValueError, llm.LLMError):
        data = {}
    if not isinstance(data, dict) or not str(data.get("cuerpo") or "").strip():
        data = {"cuerpo": raw.strip(), "asunto": ctx.asunto_respuesta, "idioma": ctx.idioma,
                "pendiente": [], "enlaces_usados": []}
    pendiente = data.get("pendiente")
    data["pendiente"] = [str(p).strip() for p in pendiente if str(p or "").strip()] if isinstance(
        pendiente, list) else []
    usados = data.get("enlaces_usados")
    data["enlaces_usados"] = [str(u).strip() for u in usados if str(u or "").strip()] if isinstance(
        usados, list) else []
    data["asunto"] = str(data.get("asunto") or ctx.asunto_respuesta or "").strip()[:500]
    idioma = str(data.get("idioma") or ctx.idioma).strip().lower()[:2]
    data["idioma"] = idioma or ctx.idioma
    data["cuerpo"] = str(data.get("cuerpo") or "").strip()
    return data


def generar(
    session: Session, *, thread: EmailThread, usuario: User, alcance: str = ALCANCE_CONTACTO,
    regenerar_de: str | None = None,
) -> BorradorAsistente:
    """Monta el contexto, pide el borrador, lo guarda con su coste y lo
    devuelve (ya en la sesión, sin commit). Lanza `llm.LLMError` si la IA no
    está configurada o no responde."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise llm.LLMNotConfiguredError("La IA no está configurada (ANTHROPIC_API_KEY).")
    if alcance not in ALCANCES:
        raise ValueError(f"Alcance desconocido: {alcance!r}.")
    ctx = ctx_mod.construir(session, thread=thread, usuario=usuario, alcance=alcance)
    sistema = PROMPT_SISTEMA
    prompt = prompt_usuario(ctx)
    try:
        raw = llm._invoke_claude(
            api_key=settings.anthropic_api_key, model=settings.anthropic_model,
            system_prompt=sistema, user_prompt=prompt, max_tokens=MAX_TOKENS_RESPUESTA,
        )
    except llm.LLMError as exc:
        uso = llm.tomar_ultimo_uso()
        registrar_llamada(
            session, proposito=PROPOSITO_ASISTENTE, modelo=settings.anthropic_model, uso=uso,
            usuario_id=usuario.id, referencia_tipo="email_thread", referencia_id=thread.id,
            ok=False, error=f"{type(exc).__name__}: {exc}",
        )
        raise
    uso = llm.tomar_ultimo_uso()
    data = _parsear(raw, ctx)
    modelo = (uso.modelo if uso else None) or settings.anthropic_model
    entrada = uso.tokens_entrada if uso else 0
    salida = uso.tokens_salida if uso else 0
    borrador = BorradorAsistente(
        thread_id=thread.id,
        message_id=ctx.ultimo_del_cliente.id if ctx.ultimo_del_cliente else None,
        contact_id=(ctx.contacto or {}).get("id") or thread.contact_id, usuario_id=usuario.id,
        idioma=data["idioma"], modelo=modelo[:80], tokens_entrada=entrada, tokens_salida=salida,
        coste_usd=coste_usd(modelo, entrada, salida), destinatario=ctx.destinatario,
        remitente=ctx.usuario.get("remitente"), asunto=data["asunto"], texto=data["cuerpo"],
        pendiente_json=json.dumps(data["pendiente"], ensure_ascii=False),
        enlaces_json=json.dumps(ctx.enlaces_lista(), ensure_ascii=False),
        contexto_json=json.dumps(
            {**ctx.resumen(), "enlaces_usados": data["enlaces_usados"]}, ensure_ascii=False,
            default=str),
        alcance=alcance, mensajes_hilo=len(ctx.mensajes), mensajes_historico=len(ctx.historico),
        estado=ESTADO_GENERADO,
    )
    session.add(borrador)
    session.flush()
    llamada = registrar_llamada(
        session, proposito=PROPOSITO_ASISTENTE, modelo=modelo, uso=uso, usuario_id=usuario.id,
        referencia_tipo="ia_borrador", referencia_id=borrador.id,
    )
    borrador.llamada_id = llamada.id
    if regenerar_de:
        anterior = session.get(BorradorAsistente, regenerar_de)
        if anterior is not None and anterior.thread_id == thread.id and anterior.estado in (
                ESTADO_GENERADO,):
            anterior.estado = ESTADO_REGENERADO
            anterior.resuelto_en = borrador.created_at
    session.flush()
    logger.info(
        "asistente: borrador %s hilo=%s usuario=%s idioma=%s modelo=%s tokens=%d/%d coste=%.5f",
        borrador.id, thread.id, usuario.id, borrador.idioma, modelo, entrada, salida,
        borrador.coste_usd,
    )
    return borrador
