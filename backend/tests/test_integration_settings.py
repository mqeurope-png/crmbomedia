from app.models.crm import ExternalSystem
from app.models.integration_settings import IntegrationMode, IntegrationStatus
from app.repositories.integration_settings import INTEGRATION_DISPLAY_NAMES
from app.schemas.integration_settings import IntegrationSettingUpdate


def test_supported_integration_systems_are_explicit():
    assert INTEGRATION_DISPLAY_NAMES == {
        ExternalSystem.AGILECRM: "AgileCRM",
        ExternalSystem.BREVO: "Brevo",
        ExternalSystem.FRESHDESK: "Freshdesk",
        ExternalSystem.FACTUSOL: "FactuSOL",
    }


def test_integration_settings_schema_accepts_non_secret_metadata_only():
    payload = IntegrationSettingUpdate(
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
