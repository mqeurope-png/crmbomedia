"""Brevo connector package.

Importing this package registers the worker handlers (side-effect on
`app.workers.jobs.OPERATIONS`) — same convention as AgileCRM.
"""
from app.integrations.brevo import jobs as _jobs  # noqa: F401
from app.integrations.brevo import sync_targets as _sync_targets  # noqa: F401
