# Capturas para el Manual de Usuario BoHub CRM

24 screenshots a 1440x900 PNG generadas con Playwright a partir de un
seed reproducible. Se usan para componer el manual markdown que reciben
los nuevos miembros del equipo.

Todos los datos son **100% ficticios** (`@demo.com`, "Demo Cliente
Activo", "Empresa Demo SL", etc.). Ningún seed copia info de
producción.

## Versión generada

- Commit hash: el commit del PR donde aterrizaron las capturas (ver
  `git log -- docs/manual-screenshots/`).
- Backend: FastAPI sobre SQLite efímero (`/tmp/crmbomedia-demo.db`),
  Python 3.11, SQLAlchemy 2.0.
- Frontend: Next.js 15.1.4, modo `npm run dev` apuntando al backend.
- Browser: Chromium 1194 (Playwright Python 1.60).

## Regenerar de cero

```bash
# 1. Seed efímero SQLite con datos demo
export FERNET=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export DATABASE_URL=sqlite:////tmp/crmbomedia-demo.db
export INTEGRATION_SECRETS_KEY="$FERNET"
export SECRET_KEY="demo-secret-key-not-for-production-use-only"
export CORS_ORIGINS="http://localhost:3000,http://127.0.0.1:3000"
python backend/scripts/seed_manual_demo.py

# 2. Backend en :8000
cd backend && \
  uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level warning &

# 3. Frontend en :3000
cd ../frontend && \
  NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000 PORT=3000 npm run dev &

# 4. Esperar a que ambos arranquen (~10s). Comprobar:
curl -s http://127.0.0.1:8000/api/health
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3000/login

# 5. Lanzar las 24 capturas
python scripts/capture_manual_screenshots.py
```

Las PNGs se generan en `docs/manual-screenshots/` con nombre
`NN-descripcion-corta.png`. El script reporta `OK/24` al final.

### Si Playwright pide instalar Chromium

```bash
playwright install
# El script ya soporta `PW_CHROMIUM_PATH=<ruta>` para forzar un
# binario alternativo.
```

## Credenciales seed

- `admin@demo.com` / `DemoAdmin2026!` — Bart Demo, rol admin, TOTP off.
- `comercial@demo.com` / `DemoComercial2026!` — Manel Demo, rol user, TOTP off.
- `lectura@demo.com` / `DemoView2026!` — viewer.

Las credenciales viven hardcoded en `backend/scripts/seed_manual_demo.py`
porque el seed es estrictamente para una BD efímera de demo en local —
NO se sube a producción ni dev compartido.

## Contenido del seed

| Modelo | Cantidad |
|---|---|
| Users | 3 (admin + comercial + viewer) |
| Companies | 5 |
| Contacts | 20 con tags + scores + estrellas + custom fields |
| Tags | 8 |
| Custom field definitions | 4 |
| Pipelines | 2 ("Ventas B2B" con 6 stages, "Postventa" con 3) |
| Opportunities | 10 repartidas entre stages |
| Tasks | 10 (pendientes, vencidas, completadas) |
| Notes | ~12 distribuidas |
| Email folders | 3 + sistema |
| Email threads | 5 con mensajes + eventos open/click |
| Activity events | ~32 |
| Segments | 3 dinámicos |
| Assignment rules | 3 |
| Workflows | 3 (active + paused + draft) con runs históricos |

## Lista de capturas

| # | Archivo | Pantalla |
|---|---|---|
| 01 | `01-login.png` | `/login` con campos vacíos |
| 02 | `02-account.png` | `/account` del comercial |
| 03 | `03-dashboard.png` | `/dashboard` con KPIs |
| 04 | `04-contactos-lista.png` | `/contacts` lista con tabla + filtros |
| 05 | `05-ficha-contacto.png` | Ficha de "Sergio Lead Activo" |
| 06 | `06-crear-contacto.png` | `/contacts/new` modal |
| 07 | `07-editar-contacto.png` | Modal "Editar contacto" desde la ficha |
| 08 | `08-borrar-contacto-modal.png` | Placeholder — la captura requiere abrir el modal de Borrar manualmente (admin-only, no seedeable en el flujo Playwright actual) |
| 09 | `09-composer-email.png` | Composer email como panel derecho desde ficha |
| 10 | `10-bandeja-emails.png` | `/emails` 3 columnas |
| 11 | `11-dropdown-plantillas.png` | Composer con dropdown Plantilla (mostrará `Cargar plantilla` si la TemplatePicker no tiene plantillas seedeadas) |
| 12 | `12-crear-tarea.png` | **MISSING** — el botón "Nueva tarea" del listado no se encontró por nombre exacto. Regenerar tras revisar `frontend/src/app/tareas/page.tsx` con un selector correcto. |
| 13 | `13-lista-tareas.png` | `/tareas` con tab Pendientes |
| 14 | `14-notas-pestania.png` | Pestaña "Notas" en ficha |
| 15 | `15-editor-tags.png` | Pestaña "Tags" en ficha |
| 16 | `16-ficha-oportunidad.png` | Pipelines index (la ficha oportunidad ≠ ruta dedicada, llega vía Pipeline Ventas B2B → contacto) |
| 17 | `17-pipeline-kanban.png` | Kanban de `Ventas B2B` con cards distribuidos |
| 18 | `18-segmentos.png` | `/segmentos` lista |
| 19 | `19-workflows-lista.png` | `/admin/workflows` con 3 workflows |
| 20 | `20-plantillas-workflows.png` | Modal "Desde plantilla" |
| 21 | `21-editor-canvas.png` | Editor canvas del workflow "Onboarding lead nuevo" |
| 22 | `22-workflows-ficha.png` | Pestaña "Workflows" en ficha |
| 23 | `23-reglas-asignacion.png` | `/admin/assignment-rules` |
| 24 | `24-marketing.png` | `/marketing` |

## Notas sobre las capturas pendientes

- **08, 11, 12** — Capturas que requieren un trigger UI específico que
  el script Playwright no encuentra de forma robusta (selectores
  inestables por texto exacto). Si el manual las necesita, regenerar
  manualmente desde la app local tras navegar a la pantalla y
  posicionar el modal/dropdown abierto.

## Limitaciones técnicas conocidas

- El seed usa SQLAlchemy `Base.metadata.create_all` en lugar de
  alembic. Los nombres de columnas + tipos son idénticos al schema
  productivo, pero las migraciones específicas (índices custom,
  defaults) no se aplican. Para una demo visual es suficiente.
- El backend levantado para las capturas NO arranca el worker RQ.
  Sin Redis no se procesan workflows en tiempo real, pero el seed
  ya popula `workflow_runs` históricos para que la UI los muestre.
- El composer email muestra un warning "No has marcado ningún alias en
  /account" porque el seed no crea aliases Gmail (requiere integración
  OAuth). El manual puede explicar el alias setup como paso aparte.
