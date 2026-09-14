# VIES — validación del NIF-IVA intracomunitario (Fase VIES)

Parte del rediseño de flujo (PROMPT MAESTRO). BoHub valida el NIF-IVA de las
empresas de la UE en el servicio oficial de la Comisión Europea (VIES) y usa
el veredicto para **confirmar o impedir** el régimen intracomunitario que
fija la ficha F_CLI y los documentos (albarán / factura / proforma).

## Qué se consulta

API REST pública y gratuita de VIES:

```
POST https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number
{"countryCode": "FR", "vatNumber": "16339753527"}
→ {"valid": true, "name": "...", "address": "...", "userError": "VALID"}
```

`countryCode` es el prefijo del NIF-IVA (Grecia = `EL`); `vatNumber` el número
**sin prefijo**. El API no perdona el prefijo dentro del número: con
`vatNumber="FR90501738249"` responde «no válido» (la web de VIES lo quita
sola y avisa en rojo; el API no). Por eso el cliente normaliza SIEMPRE antes
de llamar (`vies_request_parts`): mayúsculas, sin espacios / puntos / guiones,
y si el número empieza por las 2 letras del país se quitan:

| Entrada | País | Se envía |
|---|---|---|
| `FR90501738249`, `FR 90 501 738 249`, `fr-90.501.738.249` | FR (o sin país) | `FR` + `90501738249` |
| `90501738249` | FR | `FR` + `90501738249` (tal cual) |
| `ESB12345678` / `B12345678` | ES | `ES` + `B12345678` |
| `E12345678` | ES | `ES` + `E12345678` (la letra E no es el prefijo) |
| `EL123456789` / `GR123456789` | GR | `EL` + `123456789` |

La caché (en proceso y en la empresa) usa esa misma forma normalizada, así la
revalidación y la comprobación al cargar la ficha comparten entrada.
Cliente en `backend/app/integrations/vies/client.py`.

Diagnóstico desde el servidor (solo lectura, enseña qué se envía, qué
contesta VIES y cómo lo interpreta BoHub):

```
docker compose exec api python -m app.integrations.vies.client FR90501738249
docker compose exec api python -m app.integrations.vies.client 90501738249 FR
docker compose exec api python -m app.integrations.vies.client FR90501738249 --interpret '{"actionSucceed":false,"errorWrappers":[{"error":"MS_MAX_CONCURRENT_REQ"}]}'
```

(`--interpret` no llama a VIES: enseña cómo lee BoHub ese cuerpo.) Sin
veredicto positivo el log del `api` deja `vies … → desconocido|no_valido (…)
· enviado {…} · respuesta HTTP … {…}` con la respuesta cruda de VIES.

Solo se consulta cuando **aplica**: país de la UE distinto de España y con
NIF-IVA del país (`Company.vat`, o `tax_id` con prefijo del país). España →
nacional, fuera de la UE → exportación: VIES no aplica (`vies.applies=false`).

## Cómo se lee la respuesta

Confirmado con la respuesta real del VPS para `FR90501738249`:
`{"actionSucceed": false, "errorWrappers": [{"error": "MS_MAX_CONCURRENT_REQ"}]}`
(Francia limitando peticiones). Eso NO es un veredicto sobre el número.

| Respuesta | Estado |
|---|---|
| `valid: true` (sin errores) | `valido` |
| `actionSucceed: true` + `valid: false` | `no_valido` — el ÚNICO caso de VAT no dado de alta (sin `actionSucceed`, solo `valid: false` + `userError: "VALID"` explícito) |
| `actionSucceed: false`, cualquier `errorWrappers` (`MS_MAX_CONCURRENT_REQ`, `GLOBAL_MAX_CONCURRENT_REQ`, `MS_UNAVAILABLE`, `SERVICE_UNAVAILABLE`, `TIMEOUT`, `VAT_BLOCKED`, `IP_BLOCKED`, `INVALID_REQUESTER_INFO`, `INVALID_INPUT`…), HTTP ≠ 200, cuerpo raro o sin veredicto | `desconocido` (pendiente de validar): no bloquea, el régimen sigue por país + NIF-IVA, se reintenta |

Ante limitación de ritmo (`*_MAX_CONCURRENT_REQ*`) el cliente espera 1 s y
2 s y reintenta (2 reintentos) antes de quedarse en `desconocido`.

## Estados

| `vies_status` | Significado | Efecto en el régimen |
|---|---|---|
| `valido` | VIES dice que el número existe y está activo | **Intracomunitario confirmado** (chip «✓ verificado en VIES»; el motivo del régimen lo dice) |
| `no_valido` | VIES responde con éxito (`actionSucceed: true`) y dice que NO está dado de alta (`valid: false`); o el número ni tiene forma de NIF-IVA | **No se puede eximir → nacional con IVA**. Alerta accionable en la ficha; el pedido entra en «Incidencias» con alerta bloqueante `vat_no_valido_vies` («Revalidar en VIES») |
| `desconocido` | VIES no respondió o devolvió un error (timeout, 5xx, estado miembro caído o saturado, `errorWrappers`, respuesta sin veredicto) | No cambia la regla: se sigue por país + NIF-IVA (intracomunitario) con aviso «pendiente de validar»; se reintenta |
| `pendiente` | Aún no validado, el NIF-IVA cambió desde la última validación, o `VIES_ENABLED=false` | Igual que `desconocido` |

**Nunca** un fallo de VIES se convierte en «no válido», y **nunca** bloquea un
alta: timeout corto (4 s), un intento, cualquier excepción → `desconocido`.

## Cuándo se valida y qué se guarda

- **Al crear / editar la empresa** (`POST/PUT /api/companies`): best-effort en
  la misma petición. Al editar solo se vuelve a consultar si cambió el
  NIF-IVA o el resultado es viejo.
- **«Revalidar en VIES»** (`POST /api/companies/{id}/vies-revalidate`,
  `force=true` por defecto): salta la caché y consulta ya. La ficha lo llama
  con `force=false` al cargar cuando el estado es `pendiente` /
  `desconocido` (el backend decide si toca).
- **«Crear empresa»** (`GET /api/companies/fiscal-check`): consulta en vivo
  (cacheada) para enseñar el chip y el régimen ANTES de guardar. Con
  `exclude_id` (la ficha) reutiliza el resultado guardado en esa empresa si
  es reciente y del mismo NIF-IVA.

En la empresa se guarda: `vies_status`, `vies_checked_at`, `vies_vat` (el
NIF-IVA validado: si el actual es otro, el resultado no cuenta y vuelve a
«pendiente»), `vies_name`, `vies_address` (migración `20260917_0108`).

Cachés: en proceso por NIF-IVA (24 h firme / 10 min desconocido) y la propia
empresa (`valido` se revalida a los 30 días; `no_valido` al día, porque un
veredicto negativo puede cambiar — un alta reciente en VIES, o un resultado
erróneo; `desconocido` a la hora; y cualquier `no_valido` guardado ANTES del
fix de la lectura de la respuesta se reconsulta en cuanto se cargue la
ficha). La ficha pide la validación al cargar
(sin forzar) cuando el estado es `pendiente`, `desconocido` o `no_valido`;
«Revalidar en VIES» fuerza siempre y salta ambas cachés, así un VAT que
quedó guardado como «no válido» pasa a válido en el acto.

## Revalidación en segundo plano de los «pendiente»

VIES (sobre todo Francia) devuelve `MS_MAX_CONCURRENT_REQ` muy a menudo, así
que muchas empresas se quedan en `desconocido` y «Revalidar en VIES» vuelve a
encontrarlo saturado. Un barrido periódico (`app/services/vies_sweep.py`) las
resuelve solas hasta `valido` / `no_valido`:

- **Dónde corre**: job RQ self-rescheduling (mismo patrón que
  `workflows.scheduler`: heartbeat SETNX + `enqueue_in`) en la cola
  `vies:sweep`, que escucha **`worker-workflows`** (`--with-scheduler`). El
  API lo arma al arrancar. Cada `VIES_SWEEP_INTERVAL_MINUTES` (30).
- **Qué entra**: empresas activas de la UE (no España) con NIF-IVA y sin
  veredicto firme para su NIF-IVA actual (`desconocido`, nunca consultadas o
  con el NIF-IVA cambiado), cuyo `vies_next_retry_at` ya pasó. España y
  fuera de la UE no entran. Las nunca consultadas primero.
- **Cómo**: de una en una, `VIES_SWEEP_SPACING_SECONDS` (2 s) entre
  llamadas, como mucho `VIES_SWEEP_BATCH` (20) por barrido (el resto en el
  siguiente), forzando la consulta (salta la caché en proceso). Nunca en
  paralelo.
- **Progreso**: commit por empresa. Veredicto firme → `vies_attempts=0`,
  `vies_next_retry_at=NULL` y no vuelve a entrar. Sin veredicto →
  `vies_attempts+1` y `vies_next_retry_at` con backoff creciente: 30 min →
  1 h → 2 h → 4 h → 8 h → 24 h (≈ 5 consultas el primer día, luego una
  diaria). «Revalidar en VIES» (manual, forzado) usa el mismo control.
- **Anti-bloqueo**: 3 respuestas seguidas de limitación de ritmo cortan el
  barrido; `IP_BLOCKED` / `GLOBAL_MAX_CONCURRENT_REQ*` pausan los barridos
  `VIES_SWEEP_PAUSE_MINUTES` (60; en Redis, compartido api + worker). Nada
  de esto se convierte en «no válido».
- **Log** (en `worker-workflows`): una línea por empresa y el resumen
  `vies.sweep: N candidatas, M consultadas → válidas / no válidas / sin
  veredicto; P siguen pendientes`.
- **A mano** (solo lectura con `--dry-run`):

```
docker compose exec api python -m app.services.vies_sweep --dry-run   # lista las pendientes que ya toca
docker compose exec api python -m app.services.vies_sweep             # un barrido ahora
```

Migración `20260918_0109`: `companies.vies_next_retry_at`, `vies_attempts`.

## Dónde se usa el veredicto

`app/services/vies.py::company_vies_valid(company)` → `True` / `False` /
`None`, que entra en `vat_regime.regime_for(..., vies_valid=)`:

- `workflow` del pedido (régimen + alertas `vat_no_valido_vies` bloqueante y
  `cliente_intracomunitario` con texto «verificado» / «pendiente»).
- Ficha F_CLI propuesta al crear / corregir el cliente (`regime-preview`,
  `fix-regime`, alta de cliente): con VAT no válido → nacional con IVA.
- Aviso de régimen en albarán / emisión / proformas.

## UI

- **«Crear empresa»**: el chip del régimen añade «✓ verificado en VIES
  (nombre)» / «VAT no válido en VIES: no se puede eximir de IVA» / «VIES no
  disponible: pendiente de validar».
- **Ficha de empresa**: chip en la cabecera junto al país/régimen; fila
  «VIES» en «Datos fiscales» (estado, nombre según VIES, fecha, botón
  «Revalidar en VIES», también en «⋯»); barra de alerta bloqueante con
  «Revalidar en VIES» cuando el VAT no es válido.
- **Ficha / bandeja de pedido**: las alertas del `workflow` («Revalidar en
  VIES» lleva a la ficha de la empresa).

## Configuración

```
VIES_ENABLED=true
VIES_BASE_URL=https://ec.europa.eu/taxation_customs/vies/rest-api
VIES_TIMEOUT_SECONDS=4
```

Los tests corren con `VIES_ENABLED=false` (`tests/conftest.py`); los de VIES
(`tests/test_vies.py`) lo activan con un cliente simulado. Nada sale a
ec.europa.eu desde la suite.
