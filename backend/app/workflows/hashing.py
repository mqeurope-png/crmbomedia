"""Hash determinístico de la definición estructural de un workflow.

Sprint UX-Workflows-Editor. Bart pidió detección de duplicados exactos
y "similares" al guardar/activar. Calculamos un hash sobre la forma
canónica del workflow para que la comparación sea O(1) en DB
(consultar `definition_hash` index) en lugar de comparar JSONs enteros
fila a fila.

Definición canónica:

    {
      "trigger_type": "contact.created",
      "trigger_config": <ordered>,
      "steps": [
        {"type": "...", "config": <ordered>},   # SORTED por type+config
        ...
      ],
      "edges": [
        {"from_idx": 0, "to_idx": 1, "branch": "default"},  # SORTED
        ...
      ]
    }

Para "similar" se usa una **segunda** hash sobre la misma estructura
PERO sin los valores de configs — solo trigger_type + secuencia de
step types + edges. Dos workflows con el mismo "esqueleto" pero
distintos delays/textos generan misma hash de similitud y distinta
hash exacta.

NO usamos `definition_hash` para validación de seguridad — solo como
clave de detección de duplicados. Truncamos SHA-256 a 16 bytes (32
chars hex) — colisión accidental ~1 en 2^64, suficiente para este uso.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from app.models.workflows import Workflow, WorkflowEdge, WorkflowStep


def _canonical_dict(value: Any) -> Any:
    """Devuelve `value` con claves de dict ordenadas recursivamente
    para que `json.dumps` produzca un texto canónico."""
    if isinstance(value, dict):
        return {k: _canonical_dict(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_canonical_dict(v) for v in value]
    return value


def _step_key(step: WorkflowStep) -> str:
    """Tipo + JSON canónico de la config. Determinístico."""
    try:
        config = json.loads(step.config_json or "{}")
    except (TypeError, ValueError):
        config = {}
    payload = {"type": step.type, "config": _canonical_dict(config)}
    return json.dumps(payload, sort_keys=True, default=str)


def _build_canonical(
    workflow: Workflow,
    steps: list[WorkflowStep],
    edges: Iterable[WorkflowEdge],
    *,
    include_configs: bool,
) -> str:
    """Forma canónica como string. `include_configs=False` deja solo
    la topología (para hash de similitud)."""
    try:
        trigger_cfg = json.loads(workflow.trigger_config_json or "{}")
    except (TypeError, ValueError):
        trigger_cfg = {}

    # Indexamos steps por id para mapearlos a posiciones determinísticas
    # (las edges referencian step ids que cambian entre workflows, así
    # que las traducimos a índices del array ordenado).
    sorted_steps = sorted(steps, key=_step_key)
    id_to_idx = {s.id: i for i, s in enumerate(sorted_steps)}

    if include_configs:
        steps_payload = [
            {
                "type": s.type,
                "config": _canonical_dict(
                    json.loads(s.config_json or "{}")
                    if s.config_json
                    else {}
                ),
            }
            for s in sorted_steps
        ]
    else:
        steps_payload = [{"type": s.type} for s in sorted_steps]

    edges_payload = []
    for e in edges:
        # Las edges cuyo `from`/`to` no resuelven a un step actual se
        # ignoran — workflows con grafos inconsistentes igualmente
        # rechazados por la validación estructural.
        from_idx = id_to_idx.get(e.from_step_id)
        to_idx = id_to_idx.get(e.to_step_id)
        if from_idx is None or to_idx is None:
            continue
        edges_payload.append(
            {
                "from": from_idx,
                "to": to_idx,
                "branch": e.branch_label or "default",
            }
        )
    edges_payload.sort(
        key=lambda x: (x["from"], x["branch"], x["to"])
    )

    canonical = {
        "trigger_type": workflow.trigger_type,
        "trigger_config": _canonical_dict(trigger_cfg) if include_configs else {},
        "steps": steps_payload,
        "edges": edges_payload,
    }
    return json.dumps(canonical, sort_keys=True, default=str)


def compute_exact_hash(
    workflow: Workflow,
    steps: list[WorkflowStep],
    edges: list[WorkflowEdge],
) -> str:
    """SHA-256 truncado a 32 hex chars (16 bytes)."""
    canonical = _build_canonical(workflow, steps, edges, include_configs=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def compute_similarity_hash(
    workflow: Workflow,
    steps: list[WorkflowStep],
    edges: list[WorkflowEdge],
) -> str:
    """Topología desnuda — mismo skeleton pero distintos parámetros
    colisionan."""
    canonical = _build_canonical(workflow, steps, edges, include_configs=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
