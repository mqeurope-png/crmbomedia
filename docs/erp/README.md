# BoHub ERP — Sprint 0 (Descubrimiento)

Rama: `bohub-erp-sprint-0`. Todo lo del ERP vive aquí + rama de prototipo
`bohub-erp-sprint-0-prototype`. **Nada de esto toca producción del CRM.**

## Documentos de contexto (pre-Sprint 0)

> ⚠️ **PENDIENTE DE COPIA — acción de Bart.** Estos 3 documentos viven en el
> workspace de Cowork (`C:\Users\whats\OneDrive\Documents\Claude\Projects\crm app\`)
> y NO son accesibles desde el entorno remoto de desarrollo (ruta Windows
> local; el conector Google Drive requiere aprobación interactiva). Soltarlos
> en esta carpeta con estos nombres:
>
> - `BoHub-ERP-mapa-VALIDADO-2026-07-31.md` — mapa de solapes CRM→ERP (16 filas con veredicto).
> - `BoHub-ERP-mapa-solapes-provisional_1.md` — hipótesis inicial (histórico).
> - `BoHub-ERP-decisiones-cerradas.md` — las 10 decisiones de arquitectura.

## Entregables del Sprint 0

| # | Documento | Estado |
|---|---|---|
| 1 | [factusol-schema.md](factusol-schema.md) | ✅ base pública + script de descubrimiento listo (live pendiente de credenciales) |
| 2 | [factusol-write-flows.md](factusol-write-flows.md) | ✅ flujos diseñados + curl plantilla (live pendiente de credenciales) |
| 3 | [woocommerce-integration.md](woocommerce-integration.md) | ✅ cliente + webhook receiver + multi-tienda (test live pendiente de keys) |
| 4 | [logistics-genei-dsv.md](logistics-genei-dsv.md) | ✅ investigación + plan por proveedor |
| 5 | [sku-conciliation.md](sku-conciliation.md) | ✅ script + diseño tabla + workflow SKU nuevos |
| 6 | [state-machines.md](state-machines.md) | ✅ 4 dominios con transiciones/roles/evidencias (cierre final con Bart) |
| 7 | [data-model.md](data-model.md) | ✅ contratos de datos por entidad |
| 8 | [backlog-mvp.md](backlog-mvp.md) | ✅ backlog priorizado Sprint 1 con estimaciones |
| 9 | Prototipo navegable | ✅ rama `bohub-erp-sprint-0-prototype` (Next.js, mocks) |
| 10 | [sprint-0-summary.md](sprint-0-summary.md) | ✅ reporte final para Bart |

## Código del Sprint 0 (prototipo, no producción)

- `backend/app/integrations/factusol/client.py` — cliente API DELSOL (auth + renovación + retry + operaciones genéricas).
- `backend/scripts/factusol_discover_schema.py` — descubrimiento de esquema en vivo (genera `factusol-schema-DISCOVERED.md`).
- `backend/scripts/factusol_write_flow_test.py` — prueba end-to-end de escritura (cliente + presupuesto ficticios + cleanup).
- `backend/app/integrations/woocommerce/client.py` — cliente REST v3 de lectura.
- `backend/scripts/woocommerce_read_test.py` — leer últimos 20 pedidos.
- `backend/app/integrations/woocommerce/webhooks_prototype.py` — receiver con validación de firma (NO registrado en app.main).
- `backend/scripts/sku_conciliation.py` — matching SKU Woo ↔ CODART FACTUSOL.

## Bloqueos que necesitan a Bart

1. **Credenciales FACTUSOL** + habilitar acceso API en el hosting DELSOL de Bomedia (paso administrativo del panel).
2. **Consumer Key/Secret WooCommerce** de mbolasers.com (WP admin → WooCommerce → Ajustes → Avanzado → REST API).
3. **Cuentas Genei y DSV** (credenciales API; DSV requiere onboarding en developer.dsv.com).
4. **Red del entorno de desarrollo**: la política de red del entorno CCR bloquea `*.sdelsol.com`, `genei.es`, `developer.dsv.com`, `wordpress.org`, etc. Añadirlos al allowlist del environment o ejecutar los scripts de descubrimiento desde la máquina de Bart / servidor de producción.
5. **Copiar los 3 documentos de contexto** (arriba).
