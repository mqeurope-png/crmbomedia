# Brief para Claude Design — Revisión UX del ERP de BoHub

## 1. Qué es la aplicación

**BoHub** es el CRM/ERP interno de **Bomedia S.L.** (distribuidor europeo de impresoras UV-LED y equipos láser: artisJet, MBO, Flux). La parte **ERP** que hay que revisar gestiona el ciclo de pedidos de principio a fin y su conexión con dos sistemas externos:

- **WooCommerce** — 3 tiendas online (boprint, artisjet, fluxlasers) que entran pedidos por webhook.
- **FACTUSOL/DELSOL** — el software de facturación real de la empresa (clientes, presupuestos, pedidos, albaranes, facturas, cobros). **FACTUSOL es la fuente de verdad fiscal.**

El ERP cubre: recibir/aprobar pedidos web, crear pedidos y proformas a mano, emitir albaranes y facturas en FACTUSOL, registrar cobros, preparar envíos en el taller (SAT) y enviar documentos por email.

- **Usuarios:** equipo pequeño **no técnico** (ventas, administración, taller). La claridad importa más que la densidad.
- **Idioma de la interfaz:** español.
- **Stack front:** Next.js + React con un **sistema de tokens propio** (no Tailwind completo). Debe ser **responsive** (se usa también en pantallas pequeñas del taller).

## 2. Objetivo de la revisión

Revisar **toda la parte ERP** y proponer mejoras en todo lo mejorable, tanto a nivel de **sistema de diseño** como **pantalla por pantalla**:

- Jerarquía de la información (qué se muestra primero, qué sobra).
- **Legibilidad de los estados** del pedido (hoy van en texto pequeño y cuesta leerlos).
- Consistencia entre pantallas (mismos componentes, mismos patrones).
- Eficiencia del flujo de trabajo (menos clics, la acción correcta a mano).
- Responsive y accesibilidad (contraste, tamaños, foco, targets táctiles).
- Densidad, espaciado, affordances de botones y formularios.

**Entregable esperado:** revisión **priorizada** (impacto alto/medio/bajo, quick wins vs. cambios mayores), con propuestas concretas y, a ser posible, mockups navegables por pantalla + notas de sistema.

## 3. Dirección de diseño ya establecida (respetar y evolucionar, no partir de cero)

Ya hay un rediseño de **flujo** en marcha (implementado en fases). No es un lavado de cara: la idea es que **el sistema decida qué toca hacer y lo ponga delante**, en vez de que el usuario lea varios estados y deduzca. Los patrones establecidos:

- **Bandeja de trabajo por cola** (no tabla): colas con contador — *Por revisar / Por facturar / Por cobrar / Por enviar / Incidencias / Listo* — y cada pedido muestra su **siguiente acción** (un botón principal) + **alerta accionable** si hay bloqueo.
- **Ficha de pedido = «línea de vida»:** un **stepper de 7 pasos** (Creado → Pagado → Aprobado → Albarán → Factura → Cobro → Enviado) con el paso actual resaltado y su acción incrustada, más paneles de Resumen económico y FACTUSOL.
- **Componentes compartidos:** pastillas de estado, buscador de entidad, cabecera de ficha, paneles, colas con contador, tablas.
- Mostrar el **régimen de IVA** (nacional con IVA / intracomunitario exento / exportación) y el estado FACTUSOL donde importan.

**Referencia visual (maqueta estática):** hay una maqueta HTML con las 8 pantallas rediseñadas. Está en el repo en `docs/ux/maqueta-erp-pedidos.html`, y embebida (entre etiquetas ```html```) en el archivo `prompt-erp-rediseno-ux-maestro-y-vies.md` de esta carpeta — se puede extraer y abrir en un navegador para ver la dirección de diseño y los tokens.

## 4. Sistema visual actual (tokens de la maqueta)

```
--bg:#f4f5f7;  --card:#fff;  --ink:#1b2330;  --muted:#6b7280;  --faint:#9aa2ad;
--line:#e7e9ee;  --line2:#eef0f4;  --brand:#2f6bff;  --brand-ink:#1e40af;
Pastillas de estado (fondo / texto):
  verde   --g-bg:#e7f6ec / --g-ink:#1f7a45   (pagado, cobrado, ok)
  ámbar   --a-bg:#fdf1dd / --a-ink:#9a6700   (pendiente)
  azul    --b-bg:#e8f0ff / --b-ink:#2451c7   (por facturar)
  teal    --t-bg:#e6f5f3 / --t-ink:#0f766e   (manual)
  neutro  --n-bg:#eef0f4 / --n-ink:#5b6472
  rojo    --r-bg:#fdeaea / --r-ink:#b42318   (incidencia)
  morado  --p-bg:#f0e9fd / --p-ink:#6d3fc4   (woo / intracomunitario)
--radius:12px;  sombra suave;  tipografía del sistema (-apple-system, Segoe UI, Roboto).
```

## 5. Inventario de pantallas ERP (qué hace cada una)

1. **Bandeja de pedidos** — vista principal; colas por acción + lista de pedidos con siguiente acción y alertas. *(Se están añadiendo: filtros por origen/tienda, facturado/no, orden por fecha, y estados grandes Pagado/Facturado/Cobro/Completado.)*
2. **Ficha de pedido** — «línea de vida» (stepper 7 pasos) + resumen económico + panel FACTUSOL + documentos de envío + seguimiento + líneas + timeline. Acciones: PDF pedido/albarán/factura, emitir factura, registrar cobro, enviar a SAT, enviar factura al cliente, marcar completado, anular.
3. **Ficha de empresa** — datos fiscales (NIF, país, régimen), vínculo FACTUSOL, actividad reciente (pedidos/facturas/proformas), acciones rápidas. *(Hay estado de archivada/activa tras una limpieza reciente.)*
4. **Proformas** — colas (aceptadas·por convertir / pendientes / rechazadas / convertidas) con importe+régimen y acción «Convertir en pedido». Filtros y orden por fecha/serie/nº.
5. **Documentos FACTUSOL** — explorador con pestañas (Presupuestos / Pedidos cliente / Albaranes / Facturas), acción por documento (registrar cobro, PDF, vincular a pedido).
6. **Cola SAT (taller)** — lo que el taller necesita para preparar cada envío (nº serie, licencia WhiteRIP, origen, observaciones); acciones enviar a SAT por email, marcar procesado, albarán PDF. *(Se están añadiendo filtros, vista lista e historial de enviados.)*
7. **Crear / editar empresa** — identificación fiscal (nombre, NIF/CIF/VAT, país → régimen detectado), dirección, contacto, anti-duplicados, «crear también en FACTUSOL», validación VIES.
8. **Buscador de empresa (alta de contacto / asignar empresa)** — input que filtra por nombre/CIF/dominio, marca si está en FACTUSOL o solo CRM, y ofrece «crear empresa nueva».
9. **Nuevo pedido manual** — alta de pedido a mano exigiendo empresa vinculada a FACTUSOL; líneas, portes, dirección de envío.
10. **Nueva proforma** — alta de presupuesto real en FACTUSOL; líneas con catálogo, o duplicar de otra.
11. **Ajustes ERP** — prefijos de referencia por tienda, remitentes de email por tienda, plantillas de email por idioma, mapeo de series.

## 6. Puntos de dolor conocidos (feedback real del usuario)

- **Estados poco legibles:** pagado/facturado/cobro/completado van en texto pequeño; el usuario pide etiquetas grandes o columnas claras (verde/gris) para leerlos de un vistazo sin abrir la ficha.
- **Faltan filtros y orden** en varias listas (por fecha, tienda/origen, estado, facturado/no).
- **Duplicar proforma** es confuso y con bugs: obliga a re-mapear todos los artículos y la UI queda descuadrada.
- **Modales descuadrados/raros** en algún flujo (p. ej. asignar empresa desde ficha de contacto — ya se rehízo, pero revisar consistencia general de modales).
- **Duplicación de acciones/botones** en la ficha (p. ej. dos botones de factura); conviene una jerarquía clara acción principal vs. secundarias.
- **Densidad de la ficha:** mucha información; hay que priorizar y agrupar sin perder nada.
- Consistencia de **tablas vs. tarjetas** entre pantallas (algunas listas, otras fichas).

## 7. Restricciones

- Interfaz **en español**; tono claro para usuarios no técnicos.
- **Mantener toda la funcionalidad** existente; el rediseño reorganiza y clarifica, no elimina acciones.
- **Responsive** obligatorio (taller usa pantallas pequeñas).
- Respetar el **sistema de tokens** y los componentes ya creados (evolucionar, no romper la coherencia).
- **Datos sensibles:** no exponer datos fiscales/clientes reales en mockups públicos; usar datos de ejemplo.
- FACTUSOL manda en lo fiscal; el ERP muchas veces solo lee de él.

## 8. Cómo trabajar la revisión (sugerencia de entrega)

- Empezar por un **diagnóstico de sistema** (tokens, tipografía, escala de espaciado, patrones de estado, tablas/tarjetas, formularios, modales, responsive) con propuestas transversales.
- Luego **pantalla por pantalla**: qué funciona, qué mejorar, mockup propuesto.
- Marcar **quick wins** (bajo esfuerzo, alto impacto) frente a **cambios mayores**.
- Priorizar por impacto en el trabajo diario (la Bandeja y la Ficha de pedido son las de más uso).

## 9. Material a adjuntar (lo aporta Bart)

Para que la revisión sea sobre la app real, adjuntar **capturas de cada pantalla** (idealmente en escritorio y móvil). Lista de capturas útiles:

- [ ] Bandeja de pedidos (con las colas y varias filas, incluida alguna con alerta)
- [ ] Ficha de pedido (completa, de arriba abajo: stepper, resumen, FACTUSOL, envío, líneas, timeline)
- [ ] Ficha de empresa
- [ ] Proformas (las colas y la lista)
- [ ] Documentos FACTUSOL (las pestañas)
- [ ] Cola SAT
- [ ] Crear empresa / buscador de empresa
- [ ] Nuevo pedido manual y Nueva proforma (formularios)
- [ ] Ajustes ERP

Y como referencia de la dirección ya aprobada, la **maqueta** (`docs/ux/maqueta-erp-pedidos.html` / embebida en `prompt-erp-rediseno-ux-maestro-y-vies.md`).
