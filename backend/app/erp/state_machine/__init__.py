"""BoHub ERP — máquina de estados declarativa (Fase A PR 2)."""
from app.erp.state_machine.definitions import (
    SYSTEM,
    TRANSITIONS,
    TransitionDef,
    find_transition,
    transitions_from,
)
from app.erp.state_machine.engine import (
    TransitionError,
    apply_transition,
    available_transitions,
)

__all__ = [
    "SYSTEM",
    "TRANSITIONS",
    "TransitionDef",
    "TransitionError",
    "apply_transition",
    "available_transitions",
    "find_transition",
    "transitions_from",
]
