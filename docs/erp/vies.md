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
sin prefijo. Cliente en `backend/app/integrations/vies/client.py`.

Solo se consulta cuando **aplica**: país de la UE distinto de España y con
NIF-IVA del país (`Company.vat`, o `tax_id` con prefijo del país). España →
nacional, fuera de la UE → exportación: VIES no aplica (`vies.applies=false`).

## Estados

| `vies_status` | Significado | Efecto en el régimen |
|---|---|---|
| `valido` | VIES dice que el número existe y está activo | **Intracomunitario confirmado** (chip «✓ verificado en VIES»; el motivo del régimen lo dice) |
| `no_valido` | VIES responde y dice que NO (o el formato es inválido) | **No se puede eximir → nacional con IVA**. Alerta accionable en la ficha; el pedido entra en «Incidencias» con alerta bloqueante `vat_no_valido_vies` («Revalidar en VIES») |
| `desconocido` | VIES no respondió (timeout, 5xx, estado miembro caído, respuesta sin veredicto) | No cambia la regla: se sigue por país + NIF-IVA (intracomunitario) con aviso «pendiente de validar»; se reintenta |
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
empresa (se revalida a los 30 días; `desconocido` a la hora).

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
