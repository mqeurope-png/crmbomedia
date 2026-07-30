# Tests de frontend

Runner de tests unitarios/de componentes: **Jest + Testing Library**
(montado en el sprint _Frontend-Test-Runner_). Complementa al `next build`
(tipos) y `eslint` (estilo) con tests de **comportamiento**: clicks,
estados, renderizado condicional, formularios.

## Correr los tests

```bash
cd frontend
npm test                 # toda la suite, una vez
npm run test:watch       # modo watch (re-corre al guardar)
npm run test:coverage    # con reporte de cobertura
npm test -- Register     # filtra por nombre de fichero/suite
npm test -- -t "modal"   # filtra por nombre de test
```

CI corre `npm test -- --ci --coverage=false` tras `build` + `lint`.

## Dónde viven los tests

Co-locados junto al componente, con sufijo `.test.tsx` / `.test.ts`:

```
src/app/components/PushViewToBrevoModal.tsx
src/app/components/PushViewToBrevoModal.test.tsx
```

`testMatch` solo recoge `src/**/*.test.{ts,tsx}`.

## Patrón: Arrange / Act / Assert

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

it("hace X al pulsar Y", async () => {
  const user = userEvent.setup();       // Arrange
  render(<MiComponente foo="bar" />);
  await user.click(screen.getByRole("button", { name: /Guardar/i })); // Act
  expect(screen.getByText(/ok/i)).toBeInTheDocument();                // Assert
});
```

### Prioridad de queries

Usa la query más cercana a cómo el usuario percibe la UI:

1. `getByRole` (botones, inputs, headings, dialogs) — **preferida**.
2. `getByLabelText` (campos de formulario con `<label>`).
3. `getByText` (texto visible).
4. `container.querySelector(".clase")` — **solo** para asertar clases CSS
   que no son queryables de otra forma (p.ej. la regresión de
   `.modal-dialog`). Las reglas `testing-library/no-container` están
   desactivadas para test files por esto.

Variantes: `queryBy*` (no lanza si no existe → asertar ausencia),
`findBy*` (async, espera a que aparezca).

## Mockear fetch / la capa de API

Los componentes no llaman a `fetch` directo: usan helpers de `src/app/lib/*`
(`createCallLog`, `listBrevoLists`, `getGoogleStatus`, …). Mockea **el
módulo de lib**, no el `fetch` global — es más simple y no depende de red:

```tsx
jest.mock("../lib/brevoApi", () => ({
  resolvePrimaryBrevoAccount: jest.fn().mockResolvedValue("main"),
  listBrevoLists: jest.fn().mockResolvedValue([{ id: 1, name: "Fespa" }]),
}));
```

Para un componente que sí usa `fetch` global (raro), usa
`jest.spyOn(global, "fetch").mockResolvedValue(...)`.

## Mockear el router de Next.js

```tsx
const replace = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), replace }),
  useParams: () => ({ id: "c-1" }),
  useSearchParams: () => new URLSearchParams(""),
}));
```

`next/link` renderiza un `<a>` sin necesidad de mock.

## Modales

Estos modales se renderizan **inline** (no via portal), así que basta
`render(<Modal open />)` y consultar por rol/texto. Para asertar que la
caja usa la clase correcta (regresión histórica de modales sin `.modal-dialog`):

```tsx
const { container } = render(<Modal open />);
expect(container.querySelector(".modal-dialog")).toBeInTheDocument();
```

## Efectos async al montar

Componentes que cargan datos en `useEffect` disparan `setState` async;
usa `findBy*` o `waitFor` para esperar el resultado. Verás avisos
`act(...)` en consola en tests con asserts síncronos sobre un componente
que aún tiene fetches pendientes — no fallan el test; si molestan, espera
el estado con `await screen.findBy...`.

## Regla del equipo

> Cuando se añade un componente nuevo (o se arregla un bug de UI), **debe
> venir con su test desde el primer commit**. Los 3 bugs de "modales sin
> caja" (#264 → #265/#266) son justo el tipo de regresión que estos tests
> cazan.

No busques cobertura alta artificial: pocos tests bien escritos > muchos
superficiales.
