"""EmailThread filter/column field specs (Sprint Filtros & Listas PR-A).

Only the columns that live on `email_threads` directly are filterable in
v1 — they cover the buzón filters the screen already exposes
(state/starred/unread/folder/dates). The M:N `labels` and the
message-derived fields (direction, read_at, events) need joins and are
deferred to the `/emails` migration (PR-G); they're omitted here rather
than registered as broken filters.
"""
from __future__ import annotations

from app.models.crm import EmailThread
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
_REFERENCE = ("eq", "neq", "in", "not_in", "is_null", "is_not_null")

EMAIL_THREAD_FIELD_SPECS: dict[str, FieldSpec] = {
    "subject": FieldSpec(
        key="subject",
        label="Asunto",
        type="string",
        comparators=_STRING,
        column=EmailThread.subject,
        sortable=True,
        default_visible=True,
        grouped_under="Mensaje",
    ),
    "state": FieldSpec(
        key="state",
        label="Estado",
        type="enum",
        comparators=("eq", "neq", "in", "not_in"),
        enum_values=("inbox", "archived", "trashed", "spam"),
        column=EmailThread.state,
        sortable=True,
        default_visible=True,
        grouped_under="Buzón",
    ),
    "is_starred": FieldSpec(
        key="is_starred",
        label="Estrella",
        type="bool",
        comparators=("eq",),
        column=EmailThread.is_starred,
        sortable=True,
        default_visible=True,
        grouped_under="Buzón",
    ),
    "has_unread_replies": FieldSpec(
        key="has_unread_replies",
        label="No leído",
        type="bool",
        comparators=("eq",),
        column=EmailThread.has_unread_replies,
        sortable=True,
        default_visible=True,
        grouped_under="Buzón",
    ),
    "folder_id": FieldSpec(
        key="folder_id",
        label="Carpeta",
        type="reference",
        comparators=_REFERENCE,
        column=EmailThread.folder_id,
        grouped_under="Buzón",
        reference_table="email_folders",
    ),
    "contact_id": FieldSpec(
        key="contact_id",
        label="Contacto",
        type="reference",
        comparators=_REFERENCE,
        column=EmailThread.contact_id,
        default_visible=True,
        grouped_under="Mensaje",
        reference_table="contacts",
    ),
    "initiated_by_user_id": FieldSpec(
        key="initiated_by_user_id",
        label="Iniciado por",
        type="reference",
        comparators=_REFERENCE,
        column=EmailThread.initiated_by_user_id,
        grouped_under="Sistema",
        reference_table="users",
    ),
    "message_count": FieldSpec(
        key="message_count",
        label="Nº mensajes",
        type="int",
        comparators=("eq", "neq", "gt", "gte", "lt", "lte", "between"),
        column=EmailThread.message_count,
        sortable=True,
        default_visible=True,
        grouped_under="Mensaje",
    ),
    "first_message_at": FieldSpec(
        key="first_message_at",
        label="Primer mensaje",
        type="date",
        comparators=_DATE,
        column=EmailThread.first_message_at,
        sortable=True,
        grouped_under="Fechas",
    ),
    "last_message_at": FieldSpec(
        key="last_message_at",
        label="Último mensaje",
        type="date",
        comparators=_DATE,
        column=EmailThread.last_message_at,
        sortable=True,
        default_visible=True,
        grouped_under="Fechas",
    ),
    "snooze_until": FieldSpec(
        key="snooze_until",
        label="Pospuesto hasta",
        type="date",
        comparators=_DATE,
        column=EmailThread.snooze_until,
        sortable=True,
        grouped_under="Buzón",
    ),
}
