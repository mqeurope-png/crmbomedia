# Sprint 0 — Reporte final (para Bart)

Ramas: `bohub-erp-sprint-0` (docs + código de descubrimiento) y
`bohub-erp-sprint-0-prototype` (prototipo navegable). Nada toca producción.

## ✅ Qué se validó

1. **API DELSOL (FACTUSOL)** — modelo confirmado por doc pública: API
   genérica sobre tablas (CargaTabla/EscribirRegistro/ActualizarRegistro/
   BorrarRegistros) con Login → Bearer temporal renovable. Cliente Python
   listo (`app/integrations/factusol/client.py`) con renovación automática
   + retry; scripts de descubrimiento de esquema y de escritura e2e listos
   para ejecutar en cuanto haya credenciales.
2. **Convención de esquema FACTUSOL** — `F_XXX` + columnas prefijo+sufijo
   (CODCLI, CODART 13 chars, DESART 50) verificada en doc oficial de
   importación y en integradores. Base de factusol-schema.md construida;
   el script genera la versión DISCOVERED automáticamente.
3. **WooCommerce** — REST v3 y webhooks totalmente especificados (firma
   HMAC, ping de activación, paginación). Cliente + receiver + test de 20
   pedidos listos. **Hallazgo:** no existe topic `order.payment_complete`
   nativo — el pago se detecta con `order.updated` + `date_paid` (la
   máquina de pago ya lo contempla).
4. **Genei y DSV tienen API real** — Genei via plugins oficiales + doc por
   soporte (gratuita); DSV con developer portal formal (Booking, Tracking,
   Webhooks, etiquetas). Ninguno obliga a plan manual; ambos tienen plan B
   documentado por si el onboarding se alarga.
5. **Conciliación SKU** — algoritmo (exacto normalizado + fuzzy 0.84)
   implementado y validado con `--demo`; diseño de `product_sku_mapping` y
   workflow de SKU nuevos cerrado sobre la bandeja de excepciones.
6. **Arquitectura de estados** — 4 dominios independientes con matriz de
   permisos, guards cruzados y evidencias; contratos de datos de las 9
   entidades con fuente de verdad por entidad.
7. **Prototipo navegable** — 6 pantallas con mock en `/erp-prototype`
   (bandeja pedidos, ficha con 4 estados+historial, cola SAT táctil,
   excepciones, vista de estados, empresa+FACTUSOL).

## ⚠️ Qué NO funciona como esperábamos

1. **Red del entorno de desarrollo**: la política de red del entorno CCR
   bloquea `*.sdelsol.com`, `genei.es`, `developer.dsv.com` y
   `wordpress.org`. Toda la validación LIVE (esquema real, escrituras,
   webhooks) debe ejecutarse desde tu máquina o desde producción — o se
   amplía el allowlist del environment antes del Sprint 1. **Este es el
   descubrimiento más importante del sprint a nivel operativo.**
2. **La doc completa de la API DELSOL requiere registro** (apidoc.sdelsol.com
   pide inscripción + API key). Las rutas exactas de Login/operaciones
   quedan como hipótesis aisladas en un solo punto del cliente, ajustables
   en minutos con la doc descargada.
3. **Los 3 documentos de contexto** (mapa validado, provisional, decisiones)
   no pude copiarlos: viven en tu workspace de Cowork (ruta Windows local)
   y el conector Drive requiere aprobación interactiva. → Suéltalos en
   `docs/erp/` (nombres exactos en README.md).

## 🔓 Decisiones abiertas para ti

| # | Pregunta | Dónde impacta |
|---|---|---|
| 1 | ¿Anticipos/pagos parciales en B2B? (si no, fuera `partially_paid`) | state-machines §1 |
| 2 | ¿Foto obligatoria del bulto para marcar Embalado? | state-machines §2 + UI SAT |
| 3 | ¿Se puede preparar sin cobrar (crédito B2B) con override admin? | guard preparación |
| 4 | ¿Factura automática al cobrar (web) o siempre revisión manual? | state-machines §4 |
| 5 | ¿Ejercicio/serie de pruebas en FACTUSOL para los tests de escritura? | write-flows |
| 6 | Allowlist de red del entorno vs ejecutar validación desde tu máquina | operativa Sprint 1 |

## 📋 Acciones tuyas para desbloquear el Sprint 1

1. Habilitar acceso API en el hosting DELSOL + credenciales → `.env.local`.
2. Consumer Key/Secret de mbolasers.com (WooCommerce → REST API, Read).
3. Ejecutar los 3 scripts (`factusol_discover_schema`, `factusol_write_flow_test`,
   `woocommerce_read_test`) y pasarme la salida — o darme un entorno con red.
4. Cuentas Genei (pedir doc API a soporte) y DSV (alta developer portal).
5. Copiar los 3 documentos de contexto a `docs/erp/`.
6. Revisar el prototipo (`/erp-prototype` en la rama de prototipo) y
   responder las 6 decisiones abiertas.

## 📐 Estimación real del MVP (Sprint 1: Pedidos + SAT)

**10-13 PRs en 3 fases** (detalle en backlog-mvp.md):
- **Fase A (6 PRs)** — cimientos sin dependencias externas: migraciones de
  orders/estados/excepciones, máquina declarativa, API+UI bandeja/ficha/
  SAT/excepciones. *Puede empezar mañana, sin esperar credenciales.*
- **Fase B (4 PRs)** — WooCommerce live 🔓: integration_accounts, webhooks
  productivos, sync idempotente, conciliación SKU con pantalla admin.
- **Fase C (3-4 PRs)** — FACTUSOL live 🔓: worker-factusol serializado,
  «Crear en FACTUSOL», facturación con guards.

Riesgo principal de estimación: divergencias del esquema FACTUSOL real
(nombres de tablas de líneas, política de numeración) → +1 PR en Fase C.
