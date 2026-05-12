"""AgileCRM connector.

Importing this package registers `agilecrm:sync_contacts` and
`agilecrm:purge_quota` into `app.workers.jobs.OPERATIONS`. The job
implementations live in `app.integrations.agilecrm.jobs`; the HTTP
client is in `app.integrations.agilecrm.client`.
"""
# The import side-effects below register the operations. We deliberately
# import the module (not the symbols) to make the import order obvious
# to a reader.
from app.integrations.agilecrm import jobs as _jobs  # noqa: F401
