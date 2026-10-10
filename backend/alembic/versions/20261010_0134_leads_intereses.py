"""Respuesta a leads — el catálogo de intereses en datos y varios intereses
por lead.

- `lead_interests`: el catálogo (código estable, etiqueta, descripción para el
  modelo, comercial, orden, activo), sembrado con la lista de partida de
  `app.services.leads.intereses.DE_PARTIDA` (13 intereses).
- `lead_classifications.interests_json` / `corrected_interests_json`: la lista
  entera, ordenada por relevancia (el primero es el principal, que sigue en
  `interest` / `corrected_interest` para filtrar y para la copia del contacto).

Datos (sin pérdida: ninguna clasificación se queda sin interés):

1. Los códigos de la primera lista (08/10/2026) pasan a los nuevos
   (`RENOMBRADOS`): `uv_gran_formato` → `uv_grande`, `laser_cnc` →
   `corte_laser`, `consumibles` y `repuestos` → `tienda`, `servicio_tecnico`
   → `soporte_postventa`; `vending`, `distribucion` y `otro` no cambian.
   `uv_pequeno_mediano` no se puede repartir sin adivinar: se queda en
   `uv_mediano` y el motivo lo anota; son pocos y Bart los corrige a mano.
   Lo mismo en `corrected_interest` y en `contacts.lead_interest`.
2. Cada clasificación recibe su lista de uno (`["<código>"]`), y la corregida
   la suya si la hay.
3. El mapa de plantillas (`erp_settings.factusol_series_json` →
   `lead_response.mapa`): las claves pasan a los códigos nuevos
   (`uv_pequeno_mediano:xx` vale para `uv_pequeno:xx` y `uv_mediano:xx`) y las
   plantillas que hasta ahora se resolvían por su nombre antiguo («Lead · UV
   pequeño-mediano (ES)», «UV gran formato», «Láser y CNC») quedan escritas en
   el mapa para los códigos nuevos, así que las 30 plantillas siguen
   resolviendo igual que antes.

Sin DEFAULT en TEXT (MySQL lo rechaza). Las tablas se tocan solo si existen
(el test de la migración corre esta revisión sola sobre tablas mínimas).

Revision ID: 20261010_0134
Revises: 20261010_0133
"""

from __future__ import annotations

import json
import logging
import unicodedata
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20261010_0134"
down_revision: str | None = "20261010_0133"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

#: Lo que se le añade al motivo de las clasificaciones que eran
#: `uv_pequeno_mediano` (van a `uv_mediano`; la talla hay que confirmarla).
NOTA_UV = (" · Migración 10/10/2026: era «UV pequeño-mediano»; talla por confirmar "
           "(pequeño o mediano).")
IDIOMAS_CON_PLANTILLA: tuple[str, ...] = ("es", "en", "fr", "de", "nl", "pt")
#: Vuelta atrás (con pérdida: los códigos nuevos sin equivalente van a `otro`).
DESHACER: dict[str, str] = {
    "uv_pequeno": "uv_pequeno_mediano", "uv_mediano": "uv_pequeno_mediano",
    "uv_grande": "uv_gran_formato", "corte_laser": "laser_cnc", "grabado_laser": "laser_cnc",
    "cnc": "laser_cnc", "tienda": "consumibles", "soporte_postventa": "servicio_tecnico",
    "dtf": "otro", "packaging": "otro",
}


def _plano(texto: str) -> str:
    sin = "".join(
        c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c)
    )
    return " ".join(sin.lower().split())


def _tablas(conn: sa.Connection) -> set[str]:
    return set(sa.inspect(conn).get_table_names())


def _columnas(conn: sa.Connection, tabla: str) -> set[str]:
    return {c["name"] for c in sa.inspect(conn).get_columns(tabla)}


# --- el catálogo --------------------------------------------------------------


def _sembrar_catalogo(conn: sa.Connection, ahora: datetime) -> int:
    from app.services.leads.intereses import DE_PARTIDA  # noqa: PLC0415

    existentes = {
        r[0] for r in conn.execute(sa.text("SELECT code FROM lead_interests")).fetchall()
    }
    sembrados = 0
    for orden, d in enumerate(DE_PARTIDA):
        if d["codigo"] in existentes:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO lead_interests (code, label, description, is_commercial, position, "
                "is_active, created_at, updated_at) VALUES (:code, :label, :description, "
                ":comercial, :position, 1, :t, :t)"
            ),
            {"code": d["codigo"], "label": d["etiqueta"], "description": d["descripcion"],
             "comercial": 1 if d["comercial"] else 0, "position": orden, "t": ahora},
        )
        sembrados += 1
    return sembrados


# --- las clasificaciones ------------------------------------------------------


def _migrar_clasificaciones(conn: sa.Connection, ahora: datetime) -> dict[str, int]:
    """Códigos nuevos en `interest` y `corrected_interest`, la nota en los
    que eran «UV pequeño-mediano» y la lista de uno en las columnas JSON."""
    from app.services.leads.intereses import RENOMBRADOS  # noqa: PLC0415

    filas = conn.execute(sa.text(
        "SELECT id, interest, corrected_interest, reason, interests_json, "
        "corrected_interests_json FROM lead_classifications"
    )).fetchall()
    cuenta = {"filas": len(filas), "renombradas": 0, "uv_anotadas": 0, "sin_interes": 0}
    for fila_id, interest, corrected, reason, lista_json, corregida_json in filas:
        cambios: dict[str, Any] = {}
        nuevo = RENOMBRADOS.get(interest or "", interest)
        if nuevo != interest:
            cambios["interest"] = nuevo
            cuenta["renombradas"] += 1
            if interest == "uv_pequeno_mediano":
                # La nota no se recorta nunca: si el motivo no cabe, se
                # recorta el motivo (la nota es lo que Bart tiene que ver).
                base = reason or ""
                if len(base) + len(NOTA_UV) > 500:
                    base = base[: 500 - len(NOTA_UV) - 1].rstrip() + "…"
                cambios["reason"] = base + NOTA_UV
                cuenta["uv_anotadas"] += 1
        nuevo_corregido = RENOMBRADOS.get(corrected or "", corrected)
        if nuevo_corregido != corrected:
            cambios["corrected_interest"] = nuevo_corregido
        if not lista_json and nuevo:
            cambios["interests_json"] = json.dumps([nuevo])
        if not corregida_json and nuevo_corregido:
            cambios["corrected_interests_json"] = json.dumps([nuevo_corregido])
        if not nuevo:
            cuenta["sin_interes"] += 1
        if not cambios:
            continue
        cambios["updated_at"] = ahora
        asignaciones = ", ".join(f"{k} = :{k}" for k in cambios)
        conn.execute(
            sa.text(f"UPDATE lead_classifications SET {asignaciones} WHERE id = :id"),
            {**cambios, "id": fila_id},
        )
    return cuenta


def _migrar_contactos(conn: sa.Connection) -> int:
    from app.services.leads.intereses import RENOMBRADOS  # noqa: PLC0415

    total = 0
    for viejo, nuevo in RENOMBRADOS.items():
        res = conn.execute(
            sa.text("UPDATE contacts SET lead_interest = :nuevo WHERE lead_interest = :viejo"),
            {"nuevo": nuevo, "viejo": viejo},
        )
        total += res.rowcount or 0
    return total


# --- el mapa de plantillas ----------------------------------------------------


def _plantillas_por_nombre(conn: sa.Connection) -> dict[str, str]:
    """Nombre plano → id de la plantilla (la más reciente manda)."""
    if "email_templates" not in _tablas(conn):
        return {}
    filas = conn.execute(sa.text(
        "SELECT id, name FROM email_templates WHERE name LIKE '%Lead%' ORDER BY created_at DESC"
    )).fetchall()
    salida: dict[str, str] = {}
    for tpl_id, name in filas:
        salida.setdefault(_plano(name or ""), str(tpl_id))
    return salida


def _reescribir_mapa(mapa: dict[str, Any], por_nombre: dict[str, str]) -> dict[str, str]:
    """El mapa con los códigos nuevos más las plantillas que se resolvían por
    su nombre antiguo, escritas para los códigos nuevos."""
    from app.services.leads.intereses import RENOMBRADOS_MAPA  # noqa: PLC0415
    from app.services.leads.plantillas import NOMBRES_ANTIGUOS  # noqa: PLC0415

    nuevo: dict[str, str] = {}
    for clave, valor in mapa.items():
        interes, _, idioma = str(clave).partition(":")
        valor = str(valor) if valor is not None else ""
        for codigo in RENOMBRADOS_MAPA.get(interes, (interes,)):
            nuevo.setdefault(f"{codigo}:{idioma}", valor)
    for codigo, contenidos in NOMBRES_ANTIGUOS.items():
        for idioma in IDIOMAS_CON_PLANTILLA:
            clave = f"{codigo}:{idioma}"
            if clave in nuevo:
                continue
            for contenido in contenidos:
                tpl_id = por_nombre.get(_plano(f"Lead · {contenido} ({idioma.upper()})"))
                if tpl_id:
                    nuevo[clave] = tpl_id
                    break
    return nuevo


def _migrar_mapa(conn: sa.Connection, ahora: datetime) -> int | None:
    """Devuelve cuántas filas tiene el mapa nuevo, o `None` si no había."""
    fila = conn.execute(sa.text(
        "SELECT id, factusol_series_json FROM erp_settings WHERE id = 'singleton'"
    )).first()
    if fila is None or not fila[1]:
        return None
    try:
        data = json.loads(fila[1])
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    lead = data.get("lead_response")
    if not isinstance(lead, dict):
        return None
    mapa = lead.get("mapa") if isinstance(lead.get("mapa"), dict) else {}
    lead["mapa"] = _reescribir_mapa(mapa, _plantillas_por_nombre(conn))
    data["lead_response"] = lead
    columnas = _columnas(conn, "erp_settings")
    sql = "UPDATE erp_settings SET factusol_series_json = :j"
    params: dict[str, Any] = {"j": json.dumps(data, ensure_ascii=False)}
    if "updated_at" in columnas:
        sql += ", updated_at = :t"
        params["t"] = ahora
    conn.execute(sa.text(sql + " WHERE id = 'singleton'"), params)
    return len(lead["mapa"])


def upgrade() -> None:
    op.create_table(
        "lead_interests",
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_commercial", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )
    with op.batch_alter_table("lead_classifications") as batch:
        batch.add_column(sa.Column("interests_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("corrected_interests_json", sa.Text(), nullable=True))

    conn = op.get_bind()
    ahora = datetime.now(UTC)
    tablas = _tablas(conn)
    sembrados = _sembrar_catalogo(conn, ahora)
    cuenta = _migrar_clasificaciones(conn, ahora)
    contactos = (
        _migrar_contactos(conn)
        if "contacts" in tablas and "lead_interest" in _columnas(conn, "contacts") else 0
    )
    mapa = _migrar_mapa(conn, ahora) if "erp_settings" in tablas else None
    logger.info(
        "leads.intereses: catálogo sembrado (%d); clasificaciones %d, renombradas %d, "
        "«UV pequeño-mediano» → uv_mediano anotadas %d, sin interés %d; contactos "
        "renombrados %d; mapa de plantillas %s",
        sembrados, cuenta["filas"], cuenta["renombradas"], cuenta["uv_anotadas"],
        cuenta["sin_interes"], contactos,
        f"{mapa} filas" if mapa is not None else "sin configuración",
    )


def downgrade() -> None:
    """Quita la tabla y las columnas y devuelve los códigos antiguos donde
    los hay (con pérdida: `dtf` y `packaging` no existían, van a `otro`; la
    nota del motivo se queda)."""
    conn = op.get_bind()
    tablas = _tablas(conn)
    for nuevo, viejo in DESHACER.items():
        conn.execute(
            sa.text("UPDATE lead_classifications SET interest = :viejo WHERE interest = :nuevo"),
            {"viejo": viejo, "nuevo": nuevo},
        )
        conn.execute(
            sa.text("UPDATE lead_classifications SET corrected_interest = :viejo "
                    "WHERE corrected_interest = :nuevo"),
            {"viejo": viejo, "nuevo": nuevo},
        )
        if "contacts" in tablas and "lead_interest" in _columnas(conn, "contacts"):
            conn.execute(
                sa.text("UPDATE contacts SET lead_interest = :viejo WHERE lead_interest = :nuevo"),
                {"viejo": viejo, "nuevo": nuevo},
            )
    if "erp_settings" in tablas:
        fila = conn.execute(sa.text(
            "SELECT factusol_series_json FROM erp_settings WHERE id = 'singleton'"
        )).first()
        if fila is not None and fila[0]:
            try:
                data = json.loads(fila[0])
            except (TypeError, ValueError):
                data = None
            lead = data.get("lead_response") if isinstance(data, dict) else None
            if isinstance(lead, dict) and isinstance(lead.get("mapa"), dict):
                viejo_mapa: dict[str, str] = {}
                for clave, valor in lead["mapa"].items():
                    intereses, _, idioma = str(clave).partition(":")
                    codigos = intereses.split("+")
                    if len(codigos) != 1:
                        continue        # las combinaciones no existían
                    viejo_mapa.setdefault(f"{DESHACER.get(codigos[0], codigos[0])}:{idioma}",
                                          str(valor or ""))
                lead["mapa"] = viejo_mapa
                conn.execute(
                    sa.text("UPDATE erp_settings SET factusol_series_json = :j "
                            "WHERE id = 'singleton'"),
                    {"j": json.dumps(data, ensure_ascii=False)},
                )
    with op.batch_alter_table("lead_classifications") as batch:
        batch.drop_column("corrected_interests_json")
        batch.drop_column("interests_json")
    op.drop_table("lead_interests")
