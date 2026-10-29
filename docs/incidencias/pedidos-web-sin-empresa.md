# Incidencia — pedidos web sin empresa CRM (~26 % de los web)

**Estado:** ARREGLADO. Este documento explica la causa, el arreglo y el
procedimiento de **backfill** para recuperar los pedidos ya afectados.

## Resumen

Los pedidos web (FesteWeb → WooCommerce) **sí** crean o reutilizan el cliente en
FACTUSOL (`F_CLI`), y el pedido de cliente (`F_PCL`) queda con ese cliente. Pero
en BoHub el mapper solo sabía resolver la **empresa del CRM** cuando conseguía
sacar el NIF del *billing* de Woo (`billing_nif` / `billing_cif` /
`billing_vat_id` / `vat_id` en `meta_data`, o `billing.vat`). Cuando el billing
no lo traía, `_resolve_company()` devolvía `None` y el pedido se quedaba con
`orders.company_id` **vacío**.

Medido en producción (VPS):

| | pedidos | sin empresa |
|---|---:|---:|
| Web (WooCommerce) | 124 | **32 (~26 %)** |
| No web (manual / FACTUSOL) | 9 | 0 |

Ejemplo del caso bueno: `ARTISJ-9572` → «EURL Y'A PAS PHOTO»
(NIF-IVA `FR91523447399`) = cliente FACTUSOL `3854`.

Dos problemas relacionados aparecieron en el mismo diagnóstico:

1. **NIF-IVA extranjero.** FACTUSOL guarda el NIF con **prefijo de país**
   (`FR91523447399`). El buscador por CIF del alta manual normalizaba sin quitar
   el prefijo, así que `FR91523447399` no encontraba a la empresa (ni el número
   desnudo `91523447399` encontraba la ficha prefijada).
2. **Duplicados de Brevo.** Además de la ficha buena había 5 fichas
   «y'a pas photo» sin NIF (+1 con NIF) importadas de Brevo. **No las creaba el
   mapper**; venían de la importación de contactos.

## Causa raíz (fichero:línea)

- `backend/app/integrations/woocommerce/mapper.py`
  - `_resolve_company()` exigía **nombre de empresa Y CIF** del billing; sin CIF
    → `return None, False`, y el pedido nacía sin `company_id`.
  - `_extract_cif()` solo mira el billing de Woo; nunca preguntaba a FACTUSOL.
- `backend/app/api/companies.py:_nif_key()` normalizaba separadores y mayúsculas
  pero **conservaba el prefijo de país**, así que `FR91523447399` ≠ `91523447399`.
- `frontend/src/app/components/erp/CustomerAutocomplete.tsx` decidía buscar «por
  NIF» con `/^[A-Za-z]?\d{7,8}[A-Za-z]?$/`, que **no casa** un NIF-IVA
  extranjero → buscaba «por nombre» y no encontraba nada.

## Arreglo

**La empresa se resuelve por el CLIENTE de FACTUSOL**, no por el billing:

```
pedido web → REFPCL (prefijo de tienda + nº Woo) → F_PCL.CLIPCL → F_CLI
```

y con el NIF de ese `F_CLI` se busca la empresa en el CRM por **clave canónica**
(`company_discovery.nif_key`: sin separadores y **sin el prefijo de país de la
UE**, así `FR91523447399` ≡ `91523447399`). Si existe se vincula (y se le rellena
su `CODCLI` si estaba vacío); si no existe, se crea desde el `F_CLI`, ya enlazada.

- Servicio: `backend/app/erp/web_order_company.py` (`ensure_web_order_company`).
- Se engancha en el mapper al crear **y** al refrescar un pedido web, de modo que
  un `order.updated` posterior recupera el pedido que se importó antes de que
  FesteWeb escribiera el `F_PCL`.
- **Best-effort:** si FACTUSOL no responde, la ingesta de Woo sigue igual y el
  pedido entra sin empresa (lo recupera el backfill).
- **FACTUSOL siempre en solo lectura**: se leen `F_PCL` y `F_CLI`; crear el
  cliente sigue siendo de FesteWeb.

Y la normalización de NIF queda **unificada**: el buscador de empresas del CRM
(`find_companies_by_nif`, `GET /api/companies?q=`) y el de clientes F_CLI usan el
mismo generador de formas (`company_discovery.nif_sql_variants`), que prueba la
desnuda, la que traiga la consulta y la desnuda con cada prefijo de la UE.

## Backfill de los pedidos ya afectados

Dry-run primero (no escribe nada) y luego `--apply`:

```bash
# DRY-RUN: lista qué se vincularía/crearía y qué no se puede resolver.
docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \
    python -m scripts.backfill_empresa_pedidos_web

# Aplicar (exige teclear APLICAR, o pasar --yes en no interactivo).
# ANTES: copia de seguridad de la base de datos.
docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \
    python -m scripts.backfill_empresa_pedidos_web --apply
```

Opciones: `--limit N` (probar con unos pocos), `--order ARTISJ-9572` (uno solo),
`--csv RUTA` (informe). Los pedidos que **no** se pueden resolver se listan con
el motivo (sin `F_PCL` en FACTUSOL, cliente sin NIF…) para revisarlos a mano:
nunca se inventa una empresa sin datos fiscales.

## Duplicados de empresa

El cruce por NIF ya lo cubre `scripts.fusionar_empresas_duplicadas` (superviviente
= la vinculada a FACTUSOL; las absorbidas quedan **archivadas**, reversible). Para
el caso «y'a pas photo» (fichas **sin NIF** con el mismo nombre que la buena) hay
una pasada **opt-in** y conservadora:

```bash
# DRY-RUN por NOMBRE: solo propone cuando hay UNA ficha con NIF y las demás sin él.
docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \
    python -m scripts.fusionar_empresas_duplicadas --by-name
```

Exige un parecido de nombre ≥ 0.92 (`--name-similarity` para ajustarlo). Todo lo
ambiguo (ninguna con NIF, o dos NIF distintos) va **a revisar**, nunca se fusiona
a ciegas.

## Comprobación

1. Un pedido web nuevo (o reprocesado) con cliente en FACTUSOL muestra su empresa
   en la ficha, y la empresa queda enlazada a su `CODCLI`.
2. El backfill en dry-run lista los pendientes; tras `--apply` esos pedidos tienen
   empresa.
3. En el alta manual, buscar `FR91523447399` **y** `91523447399` (sin el prefijo)
   encuentra «EURL Y'A PAS PHOTO» en el CRM y su cliente F_CLI. La búsqueda es
   simétrica: con o sin prefijo de país, con o sin separadores, da el mismo
   resultado.

> **Nota (fix posterior).** El número **desnudo** no se reconocía como
> identificador fiscal en el alta manual (el patrón solo cubría el NIF español
> de 7-8 dígitos), así que se buscaba «por nombre» en F_CLI y no salía nada —
> y la empresa, ya vinculada tras el backfill, tampoco aparecía en la columna
> del CRM. Corregido en `frontend/src/app/lib/fiscalId.ts`.
>
> En el mismo fix: `--by-name` no llegaba a fusionar nada porque el guard de
> aplicación exigía que los absorbidos compartieran el NIF de la superviviente,
> cuando la condición del modo es justo que **no tengan NIF**. Ahora el guard
> distingue el modo y solo aborta si hay dos NIF no vacíos y distintos.

## 28/10/2026 — BOPRIN-100015: sin empresa, sin NIF y sin dirección, y sin avisar

### Qué pasó

El pedido (2.129,60 €) lo creó una persona en la web desde una intervención
de SAT:

- `_tsm_manual=yes`, `_tsm_tipo_orden=pack_servicio`;
- `billing` vacío salvo «Nadine» y el email;
- sin `billing_nif`.

BoHub no pudo emparejar la empresa, el plugin de la web no creó el pedido de
cliente en FACTUSOL, y el pedido se quedó en la bandeja con pinta de normal.
Se vinculó a mano por SQL a COMPONENTS FRANCE SPORT, S.L. (CIF B17643313,
FACTUSOL 1611).

**El origen está en la web** y lo mira Bart. En BoHub se arregló que no pase
en silencio y que se pueda corregir desde el pedido.

### Lo que hace ahora BoHub

**El aviso.** `workflow.datos_cliente_que_faltan` mira los pedidos web sin
empresa y **sin dirección de facturación** que aún no están facturados (ni
anulados, ni completados, ni procesados fuera, ni muestras). Un particular
trae su dirección y se le factura con ella: no se marca. Les pone la alerta
`datos_cliente_incompletos`:

- dice qué falta (`faltan`: empresa, NIF, dirección), mirando lo que trajo la
  web en `packing_json.shipping_address`;
- lleva la acción `vincular_cliente` y va a «Por revisar»;
- la bandeja lo marca en las dos vistas: «Sin empresa · sin NIF · sin
  dirección».

**El panel de la ficha, «Cliente del pedido».**

- `GET /api/erp/orders/{id}/cliente-web` (`app/erp/cliente_pedido_web.py`)
  propone la empresa, de más a menos segura:
  1. el cliente de FACTUSOL del pedido de cliente con su referencia (el enlace
     del #566);
  2. el NIF de la web contra BoHub;
  3. el NIF de la web contra F_CLI.

  Sin nada con qué emparejar, lo dice.
- `POST /api/erp/orders/{id}/company` la vincula:
  - con `company_id` (una ficha del CRM) o con `codcli`; en este caso usa la
    ficha que ya tiene ese cliente o crea una con sus datos, solo si tiene NIF;
  - el contacto del pedido sin empresa pasa a ser de ella;
  - solo para un pedido web sin empresa y sin facturar (409 `no_es_web`,
    `ya_tiene_empresa`, `ya_facturado`); ni propone ni vincula una empresa
    archivada (409 `empresa_archivada`); si FACTUSOL no responde, 503;
  - queda en la auditoría del pedido (`erp.order_company_linked`).

**El importador.** En el refresco (`_apply_shipping_address`), el bloque de
dirección guardado **rellena lo que tenía vacío**: el NIF y la dirección que
la web añada después. Antes no se tocaba si ya existía. La dirección (calle,
ciudad, CP, provincia, país) va en bloque: se copia entera solo si no había
ninguna, para no mezclar dos.

Nada de esto escribe en FACTUSOL ni en la web.
