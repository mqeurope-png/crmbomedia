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
from app.services.asistente import borradores
from app.services.asistente import contexto as ctx_mod
from app.services.asistente.config import configuracion as config_asistente
from app.services.asistente.config import estimar_tokens
from app.services.asistente.contexto import Contexto
from app.services.asistente.historico import ajustar_al_presupuesto
from app.services.asistente.texto import (
    NIVEL_CORREO,
    NIVEL_DUDOSO,
    a_html,
    cuerpo_a_html,
    empalmar,
    encajar_fragmento,
    enlaces_de,
    evaluar_cuerpo,
    html_a_texto,
    limpiar_html,
    parece_html,
    sin_firma,
)
from app.services.correo_compuesto import bloques as bq
from app.services.correo_compuesto import datos as datos_compuesto
from app.services.correo_compuesto import generador as generador_correo
from app.services.correo_compuesto import ia as ia_compuesto
from app.services.ia.coste import coste_usd
from app.services.ia.modelos import parametros_de_llamada
from app.services.ia.uso import registrar_llamada

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
    "compruebo y te lo confirmo») y anótalo en «pendiente». No rellenes huecos.\n"
    "9. No repitas lo que el cliente ya sabe, no resumas el histórico, no te disculpes de "
    "más.\n"
    "10. Si te dan una CORRECCIÓN sobre un BORRADOR ACTUAL o sobre el TEXTO ACTUAL DEL EDITOR, "
    "reescríbelo aplicando solo esa corrección y conservando todo lo demás, incluido el formato "
    "(listas, negritas, enlaces). Si te dan una SELECCIÓN, reescribe solo ese fragmento y "
    "devuelve en «cuerpo» únicamente el fragmento reescrito, con el mismo tipo de etiquetas que "
    "tenía; el resto del correo no se toca.\n\n"
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
    '{"tipo":"producto","producto":"P4"}: una ficha sola. La ficha (imagen, nombre, área, '
    "características, precio con su IVA y botón) sale del CATÁLOGO: tú solo eliges qué "
    "productos, por su referencia [P#] o [C#].\n"
    '- {"tipo":"video","video":"V1","titulo":"…"}: solo un vídeo de la lista VÍDEOS (va en '
    "el idioma del correo si lo hay; si es de otro idioma, dilo en el texto del bloque).\n"
    '- {"tipo":"hero","imagen":"I1","titulo":"…","subtitulo":"…","puntos":["…"],"botones":'
    '[{"texto":"…","enlace":"L2"}],"disposicion":"arriba|lateral|fondo"}: la cabecera con una '
    "imagen de la lista IMÁGENES; como mucho uno, al principio o justo después del saludo. "
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
    "importe, plazo ni enlace escrito en los textos (los ponen las fichas y los botones); sin "
    "firma (se añade después); entre 3 y 8 bloques; lo que más le encaja, primero.\n"
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
              "su referencia para un correo compuesto):"]
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
                f"Máquinas que el cliente NOMBRA en el hilo (empieza por estas); {precios}:")
            lineas.extend(_linea_producto(m, avisos, ctx.idioma, refs.get(_clave_ref(m)))
                          for m in por_eleccion["hilo"])
        if por_eleccion.get("instruccion"):
            lineas.append(f"Máquinas que pide la INSTRUCCIÓN de quien envía; {precios}:")
            lineas.extend(_linea_producto(m, avisos, ctx.idioma, refs.get(_clave_ref(m)))
                          for m in por_eleccion["instruccion"])
        posibles = por_eleccion.get("hilo_posible", []) + por_eleccion.get(
            "instruccion_posible", [])
        if posibles:
            lineas.append(
                "Máquinas que PUEDE que nombren el hilo o la instrucción (solo una palabra del "
                "nombre, o un código que sale en su descripción): ofrécelas solo si el hilo lo "
                f"confirma; {precios}:")
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
            f"del IVA de arriba y estas dos: «{avisos.get('vies')}» «{avisos.get('no_factura')}»")
    recortes = [r for r in ctx.recortes if r.startswith("catálogo")]
    if recortes:
        lineas.append(f"(Por tamaño se dejaron fuera {len(recortes)} productos; no están aquí.)")
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
        f"WEB POR LA QUE LLEGÓ: {ctx.sitio or 'desconocida'}.",
    ]


def bloque_contexto(ctx: Contexto) -> str:
    """Lo que no cambia en toda la conversación: quién envía, destinatario,
    ficha, enlaces, ejemplo de tono, correos viejos y el hilo. Es el bloque que
    se marca como cacheable para la API y el que se guarda con el turno 0 para
    que las correcciones lo manden byte a byte, sin volver a montarlo."""
    partes = [*_cabecera(ctx), "", _bloque_ficha(ctx), _bloque_catalogo(ctx), _bloque_enlaces(ctx)]
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
) -> str:
    """La parte que cambia de un turno de corrección: el texto actual (el
    borrador, o lo que hay ahora mismo en el editor, con los retoques de la
    persona) y lo que hay que cambiarle. Con `seleccion`, solo ese fragmento se
    reescribe y se devuelve solo. El contexto NO se repite aquí: va en el
    bloque cacheado."""
    titulo = (
        "TEXTO ACTUAL DEL EDITOR (tal y como está ahora, con los cambios de quien envía; es lo "
        "que hay que corregir)" if sobre_editor else "BORRADOR ACTUAL"
    )
    if seleccion:
        return (
            f"{titulo} (asunto: {asunto_actual or '—'}); es el contexto, NO lo reescribas "
            f"entero:\n{texto_actual}\n\n"
            f"SELECCIÓN (el único fragmento que tienes que reescribir):\n{seleccion}\n\n"
            f"CORRECCIÓN DE QUIEN ENVÍA: «{instruccion}»\n\n"
            "Reescribe SOLO la selección aplicando esa corrección. Devuelve en «cuerpo» "
            "únicamente el fragmento reescrito, con el mismo tipo de etiquetas que tenía (si "
            "era texto suelto dentro de un párrafo, texto suelto sin <p>), listo para "
            "sustituir a la selección; el resto del correo no se toca. «asunto»: el actual."
        )
    return (
        f"{titulo} (asunto: {asunto_actual or '—'}):\n{texto_actual}\n\n"
        f"CORRECCIÓN DE QUIEN ENVÍA: «{instruccion}»\n\n"
        "Reescribe el texto aplicando SOLO esa corrección y conservando todo lo demás "
        "(hechos, enlaces, formato, idioma salvo que la corrección lo cambie). Devuelve el JSON "
        "completo con el correo entero, no solo el cambio."
    )


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


def _json(raw: str | None) -> dict[str, Any]:
    try:
        datos = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        datos = {}
    return datos if isinstance(datos, dict) else {}


def _rellenar(
    b: BorradorAsistente, data: dict[str, Any], uso: llm.Uso | None, modelo_pedido: str, *,
    enlaces: list[str], editor_html: str | None = None, seleccion_html: str | None = None,
    sobre_editor: bool = False,
) -> None:
    """Lo que devolvió el modelo, al borrador: el cuerpo en HTML sencillo
    (limpiado; enlaces solo de `enlaces`) y su texto plano. En modo selección
    lo devuelto es el fragmento: se empalma en el texto del editor
    (`editor_html`, tal cual, sin tocar el resto) si la selección aparece en
    él; si no, el turno guarda el fragmento y el compositor lo sustituye en el
    editor. `sobre_editor`: la corrección partió de lo que había en el editor
    (el compositor no pregunta antes de ponerlo)."""
    html, plano = cuerpo_a_html(data["cuerpo"], enlaces)
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
    marcas = set(refs.marcas)
    modelos = [" ".join(t for t in re.split(r"[-_\s]+", o.lower()) if t and t not in marcas)
               for o in origenes]
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
    html_descartado: int = 0, sobre_editor: bool = False,
) -> None:
    """Un turno compuesto: la estructura (nueva, o la actual con las
    operaciones de la corrección), la guardia sobre los textos que escribe la
    IA, el correo generado (HTML y texto plano, para comparar lo enviado) y
    los avisos. `sobre_editor`: partió de lo que había en el compositor (la
    vista previa con sus retoques, o el texto del editor): se pone sin
    preguntar."""
    aviso = str(data.get("aviso") or "").strip()
    avisos: list[str] = []
    idioma_correo = str(data.get("idioma") or idioma or "").strip().lower()[:2] or idioma
    if base is not None and "operaciones" in data:
        res = ia_compuesto.aplicar_operaciones(base, data.get("operaciones"), refs)
    else:
        res = ia_compuesto.estructura_desde_ia(data.get("bloques"), refs, idioma=idioma_correo)
    estructura = res.estructura
    avisos.extend(res.avisos)
    html_descartado += res.html_descartado
    if res.html_descartado:
        avisos.append(f"La IA metió HTML en {res.html_descartado} texto(s) del correo y se ha "
                      "descartado: el HTML del correo lo genera BoHub.")
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
    if estructura["bloques"]:
        correo = generador_correo.generar(
            estructura, datos_compuesto.resolver(session, estructura), asunto=asunto)
        ajenos = ia_compuesto.importes_ajenos(libre, correo.precios)
        if ajenos:
            avisos.append(f"Un texto del correo menciona {', '.join(f'«{a}»' for a in ajenos)}, "
                          "que no sale del catálogo: los precios van en las fichas. Revísalo.")
        avisos.extend(correo.avisos)
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
    if sobre_editor:
        contexto["sobre_editor"] = True
    b.contexto_json = json.dumps(contexto, ensure_ascii=False, default=str)
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
) -> None:
    """Lo que devolvió el modelo, al turno: un correo compuesto (estructura) o
    uno normal (cuerpo), según el formato del turno. La IA nunca devuelve el
    HTML de un compuesto: si lo intenta, se descarta y se registra."""
    data_json = _json_de(raw)
    if modo != ia_compuesto.MODO_NORMAL and _es_compuesta(data_json):
        _rellenar_compuesto(session, b, data_json or {}, uso, modelo, refs=refs,
                            idioma=idioma_defecto or "es", base=base,
                            nombre_usuario=nombre_usuario,
                            nombre_destinatario=nombre_destinatario, sobre_editor=sobre_editor)
        return
    if modo == ia_compuesto.MODO_COMPUESTO and forzado:
        logger.warning("asistente: la IA devolvió un cuerpo (HTML) en vez de la estructura de un "
                       "correo compuesto (conversación %s); descartado",
                       b.conversacion_id or b.id)
        _rellenar_compuesto(session, b, {"bloques": [], "aviso": AVISO_HTML_DESCARTADO,
                                         "asunto": asunto_defecto}, uso, modelo, refs=refs,
                            idioma=idioma_defecto or "es", base=None,
                            nombre_usuario=nombre_usuario,
                            nombre_destinatario=nombre_destinatario, html_descartado=1)
        return
    if modo == ia_compuesto.MODO_NORMAL and _es_compuesta(data_json):
        data: dict[str, Any] = {"cuerpo": "", "asunto": asunto_defecto or "",
                                "idioma": idioma_defecto, "aviso": AVISO_COMPUESTO_NO_PEDIDO,
                                "pendiente": [], "enlaces_usados": []}
    else:
        data = _parsear(raw, asunto_defecto=asunto_defecto, idioma_defecto=idioma_defecto)
        if modo == ia_compuesto.MODO_COMPUESTO and data["cuerpo"]:
            data["aviso"] = f"{data['aviso']} {AVISO_CONTESTO_EN_TEXTO}".strip()
    _regla_primera(data, nombre_usuario=nombre_usuario, nombre_destinatario=nombre_destinatario)
    _rellenar(b, data, uso, modelo, enlaces=enlaces, editor_html=editor_html,
              seleccion_html=seleccion_html, sobre_editor=sobre_editor)


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
    regenerar_de: str | None = None, ahora: datetime | None = None,
    texto_actual_html: str | None = None, seleccion_html: str | None = None,
    formato: str = ia_compuesto.FORMATO_AUTO, estructura_actual: dict[str, Any] | None = None,
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
    previa) el turno 0 es una corrección de ESE compuesto."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise llm.LLMNotConfiguredError("La IA no está configurada (ANTHROPIC_API_KEY).")
    instruccion = (instruccion or "").strip()[:MAX_INSTRUCCION] or None
    editor_html = sin_firma(texto_actual_html).strip()
    actual, seleccion = texto_del_editor(texto_actual_html, seleccion_html)
    base = bq.normalizar(estructura_actual) if estructura_actual else None
    if base is not None and bq.vacia(base):
        base = None
    if base is not None:
        actual, seleccion, editor_html = "", "", ""
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
        contacto=contacto, instruccion=instruccion, ahora=ahora,
    )
    cfg = config_asistente(session)
    modelo_borrador, modelo_correccion = _modelos(cfg, settings)
    modo_actual = (ia_compuesto.MODO_COMPUESTO if base is not None
                   else ia_compuesto.MODO_NORMAL if actual else None)
    modo = ia_compuesto.modo_del_turno(formato=formato, instruccion=instruccion,
                                       modo_actual=modo_actual)
    refs = _referencias_turno0(session, ctx, instruccion, base)
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
        # los turnos siguientes lo reutilicen de caché.
        modelo, proposito = modelo_correccion, PROPOSITO_CORRECCION
        peticion_texto = peticion_correccion(
            texto_actual=actual, asunto_actual=ctx.asunto_respuesta, instruccion=instruccion or "",
            seleccion=seleccion or None, sobre_editor=True)
    else:
        modelo, proposito = modelo_borrador, PROPOSITO_ASISTENTE
        peticion_texto = cabecera + peticion_inicial(instruccion, sin_hilo=thread is None)
    # El presupuesto de tokens de entrada: se recorta por abajo (empresa y
    # correos viejos, lo más antiguo primero) hasta que el prompt cabe. El
    # hilo y el expediente no se tocan.
    ctx.tokens_estimados = ajustar_al_presupuesto(
        ctx, presupuesto=ctx.presupuesto_tokens,
        medir=lambda c: (estimar_tokens(PROMPT_SISTEMA) + estimar_tokens(bloque_contexto(c))
                         + estimar_tokens(peticion_texto)),
    )
    contexto_texto = bloque_contexto(ctx)
    resumen = ctx.resumen()
    resumen["refs"] = refs.como_dict()
    borrador_id = str(uuid4())
    borrador = BorradorAsistente(
        id=borrador_id, conversacion_id=borrador_id, turno=0, instruccion=instruccion,
        contexto_texto=contexto_texto,
        thread_id=thread.id if thread is not None else None,
        message_id=ctx.ultimo_del_cliente.id if ctx.ultimo_del_cliente else None,
        contact_id=(ctx.contacto or {}).get("id") or (thread.contact_id if thread else None),
        usuario_id=usuario.id, idioma=ctx.idioma, modelo=modelo[:80],
        destinatario=ctx.destinatario, remitente=ctx.usuario.get("remitente"),
        asunto=ctx.asunto_respuesta or None, texto="",
        pendiente_json="[]", enlaces_json=json.dumps(ctx.enlaces_lista(), ensure_ascii=False),
        contexto_json=json.dumps(resumen, ensure_ascii=False, default=str),
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
        raw = _llamar(settings, modelo=modelo, contexto_texto=contexto_texto,
                      peticion_texto=peticion_texto, cfg=cfg)
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
    # catálogo, más los que la persona ya tenía puestos en su texto.
    enlaces = [e["url"] for e in ctx.enlaces_lista()] + enlaces_de(actual) + enlaces_compuesto
    _interpretar(
        session, borrador, raw, uso, modelo, modo=modo,
        forzado=formato in (ia_compuesto.MODO_NORMAL, ia_compuesto.MODO_COMPUESTO), refs=refs,
        base=base, asunto_defecto=ctx.asunto_respuesta, idioma_defecto=ctx.idioma,
        nombre_usuario=ctx.usuario.get("nombre_pila"),
        nombre_destinatario=(ctx.contacto or {}).get("nombre"), enlaces=enlaces,
        editor_html=editor_html,
        seleccion_html=(seleccion_html or "").strip() if seleccion else None,
        sobre_editor=bool(actual) or base is not None)
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
    operaciones; el interruptor (`formato`) puede pasarlo a texto o al revés."""
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
    if base is not None:
        actual, seleccion, editor_html = "", "", ""
    elif not sobre_editor:
        actual = borradores.html_de(borrador) if (borrador.texto or "").strip() else ""
        editor_html, seleccion = actual, ""
    era_compuesto = borrador.modo == ia_compuesto.MODO_COMPUESTO and not actual
    modo_actual = (ia_compuesto.MODO_COMPUESTO if base is not None or era_compuesto
                   else ia_compuesto.MODO_NORMAL)
    modo = ia_compuesto.modo_del_turno(formato=formato, instruccion=instruccion,
                                       modo_actual=modo_actual)
    refs = ia_compuesto.Referencias.de_dict(
        _json(raiz.contexto_json).get("refs"), marcas=datos_compuesto.marcas(session),
        videos=ia_compuesto.videos_de(contexto_texto, instruccion,
                                      json.dumps(base or {}, ensure_ascii=False)))
    if modo != ia_compuesto.MODO_NORMAL:
        anadir_medios(session, refs, instruccion=instruccion, idioma=borrador.idioma or "es")
    cabecera = formato_del_turno(modo, refs, borrador.idioma or "es")
    primera_redaccion = not actual and base is None
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
        modelo, proposito = modelo_correccion, PROPOSITO_CORRECCION
        peticion_texto = cabecera + peticion_compuesta_desde_texto(
            texto_actual=actual, asunto_actual=borrador.asunto, instruccion=instruccion)
    else:
        modelo, proposito = modelo_correccion, PROPOSITO_CORRECCION
        peticion_texto = peticion_correccion(
            texto_actual=actual, asunto_actual=borrador.asunto, instruccion=instruccion,
            seleccion=seleccion or None, sobre_editor=sobre_editor)
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
    contexto = _json(borrador.contexto_json)
    nombre_usuario = (usuario.full_name or usuario.email or "").strip()
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
    enlaces = (borradores.enlaces_del_borrador(borrador) + enlaces_de(actual)
               + enlaces_compuesto)
    _interpretar(
        session, nuevo, raw, uso, modelo, modo=modo,
        forzado=formato in (ia_compuesto.MODO_NORMAL, ia_compuesto.MODO_COMPUESTO), refs=refs,
        base=base, asunto_defecto=borrador.asunto, idioma_defecto=borrador.idioma,
        nombre_usuario=nombre_usuario,
        nombre_destinatario=(contexto.get("contacto") or {}).get("nombre"), enlaces=enlaces,
        editor_html=editor_html,
        seleccion_html=(seleccion_html or "").strip() if seleccion else None,
        sobre_editor=sobre_editor or desde_vista)
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
