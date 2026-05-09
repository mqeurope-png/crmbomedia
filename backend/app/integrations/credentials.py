"""Public helper for connectors to obtain a decrypted API key.

Connectors must NEVER read api_key_encrypted directly. This helper opens
its own session, decrypts the stored ciphertext, updates
api_key_last_used_at and returns the plaintext to the caller.

The returned plaintext stays in memory for the duration of the connector
call only. Do not log it, persist it, or pass it to anything that might.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt
from app.db.session import get_engine
from app.models.crm import ExternalSystem
from app.models.integration_settings import IntegrationSetting
from app.repositories.integration_settings import touch_api_key_use


def get_decrypted_api_key(system: ExternalSystem | str) -> str | None:
    """Return the plaintext API key for `system`, or None if not configured.

    Updates api_key_last_used_at as a side effect when a key is returned.
    Accepts either an ExternalSystem enum value or its string equivalent.
    """
    if isinstance(system, str):
        system = ExternalSystem(system)

    with Session(get_engine()) as session:
        setting = session.scalar(
            select(IntegrationSetting).where(IntegrationSetting.system == system)
        )
        if not setting or not setting.api_key_encrypted:
            return None
        plaintext = decrypt(setting.api_key_encrypted)
        touch_api_key_use(session, setting)
        session.commit()
        return plaintext
