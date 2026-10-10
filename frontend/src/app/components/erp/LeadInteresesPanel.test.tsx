import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { LeadInteresesPanel } from "./LeadInteresesPanel";
import {
  createLeadInteres,
  deleteLeadInteres,
  updateLeadInteres,
  type LeadInteresConUso,
} from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  createLeadInteres: jest.fn(),
  updateLeadInteres: jest.fn(),
  deleteLeadInteres: jest.fn(),
}));

const mockCreate = createLeadInteres as jest.Mock;
const mockUpdate = updateLeadInteres as jest.Mock;
const mockDelete = deleteLeadInteres as jest.Mock;

function interes(over: Partial<LeadInteresConUso> & { id: string; label: string }): LeadInteresConUso {
  return {
    codigo: over.id, etiqueta: over.label, descripcion: "", comercial: true, orden: 0, activo: true,
    en_uso: { clasificaciones: 0, en_mapa: 0 }, ...over,
  };
}

const ITEMS = [
  interes({ id: "vending", label: "Vending", descripcion: "Máquinas expendedoras.", orden: 0,
            en_uso: { clasificaciones: 3, en_mapa: 1 } }),
  interes({ id: "smartjet", label: "SmartJet", orden: 1 }),
  interes({ id: "otro", label: "Otro", comercial: false, orden: 2 }),
];

beforeEach(() => {
  mockCreate.mockReset();
  mockUpdate.mockReset();
  mockDelete.mockReset();
});

/** Como la pantalla: el padre guarda la lista y la sustituye con lo que el
 *  panel le devuelve (la fila guardada, la lista sin la borrada). */
function Panel({ onChange, items = ITEMS }: { onChange: jest.Mock; items?: LeadInteresConUso[] }) {
  const [lista, setLista] = useState<LeadInteresConUso[] | null>(items);
  return (
    <LeadInteresesPanel
      items={lista}
      error={null}
      onChange={(next) => { onChange(next); setLista(next); }}
    />
  );
}

describe("LeadInteresesPanel (el catálogo de intereses del clasificador)", () => {
  it("enseña el catálogo con su uso: lo que está en uso no se borra, «otro» es fijo", () => {
    render(<LeadInteresesPanel items={ITEMS} error={null} onChange={jest.fn()} />);
    expect(screen.getByLabelText("Etiqueta de vending")).toHaveValue("Vending");
    expect(screen.getByLabelText("Descripción de vending")).toHaveValue("Máquinas expendedoras.");
    expect(screen.getByText("3 leads · 1 fila del mapa")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Borrar vending" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Borrar smartjet" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Borrar otro" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Activo otro")).toBeDisabled();
    expect(screen.getByLabelText("Comercial otro")).not.toBeChecked();
    // Sin cambios no hay «Guardar».
    expect(screen.queryByRole("button", { name: /^Guardar / })).not.toBeInTheDocument();
  });

  it("editar la descripción y guardar manda solo lo que cambia y actualiza la lista", async () => {
    const onChange = jest.fn();
    mockUpdate.mockImplementation((codigo: string, cambios: Record<string, unknown>) =>
      Promise.resolve(interes({ ...ITEMS[1], id: "smartjet", label: "SmartJet",
                                descripcion: String(cambios.descripcion) })));
    const user = userEvent.setup();
    render(<Panel onChange={onChange} />);
    await user.type(screen.getByLabelText("Descripción de smartjet"), "Objetos cilíndricos.");
    await user.click(screen.getByRole("button", { name: "Guardar smartjet" }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith("smartjet", {
      descripcion: "Objetos cilíndricos.",
    }));
    expect(onChange).toHaveBeenCalledTimes(1);
    const lista = onChange.mock.calls[0][0] as LeadInteresConUso[];
    expect(lista.find((i) => i.codigo === "smartjet")?.descripcion).toBe("Objetos cilíndricos.");
    // Guardado: los campos arrancan de lo devuelto, sin «Guardar» pendiente.
    expect(screen.queryByRole("button", { name: "Guardar smartjet" })).not.toBeInTheDocument();
  });

  it("desactivar viaja al momento y borrar lo que no se usa lo quita de la lista", async () => {
    const onChange = jest.fn();
    mockUpdate.mockResolvedValue(interes({ ...ITEMS[0], id: "vending", label: "Vending",
                                           activo: false }));
    mockDelete.mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<Panel onChange={onChange} />);
    await user.click(screen.getByLabelText("Activo vending"));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith("vending", { activo: false }));
    expect((onChange.mock.calls[0][0] as LeadInteresConUso[])
      .find((i) => i.codigo === "vending")?.activo).toBe(false);
    expect(screen.getByLabelText("Activo vending")).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: "Borrar smartjet" }));
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("smartjet"));
    expect((onChange.mock.calls[1][0] as LeadInteresConUso[]).map((i) => i.codigo))
      .toEqual(["vending", "otro"]);
    expect(screen.queryByLabelText("Etiqueta de smartjet")).not.toBeInTheDocument();
  });

  it("un borrado rechazado por el servidor (en uso) se dice en la fila y la fila se queda", async () => {
    const onChange = jest.fn();
    mockDelete.mockRejectedValue(new Error(
      "El interés «smartjet» está en uso (2 clasificaciones): desactívalo en vez de borrarlo.",
    ));
    const user = userEvent.setup();
    render(<Panel onChange={onChange} />);
    await user.click(screen.getByRole("button", { name: "Borrar smartjet" }));
    const fila = screen.getByLabelText("Etiqueta de smartjet").closest("tr")!;
    expect(await within(fila).findByRole("alert")).toHaveTextContent("desactívalo en vez de borrarlo");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("añadir un interés manda código, etiqueta, descripción y comercial, y entra en la lista", async () => {
    const onChange = jest.fn();
    mockCreate.mockResolvedValue(interes({ id: "packaging", label: "Packaging", orden: 3,
                                           descripcion: "Cajas y embalajes." }));
    const user = userEvent.setup();
    render(<Panel onChange={onChange} />);
    const boton = screen.getByRole("button", { name: "Añadir interés" });
    expect(boton).toBeDisabled();                       // sin código ni etiqueta
    await user.type(screen.getByLabelText("Código del interés nuevo"), "packaging");
    await user.type(screen.getByLabelText("Etiqueta del interés nuevo"), "Packaging");
    await user.type(screen.getByLabelText("Descripción del interés nuevo"), "Cajas y embalajes.");
    await user.click(boton);
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith({
      codigo: "packaging", etiqueta: "Packaging", descripcion: "Cajas y embalajes.", comercial: true,
    }));
    expect((onChange.mock.calls[0][0] as LeadInteresConUso[]).map((i) => i.codigo))
      .toEqual(["vending", "smartjet", "otro", "packaging"]);
    expect(await screen.findByRole("status")).toHaveTextContent("Interés «Packaging» añadido");
    expect(screen.getByLabelText("Etiqueta de packaging")).toHaveValue("Packaging");
    expect(screen.getByLabelText("Código del interés nuevo")).toHaveValue("");
  });

  it("el error de alta (código repetido) se enseña y la lista no cambia", async () => {
    const onChange = jest.fn();
    mockCreate.mockRejectedValue(new Error("Ya hay un interés con el código «vending»."));
    const user = userEvent.setup();
    render(<Panel onChange={onChange} />);
    await user.type(screen.getByLabelText("Código del interés nuevo"), "vending");
    await user.type(screen.getByLabelText("Etiqueta del interés nuevo"), "Otra vez");
    await user.click(screen.getByRole("button", { name: "Añadir interés" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Ya hay un interés con el código");
    expect(onChange).not.toHaveBeenCalled();
  });
});
