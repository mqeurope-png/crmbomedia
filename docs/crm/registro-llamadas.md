# Registro de llamadas (CRM-1)

El modal **«Registrar llamada»** de la ficha del contacto guarda cada llamada en
`call_logs` con su resultado, duración (por tramo), asunto, nota y las acciones
posteriores que el operador dispare. CRM-1 explota mejor esos datos.

---

## 1. Filtrar la lista de contactos por sus llamadas

En `/contacts`, el panel de filtros gana el grupo **«Llamadas»**. Son campos del
mismo builder de reglas que el resto de filtros (tags, owner, source…), así que
se combinan con ellos y se guardan en las vistas.

| Filtro | Qué hace |
|---|---|
| **Resultado de llamada** | Contactos con ≥1 llamada cuyo resultado es el elegido (Contactado, No contesta, Buzón de voz, Volver a llamar, Interesado, No interesado, Pidió información, Otro) |
| **Acción tras llamada** | ≥1 llamada donde se ejecutó esa acción (Cambió pipeline, Ajustó lead score, Ajustó star score, Creó tarea de rellamada, Añadió a workflow) |
| **Duración de llamada** | ≥1 llamada en ese **tramo** |
| **Fecha de llamada** | ≥1 llamada en esa ventana (antes/después/entre/últimos N días…) |

> **La duración es por tramo, no por segundos.** `call_logs` guarda el bucket
> que el operador marca en el modal (`<1 min`, `1-5 min`, `5-30 min`,
> `>30 min`), no la duración exacta. El filtro por segundos del spec original no
> tenía datos contra los que operar; se filtra por tramo.

> **Combinar dos filtros de llamada en el builder no exige que sea la MISMA
> llamada.** Cada condición es un `EXISTS` suelto sobre `call_logs`, como el
> resto de filtros de relación del CRM: «tiene una llamada interesada» **y**
> «tiene una llamada larga» las puede cumplir con dos llamadas distintas.
>
> El endpoint plano `GET /api/contacts?call_result=…&call_duration_bucket=…`
> **sí** exige que una sola llamada cumpla todos los criterios (un único
> `EXISTS` con todas las condiciones). Es el que usar cuando «interesada Y
> larga» tiene que ser la misma llamada.

---

## 2. La nota de la llamada aparece en el timeline

Si el operador rellena la **nota** al registrar la llamada, además de quedar en
`call_logs.notes` se crea una `Note` del contacto:

- `source = "call_log"` (el CRM no tiene un campo `via`; `source` es el
  discriminador que ya distingue `manual` / `agile:timeline` / …).
- `call_log_id` apunta a la llamada, para trazarla.
- Sale automáticamente en la pestaña **Notas** y en el **Historial**.

> **Borrar la llamada conserva la nota.** La FK es `ON DELETE SET NULL` (y el
> endpoint lo hace explícito, para que SQLite y MySQL se comporten igual): se
> suelta el enlace, no se pierde lo que se escribió.

No hay edición de llamadas en el CRM (el API solo crea, lista y borra), así que
la nota se crea una vez al registrar; no hay que reconciliarla después.

---

## 3. Acción posterior «Ajustar star score»

En **Acciones tras la llamada**, debajo de «Ajustar lead score», hay una casilla
**«Ajustar star score»**. Al marcarla se ve la valoración actual del contacto
(solo lectura) y un selector de 1-5 estrellas para la nueva.

A diferencia del lead score —que es un **delta** (`+10`, `-10`)— el star score
es un **valor absoluto** (1-5): se fija la valoración final. Al guardar:

- `contacts.star_rating` = el valor elegido.
- Queda en `call_logs.actions_taken` (`{"adjust_star_score": 4}`), que es lo que
  lee el filtro «Acción tras llamada = Ajustó star score».
- Entra en el audit log (`call_log.star_score_adjusted`, con `from`/`to`).

Lead score y star score son independientes: se pueden ajustar los dos en la
misma llamada.

---

## 4. Las llamadas en «Actividad reciente»

El resumen de la ficha lee `activity_events`, no `call_logs`. Al registrar una
llamada se emite un `ActivityEvent` (`event_type="CALL_LOG"`) con el resultado,
la nota y el tramo, así que la llamada aparece en el feed junto a emails, notas y
tareas sin duplicar (UNIQUE `crm/calls/call_log:<id>`).

---

## Nota de implementación: `call_logs.actions_taken`

Antes, las acciones posteriores se ejecutaban pero **no se guardaban** en la
llamada — solo dejaban rastro en `audit_logs` y en `follow_up_task_id`. CRM-1
añade `call_logs.actions_taken` (JSON texto) con lo que corrió y sus valores,
para (a) filtrar contactos por acción y (b) guardar el star score elegido. El
filtro por acción hace un `LIKE '%"adjust_star_score"%'` sobre ese texto:
portable SQLite/MySQL porque la clave sale entrecomillada en los dos.

Migración `20260806_0089`: `call_logs.actions_taken`, `notes.call_log_id` (FK
`ON DELETE SET NULL`) e índice en `call_logs.result_code`.
