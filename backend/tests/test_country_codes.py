"""Country normalisation helper tests."""
from __future__ import annotations

import pytest

from app.integrations.country_codes import normalize_country


@pytest.mark.parametrize(
    "raw,expected_iso,expected_name",
    [
        ("ES", "ES", "Spain"),
        ("es", "ES", "Spain"),
        ("ESP", "ES", "Spain"),
        ("España", "ES", "Spain"),
        ("espana", "ES", "Spain"),
        ("Spain", "ES", "Spain"),
        ("Reino Unido", "GB", "United Kingdom"),
        ("Estados Unidos", "US", "United States"),
        ("US", "US", "United States"),
        ("FR", "FR", "France"),
        ("Francia", "FR", "France"),
    ],
)
def test_normalize_country_known(
    raw: str, expected_iso: str, expected_name: str
) -> None:
    iso, name = normalize_country(raw)
    assert iso == expected_iso
    assert name == expected_name


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_normalize_country_unknown(raw: str | None) -> None:
    """Empty / whitespace input is unequivocally `(None, None)`. We
    deliberately don't assert on garbage strings here: pycountry's
    fuzzy search is generous and will match e.g. `"Atlántida"` →
    Honduras's Atlántida department. That's fine — the operator
    typed *something*, and a best-guess country beats throwing the
    field away. The script's logger flags unknown entries instead."""
    iso, name = normalize_country(raw)
    assert iso is None
    assert name is None


def test_brevo_mapper_normalises_country_name() -> None:
    """The Brevo mapper goes through the helper; calling it with a
    raw country name produces the canonical ISO pair."""
    from app.integrations.brevo.mapper import _country_pair  # noqa: PLC0415

    assert _country_pair("España") == {
        "address_country": "ES",
        "address_country_name": "Spain",
    }
    assert _country_pair("ES") == {
        "address_country": "ES",
        "address_country_name": "Spain",
    }
    assert _country_pair(None) == {
        "address_country": None,
        "address_country_name": None,
    }
