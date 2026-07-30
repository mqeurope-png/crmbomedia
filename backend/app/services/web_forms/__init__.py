"""Sprint Web-Forms — motor de captura de leads desde formularios web."""
from app.services.web_forms.submit import (
    SubmitOutcome,
    process_submission,
)

__all__ = ["SubmitOutcome", "process_submission"]
