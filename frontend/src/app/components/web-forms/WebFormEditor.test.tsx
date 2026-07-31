import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { apiFetch, getUsers } from "../../lib/api";
import { WebFormEditor } from "./WebFormEditor";

const push = jest.fn();

jest.mock("../../lib/api", () => ({
  apiFetch: jest.fn(),
  getUsers: jest.fn(),
}));
jest.mock("../../lib/errors", () => ({
  extractErrorMessage: (_e: unknown, f: string) => f,
}));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const mockFetch = apiFetch as jest.Mock;
const mockUsers = getUsers as jest.Mock;

beforeEach(() => {
  push.mockClear();
  mockFetch.mockReset();
  mockFetch.mockImplementation((path: string) => {
    if (path === "/api/admin/contact-fields-mappable") {
      return Promise.resolve({
        standard: [
          { value: "contact.email", label: "Email", type: "email", group: "standard" },
          { value: "contact.first_name", label: "Nombre", type: "text", group: "standard" },
        ],
        custom: [
          { value: "contact.custom.prod", label: "Producto (personalizado)", type: "text", group: "custom" },
        ],
      });
    }
    if (path === "/api/email-templates") {
      return Promise.resolve([{ id: "tpl-1", name: "Bienvenida", subject: "Hola" }]);
    }
    return Promise.resolve({ id: "form-1", fields: [] });
  });
  mockUsers.mockResolvedValue([
    { id: "u1", full_name: "Norma", email: "norma@x.com", is_active: true, role: "user" },
  ]);
});

describe("WebFormEditor", () => {
  it("arranca con los 2 campos por defecto (nombre + email)", () => {
    render(<WebFormEditor formId="new" />);
    expect(screen.getAllByLabelText(/Clave campo/)).toHaveLength(2);
    expect(screen.getByLabelText("Clave campo 1")).toHaveValue("name");
    expect(screen.getByLabelText("Clave campo 2")).toHaveValue("email");
  });

  it("añade y borra campos", async () => {
    const user = userEvent.setup();
    render(<WebFormEditor formId="new" />);
    await user.click(screen.getByRole("button", { name: /Añadir campo/i }));
    expect(screen.getAllByLabelText(/Clave campo/)).toHaveLength(3);
    await user.click(screen.getByRole("button", { name: /Borrar campo 3/i }));
    expect(screen.getAllByLabelText(/Clave campo/)).toHaveLength(2);
  });

  it("reordena campos con ↑↓", async () => {
    const user = userEvent.setup();
    render(<WebFormEditor formId="new" />);
    await user.click(screen.getByRole("button", { name: /Bajar campo 1/i }));
    expect(screen.getByLabelText("Clave campo 1")).toHaveValue("email");
    expect(screen.getByLabelText("Clave campo 2")).toHaveValue("name");
  });

  it("el dropdown «Mapear a» carga las opciones del endpoint", async () => {
    render(<WebFormEditor formId="new" />);
    // Cada campo tiene su dropdown de mapeo → la opción del endpoint
    // aparece en todos (2 campos por defecto).
    const opts = await screen.findAllByRole("option", { name: "Producto (personalizado)" });
    expect(opts.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByLabelText("Mapear campo 1")).toBeInTheDocument();
  });

  it("un campo select muestra el sub-panel de opciones (añadir/borrar)", async () => {
    const user = userEvent.setup();
    render(<WebFormEditor formId="new" />);
    await user.selectOptions(screen.getByLabelText("Tipo campo 1"), "select");
    const panel = screen.getByLabelText("Opciones campo 1");
    expect(panel).toBeInTheDocument();
    // Añadir 2 opciones.
    await user.click(screen.getByRole("button", { name: "+ Añadir opción" }));
    await user.click(screen.getByRole("button", { name: "+ Añadir opción" }));
    expect(screen.getAllByLabelText(/Valor opción/)).toHaveLength(2);
    // Borrar una.
    await user.click(screen.getByRole("button", { name: /Borrar opción 1 campo 1/i }));
    expect(screen.getAllByLabelText(/Valor opción/)).toHaveLength(1);
  });

  it("bloquea guardar un select sin opciones con error", async () => {
    const user = userEvent.setup();
    render(<WebFormEditor formId="new" />);
    await user.selectOptions(screen.getByLabelText("Tipo campo 1"), "select");
    await user.type(screen.getByLabelText("Slug"), "f-sel");
    await user.click(screen.getByRole("button", { name: /Guardar formulario/i }));
    expect(screen.getByText(/necesita al menos una opción/i)).toBeInTheDocument();
    // No debe haber intentado el POST de guardado.
    expect(mockFetch).not.toHaveBeenCalledWith("/api/admin/forms", expect.anything());
  });

  it("el panel «Más opciones» abre/cierra los inputs extra", async () => {
    const user = userEvent.setup();
    render(<WebFormEditor formId="new" />);
    expect(screen.queryByLabelText("Placeholder campo 1")).not.toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: /Más opciones/i })[0]);
    expect(screen.getByLabelText("Placeholder campo 1")).toBeInTheDocument();
    expect(screen.getByLabelText("Default campo 1")).toBeInTheDocument();
  });

  it("el selector de plantilla email carga templates al activar confirmación", async () => {
    const user = userEvent.setup();
    render(<WebFormEditor formId="new" />);
    await user.click(screen.getByLabelText(/Enviar email de confirmación/i));
    expect(await screen.findByRole("option", { name: "Bienvenida" })).toBeInTheDocument();
  });

  it("el selector de propietario fijo solo aparece si assignment_mode=fixed_owner", async () => {
    const user = userEvent.setup();
    render(<WebFormEditor formId="new" />);
    // blankForm arranca en 'rules' → sin dropdown de owner.
    expect(screen.queryByLabelText("Propietario fijo")).not.toBeInTheDocument();
    // Cambiar a fixed_owner.
    const assignSelect = screen.getByDisplayValue(/Reglas de asignación/i);
    await user.selectOptions(assignSelect, "fixed_owner");
    expect(await screen.findByLabelText("Propietario fijo")).toBeInTheDocument();
  });

  it("guarda enviando el payload correcto (con mapping) y redirige al embed", async () => {
    const user = userEvent.setup();
    render(<WebFormEditor formId="new" />);
    await user.type(screen.getByLabelText("Slug"), "contacto-mbo-es");
    await user.click(screen.getByRole("button", { name: /Guardar formulario/i }));

    await waitFor(() =>
      expect(mockFetch).toHaveBeenCalledWith("/api/admin/forms", expect.objectContaining({ method: "POST" })),
    );
    const call = mockFetch.mock.calls.find((c) => c[0] === "/api/admin/forms");
    const body = JSON.parse(call![1].body);
    expect(body.slug).toBe("contacto-mbo-es");
    expect(body.fields).toHaveLength(2);
    expect(body.fields[1].maps_to_contact_field).toBe("contact.email");
    await waitFor(() => expect(push).toHaveBeenCalledWith("/admin/forms/form-1/embed"));
  });
});
