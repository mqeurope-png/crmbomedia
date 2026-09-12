# Cliente FACTUSOL · tipo de documento del NIF/CIF y régimen de IVA (Tarea C)

**Estado: Parte 1 (investigación) — PARADA obligatoria.** Nada de esto escribe
en FACTUSOL. La Parte 2 (arreglo) solo arranca cuando Bart pegue el volcado
real de 3-4 clientes (`--cli-row`) y confirme el mapeo de columnas.

## Síntoma (Bart)

En las fichas de cliente que crea BoHub, el desplegable «tipo de documento» del
identificador (N.I.F. / NIF-IVA operador intracomunitario / Pasaporte /
Documento oficial / Certificado de residencia fiscal / Otro) no queda bien, y
el régimen de IVA (Sí / No / Intracomunitario / Exportación, y RE) hay que
revisarlo: los intracomunitarios / exportación deben quedar como tal.

## 1. Columnas de `F_CLI` → DESCONOCIDAS: hace falta el volcado

- BoHub escribe **exactamente 11 columnas** de `F_CLI` y solo en UN sitio,
  `customers.build_customer_payload` (`create_customer`, C-3-fix1): `CODCLI`,
  `NOFCLI`, `NOCCLI`, `NIFCLI`, `DOMCLI`, `POBCLI`, `CPOCLI`, `PROCLI`,
  `PAICLI` (+ `EMACLI`/`TELCLI` si vienen). **No copia ninguna fila real** y
  deja todo lo demás a los defaults de `EscribirRegistro`: ahí viven el tipo
  de documento y el régimen de IVA → el escritorio enseña el default.
- Ninguna columna de tipo de documento / régimen / RE aparece en el código,
  los docs ni ningún volcado del repo (nunca se ha volcado una fila real de
  `F_CLI`). Pistas por la convención de sufijos de las tablas ya volcadas:
  `TPDFAC`/`TIDFAC` (F_FAC), `TIVFAC`/`REQFAC`, `TIVALB`/`REQALB`,
  `TIVPRE` («tipo de IVA del documento»), `PREC*`/`IREC*` (recargo) → en
  `F_CLI` serían del estilo `TPDCLI`/`TIDCLI`, `TIVCLI`, `REQCLI`. **Son
  hipótesis**: manda el volcado.
- Nuevo modo del discovery (solo lectura):

      docker exec crmbo-api-1 python -m scripts.factusol_discover_albaranes \
          --cli-row <CODCLI nacional> <CODCLI UE con NIF-IVA> <CODCLI fuera UE>

  Vuelca cada fila completa (columna · tipo · valor) marcando las 11 que
  BoHub escribe y las candidatas por prefijo, y con 2+ clientes lista las
  columnas que DIFIEREN fuera de las que BoHub escribe: el tipo de documento y
  el régimen tienen que estar ahí. `CargaTabla` devuelve la tabla completa,
  así que salen TODAS las columnas vivas.

## 2. Cómo escribe hoy BoHub el cliente y de dónde sale el país

- Alta: `POST /api/erp/factusol/customers/create` (Fase C) → `create_customer`
  (dedupe por NIF, `MAX(CODCLI)+1`, 11 columnas). Sync/dedupe/«Traer datos»
  (`bulk_match`, `import_orphans`, `pull_into_crm`, `link`) NO escriben
  `F_CLI`: solo el CRM. La emisión de factura no toca `F_CLI`.
- País: `_country_code()` mapea ISO-2 → numérico con **10 países** y cae a
  **724 (España) para todo lo demás**; el payload del endpoint tiene
  `pais="ES"` por defecto y el botón «Crear en FACTUSOL» de la ficha de
  empresa **no envía `pais`**. Consecuencia: un cliente belga/alemán creado
  desde la ficha nace en FACTUSOL como España → aunque existiera la columna
  de régimen, el país ya está mal. `Company.vat` («VAT intracomunitario»)
  existe en el CRM pero ningún escritor de FACTUSOL lo lee.

## 3. Impacto real: ¿IVA de las facturas o solo metadato?

- **Facturas de pedidos web (F_PCL → F_FAC)**: copia por sufijo de TODAS las
  bandas (`NET/BAS/PIVA/IIVA/PREC/IREC/TIVA`, `TIVPCL→TIVFAC`,
  `REQPCL→REQFAC`). BoHub transcribe lo que ya calculó la app Woo→FACTUSOL:
  el IVA no lo decide BoHub.
- **Fase 2 (albarán → factura)** y **presupuesto → albarán/factura**: misma
  copia por sufijo (`chain.build_target_header`, sin excluir prefijos de IVA).
- **Proformas creadas por BoHub** (`quotes._totals`): el ÚNICO sitio donde
  BoHub calcula IVA — **banda 1 al 21 % por defecto**, sin escribir `TIVPRE`
  ni `IVALPS`, y con `CPAPRE=724` porque `_customer_from_company` no pasa
  `pais`. Un cliente UE/exportación con proforma → albarán → factura de BoHub
  sale con IVA 21 % **en los importes**, no solo en la ficha.
- Manuales: hasta la Tarea A no tenían factura; ahora la cadena albarán →
  factura hereda el IVA de las líneas (21 % por defecto en `tax_rate`).
- Lo que sí distingue intracomunitario hoy es solo **cosmético**: el PDF
  imprime el texto legal cuando la suma de IVA del documento es ≈ 0.
- **Lista de afectados**: se obtiene comparando `PAICLI` ≠ 724 (o
  `Company.country` ≠ ES) con las facturas emitidas por BoHub cuyo
  `PIVA1FAC` > 0 — se saca con datos reales en la Parte 2 (lectura), nunca se
  reescriben facturas ya emitidas.

## 4. ¿Existe la lógica nacional / intracomunitario / exportación?

**No.** No hay lista de países UE ni `is_eu` en el backend; `language.py`
agrupa países solo por idioma. Ni la emisión ni las proformas deciden el
régimen por país/registro. Hay que introducirla en la Parte 2.

## Parte 2 (solo tras el volcado y el OK de Bart) — plan

1. Con las columnas reales: al crear/actualizar `F_CLI`, fijar tipo de
   documento (nacional con NIF/CIF → N.I.F.; UE operador → NIF/IVA; resto el
   que toque) y régimen (nacional → IVA normal; UE con NIF-IVA →
   intracomunitario; fuera UE → exportación) en las columnas reales, copiando
   una fila real y sobrescribiendo lo mínimo con guard (como cobros/albaranes),
   y arreglando `PAICLI` (país real, no 724 por defecto).
2. Que la emisión / proformas respeten el régimen (intracomunitario y
   exportación sin IVA): lista UE + NIF-IVA.
3. Informe de clientes/facturas ya emitidos con el dato mal para corrección
   manual (no se reescriben facturas).

Tests previstos: `test_cliente_factusol_tipo_documento_nif`,
`test_cliente_factusol_regimen_intracomunitario` / `_exportacion` /
`_nacional`, `test_emision_respeta_regimen_iva`, `test_escritura_fcli_guard_minimo`.
