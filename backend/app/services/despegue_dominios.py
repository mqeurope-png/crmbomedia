"""Despegar los contactos que un dominio compartido pegó a una empresa
(1/11/2026).

`xtec.cat` no identifica un instituto como `gmail.com` no identifica una
empresa, pero el emparejamiento por dominio los trató como si lo hicieran: miles
de docentes acabaron colgados de la única empresa con ese dominio (o de una
«Centro público» creada a partir de él). Esto los separa, sin perder lo bueno.

**Qué se despega.** Un contacto con empresa cuyo correo es de un dominio
`compartido` de la lista. De un proveedor de correo, solo si su empresa lleva
ese mismo dominio (la «Outlook» de outlook.fr, o una creada con gmail.com como
web): eso lo pegó el dominio. Un cliente real con un contacto @gmail.com no se
toca: ese vínculo salió de otro sitio. Se queda vinculado si hay alguna prueba
de que es de esa empresa:

- `factusol`: es él mismo un cliente de FACTUSOL (`factusol_contact_id`);
- `pedido`: figura en un pedido del ERP de esa empresa;
- `oferta`: figura en una oferta de BoHub de esa empresa;
- `correo_factusol`: su correo es el del cliente de FACTUSOL de su empresa
  (F_CLI, leído; nunca se escribe);
- `conciliado`: la conciliación con FACTUSOL por correo lo llevó a esa
  empresa (sus apuntes en la auditoría);
- `a_mano`: alguien le puso esa empresa a mano (la ficha marca `company_id`
  como editado a mano, o la auditoría guarda la asignación);
- `alta_manual`: se dio de alta a mano (origen «Manual») y ninguna
  sincronización lo ha tocado después (sin referencias externas): su empresa
  la eligió quien lo dio de alta. Va aparte para que se vea cuántos se quedan
  solo por esto.

**Cómo.** `company_id` a nulo: ni se borra ni se desactiva. Un apunte por
contacto en `crm_despegue_dominio` con la empresa anterior y el motivo, en un
lote que se deshace entero (`revertir`). Las empresas que se quedan sin
contactos, sin cliente de FACTUSOL, sin CIF, con un dominio de la lista (o sin
dominio) y sin pedidos, ofertas, tareas ni máquinas a mano se archivan (también
con apunte). Las que
tienen cliente de FACTUSOL nunca se archivan: conservan su vínculo, su parque
y su histórico; solo pierden los contactos que no eran suyos.

No escribe en FACTUSOL. Nombres de personas y de centros no salen de aquí:
el plan identifica las empresas por id, código de cliente y dominio.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

MOTIVO = "dominio_compartido"
MOTIVO_EMPRESA = "empresa_vacia_de_dominio_compartido"
RAZONES = ("factusol", "correo_factusol", "conciliado", "pedido", "oferta", "a_mano",
           "alta_manual")
#: Las acciones de la conciliación con FACTUSOL por correo
#: (`integrations/factusol/bulk_match.py`).
_CONCILIACION = ("erp.factusol_bulk_sync_by_email",
                 "erp.factusol_bulk_sync_by_email_create_company",
                 "erp.factusol_bulk_sync_by_email_reassign")
TIPO_CONTACTO, TIPO_EMPRESA = "contacto", "empresa"


def _ahora() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class PlanEmpresa:
    company_id: str
    codcli: str | None
    dominio: str | None
    archivada: bool
    tiene_cif: bool = False
    #: Contactos con empresa = esta, antes del despegue.
    total: int = 0
    #: (contact_id, dominio de la lista) que se despegan.
    despegar: list[tuple[str, str]] = field(default_factory=list)
    #: Los que se quedan, por la primera prueba que tienen.
    quedan: Counter = field(default_factory=Counter)
    archivar: bool = False
    #: Para el CSV de Bart (no sale en la consola).
    nombre: str = ""

    @property
    def candidatos(self) -> int:
        return len(self.despegar) + sum(self.quedan.values())


@dataclass
class Plan:
    empresas: list[PlanEmpresa]
    por_dominio: Counter
    contactos_con_empresa: int
    #: Si se miró el correo de los clientes en F_CLI.
    con_factusol: bool = False
    #: Lo que se hizo de verdad al aplicar (puede ser menos que el plan si
    #: alguien movió contactos entre medias).
    hechos: dict[str, int] = field(default_factory=dict)

    @property
    def a_despegar(self) -> int:
        return sum(len(e.despegar) for e in self.empresas)

    @property
    def se_quedan(self) -> Counter:
        total: Counter = Counter()
        for e in self.empresas:
            total.update(e.quedan)
        return total

    @property
    def a_archivar(self) -> list[PlanEmpresa]:
        return [e for e in self.empresas if e.archivar]


def _marcas_manuales(raw: str | None) -> set[str]:
    try:
        datos = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return set()
    return {str(x) for x in datos} if isinstance(datos, list) else set()


def _asignaciones_a_mano(session: Session) -> set[tuple[str, str]]:
    """(contacto, empresa) que alguien asignó a mano según la auditoría
    (`POST /api/contacts/{id}/assign-company` y el PATCH de la ficha)."""
    from app.core.audit import Action  # noqa: PLC0415
    from app.models.crm import AuditLog  # noqa: PLC0415

    out: set[tuple[str, str]] = set()
    for target, meta in session.execute(
            select(AuditLog.target_id, AuditLog.metadata_json).where(
                AuditLog.action == Action.CONTACT_UPDATED, AuditLog.target_type == "contact",
                AuditLog.metadata_json.like('%company_id%'))):
        try:
            datos = json.loads(meta or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(datos, dict) or not target:
            continue
        # `assign-company` deja {"field": "company_id", "from", "to"}. El PATCH
        # de la ficha no dice a qué empresa: cuenta su marca de editado a mano.
        if datos.get("field") == "company_id" and datos.get("to"):
            out.add((target, str(datos["to"])))
    return out


def _conciliados(session: Session) -> set[tuple[str, str]]:
    """(contacto, empresa) que la conciliación con FACTUSOL por correo dejó
    juntos: la empresa refrescada o creada para el contacto, o a la que se le
    reasignó. (Cuando lo asigna a una empresa que ya estaba vinculada no deja
    apunte: eso lo cubre `correo_factusol`.)"""
    from app.models.crm import AuditLog  # noqa: PLC0415

    out: set[tuple[str, str]] = set()
    for accion, tipo, target, meta in session.execute(
            select(AuditLog.action, AuditLog.target_type, AuditLog.target_id,
                   AuditLog.metadata_json).where(AuditLog.action.in_(_CONCILIACION))):
        try:
            datos = json.loads(meta or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(datos, dict):
            continue
        if accion.endswith("_reassign"):
            if target and datos.get("new_company_id"):
                out.add((target, str(datos["new_company_id"])))
        elif tipo == "company" and target and datos.get("contact_id"):
            out.add((str(datos["contact_id"]), target))
    return out


def leer_f_cli(session: Session) -> list[dict[str, Any]]:
    """F_CLI entera. Solo lee FACTUSOL. Lanza si no se puede leer."""
    from app.integrations.factusol.client import FactusolClient  # noqa: PLC0415
    from app.integrations.factusol.service import ejercicio_for  # noqa: PLC0415

    return FactusolClient.from_settings().load_table(
        "F_CLI", filtro="1=1", ejercicio=ejercicio_for(session))


def codigos_por_nif(filas: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Los NIF de F_CLI con DOS O MÁS códigos de cliente (la misma empresa
    dada de alta varias veces en FACTUSOL). El NIF es la clave interna: no se
    enseña."""
    from app.integrations.factusol.auditoria_iva import codcli_key  # noqa: PLC0415

    por: dict[str, set[str]] = {}
    for f in filas:
        nif = re.sub(r"[^0-9A-Z]", "", str(f.get("NIFCLI") or "").upper())
        cli = codcli_key(f.get("CODCLI"))
        if len(nif) >= 5 and cli:
            por.setdefault(nif, set()).add(cli)
    return {n: c for n, c in por.items() if len(c) >= 2}


def correos_de_factusol(session: Session, *,
                        filas: list[dict[str, Any]] | None = None) -> dict[str, set[str]]:
    """Los correos de cada cliente de F_CLI (por código normalizado). Solo lee
    FACTUSOL. Lanza si no se puede leer: el que llama decide."""
    from app.integrations.factusol.auditoria_iva import codcli_key  # noqa: PLC0415

    if filas is None:
        filas = leer_f_cli(session)
    out: dict[str, set[str]] = {}
    for f in filas:
        # FACTUSOL da el CODCLI como número: `4471`, `'04471'` y `'4471.0'` son
        # el mismo cliente.
        cli = codcli_key(f.get("CODCLI"))
        correos = {c.strip().lower() for c in re.split(r"[;,\s]+", str(f.get("EMACLI") or ""))
                   if "@" in c}
        if cli and correos:
            out.setdefault(cli, set()).update(correos)
    return out


def plan(session: Session, *, correos_factusol: dict[str, set[str]] | None = None) -> Plan:
    """Qué se despega y qué se queda, por empresa. Solo lee. Con
    `correos_factusol` (ver `correos_de_factusol`), también se queda quien
    tenga el correo del cliente de FACTUSOL de su empresa."""
    from app.erp.models.ofertas import Oferta  # noqa: PLC0415
    from app.erp.models.orders import Order  # noqa: PLC0415
    from app.integrations.factusol.auditoria_iva import codcli_key  # noqa: PLC0415
    from app.models.crm import Company, Contact, Task  # noqa: PLC0415
    from app.services import dominios_compartidos as dc  # noqa: PLC0415

    lista = dc.cargar(session, refrescar=True)
    empresas = {cid: PlanEmpresa(company_id=cid, codcli=(codcli or "").strip() or None,
                                 dominio=dominio, archivada=bool(arch), nombre=nombre or "",
                                 tiene_cif=bool((cif or "").strip()))
                for cid, codcli, dominio, arch, nombre, cif in session.execute(
                    select(Company.id, Company.factusol_company_id, Company.domain,
                           Company.is_archived, Company.name, Company.tax_id))}
    pedidos = {(a, b) for a, b in session.execute(
        select(Order.contact_id, Order.company_id).where(
            Order.contact_id.is_not(None), Order.company_id.is_not(None)).distinct())}
    ofertas = {(a, b) for a, b in session.execute(
        select(Oferta.contact_id, Oferta.company_id).where(
            Oferta.contact_id.is_not(None), Oferta.company_id.is_not(None)).distinct())}
    from app.models.crm import ExternalReference  # noqa: PLC0415

    a_mano = _asignaciones_a_mano(session)
    conciliados = _conciliados(session)
    sincronizados = set(session.scalars(select(ExternalReference.contact_id).distinct()))
    por_dominio: Counter = Counter()
    con_empresa = 0
    cols = [Contact.id, Contact.email, Contact.company_id, Contact.factusol_contact_id,
            Contact.manually_edited_fields_json, Contact.origin, Contact.contacto_principal]
    for fila in session.execute(select(*cols).where(Contact.company_id.is_not(None))):
        cid, email, company_id = fila[0], fila[1], fila[2]
        e = empresas.get(company_id)
        if e is None:
            continue
        con_empresa += 1
        e.total += 1
        dominio = lista.de_la_lista(dc.dominio_de_correo(email))
        if dominio is None:
            continue
        if not lista.compartido(dominio) and lista.de_la_lista(e.dominio) != dominio:
            # Proveedor de correo y empresa con otro dominio (o ninguno): el
            # vínculo no lo hizo el dominio.
            continue
        razon = None
        cli = codcli_key(e.codcli) if e.codcli else None
        if (fila[3] or "").strip():
            razon = "factusol"
        elif correos_factusol and cli and (email or "").strip().lower() in \
                correos_factusol.get(cli, ()):
            razon = "correo_factusol"
        elif (cid, company_id) in conciliados:
            razon = "conciliado"
        elif (cid, company_id) in pedidos:
            razon = "pedido"
        elif (cid, company_id) in ofertas:
            razon = "oferta"
        elif ("company_id" in _marcas_manuales(fila[4]) or (cid, company_id) in a_mano
              or fila[6]):
            # Marcado como contacto principal de la empresa: también es a mano.
            razon = "a_mano"
        elif (fila[5] or "").strip().lower() == "manual" and cid not in sincronizados:
            razon = "alta_manual"
        if razon:
            e.quedan[razon] += 1
        else:
            e.despegar.append((cid, dominio))
            por_dominio[dominio] += 1
    tocadas = [e for e in empresas.values() if e.candidatos]
    # Las que se quedan vacías: sin cliente de FACTUSOL, sin CIF, con dominio de
    # la lista (o sin dominio) y sin negocio que las sostenga. Se archivan, no
    # se borran. (Una con CIF no es residuo de un dominio: Brevo puede volver
    # a vincular a alguien por ese CIF, y no debe encontrarla archivada.)
    vacias = [e for e in tocadas if e.despegar and e.total == len(e.despegar)
              and not e.codcli and not e.tiene_cif and not e.archivada
              and (not e.dominio or lista.contiene(e.dominio))]
    if vacias:
        from app.erp.models.historico import ParqueMaquinaManual  # noqa: PLC0415

        ids = [e.company_id for e in vacias]
        con_negocio = set()
        for modelo in (Order, Oferta, Task, ParqueMaquinaManual):
            con_negocio |= set(session.scalars(
                select(modelo.company_id).where(modelo.company_id.in_(ids)).distinct()))
        for e in vacias:
            e.archivar = e.company_id not in con_negocio
    tocadas.sort(key=lambda e: (-len(e.despegar), -e.candidatos, e.company_id))
    return Plan(empresas=tocadas, por_dominio=por_dominio, contactos_con_empresa=con_empresa,
                con_factusol=correos_factusol is not None)


def aplicar(session: Session, p: Plan, *, por: str) -> str:
    """Despega y archiva lo del plan, con un apunte por cosa, en una sola
    transacción. Devuelve el lote. No recalcula: eso lo hace quien llama."""
    from app.models.crm import Company, Contact  # noqa: PLC0415
    from app.models.dominios import DespegueDominio  # noqa: PLC0415

    lote = f"{_ahora():%Y%m%d%H%M}-{uuid.uuid4().hex[:6]}"
    ahora = _ahora()
    hechos = {"contactos": 0, "empresas": 0}
    # El plan se leyó en otra transacción: en MySQL (REPEATABLE READ) seguir
    # en ella sería mirar la foto vieja. Se empieza una nueva y se bloquea.
    session.rollback()
    try:
        for e in p.empresas:
            ids = [cid for cid, _d in e.despegar]
            for i in range(0, len(ids), 500):
                trozo = ids[i:i + 500]
                # Solo los que siguen en ESA empresa (si alguien los movió
                # entre el plan y ahora, no se tocan ni se apuntan).
                siguen = set(session.scalars(select(Contact.id).where(
                    Contact.id.in_(trozo), Contact.company_id == e.company_id)
                    .with_for_update()))
                if not siguen:
                    continue
                session.add_all(DespegueDominio(
                    lote=lote, tipo=TIPO_CONTACTO, objeto_id=cid, empresa_anterior=e.company_id,
                    dominio=d, motivo=MOTIVO, por=por, at=ahora)
                    for cid, d in e.despegar if cid in siguen)
                session.execute(update(Contact).where(
                    Contact.id.in_(siguen), Contact.company_id == e.company_id)
                    .values(company_id=None))
                hechos["contactos"] += len(siguen)
            if e.archivar:
                empresa = session.get(Company, e.company_id)
                if empresa is not None and not empresa.is_archived:
                    restantes = session.scalar(select(func.count()).select_from(Contact).where(
                        Contact.company_id == e.company_id)) or 0
                    if restantes == 0:
                        session.add(DespegueDominio(
                            lote=lote, tipo=TIPO_EMPRESA, objeto_id=e.company_id,
                            dominio=e.dominio, motivo=MOTIVO_EMPRESA, archivada_antes=False,
                            por=por, at=ahora))
                        empresa.is_archived = True
                        empresa.archived_at = datetime.now(UTC)
                        empresa.archived_reason = _razon_archivo(lote)
                        hechos["empresas"] += 1
        _auditar(session, "contacts.despegue_dominio", lote, por,
                 {"contactos": hechos["contactos"], "empresas_archivadas": hechos["empresas"]})
        session.commit()
    except Exception:
        session.rollback()
        raise
    p.hechos = hechos
    logger.info("despegue de dominios: lote %s, %d contactos", lote, hechos["contactos"])
    return lote


def _razon_archivo(lote: str) -> str:
    return f"Dominio compartido (lote {lote})"


def revertir(session: Session, lote: str, *, por: str) -> dict[str, int]:
    """Deshace un lote: cada contacto vuelve a su empresa anterior y cada
    empresa archivada se desarchiva. Si alguien le ha puesto ya otra empresa
    a un contacto, o la anterior ya no existe, ese no se toca (se cuenta)."""
    from app.models.crm import Company, Contact  # noqa: PLC0415
    from app.models.dominios import DespegueDominio  # noqa: PLC0415

    apuntes = list(session.scalars(select(DespegueDominio).where(
        DespegueDominio.lote == lote, DespegueDominio.revertido_at.is_(None))))
    if not apuntes:
        raise ValueError("lote_sin_apuntes")
    ahora = _ahora()
    out = {"contactos": 0, "empresas": 0, "con_otra_empresa": 0, "sin_empresa_anterior": 0}
    try:
        for ap in (a for a in apuntes if a.tipo == TIPO_EMPRESA):
            empresa = session.get(Company, ap.objeto_id)
            # Solo si sigue archivada por ESTE lote (si alguien la desarchivó y
            # la volvió a archivar por otro motivo, eso se respeta).
            if empresa is not None and not ap.archivada_antes and empresa.is_archived \
                    and empresa.archived_reason == _razon_archivo(lote):
                empresa.is_archived, empresa.archived_at, empresa.archived_reason = \
                    False, None, None
                out["empresas"] += 1
            ap.revertido_at = ahora
        existentes = set(session.scalars(select(Company.id).where(Company.id.in_(
            {a.empresa_anterior for a in apuntes if a.empresa_anterior}))))
        for ap in (a for a in apuntes if a.tipo == TIPO_CONTACTO):
            contacto = session.get(Contact, ap.objeto_id)
            if contacto is None:
                pass
            elif contacto.company_id is not None:
                out["con_otra_empresa"] += 1
                continue
            elif ap.empresa_anterior not in existentes:
                out["sin_empresa_anterior"] += 1
                continue
            else:
                contacto.company_id = ap.empresa_anterior
                out["contactos"] += 1
            ap.revertido_at = ahora
        _auditar(session, "contacts.despegue_dominio_revertido", lote, por, out)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return out


def lotes(session: Session) -> list[dict[str, Any]]:
    from app.models.dominios import DespegueDominio  # noqa: PLC0415

    filas = session.execute(
        select(DespegueDominio.lote, DespegueDominio.tipo, func.count(),
               func.min(DespegueDominio.at), func.count(DespegueDominio.revertido_at))
        .group_by(DespegueDominio.lote, DespegueDominio.tipo)
        .order_by(func.min(DespegueDominio.at))).all()
    out: dict[str, dict[str, Any]] = {}
    for lote, tipo, n, at, revertidos in filas:
        d = out.setdefault(lote, {"lote": lote, "at": at, "contactos": 0, "empresas": 0,
                                  "revertidos": 0})
        d["contactos" if tipo == TIPO_CONTACTO else "empresas"] += n
        d["revertidos"] += revertidos
    return list(out.values())


def _auditar(session: Session, accion: str, lote: str, por: str, datos: dict[str, Any]) -> None:
    from app.core.audit import record_event  # noqa: PLC0415

    record_event(session, action=accion, target_type="lote", target_id=lote,
                 actor_email=por, metadata={"lote": lote, **datos})


# --- lo que cambia alrededor ---------------------------------------------------------------


def ya_cliente(session: Session) -> dict[str, int]:
    """Contactos y empresas con la etiqueta YA CLIENTE ahora."""
    from app.models.crm import Contact, ContactTag, Tag  # noqa: PLC0415

    tag_id = session.scalar(select(Tag.id).where(Tag.name_normalized == "ya cliente"))
    if tag_id is None:
        return {"contactos": 0, "empresas": 0}
    contactos = session.scalar(select(func.count()).select_from(ContactTag).where(
        ContactTag.tag_id == tag_id)) or 0
    empresas = session.scalar(select(func.count(func.distinct(Contact.company_id))).select_from(
        ContactTag).join(Contact, Contact.id == ContactTag.contact_id).where(
        ContactTag.tag_id == tag_id, Contact.company_id.is_not(None))) or 0
    return {"contactos": int(contactos), "empresas": int(empresas)}


def ya_cliente_por_empresa(session: Session, p: Plan) -> list[dict[str, Any]]:
    """Por empresa del plan: cuántos de sus YA CLIENTE se despegarían (los
    puestos por el recálculo los quitará el siguiente; los de a mano, no)."""
    from app.models.crm import ContactTag, Tag  # noqa: PLC0415

    tag_id = session.scalar(select(Tag.id).where(Tag.name_normalized == "ya cliente"))
    if tag_id is None:
        return []
    con = {cid: src for cid, src in session.execute(
        select(ContactTag.contact_id, ContactTag.source).where(ContactTag.tag_id == tag_id))}
    out = []
    for e in p.empresas:
        auto = sum(1 for cid, _d in e.despegar if (con.get(cid) or "").startswith("auto:"))
        mano = sum(1 for cid, _d in e.despegar if cid in con
                   and not (con.get(cid) or "").startswith("auto:"))
        if auto or mano:
            out.append({"company_id": e.company_id, "codcli": e.codcli, "dominio": e.dominio,
                        "ya_cliente_auto": auto, "ya_cliente_a_mano": mano})
    return out


def tamanos(session: Session) -> list[dict[str, Any]]:
    """El tamaño de cada vista guardada y cada segmento dinámico de
    contactos ahora (para comparar antes y después)."""
    from app.models.crm import Contact  # noqa: PLC0415

    out = []
    for tipo, oid, nombre, arbol in _arboles(session):
        cond = _condicion(session, arbol)
        if cond is None:
            continue
        n = session.scalar(select(func.count()).select_from(Contact).where(cond)) or 0
        out.append({"tipo": tipo, "id": oid, "nombre": nombre, "contactos": int(n)})
    return out


def cambios_de_tamano(antes: list[dict[str, Any]], despues: list[dict[str, Any]],
                      *, umbral: float = 0.2, minimo: int = 20) -> list[dict[str, Any]]:
    """Las vistas y segmentos que cambian mucho (más de `umbral` y de
    `minimo` contactos)."""
    previo = {(x["tipo"], x["id"]): x for x in antes}
    out = []
    for x in despues:
        a = previo.get((x["tipo"], x["id"]))
        if a is None:
            continue
        dif = x["contactos"] - a["contactos"]
        if abs(dif) >= minimo and abs(dif) > umbral * max(a["contactos"], 1):
            out.append({**x, "antes": a["contactos"], "despues": x["contactos"]})
    return sorted(out, key=lambda x: x["despues"] - x["antes"])


def _view_rules_tree(vista: Any) -> Any:
    from app.api.routes import _view_rules_tree as arbol  # noqa: PLC0415

    return arbol(vista)


def _arboles(session: Session) -> list[tuple[str, str, str, Any]]:
    """(tipo, id, nombre, árbol de reglas) de los segmentos dinámicos y las
    vistas guardadas de contactos."""
    from app.models.crm import ContactView, Segment  # noqa: PLC0415

    out: list[tuple[str, str, str, Any]] = []
    for seg in session.scalars(select(Segment).where(Segment.is_dynamic.is_(True))):
        try:
            out.append(("segmento", seg.id, seg.name, json.loads(seg.rules_json or "{}")))
        except (TypeError, ValueError):
            continue
    for vista in session.scalars(select(ContactView)):
        if (getattr(vista, "entity_type", None) or "contact") not in ("contact", "contacts"):
            continue
        out.append(("vista", vista.id, vista.name, _view_rules_tree(vista)))
    return out


def _condicion(session: Session, arbol: Any) -> Any:
    from app.services.segments.engine import (  # noqa: PLC0415
        SegmentRuleError,
        build_filter,
        session_segment_resolver,
    )

    if not isinstance(arbol, dict) or not arbol:
        return None
    try:
        return build_filter(arbol, segment_resolver=session_segment_resolver(session))
    except (SegmentRuleError, TypeError, ValueError, KeyError):
        return None


def en_riesgo(session: Session, p: Plan) -> list[dict[str, Any]]:
    """Por vista y segmento: cuántos contactos tiene y cuántos de ellos se
    despegarían (los que pueden salir si la vista mira la empresa)."""
    from app.models.crm import Contact  # noqa: PLC0415

    ids = [cid for e in p.empresas for cid, _d in e.despegar]
    out = []
    for tipo, oid, nombre, arbol in _arboles(session):
        cond = _condicion(session, arbol)
        if cond is None:
            continue
        total = session.scalar(select(func.count()).select_from(Contact).where(cond)) or 0
        riesgo = 0
        for i in range(0, len(ids), 1000):
            riesgo += session.scalar(select(func.count()).select_from(Contact).where(
                cond, Contact.id.in_(ids[i:i + 1000]))) or 0
        if riesgo:
            out.append({"tipo": tipo, "id": oid, "nombre": nombre, "contactos": int(total),
                        "se_despegan": int(riesgo)})
    return sorted(out, key=lambda x: -x["se_despegan"])


def huecos_por_gama(session: Session) -> list[dict[str, Any]]:
    """Por gama: empresas que son hueco (la pestaña, vía el resumen) y
    contactos que da la regla «es hueco de…». Con los vínculos sanos, la
    razón es del orden de 1."""
    from app.erp.models.historico import HistoricoGama  # noqa: PLC0415
    from app.erp.models.resumen_empresa import CrmEmpresaGama  # noqa: PLC0415
    from app.models.crm import Contact  # noqa: PLC0415
    from app.services.segments.engine import build_filter  # noqa: PLC0415

    out = []
    for g in session.scalars(select(HistoricoGama).order_by(HistoricoGama.orden)):
        empresas = session.scalar(select(func.count()).select_from(CrmEmpresaGama).where(
            CrmEmpresaGama.gama == g.codigo, CrmEmpresaGama.hueco.is_(True))) or 0
        regla = {"operator": "AND", "children": [
            {"type": "rule", "field": "compra_hueco", "comparator": "in", "value": [g.codigo]}]}
        contactos = session.scalar(select(func.count()).select_from(Contact).where(
            build_filter(regla))) or 0
        out.append({"gama": g.codigo, "nombre": g.nombre, "empresas_hueco": int(empresas),
                    "contactos_regla": int(contactos),
                    "razon": round(contactos / empresas, 1) if empresas else None})
    return out


def despegados_de_empresa(session: Session, company_id: str) -> dict[str, Any] | None:
    """Los contactos que el despegue (sin revertir) quitó a una empresa: para
    que su ficha diga por qué se ha quedado sin contactos."""
    from app.models.dominios import DespegueDominio  # noqa: PLC0415

    filas = session.execute(
        select(DespegueDominio.dominio, DespegueDominio.at).where(
            DespegueDominio.tipo == TIPO_CONTACTO,
            DespegueDominio.empresa_anterior == company_id,
            DespegueDominio.revertido_at.is_(None))).all()
    if not filas:
        return None
    ultimo = max((at for _d, at in filas if at), default=None)
    return {"contactos": len(filas),
            "dominios": sorted({d for d, _at in filas if d}),
            "at": f"{ultimo.isoformat()}Z" if ultimo else None}


def clientes_sin_contactos(session: Session) -> list[dict[str, Any]]:
    """Empresas activas con cliente de FACTUSOL y ningún contacto: no hay a
    quién escribir. Con cuántos les quitó el despegue (sin revertir)."""
    from app.models.crm import Company, Contact  # noqa: PLC0415
    from app.models.dominios import DespegueDominio  # noqa: PLC0415

    con_contactos = select(Contact.company_id).where(Contact.company_id.is_not(None))
    empresas = session.execute(
        select(Company.id, Company.factusol_company_id).where(
            Company.factusol_company_id.is_not(None), func.trim(Company.factusol_company_id) != "",
            Company.is_archived.is_(False), Company.id.not_in(con_contactos))).all()
    quitados = dict(session.execute(
        select(DespegueDominio.empresa_anterior, func.count()).where(
            DespegueDominio.tipo == TIPO_CONTACTO, DespegueDominio.revertido_at.is_(None))
        .group_by(DespegueDominio.empresa_anterior)).all())
    return sorted(({"company_id": cid, "codcli": (cod or "").strip(),
                    "despegados": int(quitados.get(cid, 0))} for cid, cod in empresas),
                  key=lambda x: (-x["despegados"], x["codcli"]))
