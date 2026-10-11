"""Qué histórico mira el asistente y cuánto: cuatro niveles por orden de
prioridad, llenados hasta agotar un presupuesto de tokens.

1. El hilo actual, entero y literal. No se recorta nunca.
2. El expediente: hechos, no mensajes (ficha, pedidos, pendiente de cobro,
   etapa, clasificación, los últimos asuntos tratados). Siempre presente.
3. Los correos viejos del contacto que vienen a cuento, con su cuerpo: pocos
   (5 como mucho), el más reciente primero. Entra uno si el asunto coincide al
   normalizarlo, si el mensaje de hoy menciona una referencia que aparece en
   él, si habla de la misma máquina o producto, o si hay solape fuerte de
   palabras con la pregunta de hoy.
4. La empresa, marcada como tal: correos de OTRAS personas de la MISMA
   empresa, agrupada por el registro de empresa de BoHub, nunca por el dominio
   del correo (todos los clientes de gmail.com no son una empresa). Entran como
   asunto y fecha, etiquetados «lo escribió Fulano»; solo con «todo lo de la
   empresa» pueden traer cuerpo, con el mismo criterio del nivel 3.

Si no cabe todo, se recorta por abajo: primero lo más viejo del nivel 4, luego
lo más viejo del nivel 3 y, lo último, el catálogo (consumibles y después
máquinas, hasta dejar dos). Lo que entró queda listado para enseñarlo debajo
del borrador y guardarlo con él.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.crm import Company, Contact, EmailDirection, EmailMessage, EmailThread
from app.services.asistente.texto import recortar, sin_cita, texto_de_mensaje

#: Prefijos de respuesta y reenvío en los idiomas de la casa.
_PREFIJOS_RE = re.compile(
    r"^\s*(?:(?:re|aw|wg|fw|fwd|rv|tr|antw|sv|vs|ref|r)\s*(?:\[\d+\])?\s*:\s*)+", re.IGNORECASE)
_CORCHETES_RE = re.compile(r"\[[^\]]*\]")
_ESPACIOS_RE = re.compile(r"\s+")
_PALABRA_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
#: Referencias: pedidos, facturas, albaranes, números de serie. Un número de
#: cuatro cifras o más, o un código con letras y cifras (M-1001, FAC-2026-12,
#: SN0045, 2-000075).
_REFERENCIA_RE = re.compile(
    r"(?<![\w/])(?:[A-Za-z]{1,6}[-/]?)?\d[\d\-/.]{2,}\d(?![\w/])|(?<![\w/])\d{4,}(?![\w/])")
#: Un correo viejo entra entero salvo que sea enorme.
MAX_CHARS_HISTORICO = 4000
#: Cuántos hilos del contacto se miran como candidatos (los más recientes); de
#: cada hilo, solo su mensaje más reciente (un hilo aporta uno como mucho).
HILOS_MAX = 60
#: Solape fuerte: al menos tantas palabras de contenido compartidas…
SOLAPE_MIN_PALABRAS = 4
#: …y al menos esta fracción de las palabras de contenido del mensaje de hoy.
SOLAPE_MIN_FRACCION = 0.25

#: Palabras vacías (artículos, preposiciones, pronombres, fórmulas) en los
#: idiomas de la casa: no cuentan para el solape.
PALABRAS_VACIAS: frozenset[str] = frozenset("""
para con por que una uno unos unas los las del este esta esto estos estas pero como mas más
muy hola gracias saludos buenos buenas días tardes tengo tenemos quiero queremos quisiera
sobre también tambien desde hasta cuando donde cuanto cuánto nuestro nuestra vuestro vuestra
ustedes usted puede pueden podría podrían hemos sido está están estoy somos estamos sería
atentamente cordialmente favor información informacion consulta pregunta respuesta correo
email mail hola adjunto adjunta mensaje escribo escribir
the and for with that this from have hello thanks thank regards would like your our you are
about could should please will been there their what when where which also just best kind
dear sincerely information question reply email mail message
der die das und für mit ich wir sie ist sind nicht auch eine einen einer bitte danke grüße
gruesse freundlichen hallo guten morgen tag haben habe wurde werden kann können über nach
noch sehr schon mal sich ihre ihren ihnen unser unsere frage antwort nachricht mail
les des une pour avec nous vous est sur dans bonjour merci cordialement sont été être avoir
votre notre vos nos aussi plus très question réponse message mail
het een van voor met wij jullie dat niet hallo bedankt groeten vriendelijke zijn hebben wordt
kunnen over ook nog zeer onze uw vraag antwoord bericht mail
uma umas não nao por obrigado obrigada cumprimentos para com são está estão temos tenho nosso
nossa vossa pergunta resposta mensagem
""".split())
#: Palabras genéricas de una línea de pedido que no identifican la máquina.
GENERICAS: frozenset[str] = frozenset(
    "impresora printer drucker imprimante máquina maquina machine maschine tinta ink tinte"
    " encre unidad unidades unit units pack kit set envío envio shipping transporte portes"
    " cable cables adaptador manual garantía garantia extended".split())


@dataclass
class Candidato:
    """Un correo viejo del contacto (o de la empresa) que podría entrar."""

    id: str
    thread_id: str
    fecha: datetime | None
    lado: str
    de: str
    asunto: str
    texto: str
    quien: str | None = None          # nivel 4: quién de la empresa lo escribió
    contact_id: str | None = None

    def como_dict(self, motivo: str, *, con_texto: bool) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id, "thread_id": self.thread_id,
            "fecha": self.fecha.isoformat() if self.fecha else None,
            "lado": self.lado, "asunto": self.asunto or "(sin asunto)", "motivo": motivo,
        }
        if self.quien:
            d["quien"] = self.quien
        if con_texto:
            d["texto"] = self.texto
        return d


# --- las piezas del criterio --------------------------------------------------------


def asunto_normalizado(asunto: str | None) -> str:
    """`Re: Pedido 4411`, `RE: Pedido 4411`, `AW: [EXT] Pedido 4411` → `pedido 4411`."""
    texto = (asunto or "").strip()
    anterior = None
    while anterior != texto:
        anterior = texto
        texto = _CORCHETES_RE.sub(" ", texto)
        texto = _PREFIJOS_RE.sub("", texto)
    return _ESPACIOS_RE.sub(" ", texto).strip().lower()


_FECHA_RE = re.compile(r"^\d{1,2}[./-]\d{1,2}([./-]\d{2,4})?$|^\d{4}[./-]\d{1,2}[./-]\d{1,2}$")


def referencias(texto: str | None) -> set[str]:
    """Pedidos, facturas, albaranes, números de serie mencionados. Fuera las
    fechas, los años sueltos y los teléfonos (nueve cifras o más): lo que
    está en todas las firmas no identifica nada."""
    salida: set[str] = set()
    for m in _REFERENCIA_RE.finditer(texto or ""):
        ref = m.group(0).upper().strip(".-/")
        cifras = sum(ch.isdigit() for ch in ref)
        if cifras < 3 or cifras >= 9 or _FECHA_RE.match(ref):
            continue
        if len(ref) == 4 and ref.isdigit() and ref.startswith(("19", "20")):
            continue                                   # un año suelto
        salida.add(ref)
    return salida


def referencias_de_hoy(texto_actual: str, candidatos: Iterable[Candidato]) -> set[str]:
    """Las referencias del mensaje de hoy sin el ruido de firma: un número que
    aparece en casi todos los correos del contacto (CP, NIF, teléfono corto) no
    identifica ninguno."""
    refs = referencias(texto_actual)
    if not refs:
        return refs
    lista = list(candidatos)
    if len(lista) < 3:
        return refs
    frecuencia: Counter[str] = Counter()
    for c in lista:
        frecuencia.update(referencias(f"{c.asunto}\n{c.texto}"))
    umbral = max(2, int(len(lista) * 0.4))
    ruido = {r for r, n in frecuencia.items() if n >= umbral}
    return refs - ruido


def palabras_contenido(texto: str | None) -> set[str]:
    """Las palabras que cuentan para el solape: ni vacías ni genéricas
    («impresora» aparece en media bandeja)."""
    return {
        p for p in _PALABRA_RE.findall((texto or "").lower())
        if len(p) >= 4 and p not in PALABRAS_VACIAS and p not in GENERICAS
    }


def palabras_de_producto(descripciones: Iterable[str], extra: Iterable[str] = ()) -> set[str]:
    """Lo que identifica la máquina en las líneas de pedido: tokens con cifras
    (3000u, 20w) y palabras largas que no sean genéricas; más las palabras del
    modelo FLUX si se conoce."""
    salida: set[str] = set()
    for d in list(descripciones) + list(extra):
        for token in re.findall(r"[\w\-]+", (d or "").lower()):
            limpio = token.strip("-_")
            if not limpio or limpio in GENERICAS or limpio in PALABRAS_VACIAS:
                continue
            if any(ch.isdigit() for ch in limpio) or len(limpio) >= 5:
                salida.add(limpio)
    return salida


def motivo_de(
    cand: Candidato, *, asunto_actual: str, texto_actual: str, refs_actuales: set[str],
    palabras_actuales: set[str], productos: set[str],
) -> str | None:
    """Por qué entra el correo viejo, o None si no viene a cuento."""
    if asunto_actual and asunto_normalizado(cand.asunto) == asunto_actual:
        return "mismo asunto"
    texto_cand = f"{cand.asunto}\n{cand.texto}"
    refs_cand = referencias(texto_cand)
    comunes = refs_actuales & refs_cand
    if comunes:
        return f"menciona la referencia {sorted(comunes)[0]}"
    if productos:
        palabras_cand = {t.strip("-_") for t in re.findall(r"[\w\-]+", texto_cand.lower())}
        en_cand = productos & palabras_cand
        if en_cand:
            return f"misma máquina o producto ({sorted(en_cand)[0]})"
    if palabras_actuales:
        solape = palabras_actuales & palabras_contenido(texto_cand)
        if (len(solape) >= SOLAPE_MIN_PALABRAS
                and len(solape) / max(len(palabras_actuales), 1) >= SOLAPE_MIN_FRACCION):
            return f"solape de palabras ({len(solape)})"
    return None


def elegir(
    candidatos: Iterable[Candidato], *, asunto_actual: str | None, texto_actual: str,
    productos: Iterable[str] = (), maximo: int = 5,
) -> list[tuple[Candidato, str]]:
    """Los correos viejos que entran, el más reciente primero, con su motivo.
    Un hilo aporta como mucho su mensaje más reciente que case: el resto del
    hilo repetiría lo mismo."""
    asunto_norm = asunto_normalizado(asunto_actual)
    ordenados = sorted(candidatos, key=lambda c: c.fecha or datetime.min.replace(tzinfo=None),
                       reverse=True)
    refs = referencias_de_hoy(texto_actual, ordenados)
    palabras = palabras_contenido(texto_actual)
    prods = set(productos)
    elegidos: list[tuple[Candidato, str]] = []
    hilos_vistos: set[str] = set()
    for cand in ordenados:
        if len(elegidos) >= maximo:
            break
        if cand.thread_id in hilos_vistos or not cand.texto.strip():
            continue
        motivo = motivo_de(
            cand, asunto_actual=asunto_norm, texto_actual=texto_actual, refs_actuales=refs,
            palabras_actuales=palabras, productos=prods,
        )
        if motivo is None:
            continue
        hilos_vistos.add(cand.thread_id)
        elegidos.append((cand, motivo))
    return elegidos


# --- de dónde salen los candidatos ----------------------------------------------------


def _candidatos_de_hilos(
    session: Session, thread_ids: list[str], *, quien_por_hilo: dict[str, str] | None = None,
    contacto_por_hilo: dict[str, str] | None = None,
) -> list[Candidato]:
    """El mensaje más reciente de cada hilo (solo ese: así se traen pocos
    cuerpos, no cientos, por cada clic)."""
    if not thread_ids:
        return []
    ultimo = (
        select(EmailMessage.thread_id.label("tid"), func.max(EmailMessage.sent_at).label("m"))
        .where(EmailMessage.thread_id.in_(thread_ids), EmailMessage.sent_at.is_not(None))
        .group_by(EmailMessage.thread_id).subquery()
    )
    filas = session.execute(
        select(
            EmailMessage.id, EmailMessage.thread_id, EmailMessage.sent_at,
            EmailMessage.direction, EmailMessage.from_email, EmailMessage.subject,
            EmailMessage.body_text, EmailMessage.body_html, EmailThread.subject,
        ).join(EmailThread, EmailThread.id == EmailMessage.thread_id)
        .join(ultimo, (EmailMessage.thread_id == ultimo.c.tid)
              & (EmailMessage.sent_at == ultimo.c.m))
        .order_by(EmailMessage.sent_at.desc())
    ).all()
    salida: list[Candidato] = []
    for mid, tid, sent_at, direccion, de, asunto, body_text, body_html, asunto_hilo in filas:
        texto = recortar(sin_cita(texto_de_mensaje(body_text, body_html)), MAX_CHARS_HISTORICO)
        salida.append(Candidato(
            id=mid, thread_id=tid, fecha=sent_at,
            lado="nosotros" if direccion == EmailDirection.OUTBOUND else "cliente",
            de=de or "", asunto=asunto or asunto_hilo or "", texto=texto,
            quien=(quien_por_hilo or {}).get(tid), contact_id=(contacto_por_hilo or {}).get(tid),
        ))
    return salida


def candidatos_del_contacto(
    session: Session, contacto: Contact, *, excluir_thread_id: str | None,
) -> list[Candidato]:
    """Los mensajes de los demás hilos del contacto, los más recientes primero.
    Sin hilo actual (un correo nuevo) no se excluye ninguno."""
    consulta = select(EmailThread.id).where(EmailThread.contact_id == contacto.id)
    if excluir_thread_id:
        consulta = consulta.where(EmailThread.id != excluir_thread_id)
    ids = list(session.scalars(
        consulta.order_by(EmailThread.last_message_at.desc()).limit(HILOS_MAX)))
    return _candidatos_de_hilos(session, ids)


def empresa_agrupable(contacto: Contact, dominios_gratuitos: Iterable[str]) -> Company | None:
    """La empresa por la que se agrupa el nivel 4, o None: solo el registro de
    empresa de BoHub (nunca el dominio del correo), y no si esa «empresa» es
    en realidad un dominio gratuito."""
    empresa = contacto.company
    if empresa is None:
        return None
    gratuitos = {d.lower() for d in dominios_gratuitos}
    dominio = (empresa.domain or "").strip().lower().lstrip("@")
    nombre = (empresa.name or "").strip().lower()
    if dominio in gratuitos or nombre in gratuitos:
        return None
    return empresa


def hilos_de_la_empresa(
    session: Session, contacto: Contact, empresa: Company, *, excluir_thread_id: str | None,
    limite: int = 15,
) -> tuple[list[dict[str, Any]], list[Candidato]]:
    """`(asuntos etiquetados, candidatos con cuerpo)` de las OTRAS personas de
    la empresa. Los asuntos son títulos y fechas; los candidatos sirven solo
    si el alcance es «toda la empresa»."""
    companeros = session.execute(
        select(Contact.id, Contact.first_name, Contact.last_name, Contact.email).where(
            Contact.company_id == empresa.id, Contact.id != contacto.id,
        )
    ).all()
    if not companeros:
        return [], []
    nombres = {
        cid: (" ".join(p for p in (nombre, apellido) if p).strip() or email or "otra persona")
        for cid, nombre, apellido, email in companeros
    }
    consulta = select(EmailThread.id, EmailThread.subject, EmailThread.last_message_at,
                      EmailThread.contact_id).where(EmailThread.contact_id.in_(list(nombres)))
    if excluir_thread_id:
        consulta = consulta.where(EmailThread.id != excluir_thread_id)
    hilos = session.execute(
        consulta.order_by(EmailThread.last_message_at.desc()).limit(limite)).all()
    asuntos = [
        {"thread_id": tid, "fecha": fecha.isoformat() if fecha else None,
         "asunto": asunto or "(sin asunto)", "quien": nombres.get(cid, "otra persona"),
         "contact_id": cid}
        for tid, asunto, fecha, cid in hilos
    ]
    candidatos = _candidatos_de_hilos(
        session, [h["thread_id"] for h in asuntos],
        quien_por_hilo={h["thread_id"]: h["quien"] for h in asuntos},
        contacto_por_hilo={h["thread_id"]: h["contact_id"] for h in asuntos},
    )
    return asuntos, candidatos


# --- el presupuesto -----------------------------------------------------------------


def ajustar_al_presupuesto(
    ctx: Any, *, presupuesto: int, medir: Callable[[Any], int],
) -> int:
    """Quita lo más viejo del nivel 4 (cuerpos primero, luego asuntos) y del
    nivel 3 hasta que lo que se le pasa al modelo cabe en el presupuesto. El
    hilo y el expediente no se tocan: si no caben, no cabe nada más. Devuelve
    los tokens estimados finales y anota en `ctx.recortes` lo que se dejó
    fuera."""
    tokens = medir(ctx)
    while tokens > presupuesto:
        # 1. Cuerpos del nivel 4, el más viejo primero.
        con_texto = [e for e in ctx.empresa_asuntos if e.get("texto")]
        if con_texto:
            mas_viejo = min(con_texto, key=lambda e: e.get("fecha") or "")
            mas_viejo.pop("texto", None)
            ctx.recortes.append(f"empresa: cuerpo de «{mas_viejo.get('asunto')}»")
        elif ctx.empresa_asuntos:
            mas_viejo = min(ctx.empresa_asuntos, key=lambda e: e.get("fecha") or "")
            ctx.empresa_asuntos.remove(mas_viejo)
            ctx.recortes.append(f"empresa: «{mas_viejo.get('asunto')}»")
        elif ctx.historico:
            mas_viejo = min(ctx.historico, key=lambda h: h.get("fecha") or "")
            ctx.historico.remove(mas_viejo)
            ctx.recortes.append(f"histórico: «{mas_viejo.get('asunto')}»")
        elif (getattr(ctx, "catalogo", None) or {}).get("consumibles"):
            # 3. El catálogo, lo último: primero los consumibles (del final),
            # luego las máquinas hasta dejar dos.
            quitado = ctx.catalogo["consumibles"].pop()
            ctx.recortes.append(f"catálogo: consumible «{quitado.get('nombre')}»")
        elif len((getattr(ctx, "catalogo", None) or {}).get("maquinas") or []) > 2:
            quitado = ctx.catalogo["maquinas"].pop()
            ctx.recortes.append(f"catálogo: máquina «{quitado.get('nombre')}»")
        else:
            break                          # hilo + expediente: no se recorta más
        tokens = medir(ctx)
    return tokens
