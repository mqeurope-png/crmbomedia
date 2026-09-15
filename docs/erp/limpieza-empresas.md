# Limpieza de empresas — vincular, rellenar NIF y archivar (reversible)

Basado en el discovery (`docs/erp/discovery-empresas-factusol.md`). **FACTUSOL
SIEMPRE solo lectura**; nunca se le escribe. **Nada de borrado físico**: el
archivado es reversible (`is_archived` + «Restaurar»).

Decisión de negocio: FACTUSOL es la fuente de verdad. Se conserva (a) lo que
está en FACTUSOL (por CODCLI vinculado o por NIF/VAT normalizado) y (b)
cualquiera con negocio vivo (pedidos, proformas, tareas o actividad/email
reciente). Un contacto suelto NO cuenta como negocio. El resto (no en
FACTUSOL y sin negocio) se archiva.

Cruce por NIF/VAT normalizado (reutiliza `company_discovery.nif_key`):
mayúsculas, sin espacios/puntos/guiones/barras, y sin el prefijo de país si el
número empieza por un código de la UE (como en VIES). «ONLYGUAY SC»
`J70876677` casa con FACTUSOL 3734 «ONLYGUAY S C» aunque el nombre difiera.

## 1. Auto-vincular por NIF (escribe el CODCLI en el CRM)

```
python -m scripts.link_companies_by_nif            # DRY-RUN (no cambia nada)
python -m scripts.link_companies_by_nif --apply --yes
```

Empresas sin CODCLI guardado cuyo NIF casa con UN ÚNICO cliente de F_CLI →
se guarda el CODCLI. Si el NIF casa con VARIOS CODCLI, NO se vincula: se lista
para revisión a mano.

## 2. Rellenar NIF desde FACTUSOL (escribe `tax_id` en el CRM)

```
python -m scripts.backfill_company_nif             # DRY-RUN
python -m scripts.backfill_company_nif --apply --yes
```

Empresas vinculadas por CODCLI y sin NIF en el CRM → se copia `NIFCLI` del
cliente a `tax_id` (nunca al revés). Si el CRM ya tiene otro NIF distinto no
vacío, no se pisa: revisión.

## 3. Archivar (reversible) — el paso importante, con DRY-RUN

Regla: NO en FACTUSOL (ni por CODCLI válido ni por NIF/VAT) Y sin pedidos, sin
proformas, sin tareas y sin actividad/email reciente (`--recent-days`, 180 por
defecto). Un contacto suelto NO protege.

```
# DRY-RUN (por defecto): NO archiva nada. Recuento desglosado + CSV de candidatas.
python -m scripts.archive_companies
# aplicar de verdad (marca is_archived; NO borra): exige --apply Y --yes
python -m scripts.archive_companies --apply --yes
```

El dry-run imprime el recuento exacto por bucket (sin NIF y sin nada / sin NIF
solo-contacto / con NIF y sin nada / con NIF solo-contacto) y escribe
`/tmp/empresas_a_archivar.csv` (una fila por candidata: id, nombre, nif,
bucket, motivo). El `--apply` marca `is_archived=1` (idempotente; una empresa
que dejó de cumplir la regla entre el plan y el apply se salta) y escribe un
manifiesto CSV con los ids archivados (para auditar / restaurar).

Copiar los CSV fuera del contenedor:
`docker compose -f … cp api:/tmp/empresas_a_archivar.csv .`

## Archivado en la app

- Migración `20260919_0110`: `companies.is_archived` (indexado) + `archived_at`
  + `archived_reason`. `Restaurar` = `is_archived=0`.
- Una empresa archivada queda FUERA de: el listado de empresas
  (`/api/entities/company/search` y `/api/companies`), el buscador unificado,
  el picker y los autocompletes. El listado tiene el toggle **«Ver
  archivadas»** (`include_archived`), y la ficha muestra el banner
  «Archivada» + **«Restaurar»** (y «Archivar» en «⋯»).
- Las archivadas NO generan alertas ni incidencias en el `workflow`
  («empresa sin vincular a FACTUSOL» incluido): sus pedidos siguen su ciclo
  por su estado. Contactos / pedidos / tareas se conservan (solo se ocultan
  con la empresa).
- Endpoints: `POST /api/companies/{id}/archive` (admin) y
  `POST /api/companies/{id}/restore` (roles de edición). Idempotentes.

La fusión de duplicados del CRM (75 grupos del discovery) NO va aquí: es un
paso aparte que reasigna pedidos/contactos/tareas al superviviente.
