"""Heuristics for deriving a company from a contact's email/website.

Used by the backfill script and the Brevo mapper. Single home so
the personal-domain blocklist stays in one place and the title-case
rule for "th-containers" → "TH Containers" doesn't drift between
the cron and the live sync.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# Free-mail / personal domains we never lift into a company row.
# Curated from observed data; extend cautiously — adding a niche
# corporate ISP here orphans every contact who used it.
PERSONAL_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "yahoo.es",
        "yahoo.co.uk",
        "yahoo.fr",
        "hotmail.com",
        "hotmail.es",
        "hotmail.fr",
        "outlook.com",
        "outlook.es",
        "live.com",
        "msn.com",
        "icloud.com",
        "me.com",
        "protonmail.com",
        "proton.me",
        "mail.com",
        "aol.com",
        "gmx.com",
        "gmx.es",
        "gmx.de",
        "web.de",
        "free.fr",
        "orange.fr",
        "wanadoo.es",
        "telefonica.net",
        "movistar.es",
        "vodafone.es",
        "qq.com",
        "163.com",
        "naver.com",
        "yandex.com",
        "yandex.ru",
        "tutanota.com",
    }
)


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


def extract_company_domain(email: str | None) -> str | None:
    """Extract the company domain from an email. Returns None for
    a free-mail address (which should NOT spawn a Company row) or
    for an obviously malformed input."""
    if not email or "@" not in email:
        return None
    domain = email.split("@", 1)[1].lower().strip()
    if not domain:
        return None
    if domain in PERSONAL_DOMAINS:
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
