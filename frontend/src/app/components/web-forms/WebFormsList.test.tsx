import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { listForms } from "../../lib/formsApi";
import { WebFormsList } from "./WebFormsList";

jest.mock("../../lib/formsApi", () => ({ listForms: jest.fn() }));
jest.mock("../../lib/errors", () => ({
  extractErrorMessage: (_e: unknown, f: string) => f,
}));

const mockList = listForms as jest.Mock;

const rows = [
  { id: "1", slug: "contacto-mbo-es", name: "MBO ES", brand: "mbo",
    language: "es", is_active: true, submissions_total: 5, submissions_spam: 1,
    created_at: "2026-07-01T00:00:00Z" },
  { id: "2", slug: "contacto-artis-en", name: "Artis EN", brand: "artis",
    language: "en", is_active: true, submissions_total: 2, submissions_spam: 0,
    created_at: "2026-07-02T00:00:00Z" },
];

describe("WebFormsList", () => {
  beforeEach(() => mockList.mockResolvedValue(rows));

  it("renderiza los formularios cargados", async () => {
    render(<WebFormsList />);
    expect(await screen.findByText("MBO ES")).toBeInTheDocument();
    expect(screen.getByText("Artis EN")).toBeInTheDocument();
  });

  it("filtra por marca", async () => {
    const user = userEvent.setup();
    render(<WebFormsList />);
    await screen.findByText("MBO ES");
    await user.selectOptions(screen.getByRole("combobox", { name: /Marca/i }), "mbo");
    await waitFor(() => expect(screen.queryByText("Artis EN")).not.toBeInTheDocument());
    expect(screen.getByText("MBO ES")).toBeInTheDocument();
  });

  it("tiene botón de crear formulario", async () => {
    render(<WebFormsList />);
    await screen.findByText("MBO ES");
    const create = screen.getByRole("link", { name: /Crear formulario/i });
    expect(create).toHaveAttribute("href", "/admin/forms/new/editor");
  });
});
