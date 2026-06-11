"""Human labels + deep links for external system references.

Used by the contact detail endpoint to enrich each
`external_references` row with a display label and (when we can build
one) a URL straight to the record in the source system's own UI.
"""
from __future__ import annotations

from typing import Any

SYSTEM_LABELS: dict[str, str] = {
    "agilecrm": "AgileCRM",
    "brevo": "Brevo",
    "freshdesk": "Freshdesk",
    "factusol": "FactuSOL",
}


def system_label(system: Any) -> str:
    """Map a system slug (or `ExternalSystem` enum) to its display
    label, falling back to a title-cased slug for unknown systems."""
    value = system.value if hasattr(system, "value") else str(system)
    return SYSTEM_LABELS.get(value, value.title())


def build_external_url(
    system: Any, external_id: str, *, api_base_url: str | None
) -> str | None:
    """Best-effort deep link into the source system's UI.

    - Brevo's contact UI lives at a fixed host, so we hardcode it.
    - AgileCRM is per-tenant (`https://<sub>.agilecrm.com`); the
      account's configured `api_base_url` already carries the right
      subdomain, so we reuse it. Without a base URL we can't know the
      subdomain — return None and the chip renders without a link.
    """
    value = system.value if hasattr(system, "value") else str(system)
    if not external_id:
        return None
    if value == "brevo":
        return f"https://app.brevo.com/contact/index/{external_id}"
    if value == "agilecrm" and api_base_url:
        return f"{api_base_url.rstrip('/')}/contact/{external_id}"
    return None
