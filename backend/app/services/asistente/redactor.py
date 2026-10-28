"""Pedirle al modelo el BORRADOR de un correo y guardarlo con su coste.

Regla primera, la que se comprueba en el código y en los tests: el cuerpo del
borrador es SIEMPRE un correo que se puede enviar tal cual al destinatario, o
está vacío. Todo lo que la IA quiera decirle a la persona de BoHub (que no hay
nada que contestar, que falta un dato, que no puede confirmar un precio) va en
`aviso`, que se enseña en la franja del asistente y nunca en el editor. El
prompt lo exige, y por si el modelo se despista, la guardia
(`texto.evaluar_cuerpo`) vacía el cuerpo cuando está claro que habla a la
persona de BoHub y, ante la duda, lo deja y avisa (no borra).

Es una conversación: el primer borrador (turno 0) monta el contexto entero
(hilo, expediente, base de conocimiento) y lo guarda tal cual en
`contexto_texto`; cada corrección («más corto», «en inglés») es un turno más
que manda ese mismo bloque —byte a byte, marcado como cacheable para la API—
más el texto actual y la instrucción nueva. Así una corrección cuesta
céntimos y no otro borrador. El primer borrador usa el modelo grande; las
correcciones, el de Configuración ERP (Haiku por defecto). Si la persona
escribe qué quiere decir, esa instrucción manda sobre lo que se deduzca del
hilo; si no hay nada nuevo del cliente y no dice nada, no se llama al modelo.

La IA corrige siempre lo que hay AHORA en el editor (`texto_actual_html`):
el borrador tal cual lo dejó la persona después de retocarlo, un correo
escrito enteramente a mano (entonces el turno 0 ya es una corrección, con el
modelo de las correcciones) o un borrador guardado reabierto. Con una
selección (`seleccion_html`) la corrección se aplica solo a ese fragmento y el
resto no se toca ni un carácter. El borrador sale en HTML sencillo (las
etiquetas que admite el editor; `texto.limpiar_html`), y `texto` guarda su
texto plano para la guardia del cuerpo y la comparación de lo enviado.

Las demás reglas van en el prompt de sistema: redacta, no aporta hechos;
precios y plazos solo de los datos; sin promesas; enlaces solo de la lista; en
el idioma del mensaje; breve y con el tono de las plantillas. Aquí NO se envía
nada.
"""
from __future__ import annotations

import html as html_mod
import json
import logging
import re
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.conocimiento import FUENTE_COMPOSITOR
from app.models.crm import AuditLog, Contact, EmailThread, User
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
from app.services import empresas_grupo as eg
from app.services import llm
from app.services.asistente import (
    borradores,
    catalogo_pedido,
    consulta_fuentes,
    material_fuera,
    procedencia,
)
from app.services.asistente import contexto as ctx_mod
from app.services.asistente.config import configuracion as config_asistente
from app.services.asistente.config import estimar_tokens
from app.services.asistente.contexto import Contexto
from app.services.asistente.historico import ajustar_al_presupuesto
from app.services.asistente.texto import (
    NIVEL_CORREO,
    NIVEL_DUDOSO,
    ImagenesEditor,
    a_html,
    apartar_imagenes,
    cuerpo_a_html,
    empalmar,
    encajar_fragmento,
    enlaces_de,
    evaluar_cuerpo,
    html_a_texto,
    limpiar_html,
    parece_html,
    recortar,
    restaurar_imagenes,
    sin_firma,
    sin_marcas_de_imagen,
)
from app.services.conocimiento import catalogo as catalogo_kb
from app.services.correo_compuesto import bloques as bq
from app.services.correo_compuesto import datos as datos_compuesto
from app.services.correo_compuesto import generador as generador_correo
from app.services.correo_compuesto import ia as ia_compuesto
from app.services.ia.coste import coste_usd
from app.services.ia.modelos import parametros_de_llamada
from app.services.ia.uso import registrar_llamada
from app.services.web_forms import sitios as registro_sitios

logger = logging.getLogger(__name__)

MAX_TOKENS_RESPUESTA = 2500
MAX_INSTRUCCION = 2000
#: Lo que hay en el editor viaja con la petición de corrección; un correo
#: normal son unos pocos KB, esto es un tope contra un pegado monstruoso.
MAX_TEXTO_EDITOR = 200_000
#: Un turno sobre una selección guarda el cuerpo entero con el fragmento
#: empalmado (para volver a ese turno); más largo que esto, solo el fragmento
#: (el compositor lo pone en la selección). `texto` es TEXT (64 KB) en MySQL.
MAX_HTML_EMPALMADO = 30_000
#: La selección tal cual, para que el compositor solo sustituya ESA selección.
MAX_SELECCION_GUARDADA = 10_000

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
    "Eres el asistente de respuesta del equipo comercial de BoHub (Streamtec y MQ Europe: "
    "impresoras UV, DTF, láser FLUX, vending y sus consumibles). Redactas el "
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
    "Los precios de productos salen SOLO del bloque CATÁLOGO: copiados tal cual están escritos "
    "(mismo texto y misma moneda, nunca convertidos), cada producto con su enlace, y el correo "
    "lleva las frases de IVA, VIES y «no es una factura» que indica el bloque, en el idioma del "
    "correo; un precio marcado ORIENTATIVO lleva además su frase. Si el CATÁLOGO no tiene "
    "producto para lo que piden, no cites ningún precio ni nombres máquinas que no estén: di que "
    "se lo preparas en una oferta. Un precio o plazo que aparezca en un pedido, una nota interna, "
    "un campo de la ficha o un correo antiguo es histórico: si lo citas, di de cuándo es; no lo "
    "des como vigente.\n"
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
    "(una dirección del grupo), tono interno: directo, sin fórmulas comerciales. Te diriges "
    "SIEMPRE al destinatario en segunda persona y con el mismo trato en todo el correo: nunca "
    "hablas de él en tercera persona. En castellano, de tú: «el presupuesto que ya te envió "
    "Manel», no «que ya le envió Manel»; «tu pedido», no «su pedido». En los demás idiomas, el "
    "trato habitual de un correo comercial (Sie, vous, u…), igual de principio a fin.\n"
    "7. Sin bloque de firma (se añade después). Cierra con el nombre de pila de quien envía "
    "si te lo doy.\n"
    "8. Si algo no se puede responder con los datos, dilo con naturalidad en el texto («lo "
    "compruebo y te lo confirmo») y anótalo en «pendiente». No rellenes huecos. Pero un "
    "producto que el cliente nombra de otra forma y que está claramente en el CATÁLOGO (misma "
    "marca y modelo, otro nombre) no es un hueco: es ese, con el nombre del catálogo.\n"
    "9. No repitas lo que el cliente ya sabe, no resumas el histórico, no te disculpes de "
    "más.\n"
    "10. Si te dan una CORRECCIÓN sobre un BORRADOR ACTUAL o sobre el TEXTO ACTUAL DEL EDITOR, "
    "reescríbelo aplicando solo esa corrección y conservando todo lo demás, incluido el formato "
    "(listas, negritas, enlaces). Si te dan una SELECCIÓN, reescribe solo ese fragmento y "
    "devuelve en «cuerpo» únicamente el fragmento reescrito, con el mismo tipo de etiquetas que "
    "tenía; el resto del correo no se toca.\n"
    "11. La empresa es la que te indico en EMPRESA. Si el correo nombra a nuestra empresa (al "
    "presentarte, en la despedida, «el equipo de…»), usa esa y ninguna otra. NUNCA escribas "
    "«Bomedia» ni en el cuerpo ni en el asunto: esa marca ya no existe. Aunque aparezca en el "
    "hilo, en correos antiguos, en una plantilla, en una nota o en la ficha, no la copies, ni "
    "su firma ni su nombre de empresa.\n\n"
    "FORMATO del «cuerpo»: HTML sencillo con SOLO estas etiquetas: <p>, <br>, <strong>, <em>, "
    "<u>, <h2>, <h3>, <ul>, <ol>, <li>, <a href='…'> y, solo si hace falta comparar datos, una "
    "<table> simple (<tr>, <th>, <td>). Nada de estilos, colores, fuentes, imágenes, <div> ni "
    "<span>. Sobrio: la negrita solo para lo que importa (nombres de producto, importes, "
    "fechas); un encabezado solo si el correo tiene varias partes. Una lista de máquinas, "
    "productos o consumibles va SIEMPRE como lista (<ul>), cada elemento con el nombre en "
    "negrita y, si el producto tiene enlace en CATÁLOGO o ENLACES, el enlace puesto sobre el "
    "nombre: <li><strong><a href='…'>Nombre</a></strong> — precio y lo esencial</li>. En los "
    "atributos usa comillas simples, para no romper el JSON. Si te piden algo que no cabe en "
    "ese formato (un correo «maquetado», «con imágenes de producto», banners, colores) y el "
    "FORMATO DE ESTE TURNO no es un correo compuesto, no lo hagas en el cuerpo: redacta el "
    "correo sencillo y di en «aviso» que va en texto y que, con fichas e imágenes, se pide "
    "como correo compuesto («Con fichas» en la franja).\n\n"
    "Devuelve SOLO un JSON válido, sin texto alrededor ni markdown:\n"
    '{"asunto": "asunto del correo", "cuerpo": "el correo en HTML sencillo (las etiquetas de '
    'FORMATO), o \\"\\" si no hay nada que enviar", "idioma": "código de dos '
    'letras del idioma del cuerpo", "aviso": "lo que tengas que decirle a la persona de BoHub, '
    'o \\"\\"", "pendiente": ["lo que no has podido responder con los datos"], '
    '"enlaces_usados": ["las URL de la lista que has puesto en el cuerpo"]}'
    "\n\n"
    "CORREO COMPUESTO (solo cuando el FORMATO DE ESTE TURNO lo pida o te deje elegirlo). Es el "
    "correo con fichas de producto, tira de marca, hero con imagen, galería de muestras, vídeo y "
    "botones con el que presentamos máquinas o productos. Tú NO escribes su HTML: devuelves su "
    "ESTRUCTURA y BoHub genera el correo. Nunca pongas etiquetas HTML en ningún campo: los "
    "textos van en markdown sencillo (**negrita**, *cursiva*, «- » al principio de línea para "
    "listas, [texto](L2) para un enlace, párrafos separados por una línea en blanco).\n"
    "Bloques:\n"
    '- {"tipo":"texto","texto":"…"}: el saludo y la entrada (qué le mandas y por qué, a partir '
    "de lo que preguntó) y, al final, el siguiente paso y la despedida. Breve.\n"
    '- {"tipo":"marca","marca":"<una de MARCAS>"}: la tira de la marca (logo y web), antes de '
    "sus fichas.\n"
    '- {"tipo":"productos_fila","productos":["P1","P2","P3"]}: dos o tres fichas en fila; '
    '{"tipo":"producto","producto":"P4"}: una ficha sola, SOLO cuando de esa marca va '
    "exactamente un producto. Dos o tres productos de la misma marca van SIEMPRE juntos en un "
    "«productos_fila» (cuatro, dos filas de dos); nunca dos «producto» seguidos. Para «añade "
    "más modelos», amplía la fila que ya hay o añade otra fila, no fichas sueltas. La ficha "
    "(imagen, nombre, área, características, precio con su IVA y botón) sale del CATÁLOGO: tú "
    "solo eliges qué productos, por su referencia [P#] o [C#].\n"
    '- {"tipo":"video","video":"V1","titulo":"…"}: solo un vídeo de la lista VÍDEOS (va en '
    "el idioma del correo si lo hay; si es de otro idioma, dilo en el texto del bloque).\n"
    '- {"tipo":"hero","imagen":"I1","titulo":"…","subtitulo":"…","puntos":["…"],"botones":'
    '[{"texto":"…","enlace":"L2"}],"disposicion":"arriba|lateral|fondo"}: la cabecera con una '
    "imagen de la lista IMÁGENES; como mucho uno. En una RESPUESTA (hay HILO, o correos "
    "anteriores con el contacto) el correo empieza por el saludo y la entrada, y el hero va "
    "DESPUÉS; solo en un correo nuevo a alguien con quien no hay conversación puede abrir el "
    "correo. "
    "«arriba» (la imagen encima del texto) por defecto; «lateral», imagen y texto lado a lado; "
    "«fondo», el texto sobre la imagen. Puntos y botones, opcionales.\n"
    '- {"tipo":"muestras","imagenes":["I2","I3","I4"],"titulo":"…","pies":["…","…","…"]}: '
    "una galería de 2 a 6 imágenes de trabajos impresos de la lista IMÁGENES, con un pie corto "
    "por imagen si ayuda. El título de una imagen solo dice qué es (suele estar en inglés): no "
    "lo copies en el correo.\n"
    '- {"tipo":"cta","botones":[{"texto":"…","enlace":"L2"}]}: de uno a tres botones; el '
    "enlace es una referencia: L# (ENLACES), P# (la página de ese producto) o M:<marca> (la "
    "web de la marca).\n"
    '- {"tipo":"destacado","titulo":"…","texto":"…","estilo":"aviso|promo|condicion"}: una '
    "condición o un aviso que esté en los datos (nunca una promoción que no esté).\n"
    '- {"tipo":"separador"}.\n'
    "Reglas del compuesto: los productos SOLO por su referencia del CATÁLOGO; ningún precio, "
    "importe, plazo ni enlace escrito en los textos (los ponen las fichas y los botones); las "
    "frases de IVA, VIES y «no es una factura» las pone el pie del correo: no las escribas en "
    "ningún texto; sin firma (se añade después); entre 3 y 8 bloques; lo que más le encaja, "
    "primero; un mismo enlace va en un solo botón del correo (si el hero lleva «Ver la gama», "
    "no lo repitas en un «cta» al final). Si el cliente llama a una máquina con un nombre que "
    "no está tal cual en el CATÁLOGO (el de su formulario: «3000U PRO») y una del CATÁLOGO es "
    "claramente esa misma (misma marca y modelo: «3000Pro Freebird»), es esa: preséntala con "
    "el nombre del catálogo y no hables de ella como de otra máquina ni la des por pendiente "
    "de confirmar si ya enseñas su ficha (ni en el texto ni en «pendiente»). Si dudas de que "
    "sea la misma, pregúntaselo en una frase, sin enseñarla como si fuera otra.\n"
    'Un correo compuesto nuevo: {"modo":"compuesto","asunto":"…","idioma":"es","aviso":"",'
    '"pendiente":[],"bloques":[…]}.\n'
    "Para CORREGIR un compuesto te paso su ESTRUCTURA ACTUAL, con el «id» de cada bloque, y "
    'devuelves SOLO lo que cambia: {"modo":"compuesto","asunto":"…","aviso":"","pendiente":[],'
    '"operaciones":[…]}, con {"op":"quitar","id":"…"}, {"op":"editar","id":"…","campo":'
    '"texto|titulo|subtitulo|boton|productos|botones|estilo|puntos|pies|imagen|imagenes|'
    'disposicion","valor":…}, {"op":"añadir","despues_de":"<id o inicio>","bloque":{…}}, '
    '{"op":"mover","id":"…","despues_de":"<id o inicio>"}, '
    '{"op":"duplicar","id":"…"}, {"op":"reemplazar","id":"…","bloque":{…}} y '
    '{"op":"idioma","valor":"en"}. Lo que no toques se queda igual.'
)


def _fecha(valor: str | None) -> str:
    return (valor or "")[:16].replace("T", " ") or "sin fecha"


#: La consulta del lead en la ficha: hasta aquí (para elegir las máquinas se
#: lee entera).
CONSULTA_LEAD_PROMPT_MAX = 1500
#: De dónde salió la consulta del lead, para la ficha.
FUENTES_CONSULTA_LEAD = {"web_form": "formulario de la web", "agilecrm": "nota de Agile"}


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
    if ctx.telefonos:
        lineas.append("- Teléfonos: " + " · ".join(
            t["numero"] + (f" ({t['etiqueta']})" if t.get("etiqueta") else "")
            for t in ctx.telefonos))
    if ctx.etiquetas:
        lineas.append("- Etiquetas (internas; no las menciones en el correo): "
                      + ", ".join(ctx.etiquetas))
    if ctx.campos:
        lineas.append("- Campos personalizados de la ficha (datos internos; úsalos para "
                      "entender el caso, no los menciones como tales; un precio, plazo o "
                      "condición que aparezca aquí no es vigente):")
        lineas.extend(f"  · {campo['clave']}: {campo['valor']}" for campo in ctx.campos)
    if ctx.notas:
        lineas.append(
            "- Notas internas del equipo (de BoHub, NO del cliente; las fijadas primero, luego "
            "de la más reciente a la más antigua). Úsalas para entender el caso, pero no las "
            "cites, no digas que existen ni copies su texto en el correo; un precio, plazo o "
            "condición que aparezca en una nota no es vigente:")
        for nota in ctx.notas:
            cabecera = " · ".join(p for p in (
                _fecha(nota.get("fecha"))[:10], nota.get("autor") or "",
                "FIJADA" if nota.get("fijada") else "") if p)
            lineas.append(f"  · {cabecera}: {nota['texto']}")
    fuera = sum(1 for r in ctx.recortes if r.startswith("nota interna"))
    if fuera and ctx.notas:
        lineas.append("  (Por tamaño se dejó fuera otra nota interna; no está aquí.)" if fuera == 1
                      else f"  (Por tamaño se dejaron fuera otras {fuera} notas internas; no "
                           "están aquí.)")
    elif fuera:
        lineas.append("- Notas internas: por tamaño se dejó fuera la que había; no está aquí."
                      if fuera == 1 else
                      f"- Notas internas: por tamaño se dejaron fuera las {fuera} que había; no "
                      "están aquí.")
    if ctx.etapa:
        lineas.append(f"- Pipeline: {ctx.etapa}")
    if ctx.clasificacion:
        cl = ctx.clasificacion
        intereses = ", ".join(cl.get("intereses") or []) or "—"
        lineas.append(
            f"- Clasificación de la IA del lead: interés {intereses}"
            f" · idioma {cl.get('idioma') or '—'} · confianza {cl.get('confianza')}"
        )
    if ctx.consulta_lead and ctx.consulta_lead.get("texto"):
        consulta = ctx.consulta_lead
        de = FUENTES_CONSULTA_LEAD.get(str(consulta.get("fuente") or ""), "lead")
        texto = recortar(" ".join(str(consulta["texto"]).split()), CONSULTA_LEAD_PROMPT_MAX)
        lineas.append(
            f"- Consulta de su último lead ({de}, {_fecha(consulta.get('fecha'))[:10]}; la "
            f"escribió el cliente, es lo que pide): «{texto}»")
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
    lineas = ["ENLACES (los únicos que puedes usar, tal cual; entre corchetes, su referencia "
              "para un correo compuesto):"]
    for i, e in enumerate(lista, 1):
        lineas.append(f"- [L{i}] {e['url']} — {e['etiqueta']}")
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


def _iva_texto(iva: str | None, avisos: dict[str, str]) -> str:
    if iva == "con":
        return f"con IVA («{avisos['con']}»)"
    if iva == "sin":
        return f"sin IVA («{avisos['sin']}»)"
    return f"IVA sin declarar («{avisos['sin_declarar']}»)"


def _linea_producto(p: dict[str, Any], avisos: dict[str, str], idioma: str,
                    ref: str | None = None) -> str:
    partes = [(f"[{ref}] " if ref else "") + f"{p.get('nombre')} · {p.get('marca') or '—'}"]
    if p.get("categoria"):
        partes.append(f"área/categoría: {p['categoria']}")
    caracteristicas = [c for c in (p.get("caracteristicas") or []) if c][:2]
    if caracteristicas:
        partes.append("; ".join(caracteristicas))
    if p.get("precio_texto"):
        precio = f"PRECIO: {p['precio_texto']}"
        if p.get("moneda"):
            precio += f" (moneda {p['moneda']})"
        if p.get("idioma_respaldo"):
            precio += (f" — es el precio publicado en «{p.get('precio_idioma')}», no hay precio "
                       f"en «{idioma}»: dilo y no conviertas la moneda")
        if p.get("actualizado_origen"):
            precio += f" · fuente confirmada el {_fecha(p['actualizado_origen'])[:10]}"
        if p.get("orientativo"):
            precio += f" · ORIENTATIVO: añade «{avisos['orientativo']}»"
        partes.append(precio)
    elif p.get("precio_publico") is False:
        partes.append("PRECIO: no tiene precio público — di que el precio se prepara a medida en "
                      "la oferta; NO escribas «Consultar» ni ningún importe")
    else:
        partes.append("sin precio publicado: no inventes uno")
    if p.get("enlace"):
        partes.append(f"enlace: {p['enlace']}"
                      + (f" (en {p['idioma_enlace']})" if p.get("idioma_enlace") else ""))
    return "- " + " · ".join(partes)


def _clave_ref(p: dict[str, Any]) -> str:
    return f"{p.get('fuente')}|{p.get('origen_id')}"


def referencias_catalogo(cat: dict[str, Any]) -> dict[str, str]:
    """Las referencias de los productos del bloque CATÁLOGO, por su orden en la
    lista (no en el texto): las máquinas `P1…`, los consumibles `C1…`. Son las
    mismas que guarda el turno 0 (`ia.Referencias.desde`)."""
    salida: dict[str, str] = {}
    for prefijo, clave in (("P", "maquinas"), ("C", "consumibles")):
        for i, p in enumerate(cat.get(clave) or [], 1):
            salida[_clave_ref(p)] = f"{prefijo}{i}"
    return salida


def _bloque_catalogo(ctx: Contexto) -> str:
    """Las máquinas de su interés y los consumibles de su máquina, con el
    precio tal cual y los avisos. Es la única fuente de precios del borrador."""
    cat = ctx.catalogo or {}
    maquinas = cat.get("maquinas") or []
    consumibles = cat.get("consumibles") or []
    avisos = cat.get("avisos") or {}
    iva = cat.get("iva_por_fuente") or {}
    if not maquinas and not consumibles:
        lineas = ["CATÁLOGO: no hay ningún producto ni precio verificado para este caso. No cites "
                  "ningún precio ni nombres máquinas concretas; si piden precios, di que se lo "
                  "preparas en una oferta."]
        if cat.get("sin_maquina"):
            lineas.append(f"  (Sin máquina en el catálogo para: {', '.join(cat['sin_maquina'])}. "
                          "Si ayuda, enlaza la página de producto del interés de ENLACES.)")
        return "\n".join(lineas) + "\n"
    lineas = ["CATÁLOGO (los ÚNICOS productos y precios que puedes citar; el precio se copia TAL "
              "CUAL, con su moneda, sin convertir; cada producto con su enlace; entre corchetes, "
              "su referencia para un correo compuesto). Es una PRESELECCIÓN por lo que nombran el "
              "hilo y la instrucción y por los intereses del cliente, no el catálogo entero: si "
              "pide algo que no está aquí, no digas que no lo tenemos; di que se lo preparas "
              "aparte y anótalo en «pendiente»:"]
    refs = referencias_catalogo(cat)
    if maquinas:
        # Por de dónde salió cada máquina: lo que nombra el cliente, lo que pide
        # la instrucción, el interés clasificado.
        por_eleccion: dict[str, list[dict[str, Any]]] = {}
        for m in maquinas:
            por_eleccion.setdefault(str(m.get("eleccion") or "interes"), []).append(m)
        precios = f"precios {_iva_texto(iva.get('compositor'), avisos)}"
        if por_eleccion.get("hilo"):
            lineas.append(
                "Máquinas que el cliente NOMBRA, en sus correos o en la consulta de su lead "
                f"(empieza por estas); {precios}:")
            lineas.extend(_linea_producto(m, avisos, ctx.idioma, refs.get(_clave_ref(m)))
                          for m in por_eleccion["hilo"])
        if por_eleccion.get("instruccion"):
            lineas.append(f"Máquinas que pide la INSTRUCCIÓN de quien envía; {precios}:")
            lineas.extend(_linea_producto(m, avisos, ctx.idioma, refs.get(_clave_ref(m)))
                          for m in por_eleccion["instruccion"])
        if por_eleccion.get("consulta"):
            palabras = cat.get("palabras_consulta") or {}
            de_consulta = dict.fromkeys(
                str(m.get("interes") or "") for m in por_eleccion["consulta"])
            por_que = "; ".join(
                f"{', '.join(f'«{p}»' for p in palabras.get(i) or [])} → {i}"
                for i in de_consulta if i)
            lineas.append(
                "Máquinas de lo que PUEDE que pida también la CONSULTA del cliente, por sus "
                f"palabras ({por_que or '—'}): si de verdad pide eso, contéstalo también; si "
                f"esas palabras no hablan de eso, ignóralas; {precios}:")
            lineas.extend(_linea_producto(m, avisos, ctx.idioma, refs.get(_clave_ref(m)))
                          for m in por_eleccion["consulta"])
        posibles = por_eleccion.get("hilo_posible", []) + por_eleccion.get(
            "instruccion_posible", [])
        if posibles:
            lineas.append(
                "Máquinas que PUEDE que nombren el cliente o la instrucción (solo una palabra del "
                "nombre, o un código que sale en su descripción): ofrécelas solo si sus correos, "
                f"su consulta o la instrucción lo confirman; {precios}:")
            lineas.extend(_linea_producto(m, avisos, ctx.idioma, refs.get(_clave_ref(m)))
                          for m in posibles)
        if por_eleccion.get("interes"):
            intereses = sorted(
                {m.get("interes") for m in por_eleccion["interes"] if m.get("interes")})
            lineas.append(
                f"Máquinas que encajan con su interés ({', '.join(intereses) or '—'}); {precios}:")
            lineas.extend(_linea_producto(m, avisos, ctx.idioma, refs.get(_clave_ref(m)))
                          for m in por_eleccion["interes"])
    if cat.get("sin_maquina"):
        lineas.append(
            f"Sin máquina en el catálogo para: {', '.join(cat['sin_maquina'])}. No inventes "
            "ninguna; si ayuda, enlaza la página de producto del interés de ENLACES.")
    if consumibles:
        lineas.append(
            f"Consumibles y repuestos de la tienda para su máquina ({ctx.maquina or '—'}); "
            f"precios {_iva_texto(iva.get('woocommerce'), avisos)}:")
        lineas.extend(_linea_producto(c, avisos, ctx.idioma, refs.get(_clave_ref(c)))
                      for c in consumibles)
    if avisos:
        lineas.append(
            "Si citas algún precio, el correo lleva tal cual, en el idioma del correo, la frase "
            f"del IVA de arriba y estas dos: «{avisos.get('vies')}» «{avisos.get('no_factura')}» "
            "(en un correo compuesto no: las pone su pie)")
    recortes = [r for r in ctx.recortes if r.startswith("catálogo")]
    if recortes:
        lineas.append(f"(Por tamaño se dejaron fuera {len(recortes)} productos; no están aquí.)")
    return "\n".join(lineas) + "\n"


def _emisora_para_ia(of: dict[str, Any]) -> str:
    """La emisora de la oferta tal como la lee el modelo. Una oferta de la
    serie 1 la emite «Bomedia S.L.» (sigue así en FACTUSOL), pero ese nombre
    no puede llegar al correo: el modelo lee solo la serie."""
    emisora = str(of.get("emisora") or "").strip()
    if not emisora:
        return "—"
    if eg.hay_bomedia(emisora):
        return f"serie {of.get('serie') or '1'} (no la nombres en el correo)"
    return emisora


def _bloque_oferta(ctx: Contexto) -> str:
    """La oferta que va adjunta al correo: número, fechas, líneas e importes.
    Es la excepción a «los precios salen solo del CATÁLOGO»: estos son los de
    la oferta que recibe el cliente, y se citan tal cual."""
    of = ctx.oferta
    if not of:
        return ""
    from app.erp.factusol_pdf import _fmt_money  # noqa: PLC0415

    idioma = ctx.idioma
    moneda = of.get("moneda") or "EUR"

    def _m(v: Any) -> str:
        return f"{_fmt_money(float(v or 0), idioma, moneda)} €"

    iva = (f"importes CON IVA ({_fmt(of.get('iva_pct'))} %)" if of.get("con_iva")
           else "importes SIN IVA")
    lineas = [
        "OFERTA ADJUNTA (va como PDF en este correo). SUS importes SÍ se pueden citar, tal "
        "cual: son los de la oferta que recibe el cliente. Para estos productos no uses "
        "precios del CATÁLOGO ni otros. Di que la oferta va adjunta, qué incluye y hasta cuándo "
        "es válida; no afirmes el régimen de IVA definitivo (se fija al emitir el presupuesto, "
        "según VIES).",
        f"  Nº {of.get('numero')} · fecha {of.get('fecha')} · válida hasta "
        f"{of.get('valida_hasta')} · emisora {_emisora_para_ia(of)} · {iva}"
        + (f" · su referencia: {of['referencia']}" if of.get("referencia") else ""),
    ]
    for ln in of.get("lineas") or []:
        if ln.get("text_only"):
            lineas.append(f"  - {ln.get('descripcion')}")
            continue
        dto = f" (dto {_fmt(ln.get('descuento_pct'))} %)" if ln.get("descuento_pct") else ""
        lineas.append(
            f"  - {_fmt(ln.get('cantidad'))} × {ln.get('descripcion') or ln.get('sku') or '—'}"
            f" a {_m(ln.get('precio'))}{dto} = {_m(ln.get('total'))}")
    if of.get("con_iva"):
        lineas.append(f"  Base {_m(of.get('base'))} · IVA {_fmt(of.get('iva_pct'))} % "
                      f"{_m(of.get('iva'))} · TOTAL {_m(of.get('total'))}")
    else:
        lineas.append(f"  TOTAL {_m(of.get('total'))} (sin IVA)")
    return "\n".join(lineas) + "\n"


def _fmt(v: Any) -> str:
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        return str(v or "")
    return f"{n:g}"


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
    correos_fuera = [r for r in ctx.recortes if r.startswith(("histórico:", "empresa:"))]
    if correos_fuera:
        lineas.append(
            f"(Por tamaño se dejaron fuera {len(correos_fuera)} correos antiguos; no están aquí.)")
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
        f"WEB POR LA QUE LLEGÓ: {_web_de_llegada(ctx.sitio)}.",
        _linea_empresa(ctx),
    ]


def _web_de_llegada(sitio: str | None) -> str:
    """El sitio por el que llegó, si es una web. `contexto.sitio_de` ya
    descarta los dominios solo de correo (bomedia.net, streamtec.es) al mirar a
    quién escribió el cliente; queda el origen de la ficha (`web_form:<sitio>`),
    que podría nombrar uno. Un dominio de correo no es una web, y el modelo no
    debe leer «bomedia» como la web del cliente."""
    if not sitio:
        return "desconocida"
    reg = registro_sitios.registro().sitio(sitio)
    if reg is not None and reg.tipo == registro_sitios.TIPO_CORREO:
        return "ninguna (llegó por correo directo)"
    return sitio


def empresa_documento(ctx: Contexto) -> str | None:
    """La empresa del documento que va con el correo (la oferta adjunta), si
    es Streamtec o MQ Europe; si no hay o es otra (serie 1), None."""
    return eg.empresa_de_emisora((ctx.oferta or {}).get("serie"))


def _linea_empresa(ctx: Contexto) -> str:
    """Cómo se llama nuestra empresa en este correo (regla 11). Con oferta
    adjunta de Streamtec o MQ Europe, su emisora; si no, el idioma del correo,
    dicho como regla para que una corrección «en inglés» la cambie sola."""
    del_documento = empresa_documento(ctx)
    if del_documento:
        return f"EMPRESA: {del_documento} (la emisora de la oferta adjunta)."
    return (f"EMPRESA: {eg.STREAMTEC} si el correo va en castellano; {eg.MQ_EUROPE} si va en "
            f"cualquier otro idioma (en {NOMBRES_IDIOMA.get(ctx.idioma, ctx.idioma)}: "
            f"{eg.empresa_por_idioma(ctx.idioma)}).")


def bloque_contexto(ctx: Contexto) -> str:
    """Lo que no cambia en toda la conversación: quién envía, destinatario,
    ficha, enlaces, ejemplo de tono, correos viejos y el hilo. Es el bloque que
    se marca como cacheable para la API y el que se guarda con el turno 0 para
    que las correcciones lo manden byte a byte, sin volver a montarlo."""
    partes = [*_cabecera(ctx), "", _bloque_ficha(ctx), _bloque_catalogo(ctx)]
    oferta = _bloque_oferta(ctx)
    if oferta:
        partes.append(oferta)
    partes.append(_bloque_enlaces(ctx))
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


def peticion_correccion(
    *, texto_actual: str, asunto_actual: str | None, instruccion: str,
    seleccion: str | None = None, sobre_editor: bool = False,
    imagenes: list[str] | None = None,
) -> str:
    """La parte que cambia de un turno de corrección: el texto actual (el
    borrador, o lo que hay ahora mismo en el editor, con los retoques de la
    persona) y lo que hay que cambiarle. Con `seleccion`, solo ese fragmento se
    reescribe y se devuelve solo. El contexto NO se repite aquí: va en el
    bloque cacheado. `imagenes`: los marcadores de las imágenes del editor
    («[IMAGEN 1]»); SOLO si los hay se le pide al modelo que los conserve (sin
    imágenes, la petición es la de siempre)."""
    titulo = (
        "TEXTO ACTUAL DEL EDITOR (tal y como está ahora, con los cambios de quien envía; es lo "
        "que hay que corregir)" if sobre_editor else "BORRADOR ACTUAL"
    )
    if seleccion:
        peticion = (
            f"{titulo} (asunto: {asunto_actual or '—'}); es el contexto, NO lo reescribas "
            f"entero:\n{texto_actual}\n\n"
            f"SELECCIÓN (el único fragmento que tienes que reescribir):\n{seleccion}\n\n"
            f"CORRECCIÓN DE QUIEN ENVÍA: «{instruccion}»\n\n"
            "Reescribe SOLO la selección aplicando esa corrección. Devuelve en «cuerpo» "
            "únicamente el fragmento reescrito, con el mismo tipo de etiquetas que tenía (si "
            "era texto suelto dentro de un párrafo, texto suelto sin <p>), listo para "
            "sustituir a la selección; el resto del correo no se toca. «asunto»: el actual."
        )
    else:
        peticion = (
            f"{titulo} (asunto: {asunto_actual or '—'}):\n{texto_actual}\n\n"
            f"CORRECCIÓN DE QUIEN ENVÍA: «{instruccion}»\n\n"
            "Reescribe el texto aplicando SOLO esa corrección y conservando todo lo demás "
            "(hechos, enlaces, formato, idioma salvo que la corrección lo cambie). Devuelve el "
            "JSON completo con el correo entero, no solo el cambio."
        )
    if imagenes:
        peticion += (
            f"\n\nIMÁGENES: el texto lleva imágenes, marcadas como {', '.join(imagenes)}. "
            "Conserva cada marcador EXACTAMENTE así (sin traducirlo ni describirlo) y en su "
            "sitio, en su propio párrafo si lo tenía; no los quites aunque acortes: al volver se "
            "cambian por las imágenes."
        )
    return peticion


def texto_del_editor(texto_actual_html: str | None, seleccion_html: str | None) -> tuple[str, str]:
    """`(texto actual, selección)` limpios para el prompt: lo que hay ahora en
    el editor sin la firma del compositor y reducido a las etiquetas del
    borrador, y la selección igual. Los dos vacíos si no hay nada escrito."""
    actual = limpiar_html(sin_firma(texto_actual_html))
    if not html_a_texto(actual).strip():
        return "", ""
    sel = limpiar_html(seleccion_html) if seleccion_html else ""
    if sel and not html_a_texto(sel).strip():
        sel = ""
    return actual, sel


def prompt_usuario(ctx: Contexto) -> str:
    """El primer turno entero, como texto (contexto + petición)."""
    peticion = peticion_inicial(ctx.instruccion, sin_hilo=ctx.thread is None)
    return bloque_contexto(ctx) + "\n" + peticion


def mensaje_usuario(contexto_texto: str, peticion_texto: str, *,
                    cachear: bool = True) -> list[dict[str, Any]]:
    """El mensaje de usuario en dos bloques: el contexto y la petición, que
    cambia en cada turno y se queda fuera de la caché. El contexto se marca
    como cacheable (la API lo cobra a una décima parte cuando vuelve igual)
    solo si hay quien lo lea (`cachear`): la caché es de cada modelo, y
    escribirla cuesta un 25 % más que no hacerlo."""
    contexto: dict[str, Any] = {"type": "text", "text": contexto_texto}
    if cachear:
        contexto["cache_control"] = {"type": "ephemeral"}
    return [contexto, {"type": "text", "text": peticion_texto}]


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
    """El cuerpo es un correo o está vacío. Si lo que salió habla claramente a
    la persona de BoHub (informe), pasa al aviso y el cuerpo se queda vacío;
    si solo es dudoso, se queda en el editor y la franja pide que se revise
    (ante la duda, avisar y no borrar). La guardia mira el texto plano (el
    cuerpo viene en HTML sencillo)."""
    cuerpo = data["cuerpo"]
    plano = html_a_texto(cuerpo) if parece_html(cuerpo) else cuerpo
    veredicto = evaluar_cuerpo(
        plano, nombre_usuario=nombre_usuario, nombre_destinatario=nombre_destinatario)
    if veredicto.nivel == NIVEL_CORREO:
        return
    if veredicto.nivel == NIVEL_DUDOSO:
        nota = (f"Revisa el cuerpo antes de enviar: {veredicto.motivo}. Lo he dejado en el "
                "editor por si es un correo válido.")
    else:
        data["cuerpo"] = ""
        nota = f"La IA no ha redactado un correo ({veredicto.motivo}). Lo que dijo: «{plano}»"
    data["aviso"] = f"{data['aviso']} {nota}".strip() if data["aviso"] else nota


#: Dónde guarda el turno lo que hizo la guardia de la marca (lo enseña la
#: franja y lo cuenta quien quiera saber si sigue pasando).
CLAVE_BOMEDIA = "bomedia"
#: La entrada de auditoría de cada vez que pasa.
ACCION_BOMEDIA = "asistente.bomedia_sustituido"
#: Los textos de un correo compuesto que escribe la IA (los mismos que mira
#: `ia_compuesto.texto_libre`): ahí es donde la guardia de la marca sustituye.
CAMPOS_TEXTO_COMPUESTO = ("html", "titulo", "subtitulo", "texto", "boton", "puntos", "pies")


def _asunto_propio(asunto: str | None, heredado: str | None) -> bool:
    """Si el asunto lo ha escrito la IA en este turno. El que hereda (el del
    hilo, «Re: …», o el del turno anterior) no es suyo: cambiarlo partiría el
    hilo y la culparía de algo que no escribió."""
    return bool((asunto or "").strip()) and (asunto or "").strip() != (heredado or "").strip()


def _guardia_bomedia(data: dict[str, Any], *, empresa: str,
                     asunto_heredado: str | None = None) -> list[eg.Cambio]:
    """Regla de la marca: «Bomedia» no llega nunca al editor, aunque el hilo,
    una plantilla vieja o la ficha la nombren. Se sustituye por la empresa del
    correo en el cuerpo y en el asunto que proponga la IA (no en el heredado
    del hilo), y se devuelve cada frase tocada (antes y después) para que la
    franja la enseñe: cambiar el nombre puede reescribir un hecho («ya
    trabajaste con Bomedia en 2019») y quien revisa tiene que poder releer esa
    frase. Las direcciones (`bart@bomedia.net`) no se tocan."""
    cambios: list[eg.Cambio] = []
    asunto = str(data.get("asunto") or "")
    if eg.hay_bomedia(asunto) and _asunto_propio(asunto, asunto_heredado):
        cambios.extend(eg.cambios_en(asunto, empresa, campo="asunto"))
        data["asunto"] = eg.sustituir(asunto, empresa)[0][:500]
    cuerpo = str(data.get("cuerpo") or "")
    if eg.hay_bomedia(cuerpo):
        plano = html_a_texto(cuerpo) if parece_html(cuerpo) else cuerpo
        cambios.extend(eg.cambios_en(plano, empresa, campo="cuerpo"))
        data["cuerpo"] = eg.sustituir(cuerpo, empresa)[0]
    return cambios


def _guardia_bomedia_compuesto(estructura: dict[str, Any], *, empresa: str,
                               ) -> tuple[dict[str, Any], list[eg.Cambio]]:
    """La misma regla en un correo compuesto: en los textos que escribe la IA
    (no en las fichas del catálogo, que vienen de la base de conocimiento)."""
    libre = ia_compuesto.texto_libre(estructura)
    if not eg.hay_bomedia(libre):
        return estructura, []
    cambios = eg.cambios_en(libre, empresa, campo="cuerpo")
    bloques: list[dict[str, Any]] = []
    for b in estructura.get("bloques") or []:
        nuevo = dict(b)
        for campo in CAMPOS_TEXTO_COMPUESTO:
            if campo in nuevo:
                nuevo[campo] = eg.sustituir_en(nuevo[campo], empresa)[0]
        if isinstance(nuevo.get("botones"), list):
            nuevo["botones"] = [
                {**bt, "texto": eg.sustituir_en(bt.get("texto"), empresa)[0]}
                if isinstance(bt, dict) else bt for bt in nuevo["botones"]]
        bloques.append(nuevo)
    return {**estructura, "bloques": bloques}, cambios


def _anotar_bomedia(session: Session, b: BorradorAsistente, cambios: list[eg.Cambio], *,
                    empresa: str) -> None:
    """Lo que hizo la guardia de la marca en ESTE turno, en el turno (la
    franja lo enseña frase a frase), en el log y en la auditoría, para saber
    si sigue pasando. Cada turno empieza limpio: copia el contexto del
    anterior, y lo de un turno no puede salir como si hubiera pasado en otro."""
    contexto = _json(b.contexto_json)
    contexto.pop(CLAVE_BOMEDIA, None)
    if cambios:
        detalle = {"empresa": empresa, "cambios": [c.como_dict() for c in cambios]}
        contexto[CLAVE_BOMEDIA] = detalle
        logger.warning(
            "asistente: la IA escribió «Bomedia» en el borrador %s (conversación %s, turno %d): "
            "%d frase(s) cambiadas por %s", b.id, b.conversacion_id or b.id, b.turno or 0,
            len(cambios), empresa)
        session.add(AuditLog(
            actor_user_id=b.usuario_id, action=ACCION_BOMEDIA, target_type="ia_borrador",
            target_id=b.id, message=f"«Bomedia» sustituida por «{empresa}» en el borrador de la IA",
            metadata_json=json.dumps({**detalle, "conversacion_id": b.conversacion_id or b.id,
                                      "turno": b.turno or 0, "modelo": b.modelo},
                                     ensure_ascii=False),
        ))
    b.contexto_json = json.dumps(contexto, ensure_ascii=False, default=str)


def _json(raw: str | None) -> dict[str, Any]:
    try:
        datos = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        datos = {}
    return datos if isinstance(datos, dict) else {}


def _avisos_imagenes(faltan: list[int], descartadas: int) -> str:
    """Lo que la franja dice de las imágenes del editor que no volvieron a
    su sitio (para que nadie las pierda sin enterarse)."""
    partes: list[str] = []
    marcas = ", ".join(f"[IMAGEN {n}]" for n in faltan)
    if len(faltan) == 1:
        partes.append(f"La IA no conservó la imagen {marcas} del editor en su sitio: va al "
                      "final del correo; muévela o bórrala si sobra.")
    elif faltan:
        partes.append(f"La IA no conservó las imágenes {marcas} del editor en su sitio: van al "
                      "final del correo; muévelas o bórralas si sobran.")
    if descartadas:
        partes.append(
            f"{descartadas} imagen(es) del editor no son https y no se han conservado: vuelve a "
            "ponerlas con un enlace https.")
    return " ".join(partes)


def _rellenar(
    b: BorradorAsistente, data: dict[str, Any], uso: llm.Uso | None, modelo_pedido: str, *,
    enlaces: list[str], editor_html: str | None = None, seleccion_html: str | None = None,
    sobre_editor: bool = False, imagenes: ImagenesEditor | None = None,
) -> None:
    """Lo que devolvió el modelo, al borrador: el cuerpo en HTML sencillo
    (limpiado; enlaces solo de `enlaces`) y su texto plano. En modo selección
    lo devuelto es el fragmento: se empalma en el texto del editor
    (`editor_html`, tal cual, sin tocar el resto) si la selección aparece en
    él; si no, el turno guarda el fragmento y el compositor lo sustituye en el
    editor. `sobre_editor`: la corrección partió de lo que había en el editor
    (el compositor no pregunta antes de ponerlo). `imagenes`: las del editor,
    que se mandaron como marcadores y vuelven aquí a su sitio."""
    html, plano = cuerpo_a_html(data["cuerpo"], enlaces)
    if imagenes is not None:
        faltan: list[int] = []
        if html and imagenes.etiquetas:
            esperadas = imagenes.en_seleccion if seleccion_html else imagenes.en_texto
            html, faltan = restaurar_imagenes(html, imagenes.etiquetas, esperadas,
                                              en_bloque=not seleccion_html)
            plano = sin_marcas_de_imagen(html_a_texto(html))
        # En una selección solo cuentan las suyas: las de fuera no se tocan.
        descartadas = imagenes.descartadas_seleccion if seleccion_html else imagenes.descartadas
        nota = _avisos_imagenes(faltan, descartadas if html else 0)
        if nota:
            data["aviso"] = f"{data['aviso']} {nota}".strip() if data["aviso"] else nota
    contexto = _json(b.contexto_json)
    contexto.pop("seleccion", None)
    contexto.pop("sobre_editor", None)
    if seleccion_html:
        # El turno es sobre la selección aunque el modelo (o la guardia) no
        # devolviera nada: el compositor no debe tomar un cuerpo vacío por
        # «sustituir todo por nada». Un fragmento de texto corrido va sin
        # `<p>` si la selección era texto dentro de un párrafo.
        html = encajar_fragmento(html, seleccion_html) if html else ""
        entero = empalmar(editor_html or "", seleccion_html, html) if html else None
        if entero is not None and len(entero) > MAX_HTML_EMPALMADO:
            entero = None                       # demasiado largo: solo el fragmento
        contexto["seleccion"] = {
            "original": html_a_texto(seleccion_html)[:300],
            # Tal cual la mandó el compositor, para que al volver a elegir el
            # turno solo sustituya ESA selección (no otra que haya entonces).
            "original_html": seleccion_html if len(seleccion_html) <= MAX_SELECCION_GUARDADA
            else None,
            "fragmento_html": html,
            "empalmado": entero is not None,
        }
        if entero is not None:
            html, plano = entero, html_a_texto(entero)
        elif html:
            plano = html_a_texto(html)
    if sobre_editor:
        contexto["sobre_editor"] = True
    b.html = html
    b.texto = plano
    b.asunto = (data["asunto"] or b.asunto or "")[:500] or None
    b.idioma = data["idioma"]
    b.aviso = data["aviso"] or None
    b.pendiente_json = json.dumps(data["pendiente"], ensure_ascii=False)
    contexto["enlaces_usados"] = data["enlaces_usados"]
    b.contexto_json = json.dumps(contexto, ensure_ascii=False, default=str)
    b.modo = ia_compuesto.MODO_NORMAL
    b.estructura_json = None
    _uso_y_coste(b, uso, modelo_pedido)


def _uso_y_coste(b: BorradorAsistente, uso: llm.Uso | None, modelo_pedido: str) -> None:
    nombre = (uso.modelo if uso else None) or modelo_pedido
    b.modelo = nombre[:80]
    b.tokens_entrada = uso.tokens_entrada if uso else 0
    b.tokens_salida = uso.tokens_salida if uso else 0
    b.tokens_cache_lectura = uso.tokens_cache_lectura if uso else 0
    b.tokens_cache_escritura = uso.tokens_cache_escritura if uso else 0
    b.coste_usd = coste_usd(nombre, b.tokens_entrada, b.tokens_salida, b.tokens_cache_lectura,
                            b.tokens_cache_escritura)


# --- correo compuesto ----------------------------------------------------------------------

AVISO_HTML_DESCARTADO = (
    "La IA devolvió el correo en HTML en lugar de su estructura y se ha descartado: el HTML de "
    "un correo compuesto lo genera BoHub. Vuelve a pedirlo.")
AVISO_COMPUESTO_NO_PEDIDO = (
    "La IA devolvió un correo compuesto cuando se pedía texto y no se ha usado. Vuelve a "
    "pedirlo o cambia el formato a «Con fichas».")
AVISO_CONTESTO_EN_TEXTO = (
    "La IA ha contestado con un correo de texto. Si lo quieres con fichas de producto, pide "
    "«Con fichas».")


def _marcas_para_ia(refs: ia_compuesto.Referencias, idioma: str) -> str:
    partes: list[str] = []
    for clave in sorted(refs.marcas):
        nombre = str(refs.marcas[clave].get("nombre") or clave)
        url = refs.url_marca(clave, idioma)
        partes.append(nombre + (f" (web {url})" if url else ""))
    return ", ".join(partes) or "ninguna (no pongas tira de marca)"


def formato_del_turno(modo: str | None, refs: ia_compuesto.Referencias, idioma: str) -> str:
    """La cabecera de la petición de un turno compuesto o en el que elige la
    IA: el formato, los vídeos y las marcas que puede usar. Un turno normal no
    lleva nada (la petición es la de siempre)."""
    if modo == ia_compuesto.MODO_NORMAL:
        return ""
    if modo == ia_compuesto.MODO_COMPUESTO:
        cabecera = ("FORMATO DE ESTE TURNO: CORREO COMPUESTO. Devuelve su estructura (ver «CORREO "
                    "COMPUESTO» en las instrucciones), no un «cuerpo».")
    else:
        cabecera = ("FORMATO DE ESTE TURNO: elige tú. Por defecto, el correo de siempre (el JSON "
                    "con «cuerpo»). Solo si lo que hay que mandar es presentar productos o "
                    "máquinas del CATÁLOGO (la gama, varias opciones con su precio), devuelve un "
                    "CORREO COMPUESTO.")
    def _video(ref: str, v: dict[str, Any]) -> str:
        datos = " · ".join(str(x) for x in (v.get("titulo"), v.get("idioma")) if x)
        return f"[{ref}] {v['url']}" + (f" ({datos})" if datos else "")

    videos = "; ".join(_video(ref, v) for ref, v in refs.videos.items())
    imagenes = "; ".join(
        f"[{ref}] {v.get('titulo') or 'sin título'} ({v.get('sitio') or '—'}"
        + (f", {v['ancho']}×{v['alto']}" if v.get("ancho") and v.get("alto") else "") + ")"
        for ref, v in refs.imagenes.items())
    return (f"{cabecera}\n"
            f"VÍDEOS que puedes poner: {videos or 'ninguno (no pongas bloque de vídeo)'}.\n"
            "IMÁGENES de las webs para el hero y las muestras (el título solo dice qué es; no lo "
            f"copies): {imagenes or 'ninguna (no pongas hero ni muestras)'}.\n"
            f"MARCAS para la tira: {_marcas_para_ia(refs, idioma)}.\n\n")


def anadir_medios(
    session: Session, refs: ia_compuesto.Referencias, *, instruccion: str | None, idioma: str,
    intereses: list[str] | None = None, sitio: str | None = None,
) -> None:
    """Las imágenes de las webs y los vídeos de la tabla que se le ofrecen a la
    IA en un turno compuesto: los que van con las máquinas del correo (sus
    `P#`), con lo que pide la instrucción y con el interés del lead. Las
    palabras de la marca de una máquina no cuentan (todas las fotos de
    artisJet no son de esa máquina); las de la instrucción, sí."""
    from app.services.conocimiento import imagenes as imagenes_kb  # noqa: PLC0415
    from app.services.conocimiento import videos as videos_kb  # noqa: PLC0415

    origenes = [p["origen_id"] for ref, p in refs.productos.items()
                if ref.startswith("P") and p.get("origen_id")]
    # Las palabras del NOMBRE de cada máquina («artisJet 3000Pro» → «3000pro»), no
    # las de su clave interna, y sin las de ninguna marca (ni su clave ni su nombre).
    de_marca = set(imagenes_kb.palabras_de(*(
        f"{clave.replace('_', ' ')} {m.get('nombre') or ''}" for clave, m in refs.marcas.items())))
    grupos = catalogo_kb._maquinas_por_producto(session) if origenes else {}
    modelos: list[str] = []
    for origen in origenes:
        base = (grupos.get(origen) or [None])[0]
        if base is None:
            continue
        propias = de_marca | set(imagenes_kb.palabras_de(base.marca))
        modelos.append(" ".join(p for p in imagenes_kb.palabras_de(base.nombre)
                                if p not in propias))
    de_interes = [i.replace("_", " ") for i in intereses or []]
    palabras = imagenes_kb.palabras_de(instruccion, *de_interes, *modelos)
    for imagen in imagenes_kb.para_correo(session, palabras=palabras, sitio=sitio):
        refs.anadir_imagen(imagen)
    for video in videos_kb.para_correo(session, idioma=idioma, productos=origenes,
                                       palabras=imagenes_kb.palabras_de(instruccion)):
        refs.anadir_video(video)


def peticion_compuesta_correccion(*, estructura_texto: str, asunto_actual: str | None,
                                  instruccion: str) -> str:
    return (
        f"ESTRUCTURA ACTUAL del correo compuesto (asunto: {asunto_actual or '—'}); es lo que hay "
        f"ahora en la vista previa, con los retoques de quien envía:\n{estructura_texto}\n\n"
        f"CORRECCIÓN DE QUIEN ENVÍA: «{instruccion}»\n\n"
        "Aplica SOLO esa corrección con «operaciones» sobre los id de la estructura; lo demás se "
        "queda igual. «asunto»: el actual, salvo que la corrección lo cambie."
    )


def peticion_compuesta_desde_texto(*, texto_actual: str, asunto_actual: str | None,
                                   instruccion: str) -> str:
    return (
        f"TEXTO ACTUAL (asunto: {asunto_actual or '—'}):\n{texto_actual}\n\n"
        f"CORRECCIÓN DE QUIEN ENVÍA: «{instruccion}»\n\n"
        "Rehazlo como CORREO COMPUESTO, con «bloques» nuevos: lo que dice el texto (su entrada, "
        "sus productos como fichas del CATÁLOGO por su referencia, su cierre), aplicando la "
        "corrección."
    )


def _compuesto_como_texto(session: Session, estructura: dict[str, Any],
                          ) -> tuple[str, list[str]]:
    """Un correo compuesto pasado a un correo normal: su texto plano (en
    párrafos, con sus URL como enlaces) y esas URL, que el correo de texto
    puede llevar (el vídeo, la web de la marca, las fichas, los botones)."""
    correo = generador_correo.generar(estructura, datos_compuesto.resolver(session, estructura))
    urls = list(dict.fromkeys(
        html_mod.unescape(u) for u in re.findall(r'href="([^"]+)"', correo.html)))
    return a_html(correo.texto, urls), urls


def _json_de(raw: str) -> dict[str, Any] | None:
    try:
        data = llm._parse_segment_json(raw)
    except (ValueError, llm.LLMError):
        return None
    return data if isinstance(data, dict) else None


def _es_compuesta(data: dict[str, Any] | None) -> bool:
    return isinstance(data, dict) and (
        "bloques" in data or "operaciones" in data
        or str(data.get("modo") or "").strip().lower() == ia_compuesto.MODO_COMPUESTO)


def _referencias_turno0(session: Session, ctx: Contexto, instruccion: str | None,
                        estructura_actual: dict[str, Any] | None) -> ia_compuesto.Referencias:
    cat = ctx.catalogo or {}
    return ia_compuesto.Referencias.desde(
        maquinas=list(cat.get("maquinas") or []), consumibles=list(cat.get("consumibles") or []),
        enlaces=ctx.enlaces_lista(),
        videos=ia_compuesto.videos_de(*(m.texto for m in ctx.mensajes), instruccion,
                                      json.dumps(estructura_actual or {}, ensure_ascii=False)),
        marcas=datos_compuesto.marcas(session),
    )


def _rellenar_compuesto(
    session: Session, b: BorradorAsistente, data: dict[str, Any], uso: llm.Uso | None,
    modelo_pedido: str, *, refs: ia_compuesto.Referencias, idioma: str,
    base: dict[str, Any] | None, nombre_usuario: str | None, nombre_destinatario: str | None,
    html_descartado: int = 0, sobre_editor: bool = False, empresa_documento: str | None = None,
    comprobar_enlaces: bool = False,
) -> None:
    """Un turno compuesto: la estructura (nueva, o la actual con las
    operaciones de la corrección), la guardia sobre los textos que escribe la
    IA (la del cuerpo y la de la marca), el correo generado (HTML y texto
    plano, para comparar lo enviado) y los avisos. `sobre_editor`: partió de
    lo que había en el compositor (la vista previa con sus retoques, o el
    texto del editor): se pone sin preguntar. `empresa_documento`: la de la
    oferta adjunta, si la hay; si no, la del idioma del correo. Las fichas que
    lleva, con su procedencia (catálogo o índice de las webs), van a «Lo que
    ha usado» y al aviso; con `comprobar_enlaces` (la casilla «Webs»), también
    las del catálogo cuyo enlace no es una página de producto."""
    aviso = str(data.get("aviso") or "").strip()
    fichas: list[dict[str, Any]] = []
    avisos: list[str] = []
    idioma_correo = str(data.get("idioma") or idioma or "").strip().lower()[:2] or idioma
    if base is not None and "operaciones" in data:
        res = ia_compuesto.aplicar_operaciones(base, data.get("operaciones"), refs)
    else:
        res = ia_compuesto.estructura_desde_ia(data.get("bloques"), refs, idioma=idioma_correo)
    estructura = res.estructura
    avisos.extend(res.avisos)
    # Lo que ha puesto o cambiado este turno, antes de los arreglos automáticos:
    # los de después solo tocan eso (lo que la persona dejó así en la vista
    # previa se respeta).
    tocados = bq.bloques_tocados(estructura, base)
    # En una respuesta (o en un correo a alguien con quien ya hay conversación)
    # el correo empieza por el saludo y la entrada; el hero de apertura es de un
    # correo frío. Si la estructura que se corrige ya empezaba por otra cosa, es
    # que la persona lo dejó así en la vista previa: se respeta.
    respuesta = b.thread_id is not None or bool(_json(b.contexto_json).get("historico"))
    if respuesta and (base is None or bq.primero_es_texto(base)) and bq.abrir_con_saludo(
            estructura):
        avisos.append("Es una respuesta: el correo empieza por el saludo y el hero va detrás. "
                      "Si lo quieres arriba, muévelo en la vista previa.")
    repetidos = bq.quitar_botones_repetidos(estructura)
    if repetidos:
        avisos.append(f"Un mismo enlace iba en dos botones: se queda el primero y se quita "
                      f"{', '.join(f'«{t}»' for t in repetidos)}.")
    html_descartado += res.html_descartado
    if res.html_descartado:
        avisos.append(f"La IA metió HTML en {res.html_descartado} texto(s) del correo y se ha "
                      "descartado: el HTML del correo lo genera BoHub.")
    empresa = empresa_documento or eg.empresa_por_idioma(estructura.get("idioma") or idioma_correo)
    estructura, cambios_bomedia = _guardia_bomedia_compuesto(estructura, empresa=empresa)
    libre = ia_compuesto.texto_libre(estructura)
    veredicto = evaluar_cuerpo(libre, nombre_usuario=nombre_usuario,
                               nombre_destinatario=nombre_destinatario)
    if veredicto.nivel == NIVEL_DUDOSO:
        avisos.append(f"Revisa el correo antes de enviar: {veredicto.motivo}.")
    elif veredicto.nivel != NIVEL_CORREO:
        estructura = {**estructura, "bloques": []}
        avisos.append(f"La IA no ha redactado un correo ({veredicto.motivo}). "
                      f"Lo que dijo: «{libre}»")
    asunto = (str(data.get("asunto") or "").strip() or b.asunto or "")[:500] or None
    if asunto and eg.hay_bomedia(asunto) and _asunto_propio(asunto, b.asunto):
        cambios_bomedia = [*eg.cambios_en(asunto, empresa, campo="asunto"), *cambios_bomedia]
        asunto = eg.sustituir(asunto, empresa)[0][:500] or None
    if not estructura["bloques"]:
        # La guardia del cuerpo lo vació: no hay frases que releer en el cuerpo.
        cambios_bomedia = [c for c in cambios_bomedia if c.campo == "asunto"]
    if estructura["bloques"]:
        datos = datos_compuesto.resolver(session, estructura)

        def _marca(producto: dict[str, str]) -> str | None:
            fila = datos.productos.get(bq.clave_de(producto)) or {}
            return str(fila.get("marca") or "").strip().lower() or None

        # Dos o tres fichas de la misma marca, en fila (12/10/2026: «añade más
        # modelos» dio tres fichas sueltas, cada una a 320 px).
        juntadas, repetidas = bq.juntar_fichas_sueltas(estructura, _marca, tocados=tocados)
        if juntadas:
            avisos.append(f"Iban {juntadas} fichas sueltas seguidas de la misma marca: van en "
                          "fila.")
        if repetidas:
            avisos.append("La misma ficha iba dos veces seguidas: se queda una.")
        correo = generador_correo.generar(estructura, datos, asunto=asunto)
        # El aviso de IVA, VIES y «no es una factura» lo imprime el pie; lo que de
        # él la IA haya copiado también en un texto, se quita de ahí (12/10/2026:
        # salía dos veces). Solo lo que el pie lleva de verdad.
        en_pie = [f for f in catalogo_kb.avisos_en(estructura["idioma"]).values()
                  if f and f in correo.pie]
        if en_pie and bq.quitar_frases(estructura, en_pie, tocados=tocados):
            avisos.append("El aviso de IVA, VIES y «no es una factura» va en el pie del "
                          "correo: se ha quitado del texto, donde salía repetido.")
            correo = generador_correo.generar(estructura, datos, asunto=asunto)
            libre = ia_compuesto.texto_libre(estructura)
        # Los del catálogo y los que dijo quien envía valen como siempre; el
        # precio de la web de una ficha repetido en un texto se lee como si
        # fuera de oferta: se avisa aparte.
        validos = [p for p in correo.precios if p not in correo.precios_web]
        ajenos = ia_compuesto.importes_ajenos(libre, validos)
        fuera = set(ia_compuesto.importes_ajenos(libre, correo.precios))
        de_la_web = [a for a in ajenos if a not in fuera]
        ajenos = [a for a in ajenos if a in fuera]
        if ajenos:
            avisos.append(f"Un texto del correo menciona {', '.join(f'«{a}»' for a in ajenos)}, "
                          "que no sale del catálogo: los precios van en las fichas. Revísalo.")
        if de_la_web:
            avisos.append(f"Un texto del correo menciona {', '.join(f'«{a}»' for a in de_la_web)}, "
                          "el precio de la web de una ficha: no es un precio de oferta y ya va en "
                          "la ficha. Quítalo del texto o revísalo.")
        # Un modelo que el cliente llama de otra forma (sin alias en el compositor):
        # que el texto no hable como de otra máquina de la que ya enseña la ficha.
        nombres = [str(p.get("nombre") or "") for p in datos.productos.values()]
        for codigo, nombre in ia_compuesto.modelos_con_otro_nombre(libre, nombres):
            avisos.append(f"El texto nombra «{codigo}» y el correo enseña la ficha de «{nombre}»: "
                          "si es la misma máquina, que el texto no la dé por pendiente ni por "
                          "otra distinta.")
        avisos.extend(correo.avisos)
        if comprobar_enlaces or any(c["fuente"] == bq.FUENTE_WEB
                                    for c in bq.productos_de(estructura)):
            fichas, avisos_fichas = procedencia.fichas_del_correo(
                session, estructura, datos.productos, idioma=estructura["idioma"],
                comprobar_enlaces=comprobar_enlaces)
            avisos = [*avisos_fichas, *avisos]
        b.html, b.texto = correo.html, correo.texto
    else:
        b.html, b.texto = "", ""
        if not aviso and not avisos:
            avisos.append("La IA no ha devuelto ningún bloque del correo ni ha dicho por qué. "
                          "Vuelve a pedirlo.")
    pendiente = data.get("pendiente")
    contexto = _json(b.contexto_json)
    for clave in ("seleccion", "sobre_editor", "enlaces_usados"):
        contexto.pop(clave, None)
    if html_descartado:
        contexto["html_descartado"] = html_descartado
    else:
        contexto.pop("html_descartado", None)
    if fichas:
        contexto["fichas"] = fichas
    else:
        contexto.pop("fichas", None)
    if sobre_editor:
        contexto["sobre_editor"] = True
    b.contexto_json = json.dumps(contexto, ensure_ascii=False, default=str)
    _anotar_bomedia(session, b, cambios_bomedia, empresa=empresa)
    b.modo = ia_compuesto.MODO_COMPUESTO
    b.estructura_json = json.dumps(estructura, ensure_ascii=False)
    b.asunto = asunto
    b.idioma = estructura["idioma"]
    b.aviso = " ".join(t for t in [aviso, *avisos] if t).strip() or None
    b.pendiente_json = json.dumps(
        [str(x).strip() for x in pendiente if str(x or "").strip()]
        if isinstance(pendiente, list) else [], ensure_ascii=False)
    _uso_y_coste(b, uso, modelo_pedido)


def _interpretar(
    session: Session, b: BorradorAsistente, raw: str, uso: llm.Uso | None, modelo: str, *,
    modo: str | None, forzado: bool, refs: ia_compuesto.Referencias, base: dict[str, Any] | None,
    asunto_defecto: str | None, idioma_defecto: str | None, nombre_usuario: str | None,
    nombre_destinatario: str | None, enlaces: list[str], editor_html: str | None = None,
    seleccion_html: str | None = None, sobre_editor: bool = False,
    empresa_documento: str | None = None, imagenes: ImagenesEditor | None = None,
    comprobar_enlaces: bool = False,
) -> None:
    """Lo que devolvió el modelo, al turno: un correo compuesto (estructura) o
    uno normal (cuerpo), según el formato del turno. La IA nunca devuelve el
    HTML de un compuesto: si lo intenta, se descarta y se registra. En los
    dos, la guardia de la marca: «Bomedia» sale cambiada por la empresa del
    correo (`empresa_documento` o la del idioma) y la franja enseña la frase.
    `imagenes`: las del editor (marcadores en el prompt): en un correo normal
    vuelven a su sitio; un compuesto no las lleva, y se dice."""
    data_json = _json_de(raw)
    if modo != ia_compuesto.MODO_NORMAL and _es_compuesta(data_json):
        if imagenes is not None and (imagenes.etiquetas or imagenes.descartadas):
            # Las imágenes de un compuesto son las del índice de las webs: las
            # del editor no pasan, y la persona tiene que saberlo.
            n = len(imagenes.etiquetas) + imagenes.descartadas
            que = "la imagen" if n == 1 else f"las {n} imágenes"
            nota = (f"El correo compuesto no lleva {que} que había en el editor: si hace falta, "
                    "pásalo a texto o añádela al correo después.")
            data_json = {**(data_json or {}),
                         "aviso": f"{str((data_json or {}).get('aviso') or '').strip()} {nota}"
                         .strip()}
        _rellenar_compuesto(session, b, data_json or {}, uso, modelo, refs=refs,
                            idioma=idioma_defecto or "es", base=base,
                            nombre_usuario=nombre_usuario,
                            nombre_destinatario=nombre_destinatario, sobre_editor=sobre_editor,
                            empresa_documento=empresa_documento,
                            comprobar_enlaces=comprobar_enlaces)
        return
    if modo == ia_compuesto.MODO_COMPUESTO and forzado:
        logger.warning("asistente: la IA devolvió un cuerpo (HTML) en vez de la estructura de un "
                       "correo compuesto (conversación %s); descartado",
                       b.conversacion_id or b.id)
        _rellenar_compuesto(session, b, {"bloques": [], "aviso": AVISO_HTML_DESCARTADO,
                                         "asunto": asunto_defecto}, uso, modelo, refs=refs,
                            idioma=idioma_defecto or "es", base=None,
                            nombre_usuario=nombre_usuario,
                            nombre_destinatario=nombre_destinatario, html_descartado=1,
                            empresa_documento=empresa_documento)
        return
    if modo == ia_compuesto.MODO_NORMAL and _es_compuesta(data_json):
        data: dict[str, Any] = {"cuerpo": "", "asunto": asunto_defecto or "",
                                "idioma": idioma_defecto, "aviso": AVISO_COMPUESTO_NO_PEDIDO,
                                "pendiente": [], "enlaces_usados": []}
    else:
        data = _parsear(raw, asunto_defecto=asunto_defecto, idioma_defecto=idioma_defecto)
        if modo == ia_compuesto.MODO_COMPUESTO and data["cuerpo"]:
            data["aviso"] = f"{data['aviso']} {AVISO_CONTESTO_EN_TEXTO}".strip()
    empresa = empresa_documento or eg.empresa_por_idioma(data.get("idioma") or idioma_defecto)
    cambios = _guardia_bomedia(data, empresa=empresa, asunto_heredado=asunto_defecto)
    _regla_primera(data, nombre_usuario=nombre_usuario, nombre_destinatario=nombre_destinatario)
    if not data["cuerpo"]:
        # La guardia del cuerpo lo vació: no hay frases que releer en el cuerpo.
        cambios = [c for c in cambios if c.campo == "asunto"]
    _rellenar(b, data, uso, modelo, enlaces=enlaces, editor_html=editor_html,
              seleccion_html=seleccion_html, sobre_editor=sobre_editor, imagenes=imagenes)
    _anotar_bomedia(session, b, cambios, empresa=empresa)
    contexto = _json(b.contexto_json)
    if contexto.pop("fichas", None) is not None:
        b.contexto_json = json.dumps(contexto, ensure_ascii=False, default=str)
    if comprobar_enlaces and b.html:
        # Un correo de texto que enlaza la página de un producto del catálogo
        # que no es un producto de la web (la de contacto): se avisa.
        usados = set(enlaces_de(b.html))
        for e in contexto.get("enlaces_no_producto") or []:
            if isinstance(e, dict) and e.get("enlace") in usados:
                _con_aviso(b, procedencia.aviso_enlace_no_producto(
                    str(e.get("nombre") or ""), str(e["enlace"]), e.get("motivo")))


def _enlaces_no_producto_del_turno(session: Session, contexto_json: str | None,
                                   fuentes: consulta_fuentes.Fuentes | None) -> str | None:
    """El contexto de una corrección con los enlaces del catálogo que no
    llevan a un producto según las casillas de ESTE turno: con «Webs»
    marcada se miran (el primer borrador pudo ir sin ella); sin ella (o sin
    casillas), no hay ninguno. Las máquinas del correo son del compositor."""
    contexto = _json(contexto_json)
    if fuentes is not None and fuentes.webs:
        cat = contexto.get("catalogo") if isinstance(contexto.get("catalogo"), dict) else {}
        maquinas = [{**m, "fuente": FUENTE_COMPOSITOR} for m in cat.get("maquinas") or []
                    if isinstance(m, dict)]
        try:
            contexto["enlaces_no_producto"] = procedencia.enlaces_catalogo_no_producto(
                session, maquinas)
        except Exception:  # noqa: BLE001 — una comprobación de más no tira el borrador
            logger.warning("asistente: no se han podido comprobar los enlaces del catálogo",
                           exc_info=True)
            return contexto_json
    elif contexto.pop("enlaces_no_producto", None) is None:
        return contexto_json
    return json.dumps(contexto, ensure_ascii=False, default=str)


def _modelos(cfg: dict[str, Any], settings: Any) -> tuple[str, str]:
    """`(modelo del primer borrador, modelo de las correcciones)`."""
    return (
        cfg.get("modelo_borrador") or settings.anthropic_model,
        cfg.get("modelo_correccion") or settings.anthropic_model,
    )


def _llamar(settings: Any, *, modelo: str, contexto_texto: str, peticion_texto: str,
            cfg: dict[str, Any], modelo_correccion: str, sistema: str = PROMPT_SISTEMA) -> str:
    """Una llamada de un turno. El contexto va a la caché solo si el turno va
    con el modelo de las correcciones, que es el que lo leerá en los turnos
    siguientes: la caché es de cada modelo, así que la que escribe un primer
    borrador con otro modelo no la lee nadie (12/10/2026: «0 leídos, 6.446
    escritos» en la primera corrección). `sistema`: el de siempre, salvo en un
    turno con material de fuera (sus reglas van detrás)."""
    parametros = parametros_de_llamada(
        modelo, pensamiento_extendido=bool(cfg.get("pensamiento_extendido")))
    # `max_tokens` es lo que ocupa la respuesta: `_invoke_claude` le suma el
    # sitio para pensar si el modelo va a pensar (12/10/2026, «Con fichas»).
    return llm._invoke_claude(
        api_key=settings.anthropic_api_key, model=modelo, system_prompt=sistema,
        user_prompt=mensaje_usuario(contexto_texto, peticion_texto,
                                    cachear=modelo == modelo_correccion),
        max_tokens=MAX_TOKENS_RESPUESTA, parametros=parametros or None,
    )


def _con_marcadores(actual: str, seleccion: str, imagenes: ImagenesEditor | None,
                    ) -> tuple[str, str, list[str] | None]:
    """`(texto, selección, marcadores)` para la petición de una corrección:
    con imágenes en el editor, las versiones con marcadores; sin ellas, el
    texto y la selección de siempre y ningún marcador (la petición no cambia
    ni una coma)."""
    if imagenes is None or not imagenes.etiquetas:
        return actual, seleccion, None
    return imagenes.texto, imagenes.seleccion if seleccion else "", imagenes.marcadores


def _catalogo_pedido_turno0(session: Session, ctx: Contexto,
                            mat: material_fuera.Material | None) -> None:
    """Lo que la consulta reconoció como del catálogo (por el nombre que da el
    plan o por el enlace de su página) y no está en el CATÁLOGO de este
    correo: entra en él como pedido por la instrucción, delante (el recorte
    por tokens quita por detrás), con su ficha y su precio de venta."""
    if mat is None or not isinstance(ctx.catalogo, dict):
        return
    maquinas = list(ctx.catalogo.get("maquinas") or [])
    try:
        nuevos = catalogo_pedido.productos(session, catalogo_pedido.origenes(
            session, instruccion=None, material=mat,
            ya=[str(m.get("origen_id") or "") for m in maquinas]), idioma=ctx.idioma)
    except Exception:  # noqa: BLE001 — sin esto, el borrador va con su CATÁLOGO
        logger.warning("asistente: no se han podido traer los productos pedidos del catálogo",
                       exc_info=True)
        return
    if nuevos:
        ctx.catalogo["maquinas"] = [*nuevos, *maquinas]


def _catalogo_pedido_correccion(
    session: Session, refs: ia_compuesto.Referencias, *, instruccion: str,
    mat: material_fuera.Material | None, plan: consulta_fuentes.Plan | None, idioma: str,
    contexto: dict[str, Any], raiz_contexto: dict[str, Any] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Lo que pide una corrección del catálogo y la IA no tiene a la vista en
    el CATÁLOGO del primer borrador (el congelado, ya recortado): cada
    producto con su `P#` (el que ya tuviera en las referencias, o uno nuevo
    detrás de los que hay) y un bloque para la petición, con su ficha y su
    precio de venta. Sin plan (la casilla «Webs» desmarcada, o la corrección
    no pedía un producto), por lo que la instrucción nombra de verdad, y solo
    si pide poner algo en el correo. `(bloque, productos)`."""
    cat_raiz = (raiz_contexto or {}).get("catalogo")
    maquinas_raiz = cat_raiz.get("maquinas") if isinstance(cat_raiz, dict) else None
    if maquinas_raiz and all(isinstance(m, dict) and m.get("origen_id") for m in maquinas_raiz):
        ya = [str(m["origen_id"]) for m in maquinas_raiz]
    else:
        # Un borrador de antes (su CATÁLOGO no guarda el `origen_id`): las
        # referencias del primer borrador.
        ya = [str(c.get("origen_id") or "") for c in refs.productos.values()
              if c.get("fuente") == FUENTE_COMPOSITOR]
    con_menciones = (not (plan is not None and plan.productos)
                     and consulta_fuentes.pide_incluir(instruccion))
    try:
        nuevos = catalogo_pedido.productos(session, catalogo_pedido.origenes(
            session, instruccion=instruccion, material=mat, con_menciones=con_menciones,
            ya=ya), idioma=idioma)
    except Exception:  # noqa: BLE001 — sin esto, la corrección va con su CATÁLOGO
        logger.warning("asistente: no se han podido traer los productos pedidos del catálogo",
                       exc_info=True)
        return "", []
    if not nuevos:
        return "", []
    cat = contexto.get("catalogo") if isinstance(contexto.get("catalogo"), dict) else {}
    avisos = catalogo_kb.avisos_en(idioma)
    iva = (cat.get("iva_por_fuente") or {}).get(FUENTE_COMPOSITOR)
    numeros = [int(r[1:]) for r in refs.productos if r[:1] == "P" and r[1:].isdigit()]
    siguiente = max(numeros, default=0) + 1
    lineas = [
        "CATÁLOGO · AÑADIDO EN ESTA CORRECCIÓN: productos del catálogo que pide quien envía y "
        "no estaban en el CATÁLOGO de arriba. Son del CATÁLOGO como los demás (su ficha, con su "
        "referencia, y su precio tal cual) y mandan sobre cualquier página de nuestras webs del "
        f"mismo producto; precios {_iva_texto(iva, avisos)}:"]
    for p in nuevos:
        clave = {"fuente": str(p["fuente"]), "origen_id": str(p["origen_id"])}
        ref = refs.ref_de_producto(clave)
        if ref is None:
            ref = f"P{siguiente}"
            siguiente += 1
            refs.productos[ref] = clave
        if bq.url_valida(p.get("enlace")):
            refs.enlaces_producto[ref] = str(p["enlace"])
        lineas.append(_linea_producto(p, avisos, idioma, ref))
    return "\n".join(lineas) + "\n\n", nuevos


def _con_catalogo_pedido(contexto_json: str | None,
                         pedidos: list[dict[str, Any]]) -> str | None:
    """El contexto del turno con los productos que pidió del catálogo entre
    los del CATÁLOGO del correo («Lo que ha usado» los enseña), sin repetir
    los que ya estaban (de una corrección anterior)."""
    if not pedidos:
        return contexto_json
    contexto = _json(contexto_json)
    cat = contexto.get("catalogo") if isinstance(contexto.get("catalogo"), dict) else {}
    maquinas = [m for m in cat.get("maquinas") or [] if isinstance(m, dict)]

    def _clave(m: dict[str, Any]) -> tuple[Any, ...]:
        return ((m.get("origen_id"),) if m.get("origen_id")
                else (m.get("nombre"), m.get("enlace")))

    nuevos = [ctx_mod.resumen_maquina(p) for p in pedidos]
    claves = {_clave(m) for m in nuevos} | {(m.get("nombre"), m.get("enlace")) for m in nuevos}
    quedan = [m for m in maquinas
              if _clave(m) not in claves and (m.get("nombre"), m.get("enlace")) not in claves]
    contexto["catalogo"] = {**cat, "maquinas": [*nuevos, *quedan]}
    return json.dumps(contexto, ensure_ascii=False, default=str)


def _enlaces_material_en_refs(refs: ia_compuesto.Referencias,
                              material: material_fuera.Material | None) -> None:
    """Las URL del material, permitidas también en un correo compuesto (un
    enlace del texto o de un botón). Van detrás de las de la lista ENLACES,
    sin cambiar su numeración. Y las piezas de «Nuestras webs» que son un
    producto, como fichas (`W#`, las de este turno: las de un turno anterior
    se quitan, que su numeración era otra). Sin material, nada."""
    for ref in [r for r in refs.productos if r.startswith("W")]:
        refs.productos.pop(ref, None)
        refs.enlaces_producto.pop(ref, None)
        refs.equivalentes.pop(ref, None)
    if material is None:
        return
    for w in material.resumen.get("webs") or []:
        if isinstance(w, dict) and w.get("ficha") and w.get("sitio") and w.get("wp_id"):
            refs.anadir_producto_web(
                str(w["ref"]), {"fuente": bq.FUENTE_WEB,
                                "origen_id": f"{w['sitio']}:{w['wp_id']}"}, w.get("url"))
            catalogo = w.get("catalogo")
            if isinstance(catalogo, dict) and catalogo.get("fuente") and catalogo.get("origen_id"):
                # Un producto del catálogo que no es de las máquinas del correo:
                # su W# saca la ficha del catálogo (manda el catálogo).
                refs.anadir_equivalente_catalogo(str(w["ref"]), catalogo)
    ya = set(refs.enlaces.values())
    numeros = [int(r[1:]) for r in refs.enlaces if r[1:].isdigit()]
    siguiente = max(numeros, default=0) + 1
    for url in material.urls:
        if url not in ya and bq.url_valida(url):
            refs.enlaces[f"L{siguiente}"] = url
            ya.add(url)
            siguiente += 1


def _con_aviso(b: BorradorAsistente, nota: str) -> None:
    b.aviso = f"{b.aviso} {nota}".strip() if b.aviso else nota


def _enlace_soporte_en_compuesto(session: Session, b: BorradorAsistente, url: str) -> bool:
    """La garantía del enlace en un correo compuesto: un bloque de texto al
    final con el enlace, y el correo se vuelve a generar (HTML y texto) con él.
    False si no hay estructura o se quedó sin bloques (la guardia la vació)."""
    estructura = estructura_de(b)
    if not estructura or not estructura.get("bloques"):
        return False
    parrafo = material_fuera.parrafo_enlace(url, estructura.get("idioma") or b.idioma)
    bloques = [*estructura["bloques"],
               {"id": bq.nuevo_id(), "tipo": bq.TIPO_TEXTO, "html": bq.limpiar_texto_rico(parrafo)}]
    estructura = {**estructura, "bloques": bloques}
    correo = generador_correo.generar(estructura, datos_compuesto.resolver(session, estructura),
                                      asunto=b.asunto)
    b.html, b.texto = correo.html, correo.texto
    b.estructura_json = json.dumps(estructura, ensure_ascii=False)
    return True


def _aplicar_material(session: Session, b: BorradorAsistente,
                      material: material_fuera.Material | None, raw: str | None = None) -> None:
    """Lo que el turno hizo con lo que el asistente consultó solo, después de
    interpretar al modelo (las guardias del cuerpo y de la marca ya pasaron):

    - Qué usó: el modelo lo dice en `material_usado` (`raw`, su respuesta);
      si no lo dijo, el artículo cuenta como usado si su enlace está en el
      cuerpo o el cuerpo trae pasos.
    - La garantía del enlace: si usó el artículo de soporte y su URL no está
      en el cuerpo, se añade al final un párrafo con el enlace, en el idioma
      del correo (en un compuesto, un bloque de texto). No depende de que el
      modelo obedezca: el procedimiento se cita, y quien lo lee tiene que poder
      ir al artículo.
    - Un correo técnico con pasos que no salen de ningún artículo (no había
      procedimiento, no se pudo consultar o no usó el que salió): aviso.
    - Lo que entró, en `contexto_json["material"]` («Lo que ha usado»), con
      lo que usó marcado. Cada turno empieza limpio: el de una corrección sin
      casillas no hereda el del turno anterior.

    Sin material (`None`) no toca el borrador: solo quita, si lo hay, el apunte
    heredado del turno anterior."""
    contexto = _json(b.contexto_json)
    if material is None:
        if material_fuera.CLAVE_CONTEXTO in contexto:
            contexto.pop(material_fuera.CLAVE_CONTEXTO, None)
            b.contexto_json = json.dumps(contexto, ensure_ascii=False, default=str)
        return
    resumen = json.loads(json.dumps(material.resumen, ensure_ascii=False, default=str))
    compuesto = b.modo == ia_compuesto.MODO_COMPUESTO
    usados = material_fuera.material_usado(raw)
    texto = ""
    if b.html:
        texto = (ia_compuesto.texto_libre(estructura_de(b) or {}) if compuesto
                 else html_a_texto(b.html))
    con_pasos = bool(b.html) and material_fuera.trae_pasos(None if compuesto else b.html, texto)
    if usados is None:
        articulo_usado = bool(material.soporte_url) and bool(b.html) and (
            material.soporte_url in enlaces_de(b.html) or con_pasos)
    else:
        articulo_usado = bool(material.soporte_url) and material_fuera.REF_ARTICULO in usados
    resumen["material_usado"] = usados
    for clave in ("soporte",):
        if isinstance(resumen.get(clave), dict):
            resumen[clave]["usado"] = articulo_usado
    for clave in ("otros_soporte", "webs"):
        for entrada in resumen.get(clave) or []:
            if isinstance(entrada, dict):
                entrada["usado"] = None if usados is None else entrada.get("ref") in usados
    if material.soporte_url and articulo_usado and b.html and (
            material.soporte_url not in enlaces_de(b.html)):
        seleccion = contexto.get("seleccion")
        if compuesto:
            anadido = _enlace_soporte_en_compuesto(session, b, material.soporte_url)
        elif isinstance(seleccion, dict):
            # Un turno sobre una selección: el compositor pone el fragmento en
            # la selección (`fragmento_html`), y un párrafo de más ahí partiría
            # el texto. Se avisa con el enlace, para ponerlo a mano.
            anadido = False
            _con_aviso(b, "El fragmento no lleva el enlace al artículo de soporte: ponlo en el "
                          f"correo ({material.soporte_url}).")
        else:
            b.html = f"{b.html}{material_fuera.parrafo_enlace(material.soporte_url, b.idioma)}"
            b.texto = html_a_texto(b.html)
            anadido = True
        if anadido:
            resumen["enlace_anadido"] = True
            _con_aviso(b, material_fuera.AVISO_ENLACE_ANADIDO)
    sin_procedimiento = (material.sin_resultados or material.soporte_no_consultado
                         or (material.tecnica and material.soporte_url and not articulo_usado))
    if sin_procedimiento and con_pasos:
        resumen["pasos_sin_procedimiento"] = True
        _con_aviso(b, material_fuera.AVISO_PASOS_SIN_PROCEDIMIENTO)
    for nota in material.avisos:
        _con_aviso(b, nota)
    contexto = _json(b.contexto_json)
    contexto[material_fuera.CLAVE_CONTEXTO] = resumen
    b.contexto_json = json.dumps(contexto, ensure_ascii=False, default=str)


def _sumar_plan(b: BorradorAsistente, plan: consulta_fuentes.Plan | None) -> None:
    """El coste del plan de la consulta va también en el borrador: lo que
    cuesta un borrador es todo lo que se pidió a la IA para escribirlo."""
    if plan is not None and plan.uso is not None:
        b.coste_usd = round(float(b.coste_usd or 0) + plan.coste_usd, 6)


def _material_de_correccion(
    session: Session, *, borrador: BorradorAsistente, raiz: BorradorAsistente,
    fuentes: consulta_fuentes.Fuentes | None, instruccion: str, usuario: User, settings: Any,
    cfg: dict[str, Any], nuevo_id: str,
) -> tuple[consulta_fuentes.Plan | None, material_fuera.Material | None]:
    """Lo de fuera de una corrección. Si la conversación ya consultó, lo que
    trajo esa consulta (la primera, entera: no la de una corrección que solo
    llevaba una casilla), leído otra vez con las casillas de ahora y sin buscar
    nada nuevo. Si nunca consultó (el primer turno iba sin casillas o no tenía
    nada que contestar), se consulta ahora, con la instrucción. `(plan,
    material)`; el plan solo si hubo que consultar."""
    if fuentes is None or not fuentes.alguna:
        return None, None
    original = _material_original(session, borrador, raiz)
    entrada = consulta_fuentes.entrada_de_borrador(session, raiz, instruccion=instruccion)
    if original is not None:
        # Si la corrección pide incluir un producto («incluye tbn la MBO 6090
        # desktop»), el plan dice cuál, para buscarlo en el índice.
        plan = None
        if fuentes.webs and consulta_fuentes.pide_producto(instruccion):
            plan = consulta_fuentes.planificar(
                session, consulta_fuentes.Entrada(
                    instruccion=instruccion, idioma=borrador.idioma or "es",
                    maquinas=entrada.maquinas, modelo_flux=entrada.modelo_flux),
                settings=settings, cfg=cfg)
            consulta_fuentes.anotar_plan(session, plan, usuario_id=usuario.id,
                                         borrador_id=nuevo_id)
        return plan, consulta_fuentes.reutilizar(
            session, original, fuentes, borrador.idioma, instruccion=instruccion,
            nombres=plan.productos if plan is not None else [], maquinas=entrada.maquinas,
            del_correo_nombres=consulta_fuentes.nombres_del_correo(entrada))
    plan = consulta_fuentes.planificar(session, entrada, settings=settings, cfg=cfg)
    consulta_fuentes.anotar_plan(session, plan, usuario_id=usuario.id, borrador_id=nuevo_id)
    return plan, consulta_fuentes.consultar(session, fuentes=fuentes, plan=plan,
                                            idioma=borrador.idioma, entrada=entrada)


def _material_original(session: Session, borrador: BorradorAsistente,
                       raiz: BorradorAsistente) -> dict[str, Any] | None:
    """Lo que trajo la consulta de la conversación (la que buscó de verdad, no
    una relectura de una corrección), o None si nunca consultó."""
    for turno in (raiz, *borradores.turnos_de(session, borrador)):
        m = _json(turno.contexto_json).get(material_fuera.CLAVE_CONTEXTO)
        consulta = m.get("consulta") if isinstance(m, dict) else None
        if isinstance(m, dict) and m.get("automatico") and not (
                isinstance(consulta, dict) and consulta.get("reutilizado")):
            return m
    return None


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
    regenerar_de: str | None = None, ahora: datetime | None = None,
    texto_actual_html: str | None = None, seleccion_html: str | None = None,
    formato: str = ia_compuesto.FORMATO_AUTO, estructura_actual: dict[str, Any] | None = None,
    oferta_id: str | None = None, fuentes: consulta_fuentes.Fuentes | None = None,
) -> BorradorAsistente:
    """El primer turno: monta el contexto, pide el borrador (si hay con qué),
    lo guarda con su coste y lo devuelve (ya en la sesión, sin commit). Sin
    hilo (`thread=None`) es un correo nuevo: hace falta `destinatario` e
    `instruccion`. Con `texto_actual_html` (hay algo escrito en el editor: un
    correo a mano, un borrador guardado reabierto) el turno 0 es ya una
    CORRECCIÓN de ese texto —con el modelo de las correcciones— y hace falta
    instrucción; con `seleccion_html`, solo de ese fragmento. Lanza
    `llm.LLMError` si la IA no está configurada o no responde, y `ValueError`
    si falta algo. `ahora` solo lo fijan los tests (la antigüedad de los
    precios del catálogo). `formato` (el interruptor de la franja: `auto`,
    `normal`, `compuesto`) y la instrucción deciden si el turno es un correo
    normal o uno compuesto; con `estructura_actual` (un compuesto en la vista
    previa) el turno 0 es una corrección de ESE compuesto. `fuentes`: las
    casillas «Webs» y «Soporte» de la franja; con alguna marcada, el asistente
    consulta solo nuestras webs y el soporte del fabricante con lo que pregunta
    el cliente (`consulta_fuentes`) y lo que trae va en la petición del turno.
    Lo que no responde a tiempo se deja fuera y se avisa: nunca tira el
    borrador."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise llm.LLMNotConfiguredError("La IA no está configurada (ANTHROPIC_API_KEY).")
    instruccion = (instruccion or "").strip()[:MAX_INSTRUCCION] or None
    editor_html = sin_firma(texto_actual_html).strip()
    actual, seleccion = texto_del_editor(texto_actual_html, seleccion_html)
    # Las imágenes del editor, como marcadores, para que la corrección no las
    # pierda (None si no hay ninguna: el turno es el de siempre).
    imagenes = apartar_imagenes(editor_html, seleccion_html) if actual else None
    base = bq.normalizar(estructura_actual) if estructura_actual else None
    if base is not None and bq.vacia(base):
        base = None
    if base is not None:
        actual, seleccion, editor_html = "", "", ""
        imagenes = None
    if (actual or base is not None) and not instruccion:
        raise ValueError(
            "Escribe qué quieres cambiar del texto que hay en el editor («más corto», «más "
            "formal», «en inglés»…).")
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
        contacto=contacto, instruccion=instruccion, ahora=ahora, oferta_id=oferta_id,
    )
    cfg = config_asistente(session)
    borrador_id = str(uuid4())
    # Nada nuevo del cliente y sin instrucción: no hay con qué redactar (ni se
    # llama al modelo ni se consulta nada).
    nada_que_contestar = thread is not None and not instruccion and (
        ctx.ultimo_del_cliente is None or ctx.mensajes[-1].lado == "nosotros")
    # Las webs y el soporte, consultados solo ANTES de llamar al modelo (con
    # las casillas desmarcadas, nada: el turno es el de siempre).
    plan: consulta_fuentes.Plan | None = None
    mat: material_fuera.Material | None = None
    if fuentes is not None and fuentes.alguna and not nada_que_contestar:
        entrada = consulta_fuentes.entrada_de_contexto(ctx)
        plan = consulta_fuentes.planificar(session, entrada, settings=settings, cfg=cfg)
        consulta_fuentes.anotar_plan(session, plan, usuario_id=usuario.id,
                                     borrador_id=borrador_id)
        mat = consulta_fuentes.consultar(session, fuentes=fuentes, plan=plan, idioma=ctx.idioma,
                                         entrada=entrada)
        _catalogo_pedido_turno0(session, ctx, mat)
    modelo_borrador, modelo_correccion = _modelos(cfg, settings)
    modo_actual = (ia_compuesto.MODO_COMPUESTO if base is not None
                   else ia_compuesto.MODO_NORMAL if actual else None)
    modo = ia_compuesto.modo_del_turno(formato=formato, instruccion=instruccion,
                                       modo_actual=modo_actual)
    refs = _referencias_turno0(session, ctx, instruccion, base)
    _enlaces_material_en_refs(refs, mat)
    refs.anotar_instrucciones(instruccion)
    if modo != ia_compuesto.MODO_NORMAL:
        anadir_medios(session, refs, instruccion=instruccion, idioma=ctx.idioma,
                      intereses=ctx.intereses, sitio=ctx.sitio)
    cabecera = formato_del_turno(modo, refs, ctx.idioma)
    enlaces_compuesto: list[str] = []
    if base is not None and modo == ia_compuesto.MODO_COMPUESTO:
        # Un compuesto en la vista previa (un borrador reabierto): se corrige ESE.
        modelo, proposito = modelo_correccion, PROPOSITO_CORRECCION
        peticion_texto = cabecera + peticion_compuesta_correccion(
            estructura_texto=ia_compuesto.estructura_para_ia(base, refs),
            asunto_actual=ctx.asunto_respuesta, instruccion=instruccion or "")
    elif base is not None:
        # Un compuesto que pasa a texto: su texto plano es el borrador actual.
        modelo, proposito = modelo_correccion, PROPOSITO_CORRECCION
        texto_compuesto, enlaces_compuesto = _compuesto_como_texto(session, base)
        peticion_texto = peticion_correccion(
            texto_actual=texto_compuesto, asunto_actual=ctx.asunto_respuesta,
            instruccion=instruccion or "")
    elif actual and modo == ia_compuesto.MODO_COMPUESTO:
        modelo, proposito = modelo_correccion, PROPOSITO_CORRECCION
        peticion_texto = cabecera + peticion_compuesta_desde_texto(
            texto_actual=actual, asunto_actual=ctx.asunto_respuesta, instruccion=instruccion or "")
    elif actual:
        # Hay texto en el editor: la IA corrige ESO (regla del 11/10/2026), con
        # el modelo de las correcciones; el contexto se monta igual, para que
        # los turnos siguientes lo reutilicen de caché. Las imágenes del editor
        # van como marcadores y vuelven a su sitio al interpretar la respuesta.
        modelo, proposito = modelo_correccion, PROPOSITO_CORRECCION
        texto_prompt, seleccion_prompt, marcadores = _con_marcadores(actual, seleccion, imagenes)
        peticion_texto = peticion_correccion(
            texto_actual=texto_prompt, asunto_actual=ctx.asunto_respuesta,
            instruccion=instruccion or "", seleccion=seleccion_prompt or None, sobre_editor=True,
            imagenes=marcadores)
    else:
        modelo, proposito = modelo_borrador, PROPOSITO_ASISTENTE
        peticion_texto = cabecera + peticion_inicial(instruccion, sin_hilo=thread is None)
    # El material de fuera va en la petición (el contexto se congela con el
    # turno 0) y sus reglas, detrás del prompt de sistema; sin material, los
    # dos son los de siempre.
    if mat is not None:
        peticion_texto = mat.peticion + peticion_texto
    sistema = material_fuera.sistema(PROMPT_SISTEMA, mat)
    # El presupuesto de tokens de entrada: se recorta por abajo (empresa y
    # correos viejos, lo más antiguo primero) hasta que el prompt cabe. El
    # hilo y el expediente no se tocan.
    ctx.tokens_estimados = ajustar_al_presupuesto(
        ctx, presupuesto=ctx.presupuesto_tokens,
        medir=lambda c: (estimar_tokens(sistema) + estimar_tokens(bloque_contexto(c))
                         + estimar_tokens(peticion_texto)),
    )
    contexto_texto = bloque_contexto(ctx)
    resumen = ctx.resumen()
    resumen["refs"] = refs.como_dict()
    # La empresa del documento adjunto (si es Streamtec o MQ Europe): la heredan
    # las correcciones, que no vuelven a montar el contexto.
    empresa_doc = empresa_documento(ctx)
    resumen["empresa_documento"] = empresa_doc
    if fuentes is not None and fuentes.webs:
        # Los enlaces del catálogo de este correo que no llevan a un producto
        # de la web («Lo que ha usado» los marca; el aviso, si se usan).
        try:
            resumen["enlaces_no_producto"] = procedencia.enlaces_catalogo_no_producto(
                session, ctx.productos_catalogo())
        except Exception:  # noqa: BLE001 — una comprobación de más no tira el borrador
            logger.warning("asistente: no se han podido comprobar los enlaces del catálogo",
                           exc_info=True)
    borrador = BorradorAsistente(
        id=borrador_id, conversacion_id=borrador_id, turno=0, instruccion=instruccion,
        contexto_texto=contexto_texto,
        thread_id=thread.id if thread is not None else None,
        message_id=ctx.ultimo_del_cliente.id if ctx.ultimo_del_cliente else None,
        contact_id=(ctx.contacto or {}).get("id") or (thread.contact_id if thread else None),
        usuario_id=usuario.id, idioma=ctx.idioma, modelo=modelo[:80],
        destinatario=ctx.destinatario, remitente=ctx.usuario.get("remitente"),
        asunto=ctx.asunto_respuesta or None, texto="",
        pendiente_json="[]",
        enlaces_json=material_fuera.con_enlaces(
            json.dumps(ctx.enlaces_lista(), ensure_ascii=False), mat),
        contexto_json=json.dumps(resumen, ensure_ascii=False, default=str),
        alcance=alcance, mensajes_hilo=len(ctx.mensajes),
        mensajes_historico=len(ctx.historico) + sum(
            1 for e in ctx.empresa_asuntos if e.get("texto")),
        estado=ESTADO_GENERADO,
    )
    # Nada nuevo del cliente y sin instrucción: no se llama al modelo (no
    # cuesta nada) y el motivo va en el aviso. El contexto queda guardado: la
    # instrucción que venga después lo reutiliza.
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
        raw = _llamar(settings, modelo=modelo, contexto_texto=contexto_texto,
                      peticion_texto=peticion_texto, cfg=cfg,
                      modelo_correccion=modelo_correccion, sistema=sistema)
    except llm.LLMError as exc:
        uso = llm.tomar_ultimo_uso()
        registrar_llamada(
            session, proposito=proposito, modelo=modelo, uso=uso,
            usuario_id=usuario.id,
            referencia_tipo="email_thread" if thread is not None else "destinatario",
            referencia_id=thread.id if thread is not None else ctx.destinatario,
            ok=False, error=f"{type(exc).__name__}: {exc}",
        )
        raise
    uso = llm.tomar_ultimo_uso()
    # Enlaces que pueden ir en el cuerpo: los de la base de conocimiento y el
    # catálogo, más los que la persona ya tenía puestos en su texto (y los del
    # material de fuera, si el turno lo lleva).
    enlaces = [e["url"] for e in ctx.enlaces_lista()] + enlaces_de(actual) + enlaces_compuesto
    if mat is not None:
        enlaces += mat.urls
    _interpretar(
        session, borrador, raw, uso, modelo, modo=modo,
        forzado=formato in (ia_compuesto.MODO_NORMAL, ia_compuesto.MODO_COMPUESTO), refs=refs,
        base=base, asunto_defecto=ctx.asunto_respuesta, idioma_defecto=ctx.idioma,
        nombre_usuario=ctx.usuario.get("nombre_pila"),
        nombre_destinatario=(ctx.contacto or {}).get("nombre"), enlaces=enlaces,
        editor_html=editor_html,
        seleccion_html=(seleccion_html or "").strip() if seleccion else None,
        sobre_editor=bool(actual) or base is not None, empresa_documento=empresa_doc,
        imagenes=imagenes, comprobar_enlaces=bool(fuentes and fuentes.webs))
    _aplicar_material(session, borrador, mat, raw)
    _sumar_plan(borrador, plan)
    session.add(borrador)
    session.flush()
    llamada = registrar_llamada(
        session, proposito=proposito, modelo=borrador.modelo, uso=uso,
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


def estructura_de(b: BorradorAsistente) -> dict[str, Any] | None:
    """La estructura guardada de un turno compuesto (o None)."""
    if not b.estructura_json:
        return None
    try:
        datos = json.loads(b.estructura_json)
    except (TypeError, ValueError):
        return None
    return datos if isinstance(datos, dict) else None


def corregir(
    session: Session, *, borrador: BorradorAsistente, instruccion: str, usuario: User,
    texto_actual_html: str | None = None, seleccion_html: str | None = None,
    formato: str = ia_compuesto.FORMATO_AUTO, estructura_actual: dict[str, Any] | None = None,
    fuentes: consulta_fuentes.Fuentes | None = None,
) -> BorradorAsistente:
    """Un turno más de la conversación: el mismo bloque de contexto guardado
    (cacheable), el texto actual y la instrucción nueva. El texto actual es lo
    que hay AHORA en el editor (`texto_actual_html`, con los retoques de la
    persona) y, si no llega, el último borrador; con `seleccion_html` solo se
    reescribe ese fragmento. Si no hay texto ninguno (no había nada que
    contestar), la instrucción es el primer borrador de verdad y va con el
    modelo del primer borrador; si no, es una corrección y va con el modelo de
    las correcciones. El borrador que se corrige queda como regenerado. Un
    compuesto se corrige sobre su estructura (`estructura_actual`, la de la
    vista previa con los textos retocados; si no llega, la del turno) con
    operaciones; el interruptor (`formato`) puede pasarlo a texto o al revés.
    `fuentes`: las casillas de la franja AHORA. Una corrección no vuelve a
    buscar: lee otra vez lo que trajo la consulta de la conversación (con las
    casillas de ahora); si nunca se consultó, se consulta ahora con la
    instrucción. Va en la petición; el contexto guardado no cambia."""
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
    editor_html = sin_firma(texto_actual_html).strip()
    actual, seleccion = texto_del_editor(texto_actual_html, seleccion_html)
    # Lo que hay en el compositor manda: la vista previa (con sus retoques) o
    # el texto del editor (un compuesto que se pasó a texto es ya texto); sin
    # nada de eso, el turno que se corrige.
    if estructura_actual:
        base: dict[str, Any] | None = bq.normalizar(estructura_actual)
    elif actual:
        base = None
    else:
        base = estructura_de(borrador)
    if base is not None:
        base = bq.normalizar(base)
        if bq.vacia(base):
            base = None
    desde_vista = bool(estructura_actual) and base is not None
    sobre_editor = bool(actual) and base is None
    imagenes: ImagenesEditor | None = None
    if base is not None:
        actual, seleccion, editor_html = "", "", ""
    elif not sobre_editor:
        actual = borradores.html_de(borrador) if (borrador.texto or "").strip() else ""
        editor_html, seleccion = actual, ""
        # El turno anterior puede llevar las imágenes que volvieron a su sitio.
        imagenes = apartar_imagenes(actual) if actual else None
    else:
        imagenes = apartar_imagenes(editor_html, seleccion_html)
    era_compuesto = borrador.modo == ia_compuesto.MODO_COMPUESTO and not actual
    modo_actual = (ia_compuesto.MODO_COMPUESTO if base is not None or era_compuesto
                   else ia_compuesto.MODO_NORMAL)
    modo = ia_compuesto.modo_del_turno(formato=formato, instruccion=instruccion,
                                       modo_actual=modo_actual)
    primera_redaccion = not actual and base is None
    nuevo_id = str(uuid4())
    plan, mat = _material_de_correccion(
        session, borrador=borrador, raiz=raiz, fuentes=fuentes, instruccion=instruccion,
        usuario=usuario, settings=settings, cfg=cfg, nuevo_id=nuevo_id)
    refs = ia_compuesto.Referencias.de_dict(
        _json(raiz.contexto_json).get("refs"), marcas=datos_compuesto.marcas(session),
        videos=ia_compuesto.videos_de(contexto_texto, instruccion,
                                      json.dumps(base or {}, ensure_ascii=False)))
    # Lo que pide del catálogo y no estaba en el CATÁLOGO del primer borrador
    # (antes que el material: el W# de su página va a este mismo P#).
    bloque_pedido, pedidos_catalogo = _catalogo_pedido_correccion(
        session, refs, instruccion=instruccion, mat=mat, plan=plan,
        idioma=borrador.idioma or "es", contexto=_json(borrador.contexto_json),
        raiz_contexto=_json(raiz.contexto_json))
    _enlaces_material_en_refs(refs, mat)
    # Los importes que ha escrito quien envía en esta conversación: los únicos
    # que valen como precio de una ficha de la web.
    refs.anotar_instrucciones("\n".join(
        [*(t.instruccion or "" for t in borradores.turnos_de(session, borrador)), instruccion]))
    if modo != ia_compuesto.MODO_NORMAL:
        anadir_medios(session, refs, instruccion=instruccion, idioma=borrador.idioma or "es")
    cabecera = formato_del_turno(modo, refs, borrador.idioma or "es")
    enlaces_compuesto: list[str] = []
    if base is not None and modo == ia_compuesto.MODO_COMPUESTO:
        modelo, proposito = modelo_correccion, PROPOSITO_CORRECCION
        peticion_texto = cabecera + peticion_compuesta_correccion(
            estructura_texto=ia_compuesto.estructura_para_ia(base, refs),
            asunto_actual=borrador.asunto, instruccion=instruccion)
    elif base is not None:
        modelo, proposito = modelo_correccion, PROPOSITO_CORRECCION
        texto_compuesto, enlaces_compuesto = _compuesto_como_texto(session, base)
        peticion_texto = peticion_correccion(
            texto_actual=texto_compuesto, asunto_actual=borrador.asunto, instruccion=instruccion)
    elif primera_redaccion:
        modelo, proposito = modelo_borrador, PROPOSITO_ASISTENTE
        peticion_texto = cabecera + peticion_inicial(
            instruccion, sin_hilo=borrador.thread_id is None)
    elif modo == ia_compuesto.MODO_COMPUESTO:
        # Un compuesto no lleva las imágenes del editor: el texto, sin ellas.
        modelo, proposito = modelo_correccion, PROPOSITO_CORRECCION
        peticion_texto = cabecera + peticion_compuesta_desde_texto(
            texto_actual=imagenes.limpio if imagenes is not None else actual,
            asunto_actual=borrador.asunto, instruccion=instruccion)
    else:
        modelo, proposito = modelo_correccion, PROPOSITO_CORRECCION
        texto_prompt, seleccion_prompt, marcadores = _con_marcadores(actual, seleccion, imagenes)
        peticion_texto = peticion_correccion(
            texto_actual=texto_prompt, asunto_actual=borrador.asunto, instruccion=instruccion,
            seleccion=seleccion_prompt or None, sobre_editor=sobre_editor, imagenes=marcadores)
    peticion_texto = bloque_pedido + peticion_texto
    if mat is not None:
        peticion_texto = mat.peticion + peticion_texto
    try:
        raw = _llamar(settings, modelo=modelo, contexto_texto=contexto_texto,
                      peticion_texto=peticion_texto, cfg=cfg,
                      modelo_correccion=modelo_correccion,
                      sistema=material_fuera.sistema(PROMPT_SISTEMA, mat))
    except llm.LLMError as exc:
        uso = llm.tomar_ultimo_uso()
        registrar_llamada(
            session, proposito=proposito, modelo=modelo, uso=uso, usuario_id=usuario.id,
            referencia_tipo="ia_borrador", referencia_id=borrador.id, ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    uso = llm.tomar_ultimo_uso()
    contexto = _json(borrador.contexto_json)
    nombre_usuario = (usuario.full_name or usuario.email or "").strip()
    nuevo = BorradorAsistente(
        id=nuevo_id, conversacion_id=borrador.conversacion_id or borrador.id,
        turno=(borrador.turno or 0) + 1, padre_id=borrador.id, instruccion=instruccion,
        thread_id=borrador.thread_id, message_id=borrador.message_id,
        contact_id=borrador.contact_id, usuario_id=usuario.id, idioma=borrador.idioma,
        modelo=modelo[:80], destinatario=borrador.destinatario, remitente=borrador.remitente,
        asunto=borrador.asunto, texto="",
        enlaces_json=material_fuera.con_enlaces(borrador.enlaces_json, mat),
        contexto_json=_enlaces_no_producto_del_turno(
            session, _con_catalogo_pedido(borrador.contexto_json, pedidos_catalogo), fuentes),
        alcance=borrador.alcance,
        mensajes_hilo=borrador.mensajes_hilo, mensajes_historico=borrador.mensajes_historico,
        estado=ESTADO_GENERADO,
    )
    enlaces = (borradores.enlaces_del_borrador(borrador) + enlaces_de(actual)
               + enlaces_compuesto)
    if mat is not None:
        enlaces += mat.urls
    enlaces += [str(p["enlace"]) for p in pedidos_catalogo if p.get("enlace")]
    _interpretar(
        session, nuevo, raw, uso, modelo, modo=modo,
        forzado=formato in (ia_compuesto.MODO_NORMAL, ia_compuesto.MODO_COMPUESTO), refs=refs,
        base=base, asunto_defecto=borrador.asunto, idioma_defecto=borrador.idioma,
        nombre_usuario=nombre_usuario,
        nombre_destinatario=(contexto.get("contacto") or {}).get("nombre"), enlaces=enlaces,
        editor_html=editor_html,
        seleccion_html=(seleccion_html or "").strip() if seleccion else None,
        sobre_editor=sobre_editor or desde_vista,
        empresa_documento=eg.empresa_de_emisora(
            _json(raiz.contexto_json).get("empresa_documento")),
        imagenes=imagenes, comprobar_enlaces=bool(fuentes and fuentes.webs))
    _aplicar_material(session, nuevo, mat, raw)
    _sumar_plan(nuevo, plan)
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
