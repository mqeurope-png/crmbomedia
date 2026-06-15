"""Brevo template + campaign cache field specs (Sprint Filtros & Listas PR-A).

Both entities are local cache-mirror tables (`brevo_templates_cache`,
`brevo_campaigns_cache`), so filtering/sorting/pagination run server-side
on real columns. Campaign `stats_json` (open%/CTR) and `recipient_list_ids`
are JSON blobs: per Bart's decision they stay **display-only in v1** —
filtering on them is deferred until the metrics are materialised to
columns (riesgo §5 del spec). They're omitted from the filterable specs
here; the column list for display is defined frontend-side.
"""
from __future__ import annotations

from app.models.brevo import BrevoCampaignCache, BrevoTemplateCache
from app.services.segments.fields import FieldSpec

_STRING = (
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "eq",
    "neq",
    "is_null",
    "is_not_null",
)
_DATE = (
    "before",
    "after",
    "between",
    "in_last_n_days",
    "not_in_last_n_days",
    "older_than_n_days",
    "is_null",
    "is_not_null",
)

BREVO_TEMPLATE_FIELD_SPECS: dict[str, FieldSpec] = {
    "name": FieldSpec(
        key="name",
        label="Nombre",
        type="string",
        comparators=_STRING,
        column=BrevoTemplateCache.name,
        sortable=True,
        default_visible=True,
        grouped_under="Plantilla",
    ),
    "subject": FieldSpec(
        key="subject",
        label="Asunto",
        type="string",
        comparators=_STRING,
        column=BrevoTemplateCache.subject,
        sortable=True,
        default_visible=True,
        grouped_under="Plantilla",
    ),
    "is_active": FieldSpec(
        key="is_active",
        label="Activa",
        type="bool",
        comparators=("eq",),
        column=BrevoTemplateCache.is_active,
        sortable=True,
        default_visible=True,
        grouped_under="Plantilla",
    ),
    "tag": FieldSpec(
        key="tag",
        label="Tag",
        type="string",
        comparators=_STRING,
        column=BrevoTemplateCache.tag,
        sortable=True,
        default_visible=True,
        grouped_under="Plantilla",
    ),
    "sender_name": FieldSpec(
        key="sender_name",
        label="Remitente",
        type="string",
        comparators=_STRING,
        column=BrevoTemplateCache.sender_name,
        sortable=True,
        default_visible=True,
        grouped_under="Remitente",
    ),
    "sender_email": FieldSpec(
        key="sender_email",
        label="Email remitente",
        type="string",
        comparators=_STRING,
        column=BrevoTemplateCache.sender_email,
        grouped_under="Remitente",
    ),
    "created_at_brevo": FieldSpec(
        key="created_at_brevo",
        label="Creada (Brevo)",
        type="date",
        comparators=_DATE,
        column=BrevoTemplateCache.created_at_brevo,
        sortable=True,
        default_visible=True,
        grouped_under="Fechas",
    ),
    "modified_at_brevo": FieldSpec(
        key="modified_at_brevo",
        label="Modificada (Brevo)",
        type="date",
        comparators=_DATE,
        column=BrevoTemplateCache.modified_at_brevo,
        sortable=True,
        grouped_under="Fechas",
    ),
}

BREVO_CAMPAIGN_FIELD_SPECS: dict[str, FieldSpec] = {
    "name": FieldSpec(
        key="name",
        label="Nombre",
        type="string",
        comparators=_STRING,
        column=BrevoCampaignCache.name,
        sortable=True,
        default_visible=True,
        grouped_under="Campaña",
    ),
    "subject": FieldSpec(
        key="subject",
        label="Asunto",
        type="string",
        comparators=_STRING,
        column=BrevoCampaignCache.subject,
        sortable=True,
        default_visible=True,
        grouped_under="Campaña",
    ),
    "status": FieldSpec(
        key="status",
        label="Estado",
        type="enum",
        comparators=("eq", "neq", "in", "not_in"),
        # Free-string column in Brevo's API; values advisory.
        enum_values=("draft", "sent", "queued", "suspended", "archive"),
        column=BrevoCampaignCache.status,
        sortable=True,
        default_visible=True,
        grouped_under="Campaña",
    ),
    "type": FieldSpec(
        key="type",
        label="Tipo",
        type="enum",
        comparators=("eq", "neq", "in", "not_in"),
        enum_values=("classic", "trigger"),
        column=BrevoCampaignCache.type,
        sortable=True,
        grouped_under="Campaña",
    ),
    "sender_name": FieldSpec(
        key="sender_name",
        label="Remitente",
        type="string",
        comparators=_STRING,
        column=BrevoCampaignCache.sender_name,
        sortable=True,
        grouped_under="Remitente",
    ),
    "sender_email": FieldSpec(
        key="sender_email",
        label="Email remitente",
        type="string",
        comparators=_STRING,
        column=BrevoCampaignCache.sender_email,
        grouped_under="Remitente",
    ),
    "scheduled_at": FieldSpec(
        key="scheduled_at",
        label="Programada",
        type="date",
        comparators=_DATE,
        column=BrevoCampaignCache.scheduled_at,
        sortable=True,
        default_visible=True,
        grouped_under="Fechas",
    ),
    "sent_at": FieldSpec(
        key="sent_at",
        label="Enviada",
        type="date",
        comparators=_DATE,
        column=BrevoCampaignCache.sent_at,
        sortable=True,
        default_visible=True,
        grouped_under="Fechas",
    ),
    "created_at_brevo": FieldSpec(
        key="created_at_brevo",
        label="Creada (Brevo)",
        type="date",
        comparators=_DATE,
        column=BrevoCampaignCache.created_at_brevo,
        sortable=True,
        grouped_under="Fechas",
    ),
}
