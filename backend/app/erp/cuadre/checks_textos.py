"""Comprobación del Cuadre: «Bomedia» en lo que lee un cliente (fuente `mysql`).

Bomedia salió del ecosistema el 13/10/2026: todo lo que acaba delante de un
cliente va a nombre de Streamtec (castellano) o MQ Europe (resto de idiomas).
Esta comprobación recorre los textos GUARDADOS de cara al cliente y marca los
que aún la nombran, uno por texto, con el campo y la frase: es a la vez la
alarma del día que alguien cree una plantilla copiando una vieja y el
inventario de lo que hay que repasar a mano (las ~32 plantillas comerciales
viejas, FESPA, SmartJet… son datos y las repasa Bart; aquí no se corrige nada).

Qué recorre: plantillas de correo (asunto y cuerpo), firmas, marcas de la base
de conocimiento, pasos «enviar correo» de los workflows, las plantillas de los
avisos de envío, presupuestos y facturas de Configuración ERP, los textos
públicos de los formularios web, las líneas de las plantillas de ofertas (van
en el PDF) y el asunto y el remitente de las plantillas de Brevo.

Qué NO marca (a propósito): las direcciones y dominios de `bomedia.net` (son
direcciones vivas, no menciones; `empresas_grupo.menciones` ya no las cuenta),
las emisoras de `erp_settings` —la 1 sigue siendo «Bomedia S.L.» para la serie
1 de FACTUSOL por decisión de Bart— y la tabla `sitios_web` (bomedia.net sigue
dado de alta como dominio de correo). Tampoco los correos ya enviados ni las
ofertas emitidas: son histórico.
"""
from __future__ import annotations

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
from app.services.html_sanitizer import html_to_text

#: Ajustes ERP (bloque `series_config`) con textos que se mandan a clientes.
CLAVES_AJUSTES: dict[str, str] = {
    "shipment_email_templates": "Plantillas del aviso de envío",
    "quote_email_templates": "Plantillas del correo de presupuesto",
    "invoice_email_templates": "Plantillas del correo de factura",
}

#: Dónde se arregla cada tipo de texto (la pantalla que ya lo edita).
PLANTILLAS = "/emails/plantillas"
FIRMAS = "/account/firmas"
AJUSTES_ERP = "/erp/settings"
MARKETING = "/marketing/templates"

PISTA = ("Cámbialo por Streamtec (si el texto va en castellano) o MQ Europe (otros idiomas). "
         "Las direcciones @bomedia.net pueden quedarse.")


@dataclass(frozen=True)
class Texto:
    """Un texto guardado de cara al cliente: de qué tabla y fila, cómo se
    llama, qué campo es y su texto plano."""
    tipo: str            # clave corta del almacén (entra en `entidad_id`)
    id: str
    nombre: str          # lo que lee Bart para encontrarlo
    campo: str
    texto: str
    enlace: str | None = None


def _plano(html: str | None) -> str:
    return html_to_text(html) if html and "<" in html else (html or "")


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
    from app.email_templates.models import EmailTemplate  # noqa: PLC0415
    from app.models.conocimiento import MarcaCatalogo  # noqa: PLC0415
    from app.models.crm import EmailSignature  # noqa: PLC0415
    from app.models.workflows import Workflow, WorkflowStep  # noqa: PLC0415

    for t in session.scalars(select(EmailTemplate)):
        nombre = t.name or t.id
        yield Texto("plantilla", t.id, f"Plantilla «{nombre}»", "asunto", t.subject or "",
                    PLANTILLAS)
        yield Texto("plantilla", t.id, f"Plantilla «{nombre}»", "cuerpo",
                    _plano(t.body_html) or t.body_text or "", PLANTILLAS)

    for f in session.scalars(select(EmailSignature)):
        yield Texto("firma", f.id, f"Firma «{f.name}»", "firma", _plano(f.html_content), FIRMAS)

    for m in session.scalars(select(MarcaCatalogo).where(MarcaCatalogo.ido_en.is_(None))):
        etiqueta = f"Marca de la base de conocimiento «{m.nombre}»"
        yield Texto("marca", m.nombre, etiqueta, "nombre", m.nombre, AJUSTES_ERP)
        for ruta, texto in _cadenas(_json(m.urls_json), "urls"):
            yield Texto("marca", m.nombre, etiqueta, ruta, texto, AJUSTES_ERP)
        for ruta, texto in _cadenas(_json(m.datos_json), "diseño"):
            yield Texto("marca", m.nombre, etiqueta, ruta, texto, AJUSTES_ERP)

    nombres_wf = {w.id: w.name for w in session.scalars(select(Workflow))}
    pasos = session.scalars(select(WorkflowStep).where(WorkflowStep.type == "action_send_email"))
    for p in pasos:
        cfg = _json(p.config_json) or {}
        etiqueta = f"Workflow «{nombres_wf.get(p.workflow_id, p.workflow_id)}» · enviar correo"
        enlace = f"/admin/workflows/{p.workflow_id}"
        for campo in ("subject", "body_html", "body_text", "from_name"):
            if isinstance(cfg.get(campo), str):
                yield Texto("paso_wf", p.id, etiqueta, campo, _plano(cfg[campo]), enlace)

    yield from _textos_ajustes(session)
    yield from _textos_formularios(session)
    yield from _textos_ofertas(session)
    yield from _textos_brevo(session)


def _textos_ajustes(session: Any) -> Iterator[Texto]:
    from app.integrations.factusol.service import series_config  # noqa: PLC0415

    config = series_config(session) or {}
    for clave, titulo in CLAVES_AJUSTES.items():
        for ruta, texto in _cadenas(config.get(clave)):
            yield Texto("ajuste", clave, f"Configuración ERP · {titulo}", ruta, _plano(texto),
                        AJUSTES_ERP)


def _textos_formularios(session: Any) -> Iterator[Texto]:
    from app.models.web_forms import WebForm, WebFormField  # noqa: PLC0415

    nombres = {}
    for f in session.scalars(select(WebForm)):
        nombres[f.id] = f.name
        etiqueta = f"Formulario web «{f.name}»"
        enlace = f"/admin/forms/{f.id}/editor"
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
                    f"línea {ln.sku or ln.codart or ''}".strip(), ln.descripcion or "",
                    "/erp/ofertas")


def _textos_brevo(session: Any) -> Iterator[Texto]:
    from app.models.brevo import BrevoTemplateCache  # noqa: PLC0415

    activas = session.scalars(
        select(BrevoTemplateCache).where(BrevoTemplateCache.is_active.is_(True)))
    for t in activas:
        etiqueta = f"Plantilla de Brevo «{t.name}»"
        yield Texto("brevo", t.id, etiqueta, "asunto", t.subject or "", MARKETING)
        yield Texto("brevo", t.id, etiqueta, "remitente", t.sender_name or "", MARKETING)


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


@comprobacion(
    id="bomedia_en_textos_cliente", orden=20,
    titulo="«Bomedia» en textos que lee el cliente",
    descripcion=("Plantillas, firmas, marcas de la base de conocimiento, workflows, avisos, "
                 "formularios y plantillas de oferta que todavía nombran Bomedia (las "
                 "direcciones @bomedia.net y la serie 1 de FACTUSOL no cuentan)."),
    severidad="media", fuente=FUENTE_MYSQL, grupo="crm",
)
def bomedia_en_textos_cliente(ctx: Contexto) -> Iterator[Hallazgo]:
    for item in agrupar(textos_cliente(ctx.session)):
        frases = item["frases"]
        resto = f" (y {len(frases) - 1} frase(s) más)" if len(frases) > 1 else ""
        yield Hallazgo(
            entidad_tipo=ENTIDAD_TEXTO_CLIENTE,
            entidad_id=f"{item['tipo']}:{item['id']}"[:64],
            etiqueta=item["nombre"][:200],
            detalle=(f"Nombra «Bomedia» en {', '.join(item['campos'])}: «{frases[0]}»{resto}."),
            pista_de_arreglo=PISTA,
            enlace=item["enlace"],
            arreglo_enlace=item["enlace"],
            arreglo_boton="Abrir donde se edita" if item["enlace"] else None,
            huella_datos={"tipo": item["tipo"], "id": item["id"], "frases": sorted(frases)},
            datos={"tipo": item["tipo"], "campos": item["campos"], "frases": frases},
        )
