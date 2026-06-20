"""PR-Import-Agile-Completo.

Cubre:
- Whitelist Agile incluye Horario (unificado entre cuentas) y
  CONTACTO Persona (key canónica).
- `Productos`, `Producto`, `etiquetas`, `interests` se intercepta
  ANTES del whitelist y se convierte en tag names — NO viven en
  `custom_fields`.
- Mismo contacto desde dos cuentas con Horario distinto →
  concatenación con " · ".
- Backfill: la migración 20260623_0063 aplica la transformación a
  contactos ya importados con custom_fields legacy.
"""
from __future__ import annotations

import json

from app.integrations.agilecrm.custom_field_rules import (
    canonical_keep_key,
    is_kept_custom_field,
    is_taglike_custom_field,
    split_taglike_value,
)
from app.integrations.agilecrm.mapper import (
    extract_taglike_to_tag_names,
    map_agilecrm_contact_to_internal,
)

# ---------------------------------------------------------------------
# Helper rules
# ---------------------------------------------------------------------


def test_is_taglike_custom_field_recognizes_csv_variants() -> None:
    assert is_taglike_custom_field("Productos")
    assert is_taglike_custom_field("Producto")
    assert is_taglike_custom_field("etiquetas")
    assert is_taglike_custom_field("interests")
    assert is_taglike_custom_field("PRODUCTOS")  # case-insensitive
    assert not is_taglike_custom_field("Horario")
    assert not is_taglike_custom_field("CONTACTO Persona")


def test_split_taglike_value_handles_multiple_separators() -> None:
    # Comma-separated
    assert split_taglike_value("A2000, FLUX, MBO") == ["A2000", "FLUX", "MBO"]
    # Pipe-separated
    assert split_taglike_value("A2000|FLUX|MBO") == ["A2000", "FLUX", "MBO"]
    # Semicolon
    assert split_taglike_value("A2000; FLUX") == ["A2000", "FLUX"]
    # Newlines (Agile multiline export)
    assert split_taglike_value("A2000\nFLUX\nMBO") == ["A2000", "FLUX", "MBO"]
    # Mixed
    assert split_taglike_value("A2000, FLUX | MBO\nFRESHDESK") == [
        "A2000",
        "FLUX",
        "MBO",
        "FRESHDESK",
    ]
    # Dedup case-insensitive
    assert split_taglike_value("a2000, A2000") == ["a2000"]
    # Lista directa de API
    assert split_taglike_value(["A2000", "FLUX"]) == ["A2000", "FLUX"]
    # Garbage tokens filtrados
    assert split_taglike_value("A2000, , null, none, n/a, -") == ["A2000"]
    # None
    assert split_taglike_value(None) == []


def test_canonical_keep_key_normalizes_contacto_persona() -> None:
    assert canonical_keep_key("CONTACTO Persona") == "CONTACTO Persona"
    assert canonical_keep_key("contacto persona") == "CONTACTO Persona"
    assert canonical_keep_key("contacto_persona") == "CONTACTO Persona"
    assert canonical_keep_key("Horario") == "Horario"
    # Unmapped
    assert canonical_keep_key("Foo") is None


def test_is_kept_custom_field_combines_brevo_and_agile_whitelists() -> None:
    assert is_kept_custom_field("Horario")  # Brevo whitelist
    assert is_kept_custom_field("HORARIO")  # case-insensitive
    assert is_kept_custom_field("CONTACTO Persona")  # Agile-extra whitelist
    assert is_kept_custom_field("CONTACTO_PERSONA")  # variant
    # Productos no entra ni por whitelist ni por extra-agile —
    # va por tags-route (matching exacto upper-case).
    assert not is_kept_custom_field("Productos")
    assert not is_kept_custom_field("Productos_inventado")


# ---------------------------------------------------------------------
# Mapper end-to-end with Agile payloads
# ---------------------------------------------------------------------


def _make_agile_payload(
    *,
    first_name: str = "Bart",
    last_name: str = "P",
    email: str = "b@e.com",
    custom_props: list[tuple[str, str]] = None,
) -> dict:
    return {
        "id": "ag-1",
        "properties": [
            {"type": "SYSTEM", "name": "first_name", "value": first_name},
            {"type": "SYSTEM", "name": "last_name", "value": last_name},
            {"type": "SYSTEM", "name": "email", "value": email},
            *[
                {"type": "CUSTOM", "name": k, "value": v}
                for (k, v) in (custom_props or [])
            ],
        ],
    }


def test_mapper_productos_become_tags_not_custom_field() -> None:
    """artisjetspain CSV con `Productos: A2000, FLUX, MBO` →
    tag_names tiene los 3, custom_fields no tiene Productos."""
    payload = _make_agile_payload(
        custom_props=[("Productos", "A2000, FLUX, MBO")]
    )
    record, _ = map_agilecrm_contact_to_internal(payload)
    assert set(record["tag_names"]) == {"A2000", "FLUX", "MBO"}
    assert record["custom_fields"] is None


def test_mapper_producto_singular_become_tags() -> None:
    """boprint24 CSV con `Producto: FLUX`."""
    payload = _make_agile_payload(custom_props=[("Producto", "FLUX")])
    record, _ = map_agilecrm_contact_to_internal(payload)
    assert record["tag_names"] == ["FLUX"]


def test_mapper_etiquetas_become_tags() -> None:
    payload = _make_agile_payload(
        custom_props=[("etiquetas", "vip|cliente-cierre")]
    )
    record, _ = map_agilecrm_contact_to_internal(payload)
    assert set(record["tag_names"]) == {"vip", "cliente-cierre"}


def test_mapper_interests_become_tags() -> None:
    """mboprinters CSV con `interests`."""
    payload = _make_agile_payload(
        custom_props=[("interests", "Educación, Industrial")]
    )
    record, _ = map_agilecrm_contact_to_internal(payload)
    assert set(record["tag_names"]) == {"Educación", "Industrial"}


def test_mapper_horario_preserved_as_custom_field() -> None:
    """Horario sí va al JSON custom_fields (unificado)."""
    payload = _make_agile_payload(custom_props=[("Horario", "09:00-18:00")])
    record, _ = map_agilecrm_contact_to_internal(payload)
    cf = json.loads(record["custom_fields"])
    assert cf == {"Horario": "09:00-18:00"}


def test_mapper_contacto_persona_kept_with_canonical_key() -> None:
    """boprint CSV con `CONTACTO Persona: Juan Pérez`."""
    payload = _make_agile_payload(
        custom_props=[("CONTACTO Persona", "Juan Pérez")]
    )
    record, _ = map_agilecrm_contact_to_internal(payload)
    cf = json.loads(record["custom_fields"])
    assert cf == {"CONTACTO Persona": "Juan Pérez"}


def test_mapper_contacto_persona_normalizes_variant_keys() -> None:
    """`contacto_persona` (snake case) llega como CONTACTO Persona."""
    payload = _make_agile_payload(
        custom_props=[("contacto_persona", "Ana López")]
    )
    record, _ = map_agilecrm_contact_to_internal(payload)
    cf = json.loads(record["custom_fields"])
    assert "CONTACTO Persona" in cf
    assert cf["CONTACTO Persona"] == "Ana López"


def test_mapper_combines_native_tags_and_taglike_tokens() -> None:
    """Native Agile tags + Productos-tokens → tag_names unifica
    sin duplicar case-insensitive."""
    payload = {
        "id": "ag-1",
        "tags": ["vip", "industrial"],
        "properties": [
            {"type": "SYSTEM", "name": "first_name", "value": "C"},
            {"type": "SYSTEM", "name": "email", "value": "c@e.com"},
            {
                "type": "CUSTOM",
                "name": "Productos",
                "value": "FLUX, industrial",  # 'industrial' colisiona
            },
        ],
    }
    record, _ = map_agilecrm_contact_to_internal(payload)
    lower = {t.lower() for t in record["tag_names"]}
    assert lower == {"vip", "industrial", "flux"}


def test_mapper_unknown_custom_field_still_dropped() -> None:
    """Custom fields fuera del whitelist (y no tag-like) → se descartan."""
    payload = _make_agile_payload(
        custom_props=[
            ("RandomNoise", "x"),
            ("sib_contact_owner", "y"),
        ]
    )
    record, _ = map_agilecrm_contact_to_internal(payload)
    assert record["custom_fields"] is None


def test_mapper_horario_from_two_accounts_concatenates() -> None:
    """Si el mismo payload tuviera Horario con valores distintos
    (caso de merge entre 2 cuentas pre-procesado), se concatenan
    con " · "."""
    payload = {
        "id": "ag-1",
        "properties": [
            {"type": "SYSTEM", "name": "first_name", "value": "C"},
            {"type": "SYSTEM", "name": "email", "value": "c@e.com"},
            {"type": "CUSTOM", "name": "Horario", "value": "L-V 9-18"},
            {"type": "CUSTOM", "name": "HORARIO", "value": "Sábados 10-14"},
        ],
    }
    record, _ = map_agilecrm_contact_to_internal(payload)
    cf = json.loads(record["custom_fields"])
    # Keys uppercase normalize to "Horario" or "HORARIO" — el primer
    # name visto gana como key del JSON. Ambos valores aparecen
    # concatenados.
    horario_value = next(iter(cf.values()))
    assert "L-V 9-18" in horario_value
    assert "Sábados 10-14" in horario_value
    assert " · " in horario_value


def test_extract_taglike_only_returns_tokens_from_taglike_fields() -> None:
    payload = {
        "properties": [
            {"type": "CUSTOM", "name": "Productos", "value": "A, B"},
            {"type": "CUSTOM", "name": "Horario", "value": "9-18"},
            {"type": "CUSTOM", "name": "RandomField", "value": "x"},
            {"type": "CUSTOM", "name": "etiquetas", "value": "vip"},
        ],
    }
    tokens = extract_taglike_to_tag_names(payload)
    assert set(tokens) == {"A", "B", "vip"}


# ---------------------------------------------------------------------
# Backfill migration
# ---------------------------------------------------------------------


def test_backfill_migration_logic_on_synthetic_data(tmp_path) -> None:
    """La función `_backfill` de la migración aplica la transformación
    correcta sobre rows en SQLite efímero."""
    from sqlalchemy import create_engine, text

    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    # Crear esquema mínimo con las tablas que usa el backfill.
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE contacts (id TEXT PRIMARY KEY, "
                "custom_fields TEXT)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE tags (id TEXT PRIMARY KEY, name TEXT, "
                "name_normalized TEXT UNIQUE, color TEXT, "
                "description TEXT, created_at TEXT, updated_at TEXT)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE contact_tags ("
                "contact_id TEXT, tag_id TEXT, source TEXT, "
                "assigned_at TEXT, created_at TEXT, updated_at TEXT, "
                "PRIMARY KEY (contact_id, tag_id))"
            )
        )
        # Seed 3 contacts cubriendo los 3 casos.
        conn.execute(
            text("INSERT INTO contacts VALUES (:i, :cf)"),
            [
                {
                    "i": "c-artisjet",
                    "cf": json.dumps(
                        {"Productos": "A2000, FLUX", "Horario": "9-18"}
                    ),
                },
                {
                    "i": "c-boprint24",
                    "cf": json.dumps(
                        {
                            "Producto": "MBO",
                            "etiquetas": "vip|industrial",
                        }
                    ),
                },
                {
                    "i": "c-boprint",
                    "cf": json.dumps(
                        {
                            "contacto_persona": "Juan Pérez",
                            "RandomKept": "stays",
                        }
                    ),
                },
            ],
        )

    # Ejecutar _backfill contra el engine.
    import importlib.util
    from pathlib import Path

    mig_path = (
        Path(__file__).parent.parent
        / "alembic"
        / "versions"
        / "20260623_0063_agile_custom_field_migration.py"
    )
    spec = importlib.util.spec_from_file_location("_mig", str(mig_path))
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)

    from sqlalchemy.orm import Session

    with Session(engine) as session:
        mig._backfill(session)
        session.commit()

    with engine.connect() as conn:
        # artisjet: Productos → tags + Horario preservado
        cf_artisjet = conn.execute(
            text("SELECT custom_fields FROM contacts WHERE id = 'c-artisjet'")
        ).scalar()
        parsed = json.loads(cf_artisjet)
        assert "Productos" not in parsed
        assert parsed.get("Horario") == "9-18"
        tag_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM contact_tags "
                "WHERE contact_id = 'c-artisjet'"
            )
        ).scalar()
        assert tag_count == 2  # A2000, FLUX

        # boprint24: Producto + etiquetas → 3 tags
        cf_boprint24 = conn.execute(
            text("SELECT custom_fields FROM contacts WHERE id = 'c-boprint24'")
        ).scalar()
        assert cf_boprint24 in (None, "null")
        tag_count_2 = conn.execute(
            text(
                "SELECT COUNT(*) FROM contact_tags "
                "WHERE contact_id = 'c-boprint24'"
            )
        ).scalar()
        assert tag_count_2 == 3  # MBO, vip, industrial

        # boprint: contacto_persona normalizado, RandomKept preservado
        cf_boprint = conn.execute(
            text("SELECT custom_fields FROM contacts WHERE id = 'c-boprint'")
        ).scalar()
        parsed3 = json.loads(cf_boprint)
        assert parsed3.get("CONTACTO Persona") == "Juan Pérez"
        assert parsed3.get("RandomKept") == "stays"
        assert "contacto_persona" not in parsed3


def test_backfill_migration_is_idempotent(tmp_path) -> None:
    """Reejecutar la migración no duplica tags ni reescribe rows
    ya transformados."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.begin() as conn:
        for stmt in [
            "CREATE TABLE contacts (id TEXT PRIMARY KEY, custom_fields TEXT)",
            "CREATE TABLE tags (id TEXT PRIMARY KEY, name TEXT, "
            "name_normalized TEXT UNIQUE, color TEXT, description TEXT, "
            "created_at TEXT, updated_at TEXT)",
            "CREATE TABLE contact_tags ("
            "contact_id TEXT, tag_id TEXT, source TEXT, assigned_at TEXT, "
            "created_at TEXT, updated_at TEXT, "
            "PRIMARY KEY (contact_id, tag_id))",
        ]:
            conn.execute(text(stmt))
        conn.execute(
            text("INSERT INTO contacts VALUES (:i, :cf)"),
            {"i": "c1", "cf": json.dumps({"Productos": "A, B"})},
        )

    import importlib.util
    from pathlib import Path

    mig_path = (
        Path(__file__).parent.parent
        / "alembic"
        / "versions"
        / "20260623_0063_agile_custom_field_migration.py"
    )
    spec = importlib.util.spec_from_file_location("_mig", str(mig_path))
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)

    for _ in range(2):
        with Session(engine) as session:
            mig._backfill(session)
            session.commit()

    with engine.connect() as conn:
        tag_count = conn.execute(
            text("SELECT COUNT(*) FROM contact_tags WHERE contact_id='c1'")
        ).scalar()
        assert tag_count == 2  # A, B, no duplicados
        tags_total = conn.execute(
            text("SELECT COUNT(*) FROM tags")
        ).scalar()
        assert tags_total == 2  # mismas 2 tags
