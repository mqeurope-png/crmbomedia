import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ColumnPicker, readHiddenColumns, storeHiddenColumns } from "./ColumnPicker";

/** «Columnas»: panel de casillas (orden fijo), cierre con Escape, y si a la
 *  derecha del botón no cabe, el panel se alinea a su borde derecho. */

const COLS = [
  { key: "a", label: "Alfa" },
  { key: "b", label: "Beta", locked: true },
  { key: "c", label: "Gamma" },
];

function rect(right: number): DOMRect {
  return { left: right - 220, right, top: 0, bottom: 100, width: 220, height: 100, x: right - 220, y: 0, toJSON: () => ({}) } as DOMRect;
}

describe("ColumnPicker", () => {
  it("marca, respeta las bloqueadas, cuenta las visibles y se cierra con Escape", async () => {
    const user = userEvent.setup();
    const onChange = jest.fn();
    const { rerender } = render(<ColumnPicker columns={COLS} hidden={new Set()} onChange={onChange} />);
    const trigger = screen.getByRole("button", { name: "Columnas" });
    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("checkbox", { name: "Mostrar columna Beta" })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: "Mostrar columna Gamma" }));
    expect(onChange).toHaveBeenLastCalledWith(new Set(["c"]));
    rerender(<ColumnPicker columns={COLS} hidden={new Set(["c"])} onChange={onChange} />);
    expect(screen.getByRole("button", { name: "Columnas (2/3)" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("group", { name: "Columnas visibles" })).toBeNull();
    expect(screen.getByRole("button", { name: "Columnas (2/3)" })).toHaveFocus();
  });

  it("si el panel se saldría por la derecha de la página, se alinea al borde derecho del botón", async () => {
    const user = userEvent.setup();
    const spy = jest.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      if (this.tagName === "MAIN") return rect(800);
      if (this.classList.contains("column-picker-pop")) return rect(900);
      return rect(0);
    });
    render(<main><ColumnPicker columns={COLS} hidden={new Set()} onChange={() => {}} /></main>);
    await user.click(screen.getByRole("button", { name: "Columnas" }));
    const pop = screen.getByRole("group", { name: "Columnas visibles" });
    // (jsdom no guarda `left: auto`; en el navegador se comprueba aparte.)
    expect(pop.style.right).toBe("0px");
    spy.mockRestore();
  });

  it("si cabe, no se toca la posición", async () => {
    const user = userEvent.setup();
    const spy = jest.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      if (this.tagName === "MAIN") return rect(1000);
      if (this.classList.contains("column-picker-pop")) return rect(600);
      return rect(0);
    });
    render(<main><ColumnPicker columns={COLS} hidden={new Set()} onChange={() => {}} /></main>);
    await user.click(screen.getByRole("button", { name: "Columnas" }));
    expect(screen.getByRole("group", { name: "Columnas visibles" }).style.right).toBe("");
    spy.mockRestore();
  });

  it("guarda y lee las ocultas; tolera valores corruptos y claves desconocidas", () => {
    window.localStorage.clear();
    storeHiddenColumns("k", new Set(["a", "c"]));
    expect(readHiddenColumns("k", ["a", "b", "c"])).toEqual(new Set(["a", "c"]));
    expect(readHiddenColumns("k", ["a"])).toEqual(new Set(["a"]));
    window.localStorage.setItem("k", "{roto");
    expect(readHiddenColumns("k", ["a"])).toEqual(new Set());
    window.localStorage.setItem("k", JSON.stringify({ a: 1 }));
    expect(readHiddenColumns("k", ["a"])).toEqual(new Set());
  });
});
