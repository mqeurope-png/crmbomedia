# Deduplicar empresas por CIF (Fase C · C-7)

`/admin/dedupe-companies` — solo **ADMIN**. El apply **borra empresas**.

---

## Para qué sirve

Después de un import masivo el CRM acaba con la misma empresa varias veces.
Caso que lo motivó: tras C-6 aparecieron **dos «Exatronic Lda»** con el mismo
NIF `PT503420506` — porque en FACTUSOL hay **dos CODCLI para ese NIF** (2629 y
2819, un duplicado histórico del escritorio) y el import las trajo por separado.

Esta pantalla las agrupa y las funde en una.

**Es post-proceso.** No cambia cómo se crean las empresas: los imports siguen
creando lo que creen, y esto limpia después. Se puede pasar tantas veces como
haga falta.

---

## Solo por NIF, y solo por NIF exacto

Se agrupa por `companies.tax_id`, normalizado sin espacios ni guiones ni puntos
(`B-61.444 402` y `b61444402` son el mismo).

Las de **NIF vacío se ignoran por completo**. No es una limitación técnica: sin
NIF no hay ninguna evidencia de que dos empresas sean la misma, y esta pantalla
**borra filas**. Agrupar por nombre o por email daría falsos positivos —
«4d Factory» ↔ «FACTORY» ya nos costó un susto en C-5— y el precio de
equivocarse aquí es perder una empresa de verdad.

---

## Cómo funciona

Dos tiempos. **Nada se fusiona sin marcarlo.**

### 1. Buscar

Botón *Buscar duplicados por CIF*. Solo lee.

El resumen dice cuántos grupos hay y cuántas empresas están implicadas. Los
grupos más gordos salen primero: son los que más ensucian la base.

### 2. Elegir la principal

*Ver detalle* despliega las empresas del grupo con lo que cada una tiene:
ciudad, CODCLI de FACTUSOL, fecha de creación, nº de contactos y nº de pedidos.

Viene una **premarcada**, por este orden:

1. **Más pedidos** — es la que tiene historia comercial que conservar.
2. **Más contactos.**
3. **Más antigua** (`created_at`).
4. La que tenga vínculo con FACTUSOL.

El operador puede cambiarla con el radio button. El chip ámbar **«aportará:
ciudad, web»** dice qué campos tiene una absorbida que le faltan a la principal
— es lo que decide si merece la pena mirar el grupo.

### 3. Fusionar

Marca los grupos y pulsa *Fusionar seleccionadas*. El modal dice el número
exacto de empresas que van a **desaparecer** y de registros que se van a mover.

Por cada grupo, en su **propia transacción**:

| Paso | Qué hace |
|---|---|
| Mover | `contacts`, `orders` y `tasks` de las absorbidas pasan a apuntar a la principal |
| Completar | Los campos que la principal tenga **vacíos** se rellenan desde las absorbidas |
| CODCLI | Si la principal no tenía, hereda el de la absorbida |
| Borrar | Las absorbidas se eliminan |

**Nunca se sobrescribe un valor que la principal ya tenga.** Si el operador la
eligió como buena, sus datos mandan.

> **Los CODCLI descartados salen en rojo.** Si las dos tenían vínculo con
> FACTUSOL y a distintos clientes, se queda el de la principal y el otro se
> pierde. Puede haber facturación colgando de ese CODCLI, así que el resumen lo
> dice explícitamente y queda en el audit log.

---

## Por qué esto no reutiliza el «Fusionar» de la ficha de empresa

Ya existe `POST /api/companies/{id}/merge/{target}`, el botón de la ficha. **No
se reutiliza**, y conviene saber por qué.

Las tres FK que apuntan a `companies.id` son todas **`ON DELETE SET NULL`**:

| Tabla | Columna |
|---|---|
| `contacts` | `company_id` |
| `orders` | `company_id` |
| `tasks` | `company_id` |

Eso significa que **borrar una empresa nunca falla**: la base pone a NULL las
referencias en silencio. El merge de la ficha mueve solo `contacts`, así que al
borrar la empresa deja los pedidos y las tareas **sin empresa**, sin dar ningún
error.

Esta ruta mueve las tres y guarda snapshot. Además, las tablas a mover **no
están escritas a mano**: se leen de los metadatos de SQLAlchemy y se comparan
con la lista de las que sabemos mover. Si alguien añade una cuarta FK y no la
registra, el merge **se niega a ejecutarse** en vez de vaciarla calladamente.
Hay un test que falla si eso pasa.

---

## Cómo revertir un merge

**No hay endpoint de rollback.** Sí hay un snapshot completo de cada empresa
borrada en el **AuditLog**.

```sql
SELECT target_id, created_at, metadata_json
FROM audit_logs
WHERE action = 'erp.company_merge'
ORDER BY created_at DESC;
```

`metadata_json`:

```json
{
  "keep_id": "319bad5e…",
  "merge_ids": ["1a874379…"],
  "merged_data_snapshot": [
    {"id": "1a874379…", "name": "Exatronic Lda", "tax_id": "PT503420506",
     "city": "Aveiro", "factusol_company_id": "2819",
     "source": "factusol_import", "created_at": "2026-08-06T04:00:05"}
  ],
  "filled_fields": {"city": "1a874379…"},
  "moved": {"contacts_moved": 1, "orders_moved": 0, "tasks_moved": 0},
  "discarded_factusol_codclis": ["2819"]
}
```

Revertir a mano tiene tres partes, en este orden:

```sql
-- 1. Rehacer la empresa borrada, con su ID original.
INSERT INTO companies (id, name, tax_id, city, factusol_company_id, source, is_active)
VALUES ('1a874379…', 'Exatronic Lda', 'PT503420506', 'Aveiro', '2819',
        'factusol_import', 1);

-- 2. Devolverle lo que se movió. OJO: hay que saber QUÉ filas eran suyas —
--    `moved` solo trae el conteo, no los IDs. Si la principal ya tenía
--    contactos propios, no se distinguen. Mira el histórico o revierte
--    solo si la principal estaba vacía.
UPDATE contacts SET company_id = '1a874379…' WHERE id IN (…);

-- 3. Vaciar los campos que se completaron desde ella (los de `filled_fields`).
UPDATE companies SET city = NULL WHERE id = '319bad5e…';
```

> **La reversión es imperfecta a propósito.** El snapshot guarda la empresa
> entera, pero **no la lista de IDs movidos**: en un grupo grande eso serían
> miles de UUID en cada entrada del audit. Si necesitas poder deshacerlo fila a
> fila, haz un backup antes del lote — hay uno automático diario, y
> `/admin/backups` deja lanzar uno manual.

---

## Fuera del alcance de C-7

- Dedupe por email, nombre o teléfono. **Solo NIF.**
- Endpoint de rollback (solo el snapshot, para uso manual).
- Cambiar cómo se crean las empresas. Esto es post-proceso.
- El botón «Fusionar» de la ficha individual sigue como estaba.
