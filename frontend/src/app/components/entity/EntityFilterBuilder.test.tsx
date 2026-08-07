import { render, screen } from "@testing-library/react";
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

/** Un árbol IR con las reglas hoja indicadas (todas AND). */
function irWith(
  ...rules: { field: string; comparator: string; value: unknown }[]
) {
  return {
    operator: "AND",
    children: rules.map((r) => ({ type: "rule", ...r })),
  };
}

function renderBuilder(value: Record<string, unknown> = {}) {
  const onChange = jest.fn();
  render(<EntityFilterBuilder fields={SCHEMA} value={value} onChange={onChange} />);
  return { onChange };
}

describe("EntityFilterBuilder · CRM-1.6-fix1", () => {
  it("agrupa los campos por sección en <optgroup> del selector", async () => {
    const user = userEvent.setup();
    renderBuilder();
    // El <select> de campo solo aparece cuando hay una regla; se añade una
    // con el botón «+ Rule» del propio querybuilder (ya no hay accordion).
    await user.click(screen.getByRole("button", { name: "+ Rule" }));
    const labels = [...document.querySelectorAll("optgroup")].map((o) =>
      o.getAttribute("label"),
    );
    expect(labels).toEqual(
      expect.arrayContaining(["Datos del contacto", "Dirección", "Llamadas"]),
    );
  });

  it("no renderiza el panel accordion-guía retirado", () => {
    renderBuilder(irWith({ field: "commercial_status", comparator: "eq", value: "new" }));
    // Las cabeceras de sección del accordion ya no existen.
    expect(document.querySelector(".efb-sections")).toBeNull();
    expect(document.querySelector(".efb-section-header")).toBeNull();
    // Tampoco los botones «+ Campo» que duplicaban el desplegable.
    expect(screen.queryByRole("button", { name: "+ Estado comercial" }))
      .not.toBeInTheDocument();
  });

  it("el chip stack muestra los filtros aplicados desde el value inicial", () => {
    renderBuilder(
      irWith(
        { field: "commercial_status", comparator: "eq", value: "new" },
        { field: "address_city", comparator: "eq", value: "Madrid" },
      ),
    );
    expect(screen.getByText(/Estado comercial es igual a new/)).toBeInTheDocument();
    expect(screen.getByText(/Ciudad es igual a Madrid/)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /Quitar filtro/ }).length).toBe(2);
  });

  it("la «×» del chip quita ese filtro; «Limpiar todo» los quita todos", async () => {
    const user = userEvent.setup();
    const { onChange } = renderBuilder(
      irWith(
        { field: "commercial_status", comparator: "eq", value: "new" },
        { field: "address_city", comparator: "eq", value: "Madrid" },
      ),
    );
    expect(screen.getAllByRole("button", { name: /Quitar filtro/ }).length).toBe(2);

    // Quitar uno por su ×.
    await user.click(screen.getAllByRole("button", { name: /Quitar filtro/ })[0]);
    expect(screen.getAllByRole("button", { name: /Quitar filtro/ }).length).toBe(1);
    expect(onChange).toHaveBeenCalled();

    // Limpiar todo.
    await user.click(screen.getByRole("button", { name: "Limpiar todo" }));
    expect(screen.queryByRole("button", { name: /Quitar filtro/ }))
      .not.toBeInTheDocument();
  });
});
