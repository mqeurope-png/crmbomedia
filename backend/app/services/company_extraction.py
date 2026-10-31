"""Heuristics for deriving a company from a contact's email/website.

Used by the backfill script and the Brevo mapper. Single home so the
title-case rule for "th-containers" → "TH Containers" doesn't drift
between the cron and the live sync. Which domains never identify a
company (gmail.com, xtec.cat…) is data: `dominios_compartidos`.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from app.services.dominios_compartidos import Lista

# Los dominios que NO identifican una empresa (gmail.com, xtec.cat…) ya no
# viven aquí: son un dato, la tabla `dominios_compartidos`
# (`app/services/dominios_compartidos.py`), que se edita sin tocar código.

_WWW_PREFIX_RE = re.compile(r"^www\.", flags=re.IGNORECASE)
_TOKEN_SPLIT_RE = re.compile(r"[-_]+")


def normalise_domain(raw: str | None) -> str | None:
    """Trim, lowercase, strip `www.` and the leading scheme + path
    if the caller passed a full URL. Returns None when the input
    has no usable hostname."""
    if not raw:
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    if "://" in candidate:
        host = urlparse(candidate).netloc
    elif "/" in candidate:
        # `bomedia.net/about` — no scheme, but still a path. urlparse
        # treats this as path-only, so split manually.
        host = candidate.split("/", 1)[0]
    else:
        host = candidate
    host = host.lower().strip()
    host = _WWW_PREFIX_RE.sub("", host)
    return host or None


def extract_company_domain(email: str | None, no_emparejan: Lista) -> str | None:
    """Extract the company domain from an email. Returns None for a
    domain of the list that never identifies a company (free-mail,
    shared by many organisations: `no_emparejan`, read with
    `dominios_compartidos.cargar`) — those should NOT spawn or match a
    Company row — or for an obviously malformed input."""
    if not email or "@" not in email:
        return None
    domain = email.split("@", 1)[1].lower().strip()
    if not domain:
        return None
    if no_emparejan.contiene(domain):
        return None
    return domain


def derive_company_name_from_domain(domain: str) -> str:
    """`bomedia.net` → `Bomedia`; `th-containers.es` → `TH Containers`.
    Falls back to the raw domain when the split yields nothing
    useful (shouldn't happen with valid hostnames but cheap safety
    net)."""
    base = domain.split(".", 1)[0]
    if not base:
        return domain
    parts = [p for p in _TOKEN_SPLIT_RE.split(base) if p]
    if not parts:
        return domain
    return " ".join(_titlecase_part(p) for p in parts)


def _titlecase_part(token: str) -> str:
    """Capitalise unless the token is already an obvious acronym
    (all caps, 2-4 chars) — `th` becomes `TH` rather than `Th`."""
    if 2 <= len(token) <= 4 and token.isalpha() and token.islower():
        return token.upper()
    return token.capitalize()
