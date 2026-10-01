import { fireEvent, render, screen } from "@testing-library/react";
import { ScrollTable } from "./ScrollTable";

/** Tabla con scroll propio: región enfocable, offsets de las columnas fijas
 *  medidos de su cabecera (en orden) y sombreado de bordes según el scroll.
 *  jsdom no maqueta: se simulan los anchos y el scroll. */

function setBox(el: HTMLElement, box: Partial<Record<"scrollLeft" | "scrollWidth" | "clientWidth", number>>) {
  for (const [k, v] of Object.entries(box)) {
    Object.defineProperty(el, k, { configurable: true, value: v });
  }
}

function Table() {
  return (
    <table>
      <thead>
        <tr>
          <th className="sticky-l sticky-l-0" data-w="30">☐</th>
          <th className="sticky-l sticky-l-1" data-w="104">Situación</th>
          <th className="sticky-l sticky-l-2 sticky-l-last" data-w="100">Nº</th>
          <th>Otra</th>
          <th className="sticky-r">Acciones</th>
        </tr>
      </thead>
      <tbody><tr><td>1</td><td>2</td><td>3</td><td>4</td><td>5</td></tr></tbody>
    </table>
  );
}

let offsetSpy: jest.SpyInstance;
beforeEach(() => {
  offsetSpy = jest.spyOn(HTMLElement.prototype, "offsetWidth", "get")
    .mockImplementation(function (this: HTMLElement) {
      return Number(this.getAttribute("data-w") ?? 0);
    });
});
afterEach(() => offsetSpy.mockRestore());

describe("ScrollTable", () => {
  it("es una región con nombre y enfocable que contiene UNA tabla", () => {
    render(<ScrollTable label="Tabla de prueba"><Table /></ScrollTable>);
    const region = screen.getByRole("region", { name: "Tabla de prueba" });
    expect(region).toHaveClass("scroll-table");
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region.querySelectorAll("table")).toHaveLength(1);
    expect(region.parentElement).toHaveClass("scroll-table-frame");
  });

  it("pasa a la tabla el `left` de cada columna fija, acumulando los anchos en orden", () => {
    render(<ScrollTable label="T"><Table /></ScrollTable>);
    const table = screen.getByRole("table");
    expect(table.style.getPropertyValue("--sl-0")).toBe("0px");
    expect(table.style.getPropertyValue("--sl-1")).toBe("30px");
    expect(table.style.getPropertyValue("--sl-2")).toBe("134px");
    expect(table.style.getPropertyValue("--sl-3")).toBe("");
  });

  it("marca los bordes con columnas escondidas (sombreado) según el scroll", () => {
    render(<ScrollTable label="T" className="extra"><Table /></ScrollTable>);
    const region = screen.getByRole("region", { name: "T" });
    const frame = region.parentElement as HTMLElement;
    expect(frame).toHaveClass("extra");

    setBox(region, { scrollLeft: 0, scrollWidth: 1800, clientWidth: 1200 });
    fireEvent.scroll(region);
    expect(frame).toHaveClass("has-more-right");
    expect(frame).not.toHaveClass("is-scrolled-left");

    setBox(region, { scrollLeft: 300 });
    fireEvent.scroll(region);
    expect(frame).toHaveClass("has-more-right", "is-scrolled-left");

    setBox(region, { scrollLeft: 600 });
    fireEvent.scroll(region);
    expect(frame).not.toHaveClass("has-more-right");
    expect(frame).toHaveClass("is-scrolled-left");
  });

  it("ajusta la altura máxima a lo que queda de pantalla (con un mínimo) salvo que se desactive", () => {
    const { unmount } = render(<ScrollTable label="T"><Table /></ScrollTable>);
    // jsdom: innerHeight 768 y top 0 → 768 − 16.
    expect(screen.getByRole("region").style.getPropertyValue("--scroll-table-max-h")).toBe("752px");
    unmount();
    render(<ScrollTable label="T" fitViewport={false}><Table /></ScrollTable>);
    expect(screen.getByRole("region").style.getPropertyValue("--scroll-table-max-h")).toBe("");
  });
});
