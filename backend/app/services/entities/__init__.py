"""Multi-entity filter/column registry (Sprint Filtros & Listas).

Generalises the Contact-only segment engine + field schema to every
list-bearing entity in the CRM (companies, email threads, Brevo
template/campaign caches). The registry is the single source of truth
the unified `<EntityTable>` + `<EntityFilterBuilder>` consume via
`GET /api/entities/{entity}/filter-schema`.
"""
from app.services.entities.registry import (
    EntityDescriptor,
    get_entity,
    list_entities,
    list_fields_for_entity,
    register_entity,
)

__all__ = [
    "EntityDescriptor",
    "get_entity",
    "list_entities",
    "list_fields_for_entity",
    "register_entity",
]
