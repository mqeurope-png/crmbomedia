"""Lightweight tests for the integration_accounts schemas + naming."""
import pytest

from app.models.crm import ExternalSystem
from app.models.integration_settings import IntegrationMode, IntegrationStatus, QuotaStrategy
from app.repositories.integration_settings import DEFAULT_DISPLAY_NAMES
from app.schemas.integration_settings import (
    IntegrationAccountCreate,
    IntegrationAccountUpdate,
)


def test_default_display_names_cover_every_external_system():
    assert DEFAULT_DISPLAY_NAMES == {
        ExternalSystem.AGILECRM: "AgileCRM",
        ExternalSystem.BREVO: "Brevo",
        ExternalSystem.FRESHDESK: "Freshdesk",
        ExternalSystem.FACTUSOL: "FactuSOL",
    }


def test_integration_account_update_accepts_non_secret_metadata_only():
    payload = IntegrationAccountUpdate(
        enabled=True,
        mode=IntegrationMode.SANDBOX,
        status=IntegrationStatus.CONFIGURED,
        api_base_url="https://api.brevo.example",
        account_label="Sandbox",
        credential_status="configured_externally",
        notes="Secret stored outside the repository",
    )

    assert payload.enabled is True
    assert payload.mode == IntegrationMode.SANDBOX
    assert payload.status == IntegrationStatus.CONFIGURED
    assert payload.credential_status == "configured_externally"


def test_integration_account_create_accepts_lowercase_slug():
    payload = IntegrationAccountCreate(
        account_id="agilecrm-es",
        display_name="AgileCRM España",
    )
    assert payload.account_id == "agilecrm-es"
    assert payload.display_name == "AgileCRM España"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "-leading",
        "trailing-",
        "with space",
        "with/slash",
        "UPPERCASE!",
        "a" * 65,
    ],
)
def test_integration_account_create_rejects_bad_account_id(raw):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        IntegrationAccountCreate(account_id=raw, display_name="x")


def test_integration_account_create_accepts_quota_fields():
    payload = IntegrationAccountCreate(
        account_id="es",
        display_name="AgileCRM España",
        quota_max_contacts=800,
        quota_strategy=QuotaStrategy.KEEP_NEWEST,
        sync_priority=10,
    )
    assert payload.quota_max_contacts == 800
    assert payload.quota_strategy == QuotaStrategy.KEEP_NEWEST
    assert payload.sync_priority == 10
