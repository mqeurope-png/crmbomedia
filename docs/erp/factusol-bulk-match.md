# Conciliar CRM ↔ FACTUSOL en masa (Fase C · C-5)

`/admin/factusol-bulk-match` — solo **ADMIN**.

---

## Para qué sirve

El CRM arrastra miles de empresas de fuentes heterogéneas (WooCommerce, el
AgileCRM viejo, imports manuales, formularios web): nombres a medias, sin NIF,
direcciones de hace cinco años, duplicados. `F_CLI` tiene 4533 clientes con NIF
válido, dirección fiscal correcta y contabilidad al día.

Esta pantalla cruza las dos y deja **traer los datos limpios de FACTUSOL al
CRM**, empresa por empresa y campo por campo.

**El sentido es siempre CRM ← FACTUSOL.** No hay sync inverso: FACTUSOL es la
fuente contable y no se toca desde aquí.

---

## Dos modos

| Modo | Itera | Match | Cuándo |
|---|---|---|---|
| **Contactos por email** (por defecto) | contactos con email | `EMACLI` **exacto** | Casi siempre. Sin falsos positivos. |
| **Empresas por NIF/nombre** | empresas | NIF exacto → email → nombre difuso | Cuando la empresa tiene NIF y no hay contacto con email. |

En los dos, lo que se actualiza es **una empresa del CRM**. Lo que cambia es
por dónde se llega a ella.

### Por qué el modo por email es el recomendado

El modo por NIF/nombre se probó en producción y da mucho ruido:

- La mayoría de las empresas del CRM llegaron de imports masivos **sin NIF**,
  así que caen al match por nombre.
- El nombre difuso produce falsos positivos: «4d Factory» ↔ «FACTORY».

El email o coincide exacto o no coincide. Menos cobertura, pero lo que propone
es fiable.

**Qué actualiza:** la empresa a la que pertenece el contacto que casó.

**Qué se salta**, sin fallar y avisando:
- Contacto **sin empresa** en el CRM → `skipped_no_company`. No hay nada que
  actualizar. (Crear la empresa desde aquí es backlog.)
- Empresa **ya vinculada a OTRO** `CODCLI` → `already_linked_other`. Pisar un
  vínculo que alguien estableció a propósito sería peor que no hacer nada.

Vinculada al **mismo** CODCLI **sí** se aplica: es un refresco de datos.

En la tabla, esos dos casos salen con la casilla «Aplicar» deshabilitada y el
motivo en el tooltip — el backend los saltaría igualmente, pero así no parece
que se hayan aplicado.

---

## Cómo funciona

Dos tiempos. **Nada se escribe sin marcarlo.**

### 1. Dry-run

Botón *Ejecutar dry-run*. Solo lee: ni el CRM ni FACTUSOL se modifican.

Filtros:
- **Solo sin vincular** (por defecto) — empresas con `factusol_company_id` a
  NULL. Las ya vinculadas se gestionan desde su ficha con «Ver diferencias».
- **Todas** — incluye las vinculadas, para un refresco masivo.
- **Solo con diferencias** — esconde las que ya cuadran con FACTUSOL.

> **Una sola lectura de F_CLI.** Preguntar a DELSOL por cada empresa serían
> miles de peticiones contra un token que caduca a los 3 minutos. Se lee `F_CLI`
> entero de una vez (4533 filas) y el cruce se hace en Python — que además
> permite el match difuso por nombre, que la API no sabe hacer.

### 2. Interpretar el resultado

| Coincidencia | Cómo se busca | Fiabilidad |
|---|---|---|
| **NIF exacto** | `NIFCLI`, normalizado sin espacios ni guiones | Alta — es contable |
| **Email exacto** | `EMACLI` contra el email de los contactos de la empresa | Media |
| **Nombre parecido** | `NOFCLI`/`NOCCLI`, sin acentos ni mayúsculas, subcadena en cualquier sentido | **Baja — es una sugerencia, revísala** |

Se prueban en ese orden y se para en la primera que casa. Los nombres de menos
de 6 caracteres no se buscan: un «SL» casaría con media base.

**Varios candidatos.** Pasa de verdad: LABORATORIOS PORTA tiene dos `F_CLI` con
el mismo NIF (`codcli` 1 y 2758). Salen todos con un radio button para elegir.

*Ver diferencias* despliega el diff campo a campo: valor actual del CRM y valor
de FACTUSOL, con las filas que difieren en negrita.

### 3. Aplicar

Cada fila tiene su casilla **Aplicar**, que arranca **desmarcada** — es la que
escribe en la base. Dentro del diff, una casilla por campo (todas marcadas por
defecto) para elegir qué se sobrescribe.

*Aplicar seleccionadas* manda solo lo marcado. Cada empresa va en su propia
transacción: **que una falle no bloquea el resto del lote** — en una limpieza de
cientos de registros, abortar todo por un caso raro obligaría a repetir la
revisión entera.

Al terminar, la tabla se recarga: las aplicadas ya están vinculadas y salen de
la lista.

---

## Qué campos se sincronizan

| Campo CRM | Columna F_CLI |
|---|---|
| `name` | `NOFCLI` (nombre fiscal) |
| `tax_id` | `NIFCLI` |
| `address_line` | `DOMCLI` |
| `city` | `POBCLI` |
| `postal_code` | `CPOCLI` |
| `state` | `PROCLI` |

**No hay teléfono ni email**: la tabla `companies` no tiene esas columnas (viven
en `contacts`). El spec original los listaba; no existen.

**Un valor vacío en FACTUSOL nunca pisa el del CRM.** El objetivo es limpiar
datos, no borrarlos: si `POBCLI` está vacío y el CRM tiene ciudad, se queda la
del CRM.

Además, al aplicar se marca la empresa:
- `factusol_company_id` = el CODCLI elegido
- `factusol_sync_source` = `bulk_match`
- `factusol_synced_at` = ahora

---

## Cómo revertir

**No hay endpoint de rollback en esta versión.** Sí hay backup: los valores
previos se guardan en el **AuditLog**.

> `companies` no tiene columna `metadata_json`, así que el backup va al
> AuditLog — que además es su sitio natural: queda fechado, atribuido a quien lo
> hizo y es consultable sin ensuciar el modelo de dominio.

Para encontrar lo que había antes:

```sql
SELECT target_id, created_at, metadata_json
FROM audit_logs
WHERE action IN ('erp.factusol_bulk_sync',          -- modo por NIF/nombre
                 'erp.factusol_bulk_sync_by_email') -- modo por email
  AND target_id = '<company_id>'
ORDER BY created_at DESC;
```

Cada modo usa su propia `action`, para poder revertir un lote sin arrastrar el
otro. Lo mismo con `companies.factusol_sync_source`: `bulk_match` vs.
`bulk_by_email`.

El `metadata_json` trae:

```json
{
  "factusol_codcli": "3342",
  "applied_fields": ["name", "city"],
  "previous_values": {"name": "AUDIOVISUALES DATA", "city": "CIUDAD VIEJA"},

  "contact_id": "uuid",             // solo en el modo por email:
  "contact_email": "juan@..."       // el contacto que originó el match
}
```

Y se revierte a mano:

```sql
UPDATE companies
SET name = 'AUDIOVISUALES DATA', city = 'CIUDAD VIEJA',
    factusol_company_id = NULL, factusol_sync_source = NULL
WHERE id = '<company_id>';
```

---

## Fuera del alcance de C-5

- Endpoint de rollback (solo el backup, para uso manual).
- Sync inverso FACTUSOL ← CRM.
- Contactos: solo se tocan empresas.
- Crear en FACTUSOL las empresas sin match — eso ya está en la ficha de empresa
  («Crear en FACTUSOL», C-3).
