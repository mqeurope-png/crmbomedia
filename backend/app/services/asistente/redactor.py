"""Pedirle al modelo el BORRADOR de un correo y guardarlo con su coste.

Regla primera, la que se comprueba en el código y en los tests: el cuerpo del
borrador es SIEMPRE un correo que se puede enviar tal cual al destinatario, o
está vacío. Todo lo que la IA quiera decirle a la persona de BoHub (que no hay
nada que contestar, que falta un dato, que no puede confirmar un precio) va en
`aviso`, que se enseña en la franja del asistente y nunca en el editor. El
prompt lo exige, y por si el modelo se despista, `parece_informe_al_usuario`
vacía el cuerpo y pasa el texto al aviso.

Es una conversación: el primer borrador (turno 0) monta el contexto entero
(hilo, expediente, base de conocimiento) y lo guarda tal cual en
`contexto_texto`; cada corrección («más corto», «en inglés») es un turno más
que manda ese mismo bloque —byte a byte, marcado como cacheable para la API—
más el borrador actual y la instrucción nueva. Así una corrección cuesta
céntimos y no otro borrador. El primer borrador usa el modelo grande; las
correcciones, el de Configuración ERP (Haiku por defecto). Si la persona
escribe qué quiere decir, esa instrucción manda sobre lo que se deduzca del
hilo; si no hay nada nuevo del cliente y no dice nada, no se llama al modelo.

Las demás reglas van en el prompt de sistema: redacta, no aporta hechos;
precios y plazos solo de los datos; sin promesas; enlaces solo de la lista; en
el idioma del mensaje; breve y con el tono de las plantillas. Aquí NO se envía
nada.
"""
from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.crm import Contact, EmailThread, User
from app.models.ia import (
    ALCANCE_CONTACTO,
    ALCANCES,
    ALCANCES_SIN_HILO,
    ESTADO_GENERADO,
    ESTADO_REGENERADO,
    PROPOSITO_ASISTENTE,
    PROPOSITO_CORRECCION,
    BorradorAsistente,
)
from app.services import llm
from app.services.asistente import contexto as ctx_mod
from app.services.asistente.config import configuracion as config_asistente
from app.services.asistente.config import estimar_tokens
from app.services.asistente.contexto import Contexto
from app.services.asistente.historico import ajustar_al_presupuesto
from app.services.asistente.texto import parece_informe_al_usuario
from app.services.ia.coste import coste_usd
from app.services.ia.modelos import parametros_de_llamada
from app.services.ia.uso import registrar_llamada

logger = logging.getLogger(__name__)

MAX_TOKENS_RESPUESTA = 1500
MAX_INSTRUCCION = 2000

NOMBRES_IDIOMA = {
    "es": "español", "en": "inglés", "de": "alemán", "fr": "francés", "nl": "neerlandés",
    "pt": "portugués", "it": "italiano", "ca": "catalán",
}

AVISO_SIN_NADA_QUE_CONTESTAR = (
    "El último mensaje del hilo es nuestro: no hay nada nuevo del cliente que contestar, así "
    "que no he redactado nada. Dime qué quieres decirle (un seguimiento, una pregunta, un "
    "aviso) y lo redacto con todo el histórico delante."
)

PROMPT_SISTEMA = (
    "Eres el asistente de respuesta del equipo comercial de BoHub (Bomedia, MQ Europe, "
    "Streamtec: impresoras UV, DTF, láser FLUX, vending y sus consumibles). Redactas el "
    "BORRADOR de un correo al cliente: la respuesta a su último mensaje o, si te dan una "
    "INSTRUCCIÓN DE QUIEN ENVÍA, el correo que te piden (una pregunta, un seguimiento, un "
    "aviso). Una persona lo revisa, lo corrige si hace falta y lo envía; tú no envías nada.\n\n"
    "REGLA PRIMERA, sin excepciones: el campo «cuerpo» es SIEMPRE un correo dirigido al "
    "destinatario que se puede enviar tal cual, o está vacío (\"\"). Nunca escribas en «cuerpo» "
    "nada dirigido a la persona de BoHub que te lo pide: ni «he revisado el hilo», ni lo que has "
    "hecho, ni lo que falta, ni preguntas sobre qué quiere. Todo eso va en «aviso». Si no "
    "tienes con qué redactar (no hay nada nuevo que contestar y no te han dado instrucción, o "
    "falta el dato clave), devuelve \"cuerpo\": \"\" y explica en «aviso» qué necesitas.\n\n"
    "Reglas, por este orden:\n"
    "1. Redactas; no aportas hechos. Precios, plazos, disponibilidad, condiciones y datos "
    "técnicos solo si están en los DATOS que te paso. Nunca de memoria ni por sentido común. "
    "Un precio o plazo que aparezca en un pedido o correo antiguo es histórico: si lo citas, "
    "di de cuándo es; no lo des como vigente.\n"
    "2. Si hay INSTRUCCIÓN DE QUIEN ENVÍA, manda sobre lo que deduzcas del hilo: el correo "
    "dice lo que la instrucción pide, con los datos del contexto, y nada más.\n"
    "3. Sin promesas: ni fechas de entrega, ni descuentos, ni compromisos que no estén en los "
    "datos.\n"
    "4. Enlaces: solo los de la lista ENLACES, copiados tal cual y solo si ayudan a lo que "
    "pregunta. Ningún otro, ninguno inventado.\n"
    "5. Idioma: el que te indico en IDIOMA, aunque la ficha o el hilo digan otro, salvo que la "
    "instrucción pida otro.\n"
    "6. Tono y longitud como las plantillas del equipo: cercano y profesional, saludo breve, "
    "respuesta directa, siguiente paso claro, despedida corta. Entre 60 y 180 palabras. "
    "Párrafos cortos separados por una línea en blanco. Si el DESTINATARIO es un compañero "
    "(una dirección del grupo), tono interno: directo, sin fórmulas comerciales.\n"
    "7. Sin bloque de firma (se añade después). Cierra con el nombre de pila de quien envía "
    "si te lo doy.\n"
    "8. Si algo no se puede responder con los datos, dilo con naturalidad en el texto («lo "
    "compruebo y te lo confirmo») y anótalo en «pendiente». No rellenes huecos.\n"
    "9. No repitas lo que el cliente ya sabe, no resumas el histórico, no te disculpes de "
    "más.\n"
    "10. Si te dan una CORRECCIÓN sobre un BORRADOR ACTUAL, reescríbelo aplicando solo esa "
    "corrección y conservando todo lo demás.\n\n"
    "Devuelve SOLO un JSON válido, sin texto alrededor ni markdown:\n"
    '{"asunto": "asunto del correo", "cuerpo": "el correo en texto plano, párrafos separados '
    'por una línea en blanco, o \\"\\" si no hay nada que enviar", "idioma": "código de dos '
    'letras del idioma del cuerpo", "aviso": "lo que tengas que decirle a la persona de BoHub, '
    'o \\"\\"", "pendiente": ["lo que no has podido responder con los datos"], '
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
    if ctx.thread is None:
        return (
            "SIN HILO: es un correo nuevo, no una respuesta. No hay mensaje del cliente al que "
            "contestar: lo que hay que decir está en la INSTRUCCIÓN DE QUIEN ENVÍA; el "
            "expediente y los correos anteriores son el contexto (qué compró, cuándo, de qué se "
            "habló).\n"
        )
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
            "(No hay ningún mensaje del cliente en el hilo: solo con una instrucción de quien "
            "envía hay algo que redactar.)")
    elif ctx.mensajes and ctx.mensajes[-1].lado == "nosotros":
        lineas.append(
            "(El último mensaje es nuestro: el cliente no ha escrito nada nuevo. Sin "
            "instrucción de quien envía no hay nada que contestar: cuerpo vacío y el motivo en "
            "«aviso».)")
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
            f"CORREOS DE OTRAS PERSONAS DE LA MISMA EMPRESA ({ctx.empresa or 'la empresa'}; no "
            "de quien te escribe hoy: no les atribuyas nada de esto, y no repitas precios, "
            "descuentos ni condiciones dadas a otra persona):")
        for e in ctx.empresa_asuntos:
            quien = e.get("quien") or "otra persona de la empresa"
            lineas.append(
                f"- {_fecha(e.get('fecha'))} · lo escribió {quien} · asunto: "
                f"{e.get('asunto') or '(sin asunto)'}")
            if e.get("texto"):
                lineas.append(f"  ({str(e.get('lado', '')).upper()} · {e.get('motivo')})")
                lineas.append(e["texto"])
    if ctx.recortes:
        lineas.append(
            f"(Por tamaño se dejaron fuera {len(ctx.recortes)} correos antiguos; no están aquí.)")
    return "\n".join(lineas) + "\n"


def _cabecera(ctx: Contexto) -> list[str]:
    idioma = ctx.idioma
    destinatario = ctx.destinatario or "—"
    if ctx.destinatario_interno:
        destinatario += (" (es una dirección del grupo: escribes a un COMPAÑERO, no a un "
                         "cliente; tono y contenido internos)")
    asunto = (
        f"ASUNTO PROPUESTO: {ctx.asunto_respuesta}." if ctx.asunto_respuesta
        else "ASUNTO: es un correo nuevo; propón tú el asunto en «asunto»."
    )
    return [
        f"IDIOMA DE LA RESPUESTA: {idioma} ({NOMBRES_IDIOMA.get(idioma, idioma)}); "
        f"detectado en {ctx.idioma_fuente}. Responde en ese idioma.",
        f"QUIÉN ENVÍA: {ctx.usuario.get('nombre') or '—'} (nombre de pila: "
        f"{ctx.usuario.get('nombre_pila') or '—'}), desde "
        f"{ctx.usuario.get('remitente') or '—'}.",
        f"DESTINATARIO: {destinatario}. {asunto}",
        f"WEB POR LA QUE LLEGÓ: {ctx.sitio or 'desconocida'}.",
    ]


def bloque_contexto(ctx: Contexto) -> str:
    """Lo que no cambia en toda la conversación: quién envía, destinatario,
    ficha, enlaces, ejemplo de tono, correos viejos y el hilo. Es el bloque que
    se marca como cacheable para la API y el que se guarda con el turno 0 para
    que las correcciones lo manden byte a byte, sin volver a montarlo."""
    partes = [*_cabecera(ctx), "", _bloque_ficha(ctx), _bloque_enlaces(ctx)]
    if ctx.ejemplo_estilo:
        partes.append("EJEMPLO DEL TONO Y LA LONGITUD DEL EQUIPO (una plantilla; no la copies, "
                      "no uses sus datos):\n" + ctx.ejemplo_estilo + "\n")
    historico = _bloque_historico(ctx)
    if historico:
        partes.append(historico)
    partes.append(_bloque_hilo(ctx))
    return "\n".join(partes)


def peticion_inicial(instruccion: str | None, *, sin_hilo: bool) -> str:
    """La parte que cambia del primer turno: la instrucción (si la hay) y la orden."""
    if instruccion:
        return (
            "INSTRUCCIÓN DE QUIEN ENVÍA (manda sobre lo que deduzcas del hilo): "
            f"«{instruccion}»\n\n"
            "Redacta ahora el correo que pide esa instrucción, con los datos del contexto. El "
            "cuerpo es el correo al destinatario, listo para enviar."
        )
    if sin_hilo:
        return "Sin instrucción no hay nada que redactar: cuerpo vacío y dilo en «aviso»."
    return (
        "Redacta ahora el borrador de la respuesta al último mensaje del cliente. El cuerpo es "
        "el correo al destinatario, listo para enviar; si no hay nada que contestar, cuerpo "
        "vacío y el motivo en «aviso»."
    )


def peticion_correccion(*, texto_actual: str, asunto_actual: str | None, instruccion: str) -> str:
    """La parte que cambia de un turno de corrección: el borrador actual y lo
    que hay que cambiarle. El contexto NO se repite aquí: va en el bloque
    cacheado."""
    return (
        f"BORRADOR ACTUAL (asunto: {asunto_actual or '—'}):\n{texto_actual}\n\n"
        f"CORRECCIÓN DE QUIEN ENVÍA: «{instruccion}»\n\n"
        "Reescribe el borrador aplicando SOLO esa corrección y conservando todo lo demás "
        "(hechos, enlaces, idioma salvo que la corrección lo cambie). Devuelve el JSON completo "
        "con el borrador entero, no solo el cambio."
    )


def prompt_usuario(ctx: Contexto) -> str:
    """El primer turno entero, como texto (contexto + petición)."""
    peticion = peticion_inicial(ctx.instruccion, sin_hilo=ctx.thread is None)
    return bloque_contexto(ctx) + "\n" + peticion


def mensaje_usuario(contexto_texto: str, peticion_texto: str) -> list[dict[str, Any]]:
    """El mensaje de usuario en dos bloques: el contexto, marcado como cacheable
    (la API lo cobra a una décima parte cuando vuelve igual), y la petición,
    que cambia en cada turno y se queda fuera de la caché."""
    return [
        {"type": "text", "text": contexto_texto, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": peticion_texto},
    ]


def _parsear(raw: str, *, asunto_defecto: str | None, idioma_defecto: str | None) -> dict[str, Any]:
    """El JSON del modelo; si no devolvió JSON, el texto entero es el cuerpo
    (y la regla primera decidirá si eso es un correo o un informe)."""
    try:
        data = llm._parse_segment_json(raw)
    except (ValueError, llm.LLMError):
        data = {}
    if not isinstance(data, dict) or not any(k in data for k in ("cuerpo", "aviso")):
        data = {"cuerpo": raw.strip(), "asunto": asunto_defecto, "idioma": idioma_defecto,
                "aviso": "", "pendiente": [], "enlaces_usados": []}
    pendiente = data.get("pendiente")
    data["pendiente"] = [str(p).strip() for p in pendiente if str(p or "").strip()] if isinstance(
        pendiente, list) else []
    usados = data.get("enlaces_usados")
    data["enlaces_usados"] = [str(u).strip() for u in usados if str(u or "").strip()] if isinstance(
        usados, list) else []
    data["asunto"] = str(data.get("asunto") or asunto_defecto or "").strip()[:500]
    idioma = str(data.get("idioma") or idioma_defecto or "").strip().lower()[:2]
    data["idioma"] = idioma or idioma_defecto
    data["cuerpo"] = str(data.get("cuerpo") or "").strip()
    data["aviso"] = str(data.get("aviso") or "").strip()
    if not data["cuerpo"] and not data["aviso"]:
        # Las dos vacías: la persona vería un editor vacío sin saber por qué.
        data["aviso"] = ("La IA no ha devuelto ningún correo ni ha dicho por qué. Dime qué "
                         "quieres decirle o vuelve a intentarlo.")
    return data


def _regla_primera(data: dict[str, Any], *, nombre_usuario: str | None,
                   nombre_destinatario: str | None) -> None:
    """El cuerpo es un correo o está vacío. Si lo que salió habla a la persona
    de BoHub, pasa al aviso y el cuerpo se queda vacío."""
    motivo = parece_informe_al_usuario(
        data["cuerpo"], nombre_usuario=nombre_usuario, nombre_destinatario=nombre_destinatario)
    if motivo is None:
        return
    informe = data["cuerpo"]
    data["cuerpo"] = ""
    nota = f"La IA no ha redactado un correo ({motivo}). Lo que dijo: «{informe}»"
    data["aviso"] = f"{data['aviso']} {nota}".strip() if data["aviso"] else nota


def _json(raw: str | None) -> dict[str, Any]:
    try:
        datos = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        datos = {}
    return datos if isinstance(datos, dict) else {}


def _rellenar(b: BorradorAsistente, data: dict[str, Any], uso: llm.Uso | None,
              modelo_pedido: str) -> None:
    b.texto = data["cuerpo"]
    b.asunto = (data["asunto"] or b.asunto or "")[:500] or None
    b.idioma = data["idioma"]
    b.aviso = data["aviso"] or None
    b.pendiente_json = json.dumps(data["pendiente"], ensure_ascii=False)
    contexto = _json(b.contexto_json)
    contexto["enlaces_usados"] = data["enlaces_usados"]
    b.contexto_json = json.dumps(contexto, ensure_ascii=False, default=str)
    nombre = (uso.modelo if uso else None) or modelo_pedido
    b.modelo = nombre[:80]
    b.tokens_entrada = uso.tokens_entrada if uso else 0
    b.tokens_salida = uso.tokens_salida if uso else 0
    b.tokens_cache_lectura = uso.tokens_cache_lectura if uso else 0
    b.tokens_cache_escritura = uso.tokens_cache_escritura if uso else 0
    b.coste_usd = coste_usd(nombre, b.tokens_entrada, b.tokens_salida, b.tokens_cache_lectura,
                            b.tokens_cache_escritura)


def _modelos(cfg: dict[str, Any], settings: Any) -> tuple[str, str]:
    """`(modelo del primer borrador, modelo de las correcciones)`."""
    return (
        cfg.get("modelo_borrador") or settings.anthropic_model,
        cfg.get("modelo_correccion") or settings.anthropic_model,
    )


def _llamar(settings: Any, *, modelo: str, contexto_texto: str, peticion_texto: str,
            cfg: dict[str, Any]) -> str:
    parametros = parametros_de_llamada(
        modelo, pensamiento_extendido=bool(cfg.get("pensamiento_extendido")))
    return llm._invoke_claude(
        api_key=settings.anthropic_api_key, model=modelo, system_prompt=PROMPT_SISTEMA,
        user_prompt=mensaje_usuario(contexto_texto, peticion_texto),
        max_tokens=MAX_TOKENS_RESPUESTA, parametros=parametros or None,
    )


def _cerrar_regenerado(session: Session, regenerar_de: str | None,
                       nuevo: BorradorAsistente) -> None:
    """Al pedir otro borrador en lugar del actual (cambio de alcance, «empezar
    de nuevo»), la conversación anterior entera queda como regenerada."""
    if not regenerar_de:
        return
    anterior = session.get(BorradorAsistente, regenerar_de)
    if anterior is None or anterior.usuario_id != nuevo.usuario_id:
        return
    if anterior.thread_id != nuevo.thread_id:
        return
    from app.services.asistente.borradores import turnos_de  # noqa: PLC0415

    for turno in turnos_de(session, anterior):
        if turno.estado == ESTADO_GENERADO:
            turno.estado = ESTADO_REGENERADO
            turno.resuelto_en = nuevo.created_at


def generar(
    session: Session, *, thread: EmailThread | None, usuario: User,
    alcance: str = ALCANCE_CONTACTO, instruccion: str | None = None,
    destinatario: str | None = None, contacto: Contact | None = None,
    regenerar_de: str | None = None,
) -> BorradorAsistente:
    """El primer turno: monta el contexto, pide el borrador (si hay con qué),
    lo guarda con su coste y lo devuelve (ya en la sesión, sin commit). Sin
    hilo (`thread=None`) es un correo nuevo: hace falta `destinatario` e
    `instruccion`. Lanza `llm.LLMError` si la IA no está configurada o no
    responde, y `ValueError` si falta algo."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise llm.LLMNotConfiguredError("La IA no está configurada (ANTHROPIC_API_KEY).")
    instruccion = (instruccion or "").strip()[:MAX_INSTRUCCION] or None
    if thread is None:
        if alcance not in ALCANCES_SIN_HILO:
            raise ValueError(
                f"Sin hilo el alcance tiene que ser {' o '.join(ALCANCES_SIN_HILO)}.")
        if not (destinatario or "").strip():
            raise ValueError("Sin hilo hace falta un destinatario en «Para».")
        if not instruccion:
            raise ValueError(
                "Dime qué quieres decirle: en un correo nuevo no hay nada de lo que deducir la "
                "intención.")
    elif alcance not in ALCANCES:
        raise ValueError(f"Alcance desconocido: {alcance!r}.")
    ctx = ctx_mod.construir(
        session, thread=thread, usuario=usuario, alcance=alcance, destinatario=destinatario,
        contacto=contacto, instruccion=instruccion,
    )
    cfg = config_asistente(session)
    modelo_borrador, _ = _modelos(cfg, settings)
    peticion_texto = peticion_inicial(instruccion, sin_hilo=thread is None)
    # El presupuesto de tokens de entrada: se recorta por abajo (empresa y
    # correos viejos, lo más antiguo primero) hasta que el prompt cabe. El
    # hilo y el expediente no se tocan.
    ctx.tokens_estimados = ajustar_al_presupuesto(
        ctx, presupuesto=ctx.presupuesto_tokens,
        medir=lambda c: (estimar_tokens(PROMPT_SISTEMA) + estimar_tokens(bloque_contexto(c))
                         + estimar_tokens(peticion_texto)),
    )
    contexto_texto = bloque_contexto(ctx)
    borrador_id = str(uuid4())
    borrador = BorradorAsistente(
        id=borrador_id, conversacion_id=borrador_id, turno=0, instruccion=instruccion,
        contexto_texto=contexto_texto,
        thread_id=thread.id if thread is not None else None,
        message_id=ctx.ultimo_del_cliente.id if ctx.ultimo_del_cliente else None,
        contact_id=(ctx.contacto or {}).get("id") or (thread.contact_id if thread else None),
        usuario_id=usuario.id, idioma=ctx.idioma, modelo=modelo_borrador[:80],
        destinatario=ctx.destinatario, remitente=ctx.usuario.get("remitente"),
        asunto=ctx.asunto_respuesta or None, texto="",
        pendiente_json="[]", enlaces_json=json.dumps(ctx.enlaces_lista(), ensure_ascii=False),
        contexto_json=json.dumps(ctx.resumen(), ensure_ascii=False, default=str),
        alcance=alcance, mensajes_hilo=len(ctx.mensajes),
        mensajes_historico=len(ctx.historico) + sum(
            1 for e in ctx.empresa_asuntos if e.get("texto")),
        estado=ESTADO_GENERADO,
    )
    # Nada nuevo del cliente y sin instrucción: no hay con qué redactar. No se
    # llama al modelo (no cuesta nada) y el motivo va en el aviso. El contexto
    # queda guardado: la instrucción que venga después lo reutiliza.
    nada_que_contestar = thread is not None and not instruccion and (
        ctx.ultimo_del_cliente is None or ctx.mensajes[-1].lado == "nosotros")
    if nada_que_contestar:
        borrador.aviso = AVISO_SIN_NADA_QUE_CONTESTAR
        session.add(borrador)
        session.flush()
        _cerrar_regenerado(session, regenerar_de, borrador)
        session.flush()
        logger.info("asistente: sin nada que contestar hilo=%s usuario=%s (sin llamada)",
                    thread.id, usuario.id)
        return borrador
    try:
        raw = _llamar(settings, modelo=modelo_borrador, contexto_texto=contexto_texto,
                      peticion_texto=peticion_texto, cfg=cfg)
    except llm.LLMError as exc:
        uso = llm.tomar_ultimo_uso()
        registrar_llamada(
            session, proposito=PROPOSITO_ASISTENTE, modelo=modelo_borrador, uso=uso,
            usuario_id=usuario.id,
            referencia_tipo="email_thread" if thread is not None else "destinatario",
            referencia_id=thread.id if thread is not None else ctx.destinatario,
            ok=False, error=f"{type(exc).__name__}: {exc}",
        )
        raise
    uso = llm.tomar_ultimo_uso()
    data = _parsear(raw, asunto_defecto=ctx.asunto_respuesta, idioma_defecto=ctx.idioma)
    _regla_primera(data, nombre_usuario=ctx.usuario.get("nombre_pila"),
                   nombre_destinatario=(ctx.contacto or {}).get("nombre"))
    _rellenar(borrador, data, uso, modelo_borrador)
    session.add(borrador)
    session.flush()
    llamada = registrar_llamada(
        session, proposito=PROPOSITO_ASISTENTE, modelo=borrador.modelo, uso=uso,
        usuario_id=usuario.id, referencia_tipo="ia_borrador", referencia_id=borrador.id,
    )
    borrador.llamada_id = llamada.id
    _cerrar_regenerado(session, regenerar_de, borrador)
    session.flush()
    logger.info(
        "asistente: borrador %s hilo=%s usuario=%s idioma=%s modelo=%s tokens=%d/%d "
        "cache=%d/%d coste=%.5f",
        borrador.id, borrador.thread_id, usuario.id, borrador.idioma, borrador.modelo,
        borrador.tokens_entrada, borrador.tokens_salida, borrador.tokens_cache_lectura,
        borrador.tokens_cache_escritura, borrador.coste_usd,
    )
    return borrador


def corregir(
    session: Session, *, borrador: BorradorAsistente, instruccion: str, usuario: User,
) -> BorradorAsistente:
    """Un turno más de la conversación: el mismo bloque de contexto guardado
    (cacheable), el borrador actual y la instrucción nueva. Si el borrador
    actual está vacío (no había nada que contestar), la instrucción es el
    primer borrador de verdad y va con el modelo del primer borrador; si no,
    es una corrección y va con el modelo de las correcciones. El borrador que
    se corrige queda como regenerado."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise llm.LLMNotConfiguredError("La IA no está configurada (ANTHROPIC_API_KEY).")
    instruccion = (instruccion or "").strip()[:MAX_INSTRUCCION]
    if not instruccion:
        raise ValueError("Escribe qué quieres cambiar o decir.")
    raiz = borrador
    if borrador.conversacion_id and borrador.conversacion_id != borrador.id:
        raiz = session.get(BorradorAsistente, borrador.conversacion_id) or borrador
    contexto_texto = raiz.contexto_texto or borrador.contexto_texto
    if not contexto_texto:
        raise ValueError(
            "Este borrador no guarda su contexto (es anterior a la conversación): pide uno nuevo.")
    cfg = config_asistente(session)
    modelo_borrador, modelo_correccion = _modelos(cfg, settings)
    primera_redaccion = not (borrador.texto or "").strip()
    if primera_redaccion:
        modelo, proposito = modelo_borrador, PROPOSITO_ASISTENTE
        peticion_texto = peticion_inicial(instruccion, sin_hilo=borrador.thread_id is None)
    else:
        modelo, proposito = modelo_correccion, PROPOSITO_CORRECCION
        peticion_texto = peticion_correccion(
            texto_actual=borrador.texto, asunto_actual=borrador.asunto, instruccion=instruccion)
    try:
        raw = _llamar(settings, modelo=modelo, contexto_texto=contexto_texto,
                      peticion_texto=peticion_texto, cfg=cfg)
    except llm.LLMError as exc:
        uso = llm.tomar_ultimo_uso()
        registrar_llamada(
            session, proposito=proposito, modelo=modelo, uso=uso, usuario_id=usuario.id,
            referencia_tipo="ia_borrador", referencia_id=borrador.id, ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    uso = llm.tomar_ultimo_uso()
    data = _parsear(raw, asunto_defecto=borrador.asunto, idioma_defecto=borrador.idioma)
    contexto = _json(borrador.contexto_json)
    nombre_usuario = (usuario.full_name or usuario.email or "").strip()
    _regla_primera(data, nombre_usuario=nombre_usuario,
                   nombre_destinatario=(contexto.get("contacto") or {}).get("nombre"))
    nuevo = BorradorAsistente(
        id=str(uuid4()), conversacion_id=borrador.conversacion_id or borrador.id,
        turno=(borrador.turno or 0) + 1, padre_id=borrador.id, instruccion=instruccion,
        thread_id=borrador.thread_id, message_id=borrador.message_id,
        contact_id=borrador.contact_id, usuario_id=usuario.id, idioma=borrador.idioma,
        modelo=modelo[:80], destinatario=borrador.destinatario, remitente=borrador.remitente,
        asunto=borrador.asunto, texto="", enlaces_json=borrador.enlaces_json,
        contexto_json=borrador.contexto_json, alcance=borrador.alcance,
        mensajes_hilo=borrador.mensajes_hilo, mensajes_historico=borrador.mensajes_historico,
        estado=ESTADO_GENERADO,
    )
    _rellenar(nuevo, data, uso, modelo)
    session.add(nuevo)
    session.flush()
    llamada = registrar_llamada(
        session, proposito=proposito, modelo=nuevo.modelo, uso=uso, usuario_id=usuario.id,
        referencia_tipo="ia_borrador", referencia_id=nuevo.id,
    )
    nuevo.llamada_id = llamada.id
    if borrador.estado == ESTADO_GENERADO:
        borrador.estado = ESTADO_REGENERADO
        borrador.resuelto_en = nuevo.created_at
    session.flush()
    logger.info(
        "asistente: turno %d de %s (%s) usuario=%s modelo=%s tokens=%d/%d cache=%d/%d coste=%.5f",
        nuevo.turno, nuevo.conversacion_id, proposito, usuario.id, nuevo.modelo,
        nuevo.tokens_entrada, nuevo.tokens_salida, nuevo.tokens_cache_lectura,
        nuevo.tokens_cache_escritura, nuevo.coste_usd,
    )
    return nuevo
