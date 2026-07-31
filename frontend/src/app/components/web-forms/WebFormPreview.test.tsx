import { render, screen } from "@testing-library/react";
import type { FormField } from "../../lib/formsApi";
import { WebFormPreview } from "./WebFormPreview";

function field(over: Partial<FormField>): FormField {
  return {
    field_key: "k", label: "L", field_type: "text",
    is_required: false, is_hidden: false, options: [], position: 0, ...over,
  };
}

describe("WebFormPreview", () => {
  it("renderiza los campos visibles según su tipo", () => {
    const { container } = render(<WebFormPreview name="Contacto" fields={[
      field({ field_key: "email", label: "Email", field_type: "email", position: 0 }),
      field({ field_key: "msg", label: "Mensaje", field_type: "textarea", position: 1 }),
    ]} />);
    expect(screen.getByText("Contacto")).toBeInTheDocument();
    expect(screen.getByText("Email")).toBeInTheDocument();
    expect(screen.getByText("Mensaje")).toBeInTheDocument();
    // El tipo del control se corresponde con field_type.
    expect(container.querySelector('input[type="email"]')).toBeInTheDocument();
    expect(container.querySelector("textarea")).toBeInTheDocument();
  });

  it("marca los campos obligatorios con asterisco", () => {
    render(<WebFormPreview name="F" fields={[
      field({ field_key: "email", label: "Email", is_required: true }),
    ]} />);
    expect(screen.getByText("*")).toBeInTheDocument();
  });

  it("excluye los campos ocultos (UTM)", () => {
    render(<WebFormPreview name="F" fields={[
      field({ field_key: "utm", label: "utm_source", is_hidden: true, position: 0 }),
      field({ field_key: "name", label: "Nombre", position: 1 }),
    ]} />);
    expect(screen.queryByText("utm_source")).not.toBeInTheDocument();
    expect(screen.getByText("Nombre")).toBeInTheDocument();
  });

  it("renderiza las opciones de un select", () => {
    render(<WebFormPreview name="F" fields={[
      field({ field_key: "p", label: "Producto", field_type: "select",
        options: [{ value: "a", label: "6090" }, { value: "b", label: "1390" }] }),
    ]} />);
    expect(screen.getByRole("option", { name: "6090" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "1390" })).toBeInTheDocument();
  });

  it("un campo tipo stars renderiza 5 estrellas clickeables", () => {
    render(<WebFormPreview name="F" fields={[
      field({ field_key: "valoracion", label: "Valoración", field_type: "stars" }),
    ]} />);
    // 5 radios (clickeables) + el grupo con rol radiogroup.
    expect(screen.getAllByRole("radio")).toHaveLength(5);
    expect(screen.getByRole("radiogroup", { name: "Valoración" })).toBeInTheDocument();
  });
});
