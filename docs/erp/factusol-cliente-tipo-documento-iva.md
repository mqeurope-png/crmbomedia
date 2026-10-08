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

### Cómo se decide el régimen (la PAREJA emisor → cliente)

⚠️ Esta regla se corrigió en la fase «pareja emisor → cliente» (ver la sección
final): el régimen **no es del cliente**, es de la pareja país de la empresa
que emite → país del cliente. Lo que sigue es la regla vigente.

- Mismo país el emisor y el cliente → **nacional**, con el IVA de ese país.
- Los dos en la **UE** (lista de 27 en `vat_regime.EU_ISO2`; Grecia con
  prefijo `EL`) y distintos, con **NIF-IVA válido** del cliente —
  `Company.vat` o el NIF con el prefijo de su país (`BE0812240188`,
  `DE455128445`) → **intracomunitario**. Un NIF-IVA con prefijo de OTRO país
  no cuenta.
- Los dos en la UE y distintos, sin NIF-IVA → **nacional** con el IVA del país
  del **emisor** (consumidor final).
- Cliente fuera de la UE → **exportación**.
- Sin país del cliente en el CRM → nacional, sin tocar `PAICLI`.
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

## Fase VIES (validación del NIF-IVA)

Desde la Fase VIES el veredicto del servicio oficial de la UE entra en la
misma regla: `regime_for(..., vies_valid=)`. Con `vies_valid=False` (VIES dice
que el NIF-IVA NO es válido) **no se puede eximir**: la ficha F_CLI propuesta
y los documentos salen como nacional con IVA, y el pedido entra en
«Incidencias». `True` confirma el intracomunitario; `None` (pendiente / VIES
caído) no cambia la regla. Detalle en `docs/erp/vies.md`.

## Fase «pareja emisor → cliente» (IVA de MQ Europe)

**El fallo.** `regime_for()` tenía `"ES"` escrito a fuego como país de quien
vende, pero BoHub emite desde dos empresas en dos países: Streamtec, S.L. y
Bomedia (España, series 5 y 1) y **MQ EUROPE BV (Bélgica, serie 2)**. Visto en
producción el 08/10: CDCOPIADVD S.L.U (ESB65623175, Barcelona), NIF-IVA válido
en VIES, documento de la **serie 2**. El modal decía «(España → nacional)» y la
proforma salió con **21 %**, cuando debía salir **exenta**. Al revés es peor:
facturar SIN IVA a un cliente belga de MQ Europe deja a la empresa debiendo ese
IVA.

**El país de cada empresa** está explícito en `COMPANY_DEFAULTS[serie]
["pais_iso2"]` (`factusol_pdf.py`), editable en `/erp/settings`
(`factusol_series_json.companies`): 1 Bomedia `ES`, 5 Streamtec `ES`, 2 MQ
Europe `BE`. Deducirlo del literal `pais` sería adivinar, y aquí adivinar es
facturar mal. `issuer_iso2_for_serie(session, serie)` lo resuelve;
`issuer_companies(session)` da la lista de emisores (todas las series con
identidad, también las que no tienen nombre: descartarlas hacía desaparecer a
MQ Europe del modal y que `fix-regime` volviera a escribir el régimen español).
El campo se edita en `/erp/settings` → «País en ISO2 (ES, BE…) — decide el
IVA».

### Quién decide: manda la ficha de FACTUSOL

Donde la pareja permite **elegir** —los dos países en la UE y distintos, o sea
donde la respuesta es «con IVA o exento» (`vat_regime.pareja_elegible`)— manda
el régimen que ya tiene la ficha `F_CLI` del cliente (`ficha_manda`): es la
decisión del operador y lo que FACTUSOL va a aplicar al facturar. La excepción
es el **cliente nuevo creado desde el CRM de BoHub** (`cliente_nuevo_bohub`):
esa ficha la acaba de escribir BoHub con los datos del CRM, así que no hay
decisión que respetar. Los pedidos web no son esa excepción (ahí el NIF-IVA ya
pasa por VIES al entrar el pedido).

Fuera de esa zona el régimen es **aritmética** (mismo país → nacional, cliente
de fuera de la UE → exportación) y manda el cálculo: una ficha mal configurada
—el 525 de Noruega venía como nacional— no puede hacer que se facture con IVA.

### VIES solo donde cambia la factura

`vies_hace_falta(cliente, issuer_iso2=…, vat=…)` es True solo cuando la pareja
puede dar intracomunitario. Un cliente español facturado por Streamtec es
nacional pase lo que pase: ahí VIES no aporta nada y no se consulta. Para MQ
Europe ese mismo cliente **sí** lo necesita, y por eso el modal de
«Crear empresa» ya abre VIES para NIF-IVA españoles (antes la puerta era «UE y
no España», que dejaba fuera justo el caso del fallo). Un `False` explícito
sigue siendo lo único que impide eximir cuando decide el cálculo.

Y el mismo `"ES"` a fuego estaba en `services/vies.company_eu_vat`, que dejaba
a los clientes españoles **sin veredicto posible**: `company_vies_valid` era
siempre `None`, el barrido los saltaba y un `ESB…` dado de baja se eximía
igual. Ya no se excluye España, ni en la ficha ni en el barrido. Solo entran
los que tienen NIF-IVA **con prefijo** (`ESB65623175`): un NIF español a secas
no es un NIF-IVA y sigue sin consultarse, así que esto no mete al CRM español
entero en el barrido. El aviso «NIF-IVA no válido» del pedido
(`workflow._vies_importa`) sale solo donde VIES cambia la factura: que el CIF
de un cliente español no esté en el ROI no es una incidencia de un pedido que
factura Streamtec.

### La ficha F_CLI es UNA sola

Hay **una** base de datos de FACTUSOL y una ficha por cliente, así que no se
puede guardar «exento para MQ Europe y con IVA para Streamtec». Cuando las
empresas emisoras discrepan, `regime_preview` lo dice (`conflicto`) y BoHub
**no toca las columnas de régimen** — el país sí, que no está en disputa—;
`fix-regime` tampoco escribe. El modal enseña el régimen de cada empresa para
que nadie dé España por supuesta. Donde todas coinciden (un cliente alemán, uno
de fuera de la UE) la corrección funciona como siempre.

### Los documentos llevan el IVA de SU serie

`_customer_from_company(session, company_id, serie=…)` deja en el cliente
`regime` (el de la serie pedida), `regime_por_serie` (`{serie: régimen}`) y
`ficha_decide_por_serie`. La serie definitiva no se sabe en el endpoint —al
editar una proforma es la de la fila que ya existe en FACTUSOL—, así que la
resuelve el worker: `quotes.regime_de_serie()` elige del mapa y
`quotes.customer_con_regimen()` deja mandar a la ficha donde toca (una lectura
de `F_CLI`; si FACTUSOL no responde se sigue con el calculado). El albarán
manual hace lo mismo con `company_regime(…, serie=)` +
`company_ficha_decide(…)` → `apply_regime(…, ficha_decide=)`. El aviso de la
factura web (`regime_warning_for_pcl`) se calcula después de resolver la serie.

### Informe de auditoría (SOLO LECTURA)

    docker compose -f /opt/crmbo/docker-compose.prod.yml exec api \
        python -m scripts.auditoria_iva_emisor

Revisa las series cuya empresa NO es española (hoy la 2; las series sin
`pais_iso2` configurado se dicen y **no** se auditan, porque juzgarlas sería
suponer España otra vez) y lista: (1) las fichas `F_CLI` de sus clientes cuyo
régimen no cuadra con esa pareja, (2) las **proformas** con el IVA descuadrado
(revisables: se vuelven a emitir) y (3) los **albaranes y facturas ya
emitidos** con el IVA descuadrado. Opciones `--serie N`, `--ejercicio AAAA`,
`--csv RUTA`, `--detalle N`. No escribe nada: las fichas se corrigen en
FACTUSOL y lo ya emitido (rectificativa o abono) lo decide administración.

Cómo leerlo: como en la zona elegible **manda la ficha**, un documento que
coincide con la ficha de su cliente es el caso normal y lo que hay que repasar
es la ficha (bloque 1). Cada documento dice si coincide con ella
(`coincide_con_ficha` en el CSV) y el bloque 3 cuenta aparte los que **no**
coinciden ni con la ficha, que son los que piden explicación. El informe
también distingue, de los documentos de la serie, cuántos se han podido
juzgar: un cliente sin ficha legible o sin país no se evalúa y se dice.

Tests: `test_iva_pareja_emisor.py` (la pareja, el motivo de las dos puntas, la
ficha que manda, VIES por pareja, el país de cada serie, la proforma de la
serie 2 exenta, el albarán manual y el informe) y los casos actualizados de
`test_factusol_cliente_regimen.py` / `test_factusol_factura_regimen.py`.
