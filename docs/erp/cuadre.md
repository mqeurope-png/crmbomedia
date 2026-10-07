# ERP · Cuadre — descuadres entre BoHub, FACTUSOL, Genei, WooCommerce y la hoja de Drive

Panel que junta en un sitio lo que no cuadra entre BoHub, FACTUSOL, los envíos
(Genei u otro courier), las tiendas WooCommerce y la hoja «Seguimiento (app)»
de Drive.

> **Solo lectura.** El Cuadre no escribe en FACTUSOL, no corrige datos y no
> mueve estados. Cada descuadre enlaza a la pantalla donde ya existe la acción
> para arreglarlo. Lo único que guarda es su propio estado: abierto, revisado
> (con motivo) o resuelto.

Código: `backend/app/erp/cuadre/`. API: `backend/app/erp/api/cuadre.py`
(`/api/erp/cuadre`, capacidad `erp.cuadre`, que tienen admin y ERP Pedidos).

---

## Piezas

| Pieza | Fichero | Qué hace |
|---|---|---|
| Registro | `cuadre/registry.py` | Decorador `@comprobacion(...)`, `Hallazgo`, catálogo. |
| Comprobaciones de BoHub | `cuadre/checks_mysql.py` | Leen solo la BD de BoHub (fuente `mysql`). |
| Comprobaciones de FACTUSOL | `cuadre/checks_factusol.py` | Leen FACTUSOL (fuente `factusol`). |
| Comprobaciones de WooCommerce | `cuadre/checks_woocommerce.py` | Preguntan a las tiendas (fuente `woocommerce`). |
| Contexto | `cuadre/contexto.py` | Sesión, «ahora», umbrales y lecturas compartidas. Cada tabla de FACTUSOL se lee una vez por pasada, del ejercicio en curso. |
| Motor | `cuadre/engine.py` | Corre las comprobaciones y guarda los descuadres sin duplicar. |
| Configuración | `cuadre/config.py` | Configuración ERP → «Cuadre». Se guarda en el blob `factusol_series_json`, clave `cuadre`. |
| Job | `cuadre/job.py` | Job nocturno en `worker-sync` (cola `cuadre:run`) y «Comprobar ahora». |
| Tablas | `models/cuadre.py`, migración `20261003_0124` | `cuadre_findings` y `cuadre_runs`. |

---

## Idempotencia

- **Identidad.** Un descuadre se identifica por `check_id + entidad_id` (clave
  única en `cuadre_findings`). Dos pasadas no duplican nada.
- **Desaparece.** Si una pasada ya no ve un descuadre, este pasa a `resuelto`
  con `resuelto_at`. Nunca se borra. Si vuelve a aparecer, se reabre como
  nuevo.
- **Revisado.** «Revisado / no es un descuadre» exige un motivo corto. Un
  revisado no reaparece mientras su `huella` no cambie. La huella es el
  sha256 de `huella_datos`, los valores que motivan el descuadre. Si esos
  valores cambian, el descuadre vuelve a `abierto` como nuevo. «Volver a
  incluir» lo devuelve a abierto a mano.
- **Fallos.** Solo se tocan los descuadres de las comprobaciones que han
  corrido bien. Si una comprobación falla, sus descuadres se quedan como
  estaban, revisados incluidos; el error queda en el `resumen_json` de la
  pasada.
- **Tabla de FACTUSOL vacía o que no se puede leer.** Se trata como lectura
  rota: las comprobaciones que la usan fallan en esa pasada, en vez de dar
  todo por resuelto. El fallo se recuerda durante la pasada, así que no se
  vuelve a pedir la misma tabla desde cada comprobación.
- **Ejercicio.** Los descuadres de FACTUSOL guardan el ejercicio en el que se
  vieron. Al cambiar de ejercicio, los del anterior no se dan por resueltos,
  porque ya no se leen; se cierran con «Revisado» cuando toque.
- **Tienda que no responde.** Los descuadres de WooCommerce guardan su tienda
  (`Hallazgo.ambito`). Si una tienda no responde (o su listado llega con
  tope), los suyos no se dan por resueltos: la comprobación lo apunta en
  `ctx.no_mirados`. Si no responde ninguna, la comprobación falla.
- **Desactivadas.** Una comprobación desactivada no corre. Sus descuadres no
  salen en el panel, pero se conservan.
- **«Nuevo».** Un descuadre es nuevo si su `abierto_at` es igual o posterior
  al inicio de la última pasada terminada de su fuente.

---

## Cuándo corre

### Job nocturno

- Corre en `worker-sync` (`--with-scheduler`), cola **`cuadre:run`**, a la
  hora de Configuración ERP. Por defecto son las **03:00 de Madrid**; si la
  imagen no trae tzdata, se aplica a mano la regla CET/CEST de la UE.
- Corre todas las comprobaciones activas: primero las de BoHub, luego las de
  FACTUSOL y luego las de WooCommerce.
- Tiene interruptor (`cuadre.nocturno_activo`), **apagado por defecto**.
  Apagado, el tic sigue armado y no hace nada.
- Si cambia la hora, el tic ya armado corre a la hora vieja y se re-arma para
  la nueva. Si ya hubo una pasada nocturna en las últimas 12 h, no repite.
- Para correrlo a mano, ignorando el interruptor y la hora:
  `python -m app.erp.cuadre.job`.

### «Comprobar ahora»

- `POST /api/erp/cuadre/comprobar` corre en la propia petición las
  comprobaciones de BoHub.
- Las de FACTUSOL y las de WooCommerce (hacen llamadas a las tiendas) las
  **encola** en `cuadre:run`, una pasada `en_cola` por fuente, y la pantalla
  enseña «comprobando…». Al terminar dice cuál fue bien y cuál no.
- Si ya hay una pasada de esa fuente en cola o corriendo, no encola otra.
- Sin Redis, la pasada queda en `error` con el motivo.

### Una pasada a la vez

- Hay un cerrojo en Redis por fuente (`cuadre:lock:mysql`,
  `cuadre:lock:factusol` y `cuadre:lock:woocommerce`).
- Una pasada en cola o corriendo durante más de 2 h se da por perdida y deja
  de enseñarse como «comprobando…».

### Cuidado con DELSOL

- Las comprobaciones de FACTUSOL nunca corren en la petición web.
- Cada tabla (`F_FAC`, `F_LFA`, `F_LCO`, `F_PRE`) se lee **una vez por
  pasada** con `1=1` y solo del ejercicio en curso.
- El worker de escritura `factusol:writes` no se toca.

---

## Las comprobaciones

**Alcance.** El panel vigila lo que BoHub gestiona. Las comprobaciones de
facturas (1, 2 y 3) solo miran facturas **vinculadas a un pedido de BoHub**.
No miran las facturas que son solo de FACTUSOL: series que BoHub no usa,
facturas anteriores al ERP o hechas a mano sin pedido. Eso no es un descuadre
(`facturas_de_bohub` en `checks_factusol.py`). Las demás comprobaciones parten
del pedido de BoHub.

### Dinero (severidad alta, fuente FACTUSOL)

1. `factura_lineas_ajenas` — **Factura con líneas que no son suyas** (secuela
   del bug #382).
   - Compara la suma de las líneas de `F_LFA`, por clave compuesta, con la
     base de la cabecera.
   - Admite como buenas las formas legítimas en que no coinciden a pelo:
     - el DESCUENTO global, en la banda `IDTO`/`IPPA` de la cabecera o como
       línea «DESCUENTO» del detalle;
     - el recargo PayPal del 4 % (base = Σ líneas × 1,04);
     - los portes y la financiación, que van en su banda;
     - una banda de portes (`IPOR > 0`) con neto propio sin línea, como el
       4 % de PayPal sobre líneas + portes que FACTUSOL pone en la banda de los
       portes (2-526098, 2-526103). Su neto se deja fuera de la base. La
       contaminación de #382 sigue saltando, porque allí las líneas suman de
       más y quitar una banda solo baja la base.
   - Implementado en `lineas_cuadran`.
2. `cobro_descuadrado` — **Cobro descuadrado BoHub ↔ FACTUSOL**.
   - Compara `F_FAC.ESTFAC` con `orders.factusol_cobro_status` en los dos
     sentidos: FACTUSOL cobrada y BoHub pendiente, o BoHub cobrada y FACTUSOL
     pendiente o parcial. Los valores de ESTFAC son los de Configuración
     ERP: 2 / 1 / 0.
   - También avisa de una factura **cobrada en FACTUSOL sin ninguna línea en
     `F_LCO`**.
3. `factura_sin_cobro` — **Factura emitida sin cobro** de un pedido de BoHub,
   pasados N días (30 por defecto), con el importe pendiente: total − Σ `F_LCO`.
   No es el listado de cuentas por cobrar de FACTUSOL.
   - La factura cobrada en ESTFAC sin ninguna línea no sale aquí, porque ya
     sale en la 2.

### Envíos (severidad media, fuente BoHub)

4. `sin_envio_con_datos` — Pedido con «No requiere envío» que tiene tracking,
   envío de Genei (`packing_json.genei.shipment_code`) o courier
   (`packing_json.envio.courier`).
5. `enviado_sin_aviso` — Pedido en tránsito o entregado, con tracking y sin
   aviso de envío al cliente.
   - Cuenta como aviso el evento `erp.shipment_emailed` o el `customer_email`
     enviado en el pedido.
   - No avisa de los envíos creados con el aviso automático **apagado**
     (`disabled`) ni de los envíos de Genei anteriores al aviso de BoHub (sin
     bloque `customer_email`, porque ya los avisó Genei).
   - Solo mira los envíos de los últimos N días (30 por defecto).
   - Enlaza a la Cola SAT → «Enviados», donde está «Enviar aviso».
6. `envio_sin_entregar` — En tránsito desde hace más de N días (10 por
   defecto) sin entrega ni incidencia.
7. `entregado_sin_completar` — Entregado hace más de N días (14 por defecto),
   facturado y cobrado, sin «Marcar completado».

### Documentos y hoja (severidad media o baja)

8. `hoja_drive` (media) — Hoja de Drive descuadrada.
   - Avisa de un pedido vivo de BoHub sin fila en «Seguimiento (app)», y de
     una fila de BoHub cuyo pedido ya no existe.
   - Compara las filas esperadas (`drive_live_rows` + `drive_completados_rows`)
     con la foto del espejo (`seguimiento_sync_snapshot`).
   - No dice nada si el espejo aún no ha escrito la hoja ni en modo antiguo.
   - Deja 60 min de margen a los pedidos recién tocados.
9. `factura_sin_vincular` (media) — Factura de FACTUSOL sin vincular o con el
   vínculo roto.
   - Sin vincular: la factura existe en FACTUSOL con la referencia del pedido
     (`REFFAC`, lo mismo que hace «Vincular facturas de FACTUSOL…») y el
     pedido no la tiene.
   - Vínculo roto: el pedido tiene vinculada una factura que no está en
     FACTUSOL, por un hueco o porque se anuló.
   - El vínculo roto solo se juzga si el código cae **dentro del rango** de su
     serie en el ejercicio leído. Fuera del rango es de otro ejercicio y no se
     ha leído.
10. `factura_sin_enviar` (media) — Factura de hace más de N días (7 por
    defecto) sin enviar al cliente.
    - Cuenta como enviada el evento `erp.invoice_emailed` o «Factura enviada»
      escrita a mano en la hoja.
11. `pedido_sin_aprobar` (baja) — Pedido esperando aprobación hace más de N
    días (7 por defecto).
    - No cuenta los carritos web sin pagar, que están ocultos por estado.
12. `proforma_sin_convertir` (baja, fuente FACTUSOL) — Proforma aceptada
    (`ESTPRE=1`) hace más de N días (90 por defecto) que aún no es un pedido
    de BoHub.
    - Es el embudo comercial, no un descuadre: viene **apagada por defecto**
      (`activa_defecto=False` en el registro).
    - Se enciende en Configuración ERP.

### Integraciones (severidad media, fuente BoHub)

13. `sincronizacion_colgada` — **Sincronización colgada.** Avisa en dos casos:
    - Una cuenta de integración habilitada tiene una ejecución (`sync_logs`)
      en `pending`/`running` desde hace más de N **horas** (3 por defecto; el
      umbral de esta comprobación va en horas).
    - Una cuenta de un sistema con sincronización periódica no ha tenido
      ninguna sincronización correcta (`success`/`partial_success`) en las
      últimas 24 h. Son AgileCRM (cada hora) y las cuentas live de Brevo
      (cada 12 h). Las cuentas dadas de alta hace menos de 24 h no cuentan.
    - Enlaza al historial de sincronización de la cuenta.
    - Nace de la incidencia del 05/10/2026: 8 de 9 cuentas de AgileCRM
      llevaban 18 días sin sincronizar, bloqueadas por filas «en curso» de
      agosto.

### Integraciones (severidad alta, fuente WooCommerce)

14. `pedido_woo_pagado_sin_bohub` — **Pedido pagado en WooCommerce que no está
    en BoHub.**
    - Pide a cada tienda, en una consulta, sus pedidos pagados (`processing`,
      `completed` y `refunded`: los estados con los que BoHub crea un pedido)
      creados en los últimos N días (90 por defecto). Avisa de los que BoHub
      no tiene: ni por id de la tienda, ni por número de pedido.
    - Cada aviso lleva número, cliente, importe, fecha de pago y enlace al
      pedido en el admin de la tienda (se abre en otra pestaña). El botón
      lleva a Seguimiento, a «Poner al día estados Woo…», que lo importa.
    - Los modificados hace menos de 5 minutos no se juzgan: su webhook puede
      estar en camino.
    - Es la red: el aviso sale aunque la importación falle. Solo lee; quien
      importa es el repaso periódico (`woocommerce/missing_job.py`) o «Poner
      al día estados Woo…». Ver `docs/erp/woo-pagados-que-faltan.md`.
    - Nace del 07/10/2026: el 99976 de boprint se marcó pagado sin que la
      tienda disparara el webhook.

### Qué pedidos se miran

- Las comprobaciones de trabajo pendiente (5, 6, 7, 10 y 11) solo miran
  pedidos en flujo: ni anulados, ni quitados de las listas a mano, ni
  gestionados fuera.
- Los umbrales en días los fija Configuración ERP. En la 5, el umbral es una
  **ventana**, no una antigüedad mínima.

---

## Cómo añadir una comprobación

1. **Escribe la función** en `checks_mysql.py` si solo lee la BD de BoHub,
   en `checks_factusol.py` si lee FACTUSOL o en `checks_woocommerce.py` si
   pregunta a las tiendas. Recibe el `Contexto` y devuelve
   (o hace `yield` de) un `Hallazgo` por descuadre:

   ```python
   @comprobacion(
       id="mi_comprobacion",           # estable, máx. 40 caracteres
       titulo="Título corto",
       descripcion="Una línea: qué no cuadra.",
       severidad="media",              # alta | media | baja
       fuente=FUENTE_MYSQL,            # o FUENTE_FACTUSOL / FUENTE_WOOCOMMERCE
       grupo="envios",                 # dinero | envios | documentos | integraciones
       dias_defecto=10,                # opcional: umbral configurable
       dias_texto="Avisar pasados N días",
       orden=13,                       # posición en la pantalla
   )
   def mi_comprobacion(ctx: Contexto) -> Iterator[Hallazgo]:
       umbral = ctx.dias("mi_comprobacion", 10)
       for o in ctx.pedidos():
           ...
           yield Hallazgo(
               entidad_tipo=ENTIDAD_PEDIDO, entidad_id=o.id,
               etiqueta=o.order_number, detalle="Qué no cuadra.",
               pista_de_arreglo="Dónde se arregla.",
               enlace=enlace_pedido(o.id),
               arreglo_enlace="/erp/...", arreglo_boton="Ir a …",
               huella_datos={"valor": ...},   # lo que lo motiva, sin días
           )
   ```

2. **Reglas.**
   - **Solo lectura.** No escribas en FACTUSOL ni en la BD, y no muevas
     estados.
   - **`huella_datos`** lleva los valores que motivan el descuadre (importes,
     estados, números), **nunca los días transcurridos**. Si no, un
     «revisado» reaparecería cada día.
   - **`entidad_id`** tiene que ser estable: el `Order.id`, el `serie-código`
     de la factura…
   - **FACTUSOL** se lee solo con `ctx.tabla("F_XXX")`: una lectura por tabla y
     pasada. Nada de consultas fila a fila. Para lo que compartan varias
     comprobaciones, usa `ctx.cached(...)`.
   - **El enlace de arreglo** lleva a una pantalla donde ya existe la acción.
3. **Tests** en `backend/tests/test_erp_cuadre_checks.py`: un caso que dispara
   y uno limpio. FACTUSOL va con el doble `FakeFactusol`, nunca con la API
   real.

Nada más. El motor, la API, el job nocturno, Configuración ERP (activar y
umbral) y la pantalla la recogen solas del registro.

---

## API

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/api/erp/cuadre/resumen` | Contadores por severidad, tarjeta por comprobación activa (abiertos, revisados, nuevos), última pasada, pasadas en curso, job nocturno. |
| GET | `/api/erp/cuadre/comprobaciones` | Catálogo del registro. |
| GET | `/api/erp/cuadre/hallazgos` | Descuadres abiertos. Filtros: `check_id`, `severidad`, `solo_nuevos`, `incluir_revisados`. |
| POST | `/api/erp/cuadre/hallazgos/{id}/revisar` | `{"motivo": "…"}` obligatorio (3-255 caracteres). |
| POST | `/api/erp/cuadre/hallazgos/{id}/reincluir` | «Volver a incluir». |
| POST | `/api/erp/cuadre/comprobar` | «Comprobar ahora». |
| GET | `/api/erp/cuadre/export` | Excel con todos los descuadres abiertos. |

Configuración: `GET/PATCH /api/erp/settings`, campo `cuadre`. Un cambio
parcial se funde con lo guardado; `dias: null` vuelve al valor de serie.

La config guardada lleva `version`. Si se cambia el defecto de una
comprobación:
- se sube `CONFIG_VERSION`;
- se apunta la comprobación en `_DEFECTOS_CAMBIADOS` (`config.py`).

Así, en una config guardada antes del cambio manda el defecto nuevo hasta que
se vuelva a guardar. La sección se guarda entera, así que sin esto los
defectos de entonces quedarían fijados.

```json
{
  "nocturno_activo": false,
  "hora": "03:00",
  "checks": {"<id>": {"activo": true, "dias": 30}}
}
```

El `GET` devuelve también `cuadre_catalogo`.

---

## Despliegue

```
docker compose --env-file .env.production -f docker-compose.prod.yml -f docker-compose.plesk.yml build api frontend worker-sync
docker compose --env-file .env.production -f docker-compose.prod.yml -f docker-compose.plesk.yml up -d --force-recreate api frontend worker-sync
```

1. La migración `20261003_0124` se aplica sola al arrancar `api`.
2. `worker-sync` tiene que escuchar `cuadre:run`.
3. Tras desplegar, pulsa «Comprobar ahora» y revisa con Bart el primer lote.
4. Después, enciende el job nocturno en Configuración ERP → «Cuadre».
