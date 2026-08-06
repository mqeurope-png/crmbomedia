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

## Tres modos

| Modo | Itera | Match | Cuándo |
|---|---|---|---|
| **Contactos por email** (por defecto) | contactos con email | `EMACLI` **exacto** | Casi siempre. Sin falsos positivos. |
| **Empresas por NIF/nombre** | empresas | NIF exacto → email → nombre difuso | Cuando la empresa tiene NIF y no hay contacto con email. |
| **Importar clientes que no están en el CRM** (C-6) | clientes `F_CLI` | ninguno — son los que **no** casan con nadie | Después de conciliar, para traer el resto |

Los dos primeros **actualizan** una empresa que ya existe; lo que cambia es por
dónde se llega a ella. El tercero **crea** las que faltan.

### Por qué el modo por email es el recomendado

El modo por NIF/nombre se probó en producción y da mucho ruido:

- La mayoría de las empresas del CRM llegaron de imports masivos **sin NIF**,
  así que caen al match por nombre.
- El nombre difuso produce falsos positivos: «4d Factory» ↔ «FACTORY».

El email o coincide exacto o no coincide. Menos cobertura, pero lo que propone
es fiable.

**Qué actualiza:** la empresa a la que pertenece el contacto que casó.

#### Los desenlaces del apply

| Resultado | Cuándo | Qué hace |
|---|---|---|
| `refreshed` | El contacto tiene empresa | Le trae los datos limpios y la vincula al CODCLI |
| `created_new_company` | El contacto **no** tiene empresa | **Crea** una con todos los datos de F_CLI, la vincula y se la asigna al contacto |
| `linked_existing_company` | No tiene empresa, pero ya hay una vinculada a ese CODCLI | Le asigna **esa**, sin crear otra |
| `reassigned_to_existing_company` | Su empresa va a **otro** CODCLI y ya existe una con el del match | **Mueve el contacto** a esa. La original no se toca |
| `reassigned_to_new_company` | Su empresa va a **otro** CODCLI y no existe ninguna con el del match | **Crea** la correcta y mueve el contacto. La original no se toca |
| `skipped_already_linked_other` | — | Desenlace histórico de C-5-fix1. Hoy ese caso se reasigna |

Vinculada al **mismo** CODCLI **sí** se aplica: es un refresco de datos.

> **Por qué `linked_existing_company` existe.** Si ya hay una empresa CRM
> apuntando a ese cliente y creásemos otra, quedarían **dos empresas
> apuntando al mismo cliente de FACTUSOL** — exactamente la duplicidad que
> costó arreglar en C-3-fix3. Se reutiliza la que ya está.

Una empresa **creada** nace con **todos** los campos de F_CLI, no solo los
marcados: no hay nada previo que preservar.

#### Reasignación de contactos mal agrupados

**El caso Vilatzara.** En el primer apply real de este modo salieron 509
refrescos, 424 empresas creadas y **128 omisiones**. El 90% de esas omisiones
eran el mismo patrón: decenas de contactos colgaban de una única empresa CRM
«Institut Vilatzara» (vinculada al codcli 3960), pero sus emails `@xtec.cat`
casaban con **escuelas distintas** del Departament d'Educació — Escola Ardenya,
Escola Alexandre Galí, Escola Josep Manuel Peramàs…—, cada una con su propio
`F_CLI`.

No era un vínculo en conflicto: era una **agrupación mal hecha en el CRM**,
arrastrada de un import antiguo. C-5-fix1 los saltaba por prudencia, y el
resultado era que nadie los arreglaba.

Desde C-5-fix5 se reasignan:

1. Si ya existe otra empresa CRM vinculada al CODCLI del match → el contacto se
   **mueve** a esa.
2. Si no existe → se **crea** con los datos de F_CLI y el contacto se mueve a
   ella.

**La empresa original no se toca nunca**: conserva su vínculo, sus datos y sus
demás contactos, que pueden ser perfectamente legítimos. Lo único que cambia es
`contacts.company_id` del contacto mal asignado.

> **Qué NO hace.** No borra la empresa original aunque se quede sin contactos
> (Vilatzara sigue ahí, con menos gente): decidir si una empresa sobra es del
> operador, no de un lote masivo. Y no mueve contactos sin match por email —
> solo los que casan exacto.

En la tabla, estas filas salen con un chip azul **«Reasignar → NNNN»** y la
casilla «Aplicar» **habilitada**. El tooltip dice de dónde sale y a dónde va.
Hasta C-5-fix4 era un chip ámbar «Ya vinculada a NNNN» con la casilla
deshabilitada.

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

#### Cuántos se procesan

El modo **por email** procesa **todos** los contactos con email, sin tope. Es
barato: `F_CLI` ya se lee una sola vez y el resto es comparar strings. El número
de matches además está acotado por los emails que haya en F_CLI (~4 500), no por
los contactos que tenga el CRM.

> C-5-fix2 arregló esto: había un tope de 200 que **cortaba el bucle**, así que
> de 20 282 contactos solo se evaluaban ~4 000 y los otros 16 000 no se veían.
> Peor: el contador de «sin match» solo sumaba lo iterado, así que los totales
> del resumen ni siquiera cuadraban.

Queda un tope de seguridad de 100 000 por si alguna vez hace falta. Si se
alcanza, la respuesta trae `truncated: true` y la pantalla lo avisa — cortar en
silencio haría que se dieran por revisados contactos que nadie miró.

El modo **por empresa** sí pagina de verdad (200 por defecto, `LIMIT` en SQL).

Sobre el resumen: `con match + sin match = total`. Los «sin empresa CRM» son un
**subconjunto** de los que tienen match, no una tercera categoría.

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

> **Cuál viene premarcado: el `codcli` MAYOR.** Los CODCLI de Bomedia son
> autonuméricos, así que el mayor es el cliente dado de alta más tarde — el que
> suele traer los datos buenos. Caso real: `evamariamc1@gmail.com` casa con
> 2123, 2210 y 2278; el bueno es el 2278. La comparación es **numérica**, no
> alfabética: si no, «999» ganaría a «2278». El operador puede cambiarlo.

*Ver diferencias* despliega el diff campo a campo: valor actual del CRM y valor
de FACTUSOL, con las filas que difieren en negrita.

### 3. Aplicar

Cada fila tiene su casilla **Aplicar**, que arranca **desmarcada** — es la que
escribe en la base. Dentro del diff, una casilla por campo (todas marcadas por
defecto) para elegir qué se sobrescribe.

En la cabecera de la columna *Aplicar* hay un **checkbox master** que marca de
golpe todas las filas **visibles que se pueden aplicar**:

- Respeta el filtro «Solo con diferencias»: lo que no se ve, no se marca.
- Salta las `skipped_already_linked_other` — su casilla está deshabilitada y el
  backend las saltaría igualmente.
- Tres estados: marcado (todas), **indeterminado** (algunas), desmarcado
  (ninguna). Desmarcarlo las quita todas.
- Funciona en los dos modos.

**A partir de 50 operaciones se pide confirmación**, con el número exacto y el
recordatorio de que revertir es SQL a mano. Marcar cientos de filas con un clic
es justo lo que hace fácil lanzar un lote enorme sin querer. Por debajo de 50 se
aplica directo, sin fricción.

*Aplicar seleccionadas* manda solo lo marcado. Cada empresa va en su propia
transacción: **que una falle no bloquea el resto del lote** — en una limpieza de
cientos de registros, abortar todo por un caso raro obligaría a repetir la
revisión entera.

Al terminar, la tabla se recarga: las aplicadas ya están vinculadas y salen de
la lista.

---

## Modo 3: importar las F_CLI que no están en el CRM

Los modos 1 y 2 concilian lo que ya existe en los dos lados. Cuando terminas
con ellos quedan **miles de clientes de FACTUSOL que nunca llegaron al CRM**:
facturación de años que no entró por WooCommerce, ni por formularios, ni por los
imports antiguos. Existen en la contabilidad y no existen en el CRM.

Este modo los trae.

### Dry-run

Lista los `F_CLI` cuyo CODCLI **no está** en `companies.factusol_company_id`.
Una sola lectura de F_CLI y una sola consulta al CRM: preguntar por cada cliente
serían 4 500 SELECTs.

Filtro propio: **«Solo los que tengan email»**. De los que no lo traen solo
saldría una empresa sin nadie con quien hablar. Por defecto se listan todos.

> «Solo con diferencias» **no aparece** en este modo: no hay nada previo con lo
> que comparar.

El resumen de arriba dice cuántas huérfanas hay, cuántas con email y cuántas
sin, y de cuántos clientes de F_CLI salen.

### Aplicar

Por cada CODCLI marcado, en su **propia transacción**:

1. **Empresa** con los datos de F_CLI: `NOFCLI` → `name` (con `NOCCLI` de
   respaldo, y `Cliente <codcli>` si no hay ninguno), `NIFCLI` → `tax_id`,
   `DOMCLI` → `address_line`, `POBCLI` → `city`, `CPOCLI` → `postal_code`,
   `PROCLI` → `state`, `PAICLI` → `country`.
   Queda ya vinculada: `factusol_company_id = <codcli>`,
   `source = factusol_import`, `factusol_sync_source = import_orphans`.
2. **Contacto**, solo si hay `EMACLI`: `first_name` = el nombre de la empresa
   (F_CLI guarda razones sociales, no personas — sin apellido, se edita
   después), `email` = `EMACLI`, `phone` = `TELCLI`.
3. **Etiqueta `factusol_import`** al contacto (se crea la primera vez y se
   reutiliza después).

Confirmación a partir de 50, igual que en los otros modos.

| Resultado | Cuándo |
|---|---|
| `imported_company_and_contact` | Había `EMACLI` y el email estaba libre |
| `imported_company_only` | Sin contacto. `contact_skipped` dice por qué: `no_email`, `email_taken` o `disabled` |
| `skipped_race` | Entre el dry-run y el apply alguien vinculó ese CODCLI. No se pisa, y no es un error |

> **`email_taken`.** `contacts.email` es **UNIQUE**. Si ese email ya es de otro
> contacto, no se intenta crear —el INSERT reventaría y se llevaría por delante
> la empresa, que sí queremos— ni se le roba a su empresa actual. Se queda la
> empresa creada y el motivo en `contact_skipped`.

### Dónde acabó la etiqueta, y por qué

El spec de C-6 pedía etiquetar la **empresa**. En este CRM **las etiquetas son
de contacto**: existen `tags` y `contact_tags`, pero **no hay tabla de etiquetas
de empresa**, y `/api/companies` no tiene filtro por tag — tiene filtro por
`source`. Montar etiquetas de empresa sería migración + API + UI, fuera del
alcance de C-6.

Así que se hacen las dos cosas que sí funcionan hoy:

| Qué | Dónde | Para qué |
|---|---|---|
| `source = factusol_import` | **todas** las empresas creadas | El filtro operativo: `GET /api/companies?source=factusol_import` |
| tag `factusol_import` | el **contacto** creado | Segmentación por etiqueta, donde el CRM sabe guardarlas |

**El filtro bueno es el de `source`**: cubre el 100% del lote, incluidas las
empresas sin email, que no tienen contacto que etiquetar.

### Cómo revertir un lote de importación

```sql
SELECT target_id, created_at, metadata_json
FROM audit_logs
WHERE action = 'erp.factusol_bulk_import_orphan'
ORDER BY created_at DESC;
```

`metadata_json` trae `{codcli, created_company_id, created_contact_id,
company_name, tag}`. `created_contact_id` es `null` cuando no se creó contacto.

Aquí **no hay `previous_values`**: no existía nada antes. Se deshace borrando,
y en este orden (la asignación de etiqueta y el contacto cuelgan de la empresa):

```sql
DELETE ct FROM contact_tags ct
  JOIN contacts c ON c.id = ct.contact_id
 WHERE c.id = '<created_contact_id>';
DELETE FROM contacts  WHERE id = '<created_contact_id>';
DELETE FROM companies WHERE id = '<created_company_id>';
```

Para deshacer el lote **entero** de una vez, el `source` es el ancla — y por eso
conviene no reutilizarlo para nada más:

```sql
-- Mira primero qué se va a llevar por delante.
SELECT COUNT(*) FROM companies WHERE source = 'factusol_import';
```

La etiqueta `factusol_import` en sí no hace falta borrarla: sin asignaciones
queda huérfana y no molesta.

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
WHERE action IN (
    'erp.factusol_bulk_sync',                      -- modo por NIF/nombre
    'erp.factusol_bulk_sync_by_email',             -- modo por email: refresco
    'erp.factusol_bulk_sync_by_email_create_company') -- modo por email: creada
  AND target_id = '<company_id>'
ORDER BY created_at DESC;
```

Cada modo usa su propia `action`, para poder revertir un lote sin arrastrar el
otro. Lo mismo con `companies.factusol_sync_source`: `bulk_match` vs.
`bulk_by_email`.

> **La reasignación se audita sobre el CONTACTO.** Es la única de las cuatro
> acciones cuyo `target_type` es `contact`: lo que cambia es
> `contacts.company_id`, no una empresa. Por eso se busca por `contact_id`:
>
> ```sql
> SELECT target_id, created_at, metadata_json
> FROM audit_logs
> WHERE action = 'erp.factusol_bulk_sync_by_email_reassign'
>   AND target_id = '<contact_id>'
> ORDER BY created_at DESC;
> ```
>
> `metadata_json` trae `{contact_id, contact_email, old_company_id,
> old_company_factusol_id, new_company_id, new_company_factusol_id,
> reassign_type}`, donde `reassign_type` es `existing` o `new_created`.
>
> Se revierte devolviendo el contacto a su empresa anterior:
>
> ```sql
> UPDATE contacts SET company_id = '<old_company_id>' WHERE id = '<contact_id>';
> ```
>
> Si `reassign_type` era `new_created`, la empresa que se creó queda huérfana:
> bórrala aparte, después de comprobar que ningún otro contacto la usa.

> **Una empresa CREADA se deshace distinto.** Su entrada lleva
> `..._create_company` y `previous_values` vacío — no hay valores anteriores
> que restaurar porque la empresa no existía. Se revierte **borrándola** (y
> dejando el `company_id` del contacto a NULL), no con un `UPDATE`.

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
