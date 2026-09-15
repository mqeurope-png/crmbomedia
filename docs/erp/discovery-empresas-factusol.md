# Discovery (solo lectura): empresas del CRM vs FACTUSOL

Para decidir con datos cuántas empresas del CRM existen de verdad en FACTUSOL
y cuántas son ruido, **sin tocar las que tienen negocio vivo**. El script **no
escribe nada** (ni en el CRM ni en FACTUSOL): lee F_CLI / F_PRE por
`CargaTabla`, lee el CRM y reporta. No borra, no archiva, no vincula.

## Cómo se lanza (VPS)

```
docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \
    python -m scripts.discover_companies_factusol
# opciones: --csv RUTA (default /tmp/empresas_crm_vs_factusol.csv)
#           --recent-days 180  --ejercicio 2026  --no-proformas  --sample 30
docker compose -f /opt/crmbo/docker-compose.prod.yml cp api:/tmp/empresas_crm_vs_factusol.csv .
```

## Cruce por NIF / VAT normalizado

`nif_key` (`app/erp/company_discovery.py`): mayúsculas, sin espacios, puntos,
guiones ni barras, y **sin el prefijo de país** si el número empieza por un
código de la UE (como en VIES): `ESB12345678` ≡ `B12345678`,
`PT 503.420.506` ≡ `503420506`. Se comparan el CIF (`tax_id`) y el VAT (`vat`)
del CRM contra `NIFCLI` de F_CLI. Ejemplo real: CRM «ONLYGUAY SC» `J70876677`
casa con FACTUSOL 3734 «ONLYGUAY S C» aunque el nombre difiera.

## Lo que reporta

1. Total de empresas en el CRM (y activas); clientes leídos de F_CLI.
2. Cuántas casan por NIF/VAT normalizado y cuántas no (de las que tienen NIF);
   cuántas casan con VARIOS CODCLI (duplicados del propio FACTUSOL).
3. Cuántas sin NIF ni VAT (no cruzables).
4. Duplicados en el CRM: mismo NIF/VAT normalizado en varias fichas (grupos y
   fichas).
5. Actividad viva por empresa: pedidos (abiertos / por facturar / facturados /
   completados / ocultos del seguimiento), proformas FACTUSOL (F_PRE por el
   CODCLI que casa o el vinculado), tareas abiertas (de la empresa o de sus
   contactos), contactos, email o actividad reciente en sus contactos
   (`--recent-days`). `alive_any` = cualquiera de ellas (la lista de Bart);
   `alive_strict` = sin contar «solo tiene contactos».
6. **La tabla clave** ¿está en FACTUSOL? × ¿tiene actividad viva?:
   - **en_factusol** (se quedan): casa por NIF o está vinculada a un CODCLI
     que existe;
   - **proteger**: NO en FACTUSOL pero con actividad (se indica cuántos
     pedidos POR FACTURAR llevan);
   - **candidata_archivar**: NO en FACTUSOL y sin ninguna actividad;
   - **sin_nif_con/sin_actividad**: no cruzables (no se dan por candidatas a
     ciegas).
   Se imprime también con el criterio estricto.
7. Cuántas de las que casan NO están vinculadas hoy (`sin_vincular_casa`: el
   tamaño del auto-vincular por NIF), y el estado del vínculo del resto:
   `vinculada_ok`, `vinculada_otro_codcli` (el NIF casa con otro CODCLI),
   `vinculada_nif_no_casa`, `vinculada_codcli_inexistente`.

## CSV (una fila por empresa, separador `;`)

`company_id; name; is_active; tax_id; vat; keys; nif_malformed;
factusol_codclis; factusol_nombre; name_similarity; linked_codcli; link_status;
crm_duplicate_group; contacts; orders_total; orders_open; orders_por_facturar;
orders_invoiced; orders_completed; orders_hidden; proformas; tasks_open;
tasks_total; last_email_at; last_activity_at; alive_any; alive_strict;
in_factusol; classification; notes`

## Casos ambiguos (columna `notes`, y los primeros en pantalla)

- NIF con varios CODCLI en FACTUSOL (¿a cuál vincular?).
- Nombre muy distinto entre CRM y FACTUSOL con el mismo NIF (parecido < 0,45
  ignorando puntuación: «ONLYGUAY SC» vs «ONLYGUAY S C» da 1,0 y no salta).
- NIF mal formado (muy corto, sin dígitos, caracteres raros).
- Vinculada a un CODCLI distinto del que casa por NIF; vinculada pero el NIF
  no casa; vinculada a un CODCLI que no existe.
- NIF repetido en varias fichas del CRM.
