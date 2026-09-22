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
3. En el alta manual, buscar `FR91523447399` encuentra «EURL Y'A PAS PHOTO» en el
   CRM y su cliente F_CLI (antes no encontraba nada).
