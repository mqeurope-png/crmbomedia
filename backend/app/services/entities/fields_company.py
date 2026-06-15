"""Company filter/column field specs (Sprint Filtros & Listas PR-A).

Decisiones §2.7 confirmadas por Bart:
- `tax_id` → "CIF/NIF" y `vat` → "VAT intracomunitario": ambos expuestos.
  Para empresas españolas son distintos (CIF nacional + VAT EU `ES…`).
- `custom_fields_json` de Company: **NO se expone en v1.** Misma política
  que contacts — primero se hace whitelist con los campos reales del
  negocio, luego se exponen. Apuntado como deuda menor.
"""
from __future__ import annotations

from app.models.crm import Company
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

COMPANY_FIELD_SPECS: dict[str, FieldSpec] = {
    "name": FieldSpec(
        key="name",
        label="Nombre",
        type="string",
        comparators=_STRING,
        column=Company.name,
        sortable=True,
        default_visible=True,
        grouped_under="Datos básicos",
    ),
    "domain": FieldSpec(
        key="domain",
        label="Dominio",
        type="string",
        comparators=_STRING,
        column=Company.domain,
        sortable=True,
        default_visible=True,
        grouped_under="Datos básicos",
    ),
    "tax_id": FieldSpec(
        key="tax_id",
        label="CIF/NIF",
        type="string",
        comparators=_STRING,
        column=Company.tax_id,
        sortable=True,
        default_visible=True,
        grouped_under="Fiscal",
    ),
    "vat": FieldSpec(
        key="vat",
        label="VAT intracomunitario",
        type="string",
        comparators=_STRING,
        column=Company.vat,
        sortable=False,
        default_visible=False,
        grouped_under="Fiscal",
    ),
    "website": FieldSpec(
        key="website",
        label="Web",
        type="string",
        comparators=_STRING,
        column=Company.website,
        grouped_under="Datos básicos",
    ),
    "country": FieldSpec(
        key="country",
        label="País",
        type="string",
        comparators=_STRING,
        column=Company.country,
        sortable=True,
        default_visible=True,
        grouped_under="Dirección",
    ),
    "region": FieldSpec(
        key="region",
        label="Región",
        type="string",
        comparators=_STRING,
        column=Company.region,
        grouped_under="Dirección",
    ),
    "state": FieldSpec(
        key="state",
        label="Provincia",
        type="string",
        comparators=_STRING,
        column=Company.state,
        grouped_under="Dirección",
    ),
    "city": FieldSpec(
        key="city",
        label="Ciudad",
        type="string",
        comparators=_STRING,
        column=Company.city,
        sortable=True,
        grouped_under="Dirección",
    ),
    "address_line": FieldSpec(
        key="address_line",
        label="Dirección",
        type="string",
        comparators=_STRING,
        column=Company.address_line,
        grouped_under="Dirección",
    ),
    "postal_code": FieldSpec(
        key="postal_code",
        label="Código postal",
        type="string",
        comparators=_STRING,
        column=Company.postal_code,
        grouped_under="Dirección",
    ),
    "sector": FieldSpec(
        key="sector",
        label="Sector",
        type="string",
        comparators=_STRING,
        column=Company.sector,
        sortable=True,
        grouped_under="Negocio",
    ),
    "size_category": FieldSpec(
        key="size_category",
        label="Tamaño",
        type="string",
        comparators=_STRING,
        column=Company.size_category,
        sortable=True,
        grouped_under="Negocio",
    ),
    "source": FieldSpec(
        key="source",
        label="Fuente",
        type="enum",
        comparators=("eq", "neq", "in", "not_in"),
        enum_values=("manual", "brevo", "agilecrm", "auto-domain"),
        column=Company.source,
        sortable=True,
        default_visible=True,
        grouped_under="Origen",
    ),
    "is_active": FieldSpec(
        key="is_active",
        label="Activa",
        type="bool",
        comparators=("eq",),
        column=Company.is_active,
        sortable=True,
        grouped_under="Sistema",
    ),
    "created_at": FieldSpec(
        key="created_at",
        label="Creada",
        type="date",
        comparators=_DATE,
        column=Company.created_at,
        sortable=True,
        default_visible=True,
        grouped_under="Sistema",
    ),
    "updated_at": FieldSpec(
        key="updated_at",
        label="Actualizada",
        type="date",
        comparators=_DATE,
        column=Company.updated_at,
        sortable=True,
        default_visible=True,
        grouped_under="Sistema",
    ),
}
