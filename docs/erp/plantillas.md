# Plantillas de documento (27/10/2026)

Una plantilla es un **conjunto de líneas** (artículos de FACTUSOL, cantidades,
precios, descuentos y textos), no un documento. Lo que sale de ella se decide
al usarla: **la misma plantilla sirve para una oferta, una proforma o un
pedido**. Nacieron en el #559 como «plantillas de oferta»; desde la 0147 son el
catálogo de la empresa para los tres destinos.

**Regla de la casa:** nada de las plantillas escribe en FACTUSOL. Guardar una
plantilla desde una proforma o un albarán solo **lee** `F_PRE`/`F_LPS` o
`F_ALB`/`F_LAL`. Usarla solo rellena el formulario de siempre; lo que se
escriba después lo escribe ese flujo, como antes.

---

## Qué tiene una plantilla

- **Nombre, descripción, activa.** Una desactivada no se ofrece al crear, pero
  sigue en la pantalla de gestión.
- **Idioma** (`es`, `en`, `de`, `fr`, `nl`). Sin traducciones: la misma oferta
  en castellano y en alemán son **dos plantillas**.
- **Carpeta** (opcional), jerárquica hasta 3 niveles.
- **Etiquetas** libres y múltiples («DTF», «escuelas», «renting», «FESPA»…),
  hasta 20 por plantilla. Se guardan como se escribieron y normalizadas
  (minúsculas, sin acentos) para filtrar sin repetirlas.
- **Origen**: de qué documento se guardó (`presupuesto` = proforma, `albaran`,
  `pedido`, o `manual`) y su número visible (`2-000040`, `MANUAL-000012`).
- **Líneas**: `codart` (CODART interno de F_ART), `sku` (EQUART, el que se
  enseña), descripción, cantidad, precio, descuento e IVA. Una línea sin
  artículo, sin cantidad y sin precio es una **nota** de texto.

## Guardar como plantilla

| Desde | Dónde | Qué copia |
|---|---|---|
| Proforma | ERP · Proformas «⋯» y ERP · Documentos → Presupuestos «⋯» | `F_LPS` con su artículo (`codart` + `sku`), dto. e IVA |
| Albarán | ERP · Documentos → Albaranes «⋯» | `F_LAL` con su artículo; el IVA es un código (0 = general) y se traduce a 21 % salvo 4/10/21 |
| Pedido | Ficha del pedido «⋯» | Las líneas de mercancía del pedido (sin portes ni comisiones o cupones de la tienda), con su artículo completado leyendo F_ART (el CODART de las que solo traen el SKU y el SKU comercial de las que solo traen el CODART). Un pedido web solo lo guarda quien puede ver pedidos web |
| A mano | ERP · Plantillas «+ Nueva plantilla» | El editor de líneas de las ofertas (artículos del catálogo) |

La carpeta por defecto del usuario viene puesta (se cambia en el modal).

## Usar una plantilla

En el **punto de uso** hay un selector (no un desplegable plano):

- **Oferta**: «Elegir plantilla…» en el editor de la oferta.
- **Proforma**: «Cargar plantilla…» en «+ Nueva proforma» (no al editar ni al
  duplicar).
- **Pedido**: «Cargar plantilla…» en el paso 2 del alta de pedido manual.
- Desde la pantalla de gestión, «Usar → En una oferta / En una proforma / En un
  pedido» abre esas mismas altas con `?plantilla=ID`.

El selector tiene el **árbol de carpetas** (se abre en la carpeta por defecto
del usuario, ★), el **buscador**, el **filtro por etiquetas** y, si ya hay
cliente, **las plantillas de su idioma arriba**; las de otros idiomas siguen
ahí, en un grupo «Otros idiomas» con aviso, y al elegir una se pide
confirmación.

Cada destino reutiliza su flujo; la plantilla solo aporta las líneas en la
forma que ese flujo espera (`GET /api/erp/plantillas/{id}/lineas?destino=`):

- **oferta**: las líneas tal cual.
- **proforma**: `codart` = el CODART interno (viaja sin traducir: «el CODART
  directo manda» en `resolve_codarts`), `sku` para enseñarlo, dto. e IVA.
- **pedido**: el pedido no tiene descuento por línea ni notas sueltas: el
  precio va **neto** del descuento (a céntimos), una línea de texto se pega a
  la anterior (las del principio, a la siguiente), una línea a cantidad 0 no
  entra y una descripción de más de 255 caracteres se recorta por palabra.
  Cada cosa de estas sale como **aviso** en el formulario. `product_codart` va
  si cabe (13 caracteres). El IVA lo sigue poniendo el régimen del cliente,
  como siempre.

El idioma del cliente es el de la cascada de las ofertas (contacto → empresa →
país → empresa emisora → `es`); en un pedido manda `orders.language` si lo
tiene.

## Buscador

Hasta 6 palabras; **todas** tienen que aparecer (Y). Cada palabra se busca en
el nombre, la descripción, las etiquetas y **dentro de las líneas** (`codart`,
`sku` y descripción), sin distinguir mayúsculas. «DM3H» o «horno» encuentran la
plantilla aunque su nombre no lo diga, y la lista enseña la línea que coincide.
`%` y `_` se buscan literalmente.

## Carpetas y visibilidad

Copia el patrón de las carpetas de las plantillas de correo
(`email_template_folders`): carpeta con madre, dueño y orden, profundidad
máxima 3 (comprobada al crear **y al mover**, también la altura del subárbol),
sin ciclos; una **tabla de acceso** por carpeta (la «compartición»), y la
**carpeta por defecto** de cada usuario (`plantillas_documento_preferencias`,
la ★ del árbol). Borrar una carpeta no borra nada: sus plantillas y
subcarpetas suben a la raíz. Dos excepciones, porque aquí «sin filas de
acceso» es «lo ve todo»: una carpeta que acota el acceso de alguien no se
borra hasta quitarle ese acceso (409 `carpeta_con_acceso`), y quien tiene el
acceso acotado no borra carpetas (403 `acceso_restringido`: su contenido iría
a la raíz, que no ve). Renombrar no toca la madre; moverla, sí se comprueba.

La visibilidad va **al revés** que en el correo, a propósito: las plantillas
son de la empresa y **se ven todas**. Si administración da acceso a un
usuario en alguna carpeta (botón «Acceso» de la carpeta, `erp.config`), desde
ese momento ese usuario **solo** ve esas carpetas y sus subcarpetas —en el
árbol, la lista, el buscador, el detalle, las líneas, las etiquetas y al crear
una oferta con `plantilla_id`—, y no puede guardar plantillas ni carpetas en la
raíz. Tampoco ve el nombre de las carpetas madre que no tiene: la ruta se
corta en la primera que no ve. Un usuario sin ninguna fila de acceso lo ve
todo; el administrador, siempre todo.

## Tablas (migración 0147)

| Tabla | Qué es |
|---|---|
| `plantillas_documento` (antes `ofertas_plantillas`) | La plantilla: nombre, descripción, activa, **idioma**, **carpeta**, **origen** (`origen_tipo`, `origen_serie`, `origen_codigo`, `origen_order_id`, `origen_numero`). |
| `plantillas_documento_lineas` (antes `ofertas_plantillas_lineas`) | Sus líneas (sin cambios). |
| `plantillas_documento_etiquetas` | Etiquetas (`etiqueta`, `etiqueta_norm`), únicas por plantilla. `etiqueta_norm` va en `utf8mb4_bin` en MySQL: ya está normalizada y así la UNIQUE dice lo mismo que Python («ß» ≠ «ss»). |
| `plantillas_documento_carpetas` | Carpetas (madre, dueño, orden). |
| `plantillas_documento_carpetas_acceso` | Quién tiene acceso a cada carpeta. |
| `plantillas_documento_preferencias` | La carpeta por defecto de cada usuario. |

**Qué hace la migración 0147** (sin perder nada):

1. Crea `plantillas_documento_carpetas`.
2. **Renombra** `ofertas_plantillas` → `plantillas_documento` y
   `ofertas_plantillas_lineas` → `plantillas_documento_lineas`: mismos ids,
   mismas líneas con sus artículos; la FK `ofertas.plantilla_id` sigue a la
   tabla renombrada. Rehace el índice de `plantilla_id` con el nombre nuevo.
3. Añade `idioma` (`es` para las que había), `carpeta_id` y las columnas de
   origen; rellena el origen de las que venían de un presupuesto
   (`origen_tipo = presupuesto`, `origen_codigo` = el CODPRE,
   `origen_numero` = `serie-000000`) y marca `manual` las demás; quita
   `origen_codpre`.
4. Crea las tablas de acceso, preferencias y etiquetas.

Tiene `downgrade` completo, probado en MySQL 8 (las tablas nuevas se borran
enteras, sin quitar antes sus índices, que en MySQL sostienen sus claves
ajenas). La plantilla que existía (10 líneas) queda tal cual, sin carpeta ni
etiquetas, en castellano.

## API (`/api/erp/plantillas`)

Leer: `erp.office.view`. Escribir: `erp.proformas`. Acceso por carpeta:
`erp.config`.

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/meta` | idiomas, destinos, profundidad máxima, carpeta por defecto, si gestiona acceso, si está acotado |
| GET | `/etiquetas` | etiquetas en uso (de lo que ve) con su número |
| GET | `/usuarios` | (config) usuarios a los que acotar, con `restringido` |
| GET/POST | `/carpetas` | árbol (con `acceso_user_ids` para config) · crear |
| PATCH/DELETE | `/carpetas/{id}` | renombrar / mover (`parent_id: null` = raíz) · borrar |
| PUT | `/carpetas/{id}/acceso` | (config) `{user_ids}` |
| PUT | `/preferencias/carpeta` | carpeta por defecto del usuario |
| GET | `?q=&carpeta_id=&sin_carpeta=&etiqueta=&idioma=&activas=&contact_id=&company_id=&order_id=&serie=` | lista; con cliente, `idioma_cliente` y `idioma_coincide` por plantilla |
| POST | `` | crear: con `origen {tipo, serie, codigo}` / `{tipo: pedido, order_id}` o con `lineas` |
| GET/PATCH | `/{id}` | detalle con líneas · editar (nombre, descripción, idioma, carpeta, etiquetas, activa, líneas) |
| GET | `/{id}/lineas?destino=oferta\|proforma\|pedido` | las líneas en la forma del destino + avisos |

Las rutas `/api/erp/ofertas/plantillas*` del #559 se han quitado; crear una
oferta con `plantilla_id` sigue igual (y respeta la visibilidad).

---

## «Crear pedido»: primero el resumen

Desde un **albarán** o una **factura** (ERP · Documentos) y al **convertir una
proforma** (ERP · Proformas y ficha de empresa), «Crear pedido» ya no crea en
el momento: abre un **resumen** con la empresa, la fecha, los conceptos con
cantidades, precio, dto. e importe, el total con IVA y el documento de origen.
Nada se crea hasta pulsar «Crear pedido» (o «Crear pedido y albarán» en la
proforma); «Cancelar» no deja nada a medias.

El resumen lo calcula el servidor con **las mismas funciones que la
creación**: `plan_document_lines` reparte las líneas (descripciones largas en
líneas de texto, notas, descuento, IVA por código) tanto para el resumen
(`order_summary`) como para el alta (`add_document_lines`), y el total usa el
mismo criterio que `build_order`. En la proforma, `preview_quote_conversion`
usa `quote_lines_for_order`, `resolve_company_id` y `find_quote_order`, como
`convert_quote_to_order`. Así el resumen no puede decir otra cosa que lo que se
crea.

- Albarán / factura: `GET /api/erp/orders/from-factusol/preview` devuelve
  `resumen`; si el documento ya tiene pedido (`already_imported`) no se ofrece
  crear otro.
- Proforma: `GET /api/erp/factusol/quotes/{codpre}/convert-preview?serie=`
  (`erp.orders.create`, solo lee); si ya se convirtió (`already_existed`) no se
  ofrece crear otro.

**Caminos automáticos:** ninguno depende de que la creación sea inmediata.
`create_order_from_factusol_document` y `convert_quote_to_order` solo los
llaman sus endpoints y el job de conversión (que se encola al confirmar). El
único encadenado era el de Documentos «Vincular empresa → crear»: lo inicia
una persona y ahora vuelve al resumen en vez de crear.
