# AI features

CRMBO usa modelos LLM (Anthropic Claude) para acelerar tareas
mecánicas — hoy sólo el wizard de creación de pipelines (Sprint
P.2.5); a futuro la cualificación automática de contactos (Sprint AI
post-Brevo).

Toda integración con IA sigue un patrón único: **propose, never
apply**. El modelo propone un cambio (un pipeline, una etiqueta, un
estado…) y el operador lo revisa y confirma antes de que persista en
BD. Nunca hay aplicación automática.

## Privacidad y costes

- API key vive sólo en backend (`ANTHROPIC_API_KEY`). Nunca se expone
  al frontend; el frontend consulta `GET /api/health` que devuelve un
  flag boolean `ai_features_enabled` para decidir si pintar el CTA.
- Los prompts del operador **no se persisten**. El audit log carga
  metadata-only (longitud + recuento de elementos propuestos) para
  que un compliance officer pueda medir uso sin ver descripciones.
- Las respuestas del modelo se descartan en cuanto el operador
  decide qué hacer; sólo el pipeline final llega a `pipelines`.
- Rate-limit per-user: 5 generaciones/hora por defecto (cambiable
  vía constantes en `app/services/llm.py`). Sliding window en
  memoria por proceso — multi-worker en prod requerirá migrarlo a
  Redis cuando dé problemas.

## Errores

`POST /api/pipelines/generate-ai` mapea cada fallo a un código limpio
para que la UI muestre un mensaje accionable sin exponer detalles del
proveedor:

| Backend                  | HTTP | Mensaje UI                                          |
|--------------------------|------|-----------------------------------------------------|
| `LLMNotConfiguredError`  | 503  | "AI features are not configured on this deployment." |
| `LLMRateLimitError`      | 429  | "Has agotado las 5 generaciones de esta hora."       |
| `LLMUpstreamError`       | 502  | "Error al generar. Intenta describir tu caso de otra forma, o usa una plantilla." |

## Pruebas

Los tests monkeypatchan `llm_service._invoke_claude` para devolver
respuestas predeterminadas sin hacer llamadas reales. La cobertura
incluye: happy path, fence markdown ```json``` strip, JSON inválido,
demasiado pocas etapas, rate-limit local, 503 cuando la key falta, y
que el audit log no contenga la descripción cruda.

## Próximas extensiones

- **Sprint AI** (después de Brevo): cualificación automática de
  contactos. La IA puntúa el `lead_score` y propone un pipeline /
  etapa basándose en los datos enriquecidos. Mismo patrón:
  propose → operator reviews → save.
- Modelos plug-in: el cliente `_invoke_claude` aísla Anthropic; un
  futuro `LLMProvider` permitirá conmutar Anthropic / OpenAI /
  Google según política del cliente sin tocar las rutas.
