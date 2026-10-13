"""Comprobación del Cuadre: «Bomedia» en lo que lee un cliente (fuente `mysql`).

Bomedia salió del ecosistema el 13/10/2026: todo lo que acaba delante de un
cliente va a nombre de Streamtec (castellano) o MQ Europe (resto de idiomas).
Esta comprobación recorre los textos GUARDADOS de cara al cliente y marca los
que aún la nombran, uno por texto, con el campo y la frase: es a la vez la
alarma del día que alguien cree una plantilla copiando una vieja y el
inventario de lo que hay que repasar a mano (las ~32 plantillas comerciales
viejas, FESPA, SmartJet… son datos y las repasa Bart; aquí no se corrige nada).

Qué recorre (todo lo que sale hacia un cliente sin pasar por la guardia del
asistente):
- plantillas de correo (asunto, cuerpo HTML y cuerpo de texto) y firmas;
- el nombre visible del remitente de cada alias (el «De:» de los correos);
- pasos de workflow que mandan o preparan correo (asunto, cuerpo, remitente);
- Configuración ERP: plantillas del aviso de envío, del correo de presupuesto
  y del de factura; la identidad, los textos legales y los bancos de las
  empresas emisoras y los nombres de serie (salvo la serie 1, ver abajo); los
  almacenes de recogida (van en el albarán de devolución);
- formularios web: nombre (es el título de la página pública), marca, mensaje
  de gracias, aspecto y los textos de los campos;
- líneas de las plantillas de oferta (van en el PDF);
- plantillas de Brevo (asunto, remitente y HTML);
- correos programados aún pendientes (salen sin que nadie los vuelva a mirar);
- la base de conocimiento que pinta los correos compuestos: marcas vivas,
  productos, títulos de imágenes y de vídeos;
- la marca de las webs de `sitios_web` (el remitente de los borradores de
  lead y la {{marca}} del acuse).

Qué NO marca (a propósito): las direcciones y dominios de `bomedia.net` (son
direcciones vivas, no menciones; `empresas_grupo.menciones` ya no las cuenta);
la serie 1 —emisora «Bomedia S.L.» y su nombre de serie— que sigue así en
FACTUSOL por decisión de Bart; los dominios solo de correo de `sitios_web`
(bomedia.net sigue dado de alta); las cuentas de contrapartida («Bomedia
Sabadell»…), que nunca salen en un documento; y el histórico (correos ya
enviados, ofertas emitidas).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.erp.cuadre.contexto import Contexto
from app.erp.cuadre.registry import (
    ENTIDAD_TEXTO_CLIENTE,
    FUENTE_MYSQL,
    Hallazgo,
    comprobacion,
)
from app.services import empresas_grupo as eg

#: Ajustes ERP (bloque `series_config`) con plantillas de correo a clientes.
CLAVES_PLANTILLAS_AJUSTES: dict[str, str] = {
    "shipment_email_templates": "Plantillas del aviso de envío",
    "quote_email_templates": "Plantillas del correo de presupuesto",
    "invoice_email_templates": "Plantillas del correo de factura",
}
#: La serie que se queda como «Bomedia S.L.» (decisión de Bart, 13/10/2026).
SERIE_EXCLUIDA = 1
#: Los pasos de workflow que mandan o preparan un correo, y sus textos.
PASOS_CORREO = ("action_send_email", "action_prepare_email_draft")
CAMPOS_PASO = ("subject", "subject_override", "body_html", "body_text", "from_name",
               "from_alias_display_name")

#: Dónde se arregla cada tipo de texto (la pantalla que ya lo edita).
PLANTILLAS = "/emails/plantillas"
FIRMAS = "/account/firmas"
CUENTA = "/account"
AJUSTES_ERP = "/erp/settings"
MARKETING = "/marketing/templates"
PROGRAMADOS = "/emails/programados"
OFERTAS = "/erp/ofertas"

PISTA = ("Cámbialo por Streamtec (si el texto va en castellano) o MQ Europe (otros idiomas). "
         "Las direcciones @bomedia.net pueden quedarse.")


@dataclass(frozen=True)
class Texto:
    """Un texto guardado de cara al cliente: de qué almacén y fila, cómo se
    llama, qué campo es y su texto plano."""
    tipo: str            # clave corta del almacén (entra en `entidad_id`)
    id: str
    nombre: str          # lo que lee Bart para encontrarlo
    campo: str
    texto: str
    enlace: str | None = None


def _plano(html: str | None) -> str:
    """El texto que ve el cliente, para buscar en él: sin marcas de énfasis
    (html2text pone `_Bomedia_` para la cursiva y la palabra no se vería), con
    el texto alternativo de las imágenes (una firma que es solo un logo «Bomedia»)
    y sin las URL de los enlaces (un `?utm_source=bomedia` no lo lee nadie)."""
    if not html or "<" not in html:
        return html or ""
    import html2text  # noqa: PLC0415

    conversor = html2text.HTML2Text()
    conversor.body_width = 0
    conversor.ignore_emphasis = True
    conversor.ignore_links = True
    conversor.images_to_alt = True
    return conversor.handle(html).strip()


def _cadenas(valor: Any, ruta: str = "") -> Iterator[tuple[str, str]]:
    """`(ruta, texto)` de cada cadena dentro de un JSON (plantillas por serie e
    idioma: `es.asunto`, `5.en.cuerpo`…)."""
    if isinstance(valor, str):
        yield ruta or "texto", valor
    elif isinstance(valor, dict):
        for k, v in valor.items():
            yield from _cadenas(v, f"{ruta}.{k}" if ruta else str(k))
    elif isinstance(valor, list):
        for i, v in enumerate(valor):
            yield from _cadenas(v, f"{ruta}[{i}]")


def _json(raw: str | None) -> Any:
    try:
        return json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None


def textos_cliente(session: Any) -> Iterator[Texto]:
    """Todos los textos guardados de cara al cliente, almacén por almacén."""
    yield from _textos_plantillas(session)
    yield from _textos_remitentes(session)
    yield from _textos_workflows(session)
    yield from _textos_ajustes(session)
    yield from _textos_formularios(session)
    yield from _textos_ofertas(session)
    yield from _textos_brevo(session)
    yield from _textos_programados(session)
    yield from _textos_conocimiento(session)
    yield from _textos_sitios(session)


def _textos_plantillas(session: Any) -> Iterator[Texto]:
    from app.email_templates.models import EmailTemplate  # noqa: PLC0415
    from app.models.crm import EmailSignature  # noqa: PLC0415

    for t in session.scalars(select(EmailTemplate)):
        nombre = f"Plantilla «{t.name or t.id}»"
        yield Texto("plantilla", t.id, nombre, "asunto", t.subject or "", PLANTILLAS)
        yield Texto("plantilla", t.id, nombre, "cuerpo", _plano(t.body_html), PLANTILLAS)
        # La parte de texto plano sale tal cual (acuses, borradores de lead).
        yield Texto("plantilla", t.id, nombre, "cuerpo (texto)", t.body_text or "", PLANTILLAS)
    for f in session.scalars(select(EmailSignature)):
        yield Texto("firma", f.id, f"Firma «{f.name}»", "firma", _plano(f.html_content), FIRMAS)


def _textos_remitentes(session: Any) -> Iterator[Texto]:
    """El nombre visible del «De:» de cada alias: con él salen todos los
    correos del ERP (factura, presupuesto, oferta, aviso de envío)."""
    from app.models.crm import User, UserEmailAliasPref  # noqa: PLC0415

    filas = session.execute(
        select(UserEmailAliasPref, User.full_name, User.email)
        .join(User, User.id == UserEmailAliasPref.user_id)
        .where(User.is_active.is_(True)))
    vistos_gmail: set[str] = set()
    for p, nombre_usuario, email_usuario in filas:
        alias = (p.alias_email or "").strip().lower()
        # El nombre que pone Gmail es del alias, el mismo para todos: una vez.
        if alias and alias not in vistos_gmail:
            vistos_gmail.add(alias)
            yield Texto("remitente_gmail", alias, f"Remitente «{alias}» (Gmail)",
                        "nombre visible", p.gmail_display_name or "", CUENTA)
        # El de BoHub lo elige cada usuario para su alias.
        yield Texto("remitente", p.id,
                    f"Remitente «{alias}» de {nombre_usuario or email_usuario}",
                    "nombre visible", p.display_name_override or "", CUENTA)


def _textos_workflows(session: Any) -> Iterator[Texto]:
    from app.models.workflows import Workflow, WorkflowStep  # noqa: PLC0415

    nombres_wf = {w.id: w.name for w in session.scalars(select(Workflow))}
    for p in session.scalars(select(WorkflowStep).where(WorkflowStep.type.in_(PASOS_CORREO))):
        cfg = _json(p.config_json) or {}
        accion = "enviar correo" if p.type == "action_send_email" else "preparar borrador"
        etiqueta = f"Workflow «{nombres_wf.get(p.workflow_id, p.workflow_id)}» · {accion}"
        for campo in CAMPOS_PASO:
            if isinstance(cfg.get(campo), str):
                yield Texto("paso_wf", p.id, etiqueta, campo, _plano(cfg[campo]),
                            f"/admin/workflows/{p.workflow_id}")


def _textos_ajustes(session: Any) -> Iterator[Texto]:
    from app.erp.factusol_pdf import companies_config  # noqa: PLC0415
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    config = series_config(session) or {}
    for clave, titulo in CLAVES_PLANTILLAS_AJUSTES.items():
        for ruta, texto in _cadenas(config.get(clave)):
            yield Texto("ajuste", clave, f"Configuración ERP · {titulo}", ruta, _plano(texto),
                        AJUSTES_ERP)
    # La identidad de cada emisora (nombre, dirección, textos legales, pie,
    # bancos…) va en el PDF de su serie. La 1 sigue siendo «Bomedia S.L.».
    for serie, empresa in companies_config(session).items():
        if serie == SERIE_EXCLUIDA:
            continue
        for ruta, texto in _cadenas(empresa):
            yield Texto("emisora", str(serie), f"Configuración ERP · Empresa emisora serie {serie}",
                        ruta, texto, AJUSTES_ERP)
    nombres = config.get("names") if isinstance(config.get("names"), dict) else {}
    for serie, nombre in nombres.items():
        if str(serie) != str(SERIE_EXCLUIDA) and isinstance(nombre, str):
            yield Texto("serie", str(serie), f"Configuración ERP · Nombre de la serie {serie}",
                        "nombre", nombre, AJUSTES_ERP)
    for ruta, texto in _cadenas(config.get("pickup_warehouses"), "almacenes"):
        yield Texto("ajuste", "pickup_warehouses", "Configuración ERP · Almacenes de recogida",
                    ruta, texto, AJUSTES_ERP)


def _textos_formularios(session: Any) -> Iterator[Texto]:
    from app.models.web_forms import WebForm, WebFormField  # noqa: PLC0415

    nombres = {}
    for f in session.scalars(select(WebForm)):
        nombres[f.id] = f.name
        etiqueta = f"Formulario web «{f.name}»"
        enlace = f"/admin/forms/{f.id}/editor"
        # El nombre es el título de la página pública; la marca, la del acuse.
        yield Texto("form", f.id, etiqueta, "nombre", f.name or "", enlace)
        yield Texto("form", f.id, etiqueta, "marca", f.brand or "", enlace)
        yield Texto("form", f.id, etiqueta, "mensaje de gracias",
                    _plano(f.submit_success_message), enlace)
        for ruta, texto in _cadenas(_json(f.appearance_json), "aspecto"):
            yield Texto("form", f.id, etiqueta, ruta, texto, enlace)
    for c in session.scalars(select(WebFormField)):
        etiqueta = f"Formulario web «{nombres.get(c.form_id, c.form_id)}»"
        enlace = f"/admin/forms/{c.form_id}/editor"
        for campo in ("label", "placeholder", "help_text", "default_value"):
            valor = getattr(c, campo, None)
            if valor:
                yield Texto("form", c.form_id, etiqueta, f"campo {c.field_key} · {campo}", valor,
                            enlace)
        for ruta, texto in _cadenas(_json(c.options_json), f"campo {c.field_key} · opciones"):
            yield Texto("form", c.form_id, etiqueta, ruta, texto, enlace)


def _textos_ofertas(session: Any) -> Iterator[Texto]:
    from app.erp.models.ofertas import OfertaPlantilla, OfertaPlantillaLinea  # noqa: PLC0415

    nombres = {p.id: p.nombre for p in session.scalars(select(OfertaPlantilla))}
    for ln in session.scalars(select(OfertaPlantillaLinea)):
        yield Texto("oferta_pl", ln.plantilla_id,
                    f"Plantilla de oferta «{nombres.get(ln.plantilla_id, ln.plantilla_id)}»",
                    f"línea {ln.sku or ln.codart or ''}".strip(), ln.descripcion or "", OFERTAS)


def _textos_brevo(session: Any) -> Iterator[Texto]:
    from app.models.brevo import BrevoTemplateCache  # noqa: PLC0415

    activas = session.scalars(
        select(BrevoTemplateCache).where(BrevoTemplateCache.is_active.is_(True)))
    for t in activas:
        etiqueta = f"Plantilla de Brevo «{t.name}»"
        yield Texto("brevo", t.id, etiqueta, "asunto", t.subject or "", MARKETING)
        yield Texto("brevo", t.id, etiqueta, "remitente", t.sender_name or "", MARKETING)
        yield Texto("brevo", t.id, etiqueta, "cuerpo", _plano(t.html_content), MARKETING)


def _textos_programados(session: Any) -> Iterator[Texto]:
    """Los correos programados que aún no han salido: cuando les toque saldrán
    sin que nadie los vuelva a mirar."""
    from app.models.crm import EmailMessage  # noqa: PLC0415

    pendientes = session.scalars(
        select(EmailMessage).where(EmailMessage.scheduled_status == "pending"))
    for m in pendientes:
        etiqueta = f"Correo programado «{m.subject or m.id}»"
        yield Texto("programado", m.id, etiqueta, "asunto", m.subject or "", PROGRAMADOS)
        yield Texto("programado", m.id, etiqueta, "cuerpo",
                    _plano(m.body_html) or m.body_text or "", PROGRAMADOS)


def _textos_conocimiento(session: Any) -> Iterator[Texto]:
    """Lo que pinta un correo compuesto tal cual, sin pasar por la guardia del
    asistente: marcas, fichas de producto y títulos de imágenes y vídeos."""
    from app.models.conocimiento import (  # noqa: PLC0415
        ImagenWeb,
        MarcaCatalogo,
        ProductoCatalogo,
        VideoKb,
    )

    for m in session.scalars(select(MarcaCatalogo).where(MarcaCatalogo.ido_en.is_(None))):
        etiqueta = f"Marca de la base de conocimiento «{m.nombre}»"
        yield Texto("marca", m.nombre, etiqueta, "nombre", m.nombre, AJUSTES_ERP)
        for ruta, texto in _cadenas(_json(m.urls_json), "urls"):
            yield Texto("marca", m.nombre, etiqueta, ruta, texto, AJUSTES_ERP)
        for ruta, texto in _cadenas(_json(m.datos_json), "diseño"):
            yield Texto("marca", m.nombre, etiqueta, ruta, texto, AJUSTES_ERP)
    productos = session.scalars(select(ProductoCatalogo).where(
        ProductoCatalogo.ido_en.is_(None), ProductoCatalogo.descartado.is_(False),
        ProductoCatalogo.visible.is_(True)))
    for p in productos:
        clave = f"{p.fuente}:{p.origen_id}"
        etiqueta = f"Producto del catálogo «{p.nombre}»"
        for campo, texto in (("nombre", p.nombre), ("marca", p.marca),
                             (f"descripción ({p.idioma})", p.descripcion or "")):
            yield Texto("producto", clave, etiqueta, campo, texto, AJUSTES_ERP)
        for ruta, texto in _cadenas(_json(p.caracteristicas_json), f"características ({p.idioma})"):
            yield Texto("producto", clave, etiqueta, ruta, texto, AJUSTES_ERP)
    for i in session.scalars(select(ImagenWeb).where(ImagenWeb.ido_en.is_(None),
                                                     ImagenWeb.oculta.is_(False))):
        yield Texto("imagen", i.id, f"Imagen de la web «{i.titulo or i.id}»", "título",
                    i.titulo or "", AJUSTES_ERP)
    for v in session.scalars(select(VideoKb).where(VideoKb.ido_en.is_(None),
                                                   VideoKb.visible.is_(True))):
        yield Texto("video", v.id, f"Vídeo «{v.titulo or v.video_id}»", "título", v.titulo or "",
                    AJUSTES_ERP)


def _textos_sitios(session: Any) -> Iterator[Texto]:
    """La marca de cada web: es el remitente de los borradores de lead y la
    {{marca}} del acuse. Los dominios solo de correo (bomedia.net) no."""
    from app.models.sitios import TIPO_WEB, SitioWeb  # noqa: PLC0415

    for s in session.scalars(select(SitioWeb).where(SitioWeb.tipo == TIPO_WEB)):
        yield Texto("sitio", s.clave, f"Web «{s.dominio}»", "marca", s.marca or "", AJUSTES_ERP)


def agrupar(textos: Iterable[Texto]) -> list[dict[str, Any]]:
    """Los textos con «Bomedia», uno por fila de origen: sus campos y las
    frases (sin repetir). Es el inventario: lo que enseña la pantalla y el
    listado que se repasa a mano."""
    por_origen: dict[tuple[str, str], dict[str, Any]] = {}
    for t in textos:
        frases = eg.frases(t.texto)
        if not frases:
            continue
        item = por_origen.setdefault((t.tipo, t.id), {
            "tipo": t.tipo, "id": t.id, "nombre": t.nombre, "enlace": t.enlace,
            "campos": [], "frases": [],
        })
        if t.campo not in item["campos"]:
            item["campos"].append(t.campo)
        for f in frases:
            if f not in item["frases"]:
                item["frases"].append(f)
    return sorted(por_origen.values(), key=lambda i: (i["tipo"], i["nombre"].lower()))


def entidad_id(tipo: str, ident: str) -> str:
    """`<almacén>:<id>` en los 64 caracteres de la columna; si no cabe (el
    nombre de una marca, un producto), con un resumen que no choque con otro."""
    clave = f"{tipo}:{ident}"
    if len(clave) <= 64:
        return clave
    resumen = hashlib.sha1(clave.encode("utf-8")).hexdigest()[:16]
    return f"{clave[:47]}~{resumen}"


@comprobacion(
    id="bomedia_en_textos_cliente", orden=20,
    titulo="«Bomedia» en textos que lee el cliente",
    descripcion=("Plantillas, firmas, remitentes, workflows, avisos, formularios, emisoras, "
                 "plantillas de oferta y de Brevo, correos programados y catálogo que todavía "
                 "nombran Bomedia (las direcciones @bomedia.net y la serie 1 de FACTUSOL no "
                 "cuentan)."),
    severidad="media", fuente=FUENTE_MYSQL, grupo="crm",
)
def bomedia_en_textos_cliente(ctx: Contexto) -> Iterator[Hallazgo]:
    for item in agrupar(textos_cliente(ctx.session)):
        frases = item["frases"]
        resto = f" (y {len(frases) - 1} frase(s) más)" if len(frases) > 1 else ""
        yield Hallazgo(
            entidad_tipo=ENTIDAD_TEXTO_CLIENTE,
            entidad_id=entidad_id(item["tipo"], item["id"]),
            etiqueta=item["nombre"][:200],
            detalle=(f"Nombra «Bomedia» en {', '.join(item['campos'])}: «{frases[0]}»{resto}."),
            pista_de_arreglo=PISTA,
            enlace=item["enlace"],
            arreglo_enlace=item["enlace"],
            arreglo_boton="Abrir donde se edita" if item["enlace"] else None,
            huella_datos={"tipo": item["tipo"], "id": item["id"], "frases": sorted(frases)},
            datos={"tipo": item["tipo"], "campos": item["campos"], "frases": frases},
        )
