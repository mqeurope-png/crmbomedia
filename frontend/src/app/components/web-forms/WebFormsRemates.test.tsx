import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { apiFetch, getUsers } from "../../lib/api";
import { WebFormEditor } from "./WebFormEditor";
import { WebFormEmbedCode } from "./WebFormEmbedCode";

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

beforeEach(() => {
  push.mockClear();
  mockFetch.mockReset();
  mockFetch.mockImplementation((path: string) => {
    if (path === "/api/admin/contact-fields-mappable") {
      return Promise.resolve({
        standard: [
          { value: "contact.email", label: "Email", type: "email", group: "standard" },
          { value: "contact.first_name", label: "Nombre", type: "text", group: "standard" },
          { value: "contact.marketing_consent", label: "Consentimiento comercial",
            type: "checkbox", group: "standard" },
        ],
        custom: [],
      });
    }
    if (path === "/api/admin/forms/preview") {
      return Promise.resolve({ html: "<html><body>vista</body></html>" });
    }
    if (path === "/api/email-templates") return Promise.resolve([]);
    if (path.startsWith("/api/admin/tags-selectable")) return Promise.resolve([]);
    return Promise.resolve({ id: "form-1", fields: [] });
  });
  (getUsers as jest.Mock).mockResolvedValue([]);
});

function cuerpoDe(path: string, method = "POST") {
  const call = mockFetch.mock.calls.find(
    ([p, init]) => p === path && (init?.method ?? "GET") === method,
  );
  return call ? JSON.parse(call[1].body) : null;
}

describe("Editor de formularios — remates", () => {
  it("un campo de etiquetas pierde el mapeo y no se puede mapear", async () => {
    const user = userEvent.setup();
    render(<WebFormEditor formId="new" />);
    await screen.findAllByRole("option", { name: "Nombre" });
    // El campo 1 (nombre) viene mapeado a contact.first_name.
    expect(screen.getByLabelText("Mapear campo 1")).toHaveValue("contact.first_name");
    await user.selectOptions(screen.getByLabelText("Tipo campo 1"), "tags");
    expect(screen.queryByLabelText("Mapear campo 1")).toBeNull();
    await user.type(screen.getByLabelText("Slug"), "f-tags");
    await user.click(screen.getByRole("button", { name: /Guardar formulario/i }));
    await waitFor(() => expect(cuerpoDe("/api/admin/forms")).not.toBeNull());
    expect(cuerpoDe("/api/admin/forms").fields[0].maps_to_contact_field).toBeNull();
  });

  it("el consentimiento comercial solo se ofrece en una casilla", async () => {
    const user = userEvent.setup();
    render(<WebFormEditor formId="new" />);
    await screen.findAllByRole("option", { name: "Nombre" });
    const opciones = (n: number) =>
      within(screen.getByLabelText(`Mapear campo ${n}`)).queryByRole("option", {
        name: "Consentimiento comercial",
      });
    expect(opciones(1)).toBeNull();
    await user.selectOptions(screen.getByLabelText("Tipo campo 1"), "checkbox");
    expect(opciones(1)).not.toBeNull();
  });

  it("guarda «Activo» y la apariencia, y la vista previa la pide al servidor", async () => {
    const user = userEvent.setup();
    render(<WebFormEditor formId="new" />);
    await user.click(screen.getByLabelText(/Activo/));
    await user.type(screen.getByLabelText("Ancho (%)"), "50");
    await user.selectOptions(screen.getByLabelText("Alineación"), "center");
    await user.type(screen.getByLabelText("Texto del botón"), "Pide información");
    expect(await screen.findByTitle("Vista previa del formulario", {}, { timeout: 2000 }))
      .toBeInTheDocument();
    await waitFor(() => {
      const prev = cuerpoDe("/api/admin/forms/preview");
      expect(prev?.appearance).toMatchObject({ width_pct: 50, align: "center" });
    }, { timeout: 2000 });
    await user.type(screen.getByLabelText("Slug"), "f-ap");
    await user.click(screen.getByRole("button", { name: /Guardar formulario/i }));
    await waitFor(() => expect(cuerpoDe("/api/admin/forms")).not.toBeNull());
    const body = cuerpoDe("/api/admin/forms");
    expect(body.is_active).toBe(false);
    expect(body.appearance).toMatchObject({
      width_pct: 50, align: "center", submit_text: "Pide información",
    });
  });
});

describe("Respaldo de la marca (embed por web)", () => {
  it("solo se ofrece cuando el formulario tiene marca, y se guarda", async () => {
    const user = userEvent.setup();
    render(<WebFormEditor formId="new" />);
    expect(screen.queryByLabelText(/Respaldo de/)).toBeNull();
    await user.type(screen.getByLabelText("Marca"), "mbolasers");
    const casilla = screen.getByLabelText(/Respaldo de «mbolasers»/);
    await user.click(casilla);
    await user.type(screen.getByLabelText("Slug"), "mbolasers-es");
    await user.click(screen.getByRole("button", { name: /Guardar formulario/i }));
    await waitFor(() => expect(cuerpoDe("/api/admin/forms")).not.toBeNull());
    expect(cuerpoDe("/api/admin/forms").is_brand_default).toBe(true);
  });
});

describe("Código de inserción de un formulario desactivado", () => {
  const embed = {
    script_snippet: "<script></script>",
    iframe_snippet: "<iframe></iframe>",
    html_snippet: "<form></form>",
    is_active: false,
  };

  it("avisa de que no se verá hasta activarlo, también al copiar", async () => {
    const user = userEvent.setup();
    render(<WebFormEmbedCode embed={embed} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/desactivado/);
    await user.click(screen.getByRole("button", { name: /Copiar Script JS/i }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Copiado, pero no se verá en la web hasta activar el formulario.",
    );
  });

  it("activo: sin aviso", () => {
    render(<WebFormEmbedCode embed={{ ...embed, is_active: true }} />);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
