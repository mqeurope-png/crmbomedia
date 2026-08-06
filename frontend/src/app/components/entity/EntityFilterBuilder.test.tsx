import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EntityFilterBuilder } from "./EntityFilterBuilder";
import type { FieldDescriptor } from "../../lib/entitySchema";

function field(over: Partial<FieldDescriptor> & { key: string }): FieldDescriptor {
  return {
    label: over.key, type: "string", comparators: ["eq"], enum_values: [],
    sortable: false, displayable: true, filterable: true, default_visible: false,
    grouped_under: "General", source: "column", reference_table: null,
    ...over,
  };
}

const SCHEMA: FieldDescriptor[] = [
  field({ key: "tags", label: "Tags", grouped_under: "Datos del contacto",
          type: "tag-multi", comparators: ["contains_any"] }),
  field({ key: "commercial_status", label: "Estado comercial",
          grouped_under: "Datos del contacto", type: "enum",
          comparators: ["eq"], enum_values: ["new", "won"] }),
  field({ key: "address_city", label: "Ciudad", grouped_under: "Dirección" }),
  field({ key: "call_result", label: "Resultado de llamada",
          grouped_under: "Llamadas", type: "enum", comparators: ["in"],
          enum_values: ["contacted", "interested"] }),
];

const KEY = "test-filter-sections";

beforeEach(() => window.localStorage.clear());

function renderBuilder(over: Record<string, unknown> = {}) {
  const onChange = jest.fn();
  render(
    <EntityFilterBuilder
      fields={SCHEMA}
      value={{}}
      onChange={onChange}
      sectionsStorageKey={KEY}
      defaultOpenSections={["Datos del contacto"]}
      {...over}
    />,
  );
  return { onChange };
}

describe("EntityFilterBuilder · CRM-1.6", () => {
  it("agrupa los campos por sección en optgroups del selector", async () => {
    const user = userEvent.setup();
    renderBuilder();
    // El <select> de campo solo aparece cuando hay una regla; se añade una.
    await user.click(screen.getByRole("button", { name: "+ Tags" }));
    const labels = [...document.querySelectorAll("optgroup")].map((o) =>
      o.getAttribute("label"),
    );
    expect(labels).toEqual(
      expect.arrayContaining(["Datos del contacto", "Dirección", "Llamadas"]),
    );
  });

  it("muestra las 7 secciones; solo las default abiertas", () => {
    renderBuilder();
    // «Datos del contacto» abierta → sus campos visibles como botones de añadir.
    expect(screen.getByRole("button", { name: "+ Tags" })).toBeInTheDocument();
    // «Llamadas» cerrada → su campo no visible.
    expect(screen.queryByRole("button", { name: "+ Resultado de llamada" }))
      .not.toBeInTheDocument();
  });

  it("expandir una sección revela sus campos", async () => {
    const user = userEvent.setup();
    renderBuilder();
    await user.click(screen.getByRole("button", { name: /Llamadas/ }));
    expect(screen.getByRole("button", { name: "+ Resultado de llamada" }))
      .toBeInTheDocument();
  });

  it("añadir un filtro crea un chip y actualiza el contador de sección", async () => {
    const user = userEvent.setup();
    const { onChange } = renderBuilder();
    await user.click(screen.getByRole("button", { name: "+ Estado comercial" }));

    // Chip aplicado arriba.
    expect(screen.getByText(/Estado comercial es igual a/)).toBeInTheDocument();
    // Contador de la sección pasa a (1).
    const header = screen.getByRole("button", { name: /Datos del contacto/ });
    expect(within(header).getByText("(1)")).toBeInTheDocument();
    // El onChange emitió el árbol.
    expect(onChange).toHaveBeenCalled();
  });

  it("la «×» del chip quita ese filtro; «Limpiar todo» los quita todos", async () => {
    const user = userEvent.setup();
    renderBuilder();
    await user.click(screen.getByRole("button", { name: "+ Estado comercial" }));
    await user.click(screen.getByRole("button", { name: "+ Tags" }));
    expect(screen.getAllByRole("button", { name: /Quitar filtro/ }).length).toBe(2);

    // Quitar uno por su ×.
    await user.click(screen.getAllByRole("button", { name: /Quitar filtro/ })[0]);
    expect(screen.getAllByRole("button", { name: /Quitar filtro/ }).length).toBe(1);

    // Limpiar todo.
    await user.click(screen.getByRole("button", { name: "Limpiar todo" }));
    expect(screen.queryByRole("button", { name: /Quitar filtro/ }))
      .not.toBeInTheDocument();
  });

  it("persiste el estado abierto/cerrado en localStorage", async () => {
    const user = userEvent.setup();
    renderBuilder();
    // Abrir «Llamadas», que arranca cerrada.
    await user.click(screen.getByRole("button", { name: /Llamadas/ }));
    const stored = JSON.parse(window.localStorage.getItem(KEY) || "[]");
    expect(stored).toEqual(
      expect.arrayContaining(["Datos del contacto", "Llamadas"]),
    );
  });

  it("al recargar, restaura las secciones guardadas en localStorage", () => {
    window.localStorage.setItem(KEY, JSON.stringify(["Llamadas"]));
    renderBuilder();
    // «Llamadas» abierta (de localStorage), «Datos del contacto» NO (el
    // localStorage manda sobre los defaults).
    expect(screen.getByRole("button", { name: "+ Resultado de llamada" }))
      .toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "+ Tags" }))
      .not.toBeInTheDocument();
  });
});
