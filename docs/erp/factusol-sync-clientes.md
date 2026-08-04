# BoHub ERP — Sync de clientes CRM ↔ FACTUSOL (Fase C · C-3)

Cómo se busca, vincula y (excepcionalmente) crea un cliente de FACTUSOL desde el
CRM. **Solo vínculo: el ERP nunca sobreescribe datos** en ninguno de los dos
sistemas.

## Quién crea el cliente en FACTUSOL (y el bug histórico)

> **Contexto obligatorio antes de tocar nada de escritura de clientes.**

Una **app externa WooCommerce→FACTUSOL** replica cada pedido de las 3 tiendas
como Pedido de Cliente (F_PCL) **creando ella misma el cliente** en F_CLI. El
CRM **no** participa en ese alta.

En C-1 el CRM intentaba crear el cliente por su cuenta
(`ensure_customer_in_factusol`) y en producción reventó con
**`BDEscribirRegistroError`**: intentaba insertar un cliente que la app externa
ya había creado. Se retiró en **C-2-fix1** (`f6c7155`).

C-3 vuelve a permitir crear clientes, pero **solo donde no hay conflicto** y con
dos guards:

1. **Bloqueo por origen** — si el cliente tiene algún pedido de WooCommerce, el
   endpoint devuelve **409 `woo_managed_customer`**: ahí manda la app externa y
   el CRM solo puede *vincular*.
2. **Dedupe por NIF** — antes de escribir se consulta
   `F_CLI WHERE UPPER(CIFCLI)=UPPER(<nif>)`. Si ya existe, **no se escribe**: se
   devuelve el CODCLI existente y se vincula (`created: false`).

Resultado: el alta desde el CRM queda de facto reservada a **pedidos manuales**
(teléfono, muestras, reparaciones), que es justo donde no hay app externa.

## Las 3 vías

```
                  ┌──────────────────────────────────────────┐
   Buscas (NIF    │  ¿Está en FACTUSOL?                       │
   o nombre) ────►│                                           │
                  └───┬───────────────────────┬───────────────┘
                      │ sí                    │ no
          ┌───────────▼──────────┐   ┌────────▼─────────────────┐
          │ ¿vinculado en CRM?   │   │ ¿está en el CRM?         │
          └───┬──────────┬───────┘   └────┬──────────────┬──────┘
       sí ────┘          └──── no         │ sí           │ no
          │                  │            │              │
   ✓ Usar tal cual    Vincular al CRM   «Crear en     Crear primero
   (badge «✓ En CRM») (badge amarillo)   FACTUSOL»    en Contactos
                                         (badge azul)
```

- **En ambos** → se usa directamente; si los datos difieren, se avisa (ver abajo).
- **Solo en FACTUSOL** → se vincula al CRM guardando el CODCLI.
- **Solo en CRM** → botón «Crear en FACTUSOL» (sujeto a los 2 guards de arriba).

## Dónde vive el vínculo

| CRM | Columna | Origen |
|---|---|---|
| Empresa | `companies.factusol_company_id` | migración **0080** (Fase A) |
| Contacto | `contacts.factusol_contact_id` | migración **0080** (Fase A) |

C-3 **no añade columnas**: reutiliza las existentes. La migración **0087** solo
crea **índices** sobre ellas, porque el autocomplete cruza los CODCLI
encontrados contra el CRM en cada búsqueda (con 4531 clientes, el full scan se
nota). `companies.factusol_sync_source` queda a `erp_link` al vincular.

## Por qué NO auto-sincronizamos datos

Un cliente puede tener **dirección distinta en cada sistema y ambas ser
correctas**: la de FACTUSOL suele ser la fiscal, la del CRM la de contacto
comercial. Sincronizar automáticamente destruiría información que alguien
introdujo a propósito, y sin histórico para recuperarla.

Por eso la ficha de empresa muestra las diferencias campo a campo
(Nombre / NIF / Dirección / Ciudad / CP / Provincia) con la etiqueta de cada
origen, y **deja la corrección al operador** en el sistema que proceda.

## Endpoints

| Método | Ruta | Qué hace |
|---|---|---|
| `GET` | `/api/erp/factusol/customers/search?q=&by=nif\|email\|name` | Busca en F_CLI y marca los que ya están vinculados al CRM |
| `POST` | `/api/erp/factusol/customers/link` | Vincula un CODCLI a una empresa/contacto (409 si ya está vinculado a otro) |
| `POST` | `/api/erp/factusol/customers/create` | Crea en F_CLI con los 2 guards y vincula |

Notas de implementación:
- **`by=nif` y `by=email` son exactos** (case-insensitive); `by=name` es `LIKE`.
- **`LIMIT` no funciona** en el filtro de la API DELSOL: se traen todas las filas
  y se recortan a 50 en Python.
- El `filtro` es **SQL crudo**, así que los literales se escapan
  (`_sql_escape`) — un NIF con comilla no puede romper ni inyectar la consulta.
- Los IDs del CRM son **UUID (String 36)**, no enteros.

## Diagnóstico

**`BDEscribirRegistroError` al crear un cliente**
1. Comprueba si el cliente ya existe: `GET …/customers/search?q=<NIF>&by=nif`.
   Si aparece → alguien (probablemente la app externa) lo creó; **vincúlalo** en
   vez de crearlo.
2. Si el cliente tiene pedidos Woo, el endpoint ya lo bloquea con 409
   `woo_managed_customer` — es el comportamiento correcto, no un fallo.
3. Revisa los logs del `api` (`docker compose logs api`): cada alta deja
   `factusol: cliente creado CODCLI …` o `… ya existe (CODCLI …)`.

**409 `already_linked`** — ese CODCLI ya está vinculado a otra empresa/contacto.
Comprueba cuál con la búsqueda (el resultado trae `crm_link`) y decide si el
vínculo antiguo era erróneo.

**503 `factusol_unavailable`** — faltan credenciales FACTUSOL o la config está
rota; el resto del CRM sigue funcionando.

## Fuera de alcance

- Import masivo de los 4531 clientes al CRM (se vinculan on-demand).
- Sync inverso automático (FACTUSOL no tiene webhooks).
- Auto-sincronización de datos divergentes.
