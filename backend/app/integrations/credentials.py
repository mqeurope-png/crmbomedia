"""Public helper for connectors to obtain a decrypted API key.

Connectors must NEVER read `api_key_encrypted` directly. This helper
opens its own session, decrypts the stored ciphertext, updates
`api_key_last_used_at` and returns the plaintext to the caller.

The plaintext stays in memory only for the duration of the connector
call. Do not log it, persist it, or pass it to anything that might.

Since the multi-account refactor (migration 20260515_0007) callers must
pass the `account_id` they want to use. Legacy single-account installs
have an `account_id='default'` row that the migration left behind, so
the helper also accepts a `default_account_id` parameter to keep
single-account connector code idiomatic.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt
from app.db.session import get_engine
from app.models.crm import ExternalSystem
from app.models.integration_settings import IntegrationAccount
from app.repositories.integration_settings import touch_api_key_use


def get_decrypted_api_key(
    system: ExternalSystem | str,
    account_id: str = "default",
) -> str | None:
    """Return the plaintext API key for `(system, account_id)`, or None.

    Updates `api_key_last_used_at` as a side effect when a key is
    returned. Accepts either an `ExternalSystem` value or its string
    equivalent. The legacy single-account call style
    `get_decrypted_api_key("agilecrm")` keeps working because the
    migration left every pre-existing row with `account_id='default'`.
    """
    if isinstance(system, str):
        system = ExternalSystem(system)

    with Session(get_engine()) as session:
        account = session.scalar(
            select(IntegrationAccount).where(
                IntegrationAccount.system == system,
                IntegrationAccount.account_id == account_id,
            )
        )
        if not account or not account.api_key_encrypted:
            return None
        plaintext = decrypt(account.api_key_encrypted)
        touch_api_key_use(session, account)
        session.commit()
        return plaintext
