# UI architecture

Sprint UX introdujo un **app shell** persistente: sidebar fijo a la
izquierda, topbar fijo arriba, área de contenido con scroll interno.
Antes el frontend era una sucesión de páginas con header propio y
back-link manual — esto encaja todo bajo un mismo chasis.

## Componentes del shell

- `<AppShell>` (`components/AppShell.tsx`). Wrapper aplicado en
  `layout.tsx`. Lee la ruta con `usePathname()`; si es `/login` o
  `/password-reset` se desactiva (no hay sidebar/topbar en flujos
  anónimos). Trae al `currentUser` una sola vez para que el sidebar
  decida qué items pintar por rol.
- `<Sidebar>`. Navegación principal con iconos de **lucide-react**.
  Plegable a 64 px solo iconos / 240 px expandido; el estado se
  persiste en `localStorage` bajo `crmbo:sidebar:collapsed`. Items
  filtrados por rol via `allowedRoles`.
- `<TopBar>`. Logo + `<GlobalSearch>` (autocomplete de contactos por
  `/api/contacts?q=…&limit=10`, debounced 250 ms, click navega a
  `/contacts/[id]`) + bell placeholder + `<UserMenu>`.
- `<UserMenu>`. Avatar con iniciales + dropdown con perfil,
  contraseña, 2FA y logout.
- `<PageHeader>`. Header sticky reutilizable: `title`, `eyebrow`,
  `description`, `crumbs` (breadcrumbs opcionales) y `actions`. Va
  pegado a `top: 0` del scroll interno del contenido.

## Modelo de scroll

`body { overflow: hidden }`. El layout es un grid 2×2:

```
┌────────────────────────────────────────┐
│ topbar (h: 56px)                       │
├────────┬───────────────────────────────┤
│ side   │ content (overflow-y: auto)    │
│ (W=240)│                               │
└────────┴───────────────────────────────┘
```

El sidebar y el topbar quedan visibles siempre. El scroll vive en
`.app-shell-content`. Las cabeceras de tabla (`.data-table thead th`)
se hacen sticky dentro de ese scroll para que se mantengan visibles
al desplazarse listas largas.

## Cómo añadir una página

1. Crear `src/app/<ruta>/page.tsx` como **client component**
   (`"use client"`). El AppShell ya envuelve a través de `layout.tsx`.
2. Empezar el render con `<main className="shell">` (o
   `"shell narrow"` / `"shell shell-wide"` según el ancho que necesite
   la página). El `.shell` solo aporta `max-width` y `margin: 0 auto`;
   ya no fuerza padding-top como hacía antes.
3. Primer hijo: `<PageHeader>` con título, eyebrow opcional,
   breadcrumbs si la página es una sub-vista, y `actions` con los
   botones principales (siempre `className="button small"` para
   coherencia con el resto del header).
4. Restante: secciones (`.panel`, `.card`, tablas…) según el contenido.

```tsx
return (
  <main className="shell">
    <PageHeader
      title="Mi pantalla"
      eyebrow="Área"
      crumbs={[{ label: "Contactos", href: "/contacts" }, { label: "Detalle" }]}
      actions={<button className="button small">Acción</button>}
    />
    {/* … */}
  </main>
);
```

5. Si la página añade un item al sidebar, editar `Sidebar.tsx` y
   añadir un objeto a `NAV_ITEMS` con `icon` (lucide-react) y
   `allowedRoles` si la pantalla requiere admin/manager.

## Densidad y coherencia

- Botones primarios: `className="button"` (default) o
  `"button small"` cuando viven dentro de un header / fila.
- Botones secundarios: `"button secondary [small]"`.
- Cabeceras de tablas: dentro del shell se vuelven sticky
  automáticamente vía `.app-shell-content .data-table thead th`.
- Hovers de filas: `:hover { background: #f6f8fb }` (aplicado por la
  regla del shell).
- Iconos pequeños: `<Icon size={14} aria-hidden />` para no romper la
  línea visual del texto adjunto.

## Modal vs drawer vs expansión inline

- Crear / editar entidades cortas → `<Modal>` o `<ConfirmDialog>`.
- Detalles secundarios que conviven con la lista (sync history,
  formularios de API key…) → expansión inline en una card colapsable
  (ver `<IntegrationAccountCard>`). Solo una expandida a la vez para
  evitar scroll infinito.
- Drawers full-height: por ahora no se usan; si se introducen,
  documentar aquí el shorthand.

## Responsive

Por debajo de 768 px:
- El sidebar se convierte en drawer fuera de pantalla.
- El topbar añade un botón hamburger que lo abre.
- Un overlay `.app-shell-scrim` cubre el contenido mientras el drawer
  está abierto; cualquier click fuera lo cierra.
- El brand-name del topbar se oculta para ahorrar espacio (queda
  solo el cuadrado del logo).
