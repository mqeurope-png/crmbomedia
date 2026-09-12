# Cliente FACTUSOL · tipo de documento del NIF/CIF y régimen de IVA (Tarea C)

**Estado: Parte 2 hecha (mapeo confirmado con volcados reales, 2026-09-12).**
BoHub escribe el tipo de documento, el régimen de IVA y el país real en `F_CLI`
al crear un cliente, corrige a demanda los que ya existen (con vista previa y
guard), y los documentos que calcula (proformas, albarán manual) salen sin IVA
para intracomunitario / exportación. Las facturas ya emitidas **nunca** se
reescriben: hay un informe de solo lectura para corregirlas a mano.

## Síntoma (Bart)

En las fichas de cliente que crea BoHub, el desplegable «tipo de documento» del
identificador (N.I.F. / NIF-IVA operador intracomunitario / Pasaporte /
Documento oficial / Certificado de residencia fiscal / Otro) no queda bien, y
el régimen de IVA (Sí / No / Intracomunitario / Exportación, y RE) hay que
revisarlo: los intracomunitarios / exportación deben quedar como tal.

## 1. Mapeo de `F_CLI` — CONFIRMADO con volcado real

Volcados: `--cli-row 3392 3011 525` y `--cli-row 4279 3392` (2026-09-12).
Cuatro clientes: nacional 3011 (ES, `48288265H`), intracomunitarios 3392 (BE,
`BE0812240188`) y 4279 (DE, `DE455128445`, **configurado a mano en el
escritorio** = referencia), exportación 525 (NO, `NO 976 029 100`).

| Régimen | `IFICLI` (tipo de documento) | `IVACLI` (aplicar IVA) | `TIVCLI` (tipo impositivo) |
|---|---|---|---|
| Nacional (España) | `0` = N.I.F. | `0` | `1` = 21 % |
| Intracomunitario (UE con NIF-IVA) | `2` = NIF/IVA operador intracomunitario | `2` | `4` = Exento |
| Exportación (fuera UE) | *sin forzar* (ver nota) | `3` | `3` = 0 % |

Evidencia y notas:

- **`IFICLI` es el tipo de documento**, no `DOCCLI` (que vale `0` hasta en el
  4279 bien configurado). 4279 y 3392 → `IFICLI=2`; los nacionales / no
  configurados → `0`.
- **`IVACLI`** es el «Aplicarlo» del escritorio: 0 nacional / 2
  intracomunitario / 3 exportación. **`TIVCLI`** es el tipo impositivo del
  cliente: 1 = 21 %, 3 = 0 %, 4 = Exento.
- **Exportación · `IFICLI`**: el único cliente de exportación volcado (525,
  Noruega) estaba a `0`, sin referencia de cómo lo deja el escritorio bien
  configurado. Decisión de Bart: se fija el IVA (`IVACLI=3`, `TIVCLI=3`) y
  **`IFICLI` no se toca** hasta tener otro volcado. Cuando haya un cliente de
  exportación bien configurado a mano: `--cli-row <CODCLI>` y se añade el
  valor a `vat_regime.FCLI_REGIME_COLUMNS`.
- **`PAICLI`** estaba inconsistente: 724 / 056 / 276 (ISO numérico, bien) pero
  `'Norway'` literal en el 525. Causa: `_country_code()` mapeaba 10 países y
  caía a 724 o dejaba pasar el nombre.
- El recargo de equivalencia (`REQCLI`, `PRECLI`) vale 0 en los cuatro: no se
  toca.

## 2. Qué hace BoHub ahora

Todo en `app/integrations/factusol/vat_regime.py` (lógica pura) y
`customers.py` (escritura), con el patrón de siempre: fila real + sobrescribir
lo mínimo + guard + registro exacto en el log.

### Cómo se decide el régimen (país del CRM + NIF-IVA)

- España → **nacional**.
- País de la **UE** (lista de 27 en `vat_regime.EU_ISO2`; Grecia con prefijo
  `EL`) con **NIF-IVA válido** — `Company.vat` (que hasta ahora nadie leía) o
  el NIF con el prefijo del país (`BE0812240188`, `DE455128445`) →
  **intracomunitario**. UE sin NIF-IVA → nacional (consumidor final, IVA
  español). Un NIF-IVA con prefijo de OTRO país no cuenta.
- Fuera de la UE → **exportación**.
- Sin país en el CRM → nacional, sin tocar `PAICLI`.
- Fuera de alcance: Canarias / Ceuta / Melilla (IGIC / IPSI), que el CRM no
  distingue de la Península.

### Alta (`POST /customers/create` → `create_customer`)

- El país y el NIF-IVA salen de la empresa CRM (`country`, `vat`) si el
  payload no los trae («Crear en FACTUSOL» de la ficha ya los manda).
- Se escriben las 11 columnas de siempre **más** `IFICLI` / `IVACLI` /
  `TIVCLI` según el régimen y `PAICLI` con el ISO numérico REAL (tabla ISO
  completa vía `language.country_numeric`: Noruega → 578, Austria → 040…;
  solo lo vacío / no reconocido cae a 724 con aviso en el log).
- **Guard**: la fila real más reciente de `F_CLI` (la misma lectura que el
  contador `MAX+1`) tiene que tener esas columnas con tipo entero. Si no
  cuadra → `FactusolError` («No se ha escrito nada»), 502 en la API, y la
  empresa no queda vinculada.
- Respuesta: `regime` / `regime_label` de la ficha creada.

### Corrección de un cliente existente (ficha de empresa → «Régimen de IVA en FACTUSOL»)

- `GET /customers/regime-preview?company_id=`: régimen por país + NIF-IVA, lo
  que codifica hoy la ficha (`IFICLI`/`IVACLI`/`TIVCLI`/`PAICLI` reales) y qué
  columnas cambiarían, con etiquetas legibles. No escribe.
- `POST /customers/fix-regime {company_id}` → `update_customer_regime`: lee
  la fila REAL por `CODCLI`, y manda a **`ActualizarRegistro` SOLO la clave +
  las columnas que cambian**, cada una con el tipo de la fila real (guard: si
  una columna no existe o el tipo no cuadra, no se escribe nada). Sin cambios
  → `changed=false` y nada escrito. Auditoría
  `erp.factusol_customer_regime` con lo escrito.
- Frontend: `CompanyFactusolPanel` — «Comprobar régimen de IVA» abre el modal
  con «por la empresa: X (motivo)», el estado de la ficha y la tabla
  FACTUSOL (ahora) → quedará; «Corregir en FACTUSOL (n)» solo tras confirmar;
  una ficha coherente no ofrece corregir.

### Documentos que CALCULA BoHub: sin IVA para intracomunitario / exportación

- **Proformas** (`quotes._totals` + `build_quote_payload`): el cliente que
  llega de la empresa CRM (`_customer_from_company`) trae `pais` (ISO2),
  `vat` y `regime`; con intracomunitario / exportación la banda 1 va a
  `PIVA1PRE=0`, `IIVA1PRE=0`, `TOTPRE=NET1PRE`. `CPAPRE` es el ISO numérico
  real del país (empresa o dirección alternativa), no 724 fijo.
- **Albarán manual desde las líneas** (Tarea A, `albaran_manual.apply_regime`):
  manda el régimen de la empresa CRM (país + NIF-IVA); sin país en el CRM, el
  de la ficha `F_CLI` (`IVACLI`); si tampoco, nacional. Intracomunitario /
  exportación → todas las líneas al 0 % y cabecera coherente. Si la ficha
  `F_CLI` dice otra cosa que el CRM se guarda un aviso
  (`packing_json.factusol_albaran.regime_warning`) — la ficha se corrige desde
  la empresa, nunca desde el albarán.
- **Cadena** albarán → factura, presupuesto → albarán: copia por sufijo, así
  que hereda el 0 %.
- **Proforma → pedido** (Fase 1): si la cabecera lleva `PIVA1PRE=0` (proforma
  sin IVA, del escritorio o de BoHub) las líneas del pedido salen al 0 % (las
  líneas `F_LPS` no son fiables: `IVALPS` es un código) y `build_order`
  respeta un 0 % explícito (solo la línea sin dato cae al 21 %).
- **Factura de pedido web** (`emit_invoice`, copia del `F_PCL`): **no se
  recalcula** — son los importes que el cliente pagó en la tienda. Si la
  empresa es intracomunitaria / exportación y el pedido lleva IVA se deja
  `regime_warning` en el resultado, el historial del pedido y el SyncLog, para
  revisarlo a mano en FACTUSOL.
- No se escribe `TIV*` en las cabeceras de documento (no hay volcado de una
  proforma / albarán intracomunitario hecho en el escritorio que confirme el
  código); los importes a 0 son columnas ya verificadas.

## 3. Informe de clientes / facturas con el dato mal (solo lectura)

    docker exec crmbo-api-1 python -m scripts.factusol_discover_albaranes --regimen-iva

Lista (1) los clientes `F_CLI` con `PAICLI` no numérico / no reconocido / vacío,
régimen (`IVACLI`/`TIVCLI`) incoherente con el país + NIF-IVA o `IFICLI`
incoherente, con el motivo; y (2) las facturas `F_FAC` del ejercicio con IVA > 0
a clientes que por país / ficha son intracomunitarios o de exportación. Las
fichas se corrigen desde la empresa en BoHub («Régimen de IVA en FACTUSOL») o
en el escritorio; **las facturas solo a mano en FACTUSOL** (BoHub no reescribe
ninguna).

## 4. Volcados de referencia (resumen)

| CODCLI | País | NIF | `IFICLI` | `IVACLI` | `TIVCLI` | `PAICLI` | Estado |
|---|---|---|---|---|---|---|---|
| 3011 | ES | 48288265H | 0 | 0 | 1 | 724 | nacional, bien |
| 3392 | BE | BE0812240188 | 2 | 2 | 4 | 056 | intracomunitario, bien |
| 4279 | DE | DE455128445 | 2 | 2 | 4 | 276 | intracomunitario, bien (a mano) |
| 525 | NO | NO 976 029 100 | 0 | 3 | 3 | `'Norway'` | exportación con IVA bien; `PAICLI` mal; `IFICLI` sin referencia |

Tests: `test_factusol_cliente_regimen.py` (régimen por país + NIF-IVA,
`test_cliente_factusol_ifi_nacional` / `_intracomunitario` / `_exportacion`,
`test_pais_paicli_iso_numerico`, `test_escritura_fcli_guard_minimo`, preview /
fix con auditoría y guards), `test_factusol_factura_regimen.py`
(`test_emision_intracomunitario_sin_iva` / `_exportacion_sin_iva` /
`_nacional_con_iva`, conversión con 0 % explícito, aviso en la factura web),
`test_factusol_discover_albaranes.py::test_regimen_iva_report_flags_customers_and_invoices`,
`CompanyFactusolPanel.test.tsx` (modal, confirmación, ficha coherente, alta con
país + NIF-IVA).
