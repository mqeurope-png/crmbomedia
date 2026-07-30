import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { apiFetch } from "../../lib/api";
import { WebFormEditor } from "./WebFormEditor";

const push = jest.fn();

jest.mock("../../lib/api", () => ({
  apiFetch: jest.fn().mockResolvedValue({ id: "form-1", fields: [] }),
  getUsers: jest.fn().mockResolvedValue([]),
}));
jest.mock("../../lib/errors", () => ({
  extractErrorMessage: (_e: unknown, f: string) => f,
}));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const mockFetch = apiFetch as jest.Mock;

describe("WebFormEditor", () => {
  beforeEach(() => {
    push.mockClear();
    mockFetch.mockClear();
    mockFetch.mockResolvedValue({ id: "form-1", fields: [] });
  });

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
    // name, email → bajar el primero → email, name.
    await user.click(screen.getByRole("button", { name: /Bajar campo 1/i }));
    expect(screen.getByLabelText("Clave campo 1")).toHaveValue("email");
    expect(screen.getByLabelText("Clave campo 2")).toHaveValue("name");
  });

  it("guarda enviando el payload correcto y redirige al embed", async () => {
    const user = userEvent.setup();
    render(<WebFormEditor formId="new" />);
    await user.type(screen.getByLabelText("Slug"), "contacto-mbo-es");
    await user.click(screen.getByRole("button", { name: /Guardar formulario/i }));

    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    const [path, init] = mockFetch.mock.calls[0];
    expect(path).toBe("/api/admin/forms");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body);
    expect(body.slug).toBe("contacto-mbo-es");
    expect(body.fields).toHaveLength(2);
    expect(body.fields[0].position).toBe(0);
    await waitFor(() => expect(push).toHaveBeenCalledWith("/admin/forms/form-1/embed"));
  });
});
