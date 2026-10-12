# Ofertas: presupuestar a un lead sin tocar FACTUSOL (12/10/2026)

Un presupuesto de FACTUSOL (`F_PRE`) necesita un cliente con datos fiscales;
un lead no los tiene. La **oferta** es un presupuesto que vive en BoHub: se
manda a cualquiera y solo baja a FACTUSOL si el cliente dice que sí.

**Regla de la casa:** nada de las ofertas escribe en FACTUSOL salvo el botón
«Convertir en presupuesto», que pulsa una persona. Lo vigila un test que
escanea el paquete (`tests/test_ofertas.py::test_ningun_camino_de_ofertas_
escribe_en_factusol_salvo_convertir`), igual que el de «ningún camino envía un
borrador solo» del asistente.

---

## El flujo

1. **Plantilla.** En ERP · Proformas, sobre un presupuesto existente, «⋯ →
   Guardar como plantilla de oferta». Copia sus líneas —con sus artículos de
   FACTUSOL (`codart` interno + `sku` comercial)—, descripción, cantidades y
   precios. Solo lee `F_PRE`/`F_LPS`. La plantilla lleva nombre, descripción y
   si está activa; también se crean a mano, con líneas sueltas. Gestión en
   ERP · Ofertas → pestaña «Plantillas» (crear, editar, desactivar).
2. **Oferta.** Desde la ficha del contacto («Nueva oferta» en la tarjeta
   Ofertas del Resumen) o desde ERP · Ofertas, a partir de una plantilla. Sin
   cliente de FACTUSOL, sin datos fiscales, sin escribir nada en FACTUSOL.
   Lleva contacto, empresa de BoHub si la tiene, empresa emisora (serie: 1
   Bomedia · 2 MQ Europe · 4 Lambert · 5 Streamtec), idioma, líneas, validez y
   estado. Las líneas conservan el artículo de la plantilla; cantidades,
   precios, descuentos y textos se cambian, se añaden y se quitan líneas; una
   línea sin artículo es válida (se resuelve al convertir). Numeración propia
   `OF-2026-0001` con contador por año (`ofertas_contador`); prefijo y validez
   por defecto (30 días) en Configuración ERP → «Ofertas». La crea y envía
   cualquier comercial: `erp.proformas` y `erp.email.client`, que ya tiene.
3. **Editar.** También enviada. Al editar la fecha pasa a hoy y la validez se
   recalcula desde ahí, salvo que se cambie la fecha a mano (entonces manda).
   Cada edición sube la `version`; el historial de envíos (`ofertas_envios`)
   apunta qué versión se mandó, cuándo y a quién, y la ficha avisa «esta
   versión aún no se ha enviado». Una caducada que se edita vuelve a borrador
   (hay que reenviarla); una convertida no se edita.
4. **PDF.** Variante `oferta` del maquetador de presupuestos
   (`factusol_pdf.generate_document_pdf`): título OFERTA / OFFER / ANGEBOT /
   OFFRE / OFFERTE, casilla DOCUMENTO «Oferta», idioma del contacto (contacto
   → empresa → país → emisora → es), nombre de fichero de #531-#533
   (`Angebot MANUFAKTUR FUR GESTALTUNG OF-2026-0001.pdf`). **IVA** (decidido el
   11/10): con IVA o sin IVA a elección; el pie lleva un texto fijo en los
   cinco idiomas: «Importes con IVA (21 %).» / «Importes sin IVA.» + «Este
   documento es una oferta comercial, no una factura. El régimen de IVA
   definitivo se fijará al emitir el presupuesto o la factura, según el país
   del cliente y la validación de su NIF-IVA en VIES…». No imprime el texto
   intracomunitario de la emisora ni la validez genérica de 30 días (lleva
   «Oferta válida hasta el dd-mm-aaaa.»). Régimen definitivo al convertir.
5. **Enviar.** Por el camino de siempre (`gmail.service.send_email`,
   remitente verificado por serie o del usuario, PDF adjunto). El cuerpo lo
   propone el asistente «Ayúdame a contestar» (botón «Redactar con la IA» del
   modal): la oferta entra en su contexto como entra el catálogo (`oferta_id`
   en `POST /api/emails/asistente`; bloque «OFERTA ADJUNTA» con número, fechas,
   líneas e importes, los únicos precios que puede citar para esos productos).
   Nada se envía solo: `confirm: true`. Al enviar → estado `enviada` + fecha +
   fila en el historial + evento `erp.oferta_emailed` (sobre la oferta y en
   el timeline del contacto).
6. **Convertir en presupuesto.** Solo cuando el cliente acepta, a mano.
   Hace falta empresa en FACTUSOL: si el contacto no tiene empresa o su
   empresa no está vinculada, el endpoint responde 409 `company_not_linked`
   (con `company_id` / `contact_id`) y la pantalla lleva al **mismo flujo de
   #502** (ficha de empresa → panel FACTUSOL: vincular o crear cliente; sin
   empresa, la ficha del contacto). No hay otro camino. Línea con importe y
   sin artículo → 422 `lineas_sin_articulo` con las posiciones: se para y se
   pide resolver (una nota sin cantidad ni precio sí vale: va como línea de
   texto a `F_LPS`). Pasados los prechequeos se encola
   `convert_oferta_job` en `factusol:writes` (worker serializado) y
   `ofertas.convertir` llama a `create_quote` con la serie de la emisora, el
   cliente de la empresa (`quote_customer.customer_from_company`, el mismo
   cálculo de país y régimen por pareja emisor → cliente de #528) y las
   líneas con sus artículos: el `F_PRE` resultante es igual que uno a mano.
   La oferta queda `convertida` y enlazada (`factusol_serie` + `codpre` +
   ejercicio), consultable en su ficha. Lo de después (pedido, albarán,
   factura) es el flujo que ya existe. No se escribe `F_PCL`.
7. **Pipeline (hechos para la Fase 3).** Al enviar, el contacto pasa a
   «Propuesta enviada» en cada pipeline no archivado donde esté y que tenga
   esa etapa (o «Oferta enviada»); no se le añade a ningún pipeline. Además se
   emite `oferta.enviada` (`dispatch_event`). Una enviada con la validez
   pasada es `caducada`: el job diario (`app/erp/ofertas_job.py`, cola
   `ofertas:caducar` en `worker-sync`, 06:20 de Madrid) lo deja escrito y
   emite `oferta.caducada`. Mover a «Cerrado perdido» es cosa del motor que
   consuma ese hecho (Fase 3 de la respuesta a leads, aún no construida): la
   oferta solo emite. La venta sigue atada al pedido. Los dos hechos están en
   el catálogo de disparadores de los workflows (filtro opcional por serie).

## Tablas (migración 0146)

| Tabla | Qué es |
|---|---|
| `ofertas_plantillas` + `ofertas_plantillas_lineas` | La plantilla y sus líneas (`codart`, `sku`, descripción, cantidad, precio, dto, IVA). |
| `ofertas` + `ofertas_lineas` | La oferta (contacto, empresa, plantilla, serie, idioma, con_iva, fecha, validez, `valida_hasta`, estado, referencia, notas, totales, versión, enlace al `F_PRE`) y sus líneas. |
| `ofertas_envios` | Historial de envíos: cuándo, quién, versión, destinatarios, asunto, remitente, adjunto, ids de Gmail. |
| `ofertas_contador` | Un contador por año para `OF-AAAA-NNNN`. |

Estados: `borrador`, `enviada`, `caducada`, `convertida`. `caducada` también se
deriva al leer (`estado_efectivo`): la API devuelve `estado` (efectivo) y
`estado_guardado`.

## API (`/api/erp/ofertas`)

| Método | Ruta | Permiso | Qué hace |
|---|---|---|---|
| GET | `/meta` | ver ERP | prefijo, validez, series con nombre, idiomas, estados |
| GET/POST | `/plantillas` | ver / `erp.proformas` | listar · crear (desde `{serie, codpre}` o con `lineas`) |
| GET/PATCH | `/plantillas/{id}` | ver / `erp.proformas` | detalle · nombre, descripción, activa, líneas |
| GET | `` `?contact_id=&company_id=&estado=&q=` | ver ERP | listado |
| POST | `` | `erp.proformas` | crear |
| GET/PATCH | `/{id}` | ver / `erp.proformas` | detalle con líneas y envíos · editar |
| GET | `/{id}/pdf?lang=&bank=` | ver ERP | el PDF |
| GET | `/{id}/email-preview?lang=` | `erp.email.client` | destinatarios, idioma, asunto/cuerpo, remitente, adjunto |
| POST | `/{id}/email` | `erp.email.client` | enviar (`confirm` obligatorio) |
| POST | `/{id}/convertir` | `erp.proformas` | prechequeos + encolar; 202 `{job_id}` |
| GET | `/convertir/status/{job_id}` | ver ERP | estado del job (como las proformas) |

## Comprobación a mano (tras desplegar)

Presupuesto real → «Guardar como plantilla de oferta» → oferta para un lead
que no está en FACTUSOL → PDF → enviar (el asistente propone el cuerpo) →
convertir: el 409 lleva a crear la empresa por el camino de siempre → volver a
convertir → el presupuesto resultante en FACTUSOL, igual que uno a mano.

## Pendientes conocidos

- El contador por año se bloquea con `SELECT … FOR UPDATE` en MySQL (SQLite lo
  ignora); la primera oferta del año en dos altas simultáneas podría chocar en
  el `INSERT` del contador: se reintenta y listo. Lo cubre además el `UNIQUE`
  del número.
- Un solo tipo de IVA por oferta (como `F_PRE`, banda 1): con varios, se aplica
  el de la primera línea con importe y queda en el log.
