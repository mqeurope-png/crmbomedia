"""Un solo contacto por empresa (1/11/2026).

Sobre cualquier filtro de contactos, quedarse con UNO por empresa: el primero
según un orden de preferencia. Es una sola pieza SQL (ROW_NUMBER por empresa)
que usan la lista de contactos, «seleccionar todo», los segmentos, Brevo y el
CSV, para que todos cuenten lo mismo.

**Orden de preferencia** (configurable; se puede quitar o mover cualquiera):

- `principal`: el marcado como contacto principal en la ficha de empresa;
- `email_valido`: con email y válido;
- `interaccion`: la interacción más reciente (llamada, nota, email, tarea);
- `antiguedad`: el más antiguo (el primero que se dio de alta).

Al final siempre desempata el id, para que el resultado no cambie de una
carga a otra.

**Contactos sin empresa:** con «uno por empresa» quedan fuera (no tienen
empresa por la que elegir) y se cuentan aparte.

**Por qué salió cada uno** (`motivos`): se compara al elegido con el segundo
de su empresa, criterio a criterio, y se dice el primero en el que gana. Si
no hay segundo, «único contacto de la empresa que cumple». Nunca se dice
«principal» si no está marcado.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, case, func, select, union_all
from sqlalchemy.orm import Session

from app.models.crm import Contact

PRINCIPAL, EMAIL_VALIDO, INTERACCION, ANTIGUEDAD = (
    "principal", "email_valido", "interaccion", "antiguedad")
ORDEN_DEFECTO: tuple[str, ...] = (PRINCIPAL, EMAIL_VALIDO, INTERACCION, ANTIGUEDAD)
ETIQUETAS = {
    PRINCIPAL: "Contacto principal",
    EMAIL_VALIDO: "Con email válido",
    INTERACCION: "Interacción más reciente",
    ANTIGUEDAD: "El más antiguo",
}
#: Los campos de reglas que hablan de la EMPRESA: con alguno, la vista va por
#: defecto «uno por empresa».
PREFIJOS_EMPRESA = ("empresa_", "parque_", "compra_")


def normaliza_orden(orden: Iterable[str] | str | None) -> list[str]:
    """La lista de criterios válidos, sin repetir, en el orden dado. None (o
    una cadena vacía) → el orden de siempre; una lista vacía → ninguno (solo
    desempata el id). Una cadena se parte por comas."""
    if orden is None or (isinstance(orden, str) and not orden.strip()):
        return list(ORDEN_DEFECTO)
    if isinstance(orden, str):
        orden = orden.split(",")
    out: list[str] = []
    for o in orden:
        o = str(o or "").strip()
        if o in ORDEN_DEFECTO and o not in out:
            out.append(o)
    return out


#: Cómo se guarda «ningún criterio» (solo desempata el id): una cadena vacía se
#: leería como el orden de siempre.
SIN_CRITERIOS = "ninguno"


def orden_a_texto(orden: Iterable[str] | None) -> str | None:
    """Para guardar en el segmento: None = el de siempre."""
    lista = normaliza_orden(orden)
    if lista == list(ORDEN_DEFECTO):
        return None
    return ",".join(lista) if lista else SIN_CRITERIOS


def tiene_reglas_de_empresa(arbol: Any) -> bool:
    """¿El árbol de reglas mira la empresa (empresa/parque/compras)?"""
    if isinstance(arbol, dict):
        campo = str(arbol.get("field") or "")
        if campo.startswith(PREFIJOS_EMPRESA):
            return True
        return any(tiene_reglas_de_empresa(h) for h in arbol.get("children") or [])
    return False


def _ultima_interaccion(base: Any) -> Any:
    """(contact_id, ultima): la interacción más reciente de cada contacto que
    cumple `base` (solo de esos: con miles de correos y llamadas, agrupar las
    de todos los contactos para quedarse con unos pocos es lo caro)."""
    from app.services.segments.engine import _interaction_sources  # noqa: PLC0415

    partes = [select(cid.label("contact_id"), fecha.label("fecha"))
              .select_from(cid.table).join(Contact, Contact.id == cid)
              .where(fecha.is_not(None), base, Contact.company_id.is_not(None))
              for cid, fecha in _interaction_sources()]
    todas = union_all(*partes).subquery()
    return (select(todas.c.contact_id, func.max(todas.c.fecha).label("ultima"))
            .group_by(todas.c.contact_id).subquery())


def _claves(orden: Sequence[str], ult: Any) -> list[Any]:
    claves: list[Any] = []
    for c in orden:
        if c == PRINCIPAL:
            claves.append(case((Contact.contacto_principal.is_(True), 0), else_=1))
        elif c == EMAIL_VALIDO:
            claves.append(case((and_(Contact.is_email_valid.is_(True),
                                     func.coalesce(func.trim(Contact.email), "") != ""), 0),
                               else_=1))
        elif c == INTERACCION and ult is not None:
            # Primero los que tienen alguna, y de ellos la más reciente.
            claves.extend([case((ult.c.ultima.is_(None), 1), else_=0), ult.c.ultima.desc()])
        elif c == ANTIGUEDAD:
            claves.append(Contact.created_at.asc())
    claves.append(Contact.id.asc())
    return claves


def _ranking(base: Any, orden: Sequence[str], *, columnas: Sequence[Any] = ()) -> Any:
    """Subconsulta con `id`, `company_id`, `rn` (puesto en su empresa) y las
    columnas pedidas, de los contactos CON empresa que cumplen `base`."""
    ult = _ultima_interaccion(base) if INTERACCION in orden else None
    rn = func.row_number().over(partition_by=Contact.company_id,
                                order_by=_claves(orden, ult)).label("rn")
    cols = [Contact.id.label("id"), Contact.company_id.label("company_id"), rn, *columnas]
    if ult is not None:
        cols.append(ult.c.ultima.label("ultima"))
    q = select(*cols).select_from(Contact)
    if ult is not None:
        q = q.outerjoin(ult, ult.c.contact_id == Contact.id)
    return q.where(base, Contact.company_id.is_not(None)).subquery()


def clausula(base: Any, orden: Iterable[str] | None = None) -> Any:
    """`base` reducido a un contacto por empresa (los sin empresa, fuera)."""
    r = _ranking(base, normaliza_orden(orden))
    return Contact.id.in_(select(r.c.id).where(r.c.rn == 1))


def filtro(base: Any, *, uno_por_empresa: bool, orden: Iterable[str] | None = None) -> Any:
    return clausula(base, orden) if uno_por_empresa else base


def recuentos(session: Session, base: Any) -> dict[str, int]:
    """Cuántos contactos cumplen, de cuántas empresas, y cuántos sin empresa."""
    contactos, empresas, sin_empresa = session.execute(
        select(func.count(Contact.id),
               func.count(func.distinct(Contact.company_id)),
               func.sum(case((Contact.company_id.is_(None), 1), else_=0)))
        .where(base)).one()
    return {"contactos": int(contactos or 0), "empresas": int(empresas or 0),
            "sin_empresa": int(sin_empresa or 0)}


def _fecha(v: Any) -> str:
    if isinstance(v, datetime):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, date):
        return v.strftime("%d/%m/%Y")
    return str(v)[:10] if v else ""


def motivos(session: Session, base: Any, orden: Iterable[str] | None,
            ids: Sequence[str]) -> dict[str, dict[str, str]]:
    """Por qué salió cada contacto elegido de `ids` (los de una página): el
    primer criterio en el que gana al segundo de su empresa."""
    if not ids:
        return {}
    orden_l = normaliza_orden(orden)
    empresas = {cid for cid in session.scalars(
        select(Contact.company_id).where(Contact.id.in_(list(ids)),
                                         Contact.company_id.is_not(None)))}
    if not empresas:
        return {}
    r = _ranking(and_(base, Contact.company_id.in_(empresas)), orden_l,
                 columnas=[Contact.contacto_principal.label("principal"),
                           Contact.is_email_valid.label("valido"),
                           Contact.email.label("email"),
                           Contact.created_at.label("alta")])
    filas = session.execute(select(r).where(r.c.rn <= 2)).mappings().all()
    por_empresa: dict[str, dict[int, Any]] = {}
    for f in filas:
        por_empresa.setdefault(f["company_id"], {})[f["rn"]] = f
    out: dict[str, dict[str, str]] = {}
    for _cid, puestos in por_empresa.items():
        primero, segundo = puestos.get(1), puestos.get(2)
        if primero is None or primero["id"] not in ids:
            continue
        out[primero["id"]] = _motivo(primero, segundo, orden_l)
    return out


def _valido(f: Any) -> bool:
    return bool(f["valido"]) and bool((f["email"] or "").strip())


def _motivo(a: Any, b: Any, orden: Sequence[str]) -> dict[str, str]:
    if b is None:
        return {"criterio": "unico", "texto": "Único contacto de la empresa que cumple"}
    for c in orden:
        if c == PRINCIPAL and a["principal"] and not b["principal"]:
            return {"criterio": c, "texto": "Contacto principal (marcado en la ficha)"}
        if c == EMAIL_VALIDO and _valido(a) and not _valido(b):
            return {"criterio": c, "texto": "Tiene email válido y los demás no"}
        if c == INTERACCION:
            ua, ub = a.get("ultima"), b.get("ultima")
            if ua is not None and (ub is None or ua > ub):
                return {"criterio": c, "texto": f"Interacción más reciente ({_fecha(ua)})"}
        if c == ANTIGUEDAD and a["alta"] and b["alta"] and a["alta"] < b["alta"]:
            return {"criterio": c, "texto": f"El más antiguo (alta {_fecha(a['alta'])})"}
    return {"criterio": "desempate", "texto": "Empate en todo: el primero de la lista"}


# --- segmentos ------------------------------------------------------------------------


def sin_no_clientes_finales() -> Any:
    """Fuera los contactos de empresas marcadas «no es cliente final» (la
    financiera, un revendedor, nosotros): no entran en las campañas
    (1/11/2026). Los sin empresa, sí."""
    from sqlalchemy import or_  # noqa: PLC0415

    from app.models.crm import Company  # noqa: PLC0415

    return or_(Contact.company_id.is_(None), Contact.company_id.not_in(
        select(Company.id).where(Company.no_cliente_final.is_(True))))


def filtro_de_segmento(segmento: Any, base: Any) -> Any:
    """El filtro de un segmento (lo que usan las campañas y Brevo): con su «uno
    por empresa», si lo lleva, y sin los contactos de empresas que no son
    cliente final."""
    base = and_(base, sin_no_clientes_finales())
    return filtro(base, uno_por_empresa=bool(getattr(segmento, "uno_por_empresa", False)),
                  orden=getattr(segmento, "orden_contacto", None))
