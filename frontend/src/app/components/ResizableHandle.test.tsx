import { fireEvent, render, screen } from "@testing-library/react";
import { ResizableHandle, usePanelWidth } from "./ResizableHandle";

/** CRM-BANDEJA — el panel medio de /emails debe poder ampliarse hasta
 *  800px y el ancho elegido debe persistir en localStorage. Probamos el
 *  hook + handle con un harness mínimo (montar el layout entero de
 *  /emails exigiría mockear sidebar/lista/composer al completo). */

const STORAGE_KEY = "crmbomedia_ui:test:middle_width";

function Harness() {
  const panel = usePanelWidth({
    key: STORAGE_KEY,
    defaultPx: 380,
    minPx: 280,
    maxPx: 800,
  });
  return (
    <div>
      <span data-testid="width">{panel.width}</span>
      <ResizableHandle
        onMouseDown={panel.startDrag}
        isDragging={panel.isDragging}
        ariaLabel="Redimensionar lista de hilos"
      />
    </div>
  );
}

function drag(handle: HTMLElement, fromX: number, toX: number) {
  fireEvent.mouseDown(handle, { clientX: fromX });
  fireEvent.mouseMove(window, { clientX: toX });
  fireEvent.mouseUp(window);
}

describe("ResizableHandle + usePanelWidth — CRM-BANDEJA", () => {
  beforeEach(() => window.localStorage.clear());

  it("arrastrar el divider amplía el panel y persiste en localStorage", () => {
    render(<Harness />);
    const handle = screen.getByRole("button", {
      name: /Redimensionar lista de hilos/i,
    });
    expect(screen.getByTestId("width")).toHaveTextContent("380");

    drag(handle, 0, 200);

    expect(screen.getByTestId("width")).toHaveTextContent("580");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("580");
  });

  it("permite superar los 600px antiguos hasta el tope de 800px (clamp)", () => {
    render(<Harness />);
    const handle = screen.getByRole("button", {
      name: /Redimensionar lista de hilos/i,
    });

    // +320 → 700px: por encima del viejo max de 600.
    drag(handle, 0, 320);
    expect(screen.getByTestId("width")).toHaveTextContent("700");

    // Arrastre exagerado → clamp en 800.
    drag(handle, 0, 900);
    expect(screen.getByTestId("width")).toHaveTextContent("800");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("800");
  });

  it("restaura el ancho persistido al montar de nuevo", () => {
    window.localStorage.setItem(STORAGE_KEY, "640");
    render(<Harness />);
    expect(screen.getByTestId("width")).toHaveTextContent("640");
  });
});
