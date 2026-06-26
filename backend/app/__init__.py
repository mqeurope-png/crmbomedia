"""Package init.

PR-Fix-Sincronizar-Stats-3a-Vez. Logging is configured here so it
runs for EVERY entry point that loads `app.*`:

- API container: `uvicorn app.main:app` → imports trigger this module.
- Worker containers: `rq worker brevo:* ...` → at first job pickup,
  the registered handler imports `app.workers.jobs` → triggers this.
- CLI scripts / `python -m app.db.init_db` / cron sweeps.

Without this, uvicorn and rq leave the root logger empty so every
`logger.info(...)` from app modules vanishes silently — exactly what
killed the diagnostic log added in PR #242.
"""
from __future__ import annotations

import logging
import os

_LOG_LEVEL = os.environ.get(
    "APP_LOG_LEVEL", "INFO"
).upper()
_LEVEL = getattr(logging, _LOG_LEVEL, logging.INFO)

# `basicConfig` is a no-op if the root logger already has handlers —
# that's the case under pytest (the `logging` plugin attaches its
# own handler before our package import runs). To avoid stomping on
# pytest while still configuring uvicorn/rq workers (which start
# with the root logger empty), only call `basicConfig` when no
# handler is present.
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
# Always ensure the `app` namespace propagates at INFO regardless of
# the root logger's level — this is the load-bearing assertion: every
# `logger.info(...)` from `app.*` reaches its handlers in production
# AND keeps showing up under pytest's caplog at the requested level.
logging.getLogger("app").setLevel(_LEVEL)

# Tame third-party noise that would otherwise flood docker logs at INFO.
for _noisy in ("httpcore", "httpx", "urllib3", "asyncio"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
