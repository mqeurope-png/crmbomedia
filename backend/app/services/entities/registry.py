"""Entity registry for the unified filter/column system.

Each `EntityDescriptor` binds an entity key (`contact`, `company`, …)
to its SQLAlchemy model + the `FieldSpec` whitelist that drives both
filtering (the rule engine) and the column configurator. The engine's
`build_entity_filter(entity, tree)` reads `field_specs` / `id_column` /
`base_model` off the descriptor.

Contact is registered here too (reusing the canonical
`segments.fields.FIELD_SPECS`) so there's one lookup table for the new
`/api/entities/{entity}/filter-schema` endpoint — but the legacy
Contact paths (`build_filter`, `/api/segments/available-fields`) keep
working untouched.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.services.segments.fields import (
    FIELD_SPECS as CONTACT_FIELD_SPECS,
)
from app.services.segments.fields import (
    FieldSpec,
    field_spec_to_ui,
)


@dataclass(frozen=True)
class EntityDescriptor:
    key: str
    label: str
    base_model: Any
    field_specs: Mapping[str, FieldSpec]
    id_attr: str = "id"
    # Default column the unified list endpoint sorts by when the caller
    # doesn't ask for one (PR-B consumes this).
    default_sort: str = "created_at"
    default_sort_dir: str = "desc"

    @property
    def id_column(self) -> Any:
        return getattr(self.base_model, self.id_attr)

    def sort_column(self, key: str) -> Any | None:
        """Resolve a sort key against the entity's registered fields.
        Returns the SQLAlchemy column for valid keys, None otherwise —
        the route layer maps None → 400 so callers can't sort on
        arbitrary attributes (extends the anti-injection boundary to
        ordering)."""
        spec = self.field_specs.get(key)
        if spec is None or not spec.sortable or spec.column is None:
            return None
        return spec.column

    def serialize_row(self, row: Any) -> dict[str, Any]:
        """Project a model row to a dict using the registered fields.

        Each `column`-source spec contributes its raw attribute (column
        keys come from `spec.column.key`). `computed` specs with a
        `concat` extras tuple (Contact's `name` = first_name + " " +
        last_name) get their parts joined here so the column renders
        a real value in `<EntityTable>` instead of "—". Other computed
        sources (no `concat`) and `related_table` sources are skipped —
        their join expansion happens in per-entity rendering (PR-E for
        contacts adds tag_objects etc.).

        Sprint Filtros & Listas (PR-B + PR-Cb hotfix): this keeps the
        new generic `/api/entities/{entity}/search` from needing a
        Pydantic schema per entity; each registered field is the
        contract."""
        out: dict[str, Any] = {"id": getattr(row, self.id_attr)}
        for spec in self.field_specs.values():
            # Computed with explicit `concat(first, last)` extras — e.g.
            # Contact `name`. Project as " ".join(non-empty parts) so
            # the column lands a real string in the row dict.
            if spec.source == "computed" and "concat" in spec.extras:
                parts = [
                    getattr(row, attr, None) for attr in spec.extras["concat"]
                ]
                joined = " ".join(str(p) for p in parts if p).strip()
                out[spec.key] = joined or None
                continue
            if spec.source != "column" or spec.column is None:
                continue
            attr = spec.column.key
            value = getattr(row, attr, None)
            # Enum → its string value so JSON serialises cleanly across
            # SQLAlchemy native_enum / values_callable variants.
            if hasattr(value, "value") and not isinstance(value, (bytes, str, int, float, bool)):
                value = value.value
            out[spec.key] = value
        return out


_REGISTRY: dict[str, EntityDescriptor] = {}


def register_entity(descriptor: EntityDescriptor) -> None:
    _REGISTRY[descriptor.key] = descriptor


def get_entity(key: str) -> EntityDescriptor | None:
    return _REGISTRY.get(key)


def list_entities() -> list[str]:
    return sorted(_REGISTRY.keys())


def list_fields_for_entity(key: str) -> list[dict[str, Any]] | None:
    """Serialise an entity's fields to the UI shape (filter dropdowns +
    column configurator). Returns None for an unknown entity so the
    route layer can 404."""
    descriptor = get_entity(key)
    if descriptor is None:
        return None
    return [field_spec_to_ui(spec) for spec in descriptor.field_specs.values()]


def _register_builtin_entities() -> None:
    """Wire the five entities. Imports are local to avoid import cycles
    (the field modules import models; this module is imported early via
    the API layer)."""
    from app.models.brevo import BrevoCampaignCache, BrevoTemplateCache
    from app.models.crm import Company, Contact, EmailThread
    from app.services.entities.fields_brevo import (
        BREVO_CAMPAIGN_FIELD_SPECS,
        BREVO_TEMPLATE_FIELD_SPECS,
    )
    from app.services.entities.fields_company import COMPANY_FIELD_SPECS
    from app.services.entities.fields_email import EMAIL_THREAD_FIELD_SPECS

    register_entity(
        EntityDescriptor(
            key="contact",
            label="Contactos",
            base_model=Contact,
            field_specs=CONTACT_FIELD_SPECS,
            default_sort="created_at",
            default_sort_dir="desc",
        )
    )
    register_entity(
        EntityDescriptor(
            key="company",
            label="Empresas",
            base_model=Company,
            field_specs=COMPANY_FIELD_SPECS,
            default_sort="name",
            default_sort_dir="asc",
        )
    )
    register_entity(
        EntityDescriptor(
            key="email_thread",
            label="Emails",
            base_model=EmailThread,
            field_specs=EMAIL_THREAD_FIELD_SPECS,
            default_sort="last_message_at",
            default_sort_dir="desc",
        )
    )
    register_entity(
        EntityDescriptor(
            key="brevo_template",
            label="Plantillas Brevo",
            base_model=BrevoTemplateCache,
            field_specs=BREVO_TEMPLATE_FIELD_SPECS,
            default_sort="modified_at_brevo",
            default_sort_dir="desc",
        )
    )
    register_entity(
        EntityDescriptor(
            key="brevo_campaign",
            label="Campañas Brevo",
            base_model=BrevoCampaignCache,
            field_specs=BREVO_CAMPAIGN_FIELD_SPECS,
            default_sort="sent_at",
            default_sort_dir="desc",
        )
    )


_register_builtin_entities()
