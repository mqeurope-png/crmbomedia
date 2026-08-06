import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DedupeCompaniesPage from "./page";
import {
  findDuplicateCompanies,
  mergeDuplicateCompanies,
} from "../../lib/companiesApi";

jest.mock("../../lib/companiesApi", () => ({
  findDuplicateCompanies: jest.fn(),
  mergeDuplicateCompanies: jest.fn(),
  // Lógica pura: se usa la real para que el test compruebe de verdad la
  // preselección, no un doble que la invente.
  pickDefaultKeep: jest.requireActual("../../lib/companiesApi").pickDefaultKeep,
}));
const mockFind = findDuplicateCompanies as jest.Mock;
const mockMerge = mergeDuplicateCompanies as jest.Mock;

function company(id: string, over = {}) {
  return {
    id, name: "Exatronic Lda", city: "Aveiro", address_line: null,
    postal_code: null, state: null, country: "Portugal", website: null,
    domain: null, notes: null, factusol_company_id: "2629",
    source: "factusol_import", created_at: "2026-08-06T04:00:00Z",
    contacts_count: 0, orders_count: 0, tasks_count: 0,
    ...over,
  };
}

function duplicates(over = {}) {
  return {
    total_groups: 1, total_companies_involved: 2,
    groups: [{
      tax_id: "PT503420506",
      companies: [
        company("319bad5e", { contacts_count: 1, factusol_company_id: "2629" }),
        company("1a874379", {
          factusol_company_id: "2819", city: null,
          created_at: "2026-08-06T04:00:05Z",
        }),
      ],
    }],
    ...over,
  };
}

beforeEach(() => {
  mockFind.mockReset();
  mockMerge.mockReset();
  mockFind.mockResolvedValue(duplicates());
  mockMerge.mockResolvedValue({
    merged_groups: 1, companies_deleted: 1, contacts_moved: 1,
    orders_moved: 0, tasks_moved: 0,
    results: [{
      keep_id: "319bad5e", merged_ids: ["1a874379"], contacts_moved: 1,
      orders_moved: 0, tasks_moved: 0, filled_fields: [],
      discarded_factusol_codclis: ["2819"],
    }],
    errors: [],
  });
});

describe("DedupeCompaniesPage", () => {
  it("no consulta nada hasta que se pulsa el botón", () => {
    render(<DedupeCompaniesPage />);
    expect(mockFind).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Buscar duplicados por CIF" }))
      .toBeInTheDocument();
  });

  it("pinta los grupos con su CIF y el número de empresas", async () => {
    const user = userEvent.setup();
    render(<DedupeCompaniesPage />);
    await user.click(screen.getByRole("button", { name: "Buscar duplicados por CIF" }));

    await waitFor(() => expect(mockFind).toHaveBeenCalled());
    expect(await screen.findByText(/CIF: PT503420506/)).toBeInTheDocument();
    expect(screen.getByText(/grupo\(s\) de duplicados/))
      .toHaveTextContent("2 empresa(s) afectada(s)");
  });

  it("expandir enseña las empresas del grupo con sus contadores", async () => {
    const user = userEvent.setup();
    render(<DedupeCompaniesPage />);
    await user.click(screen.getByRole("button", { name: "Buscar duplicados por CIF" }));
    await user.click(await screen.findByRole("button", { name: "Ver detalle" }));

    expect(screen.getByText("2629")).toBeInTheDocument();
    expect(screen.getByText("2819")).toBeInTheDocument();
    expect(screen.getAllByText("Exatronic Lda")).toHaveLength(2);
  });

  it("preselecciona como principal la que tiene más contactos", async () => {
    const user = userEvent.setup();
    render(<DedupeCompaniesPage />);
    await user.click(screen.getByRole("button", { name: "Buscar duplicados por CIF" }));
    await user.click(await screen.findByRole("button", { name: "Ver detalle" }));

    expect(screen.getByLabelText(/Mantener Exatronic Lda \(319bad5e\)/))
      .toBeChecked();
    expect(screen.getByLabelText(/Mantener Exatronic Lda \(1a874379\)/))
      .not.toBeChecked();
  });

  it("los pedidos pesan más que los contactos al preseleccionar", async () => {
    mockFind.mockResolvedValue(duplicates({
      groups: [{
        tax_id: "PT503420506",
        companies: [
          company("con-contactos", { contacts_count: 5, orders_count: 0 }),
          company("con-pedidos", { contacts_count: 0, orders_count: 1 }),
        ],
      }],
    }));
    const user = userEvent.setup();
    render(<DedupeCompaniesPage />);
    await user.click(screen.getByRole("button", { name: "Buscar duplicados por CIF" }));
    await user.click(await screen.findByRole("button", { name: "Ver detalle" }));

    expect(screen.getByLabelText(/\(con-pedidos\)/)).toBeChecked();
  });

  it("el operador puede cambiar cuál se mantiene", async () => {
    const user = userEvent.setup();
    render(<DedupeCompaniesPage />);
    await user.click(screen.getByRole("button", { name: "Buscar duplicados por CIF" }));
    await user.click(await screen.findByRole("button", { name: "Ver detalle" }));
    await user.click(screen.getByLabelText(/\(1a874379\)/));
    await user.click(screen.getByLabelText(/Marcar grupo PT503420506/));
    await user.click(screen.getByRole("button", { name: /Fusionar seleccionadas \(1\)/ }));
    await user.click(await screen.findByRole("button", { name: /Sí, fusionar y borrar/ }));

    await waitFor(() => expect(mockMerge).toHaveBeenCalled());
    expect(mockMerge.mock.calls[0][0]).toEqual([
      { keep_id: "1a874379", merge_ids: ["319bad5e"] },
    ]);
  });

  it("marca con un chip lo que aportaría la absorbida", async () => {
    // La segunda no tiene ciudad y la principal sí: no aporta ciudad. Al revés
    // sí. Es lo que decide si vale la pena mirar el grupo.
    mockFind.mockResolvedValue(duplicates({
      groups: [{
        tax_id: "PT503420506",
        companies: [
          company("principal", { contacts_count: 1, city: null, website: null }),
          company("otra", { city: "Aveiro", website: "exatronic.pt" }),
        ],
      }],
    }));
    const user = userEvent.setup();
    render(<DedupeCompaniesPage />);
    await user.click(screen.getByRole("button", { name: "Buscar duplicados por CIF" }));
    await user.click(await screen.findByRole("button", { name: "Ver detalle" }));

    expect(screen.getByText(/aportará: ciudad, web/)).toBeInTheDocument();
    expect(screen.getByText("principal")).toBeInTheDocument();
  });

  it("nada se fusiona sin marcarlo: el botón arranca deshabilitado", async () => {
    const user = userEvent.setup();
    render(<DedupeCompaniesPage />);
    await user.click(screen.getByRole("button", { name: "Buscar duplicados por CIF" }));

    expect(await screen.findByRole("button", { name: /Fusionar seleccionadas \(0\)/ }))
      .toBeDisabled();
  });

  it("el modal dice cuántas empresas se van a BORRAR", async () => {
    mockFind.mockResolvedValue(duplicates({
      groups: [{
        tax_id: "PT503420506",
        companies: [
          // Más pedidos → sale premarcada como principal, así que lo suyo NO
          // se cuenta: no se mueve a ninguna parte.
          company("principal", { contacts_count: 2, orders_count: 9 }),
          company("otra", { contacts_count: 4, orders_count: 5,
                            tasks_count: 6 }),
        ],
      }],
    }));
    const user = userEvent.setup();
    render(<DedupeCompaniesPage />);
    await user.click(screen.getByRole("button", { name: "Buscar duplicados por CIF" }));
    await user.click(await screen.findByLabelText(/Marcar grupo PT503420506/));
    await user.click(screen.getByRole("button", { name: /Fusionar seleccionadas \(1\)/ }));

    const dialog = await screen.findByRole("dialog");
    // Solo cuenta lo que cuelga de las ABSORBIDAS, no de la principal.
    expect(dialog).toHaveTextContent("borrará 1 empresa(s)");
    expect(dialog).toHaveTextContent("4 contacto(s), 5 pedido(s) y 6 tarea(s)");
    expect(mockMerge).not.toHaveBeenCalled();
  });

  it("cancelar el modal no fusiona nada", async () => {
    const user = userEvent.setup();
    render(<DedupeCompaniesPage />);
    await user.click(screen.getByRole("button", { name: "Buscar duplicados por CIF" }));
    await user.click(await screen.findByLabelText(/Marcar grupo PT503420506/));
    await user.click(screen.getByRole("button", { name: /Fusionar seleccionadas/ }));
    await user.click(await screen.findByRole("button", { name: "Cancelar" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockMerge).not.toHaveBeenCalled();
  });

  it("el master marca todos los grupos", async () => {
    mockFind.mockResolvedValue(duplicates({
      total_groups: 2, total_companies_involved: 4,
      groups: [
        { tax_id: "A1", companies: [company("a"), company("b")] },
        { tax_id: "B2", companies: [company("c"), company("d")] },
      ],
    }));
    const user = userEvent.setup();
    render(<DedupeCompaniesPage />);
    await user.click(screen.getByRole("button", { name: "Buscar duplicados por CIF" }));
    await user.click(await screen.findByLabelText("Marcar todos los grupos"));

    expect(screen.getByRole("button", { name: /Fusionar seleccionadas \(2\)/ }))
      .toBeEnabled();
    expect(screen.getByLabelText(/Marcar grupo A1/)).toBeChecked();
    expect(screen.getByLabelText(/Marcar grupo B2/)).toBeChecked();
  });

  it("el master queda indeterminado con selección parcial", async () => {
    mockFind.mockResolvedValue(duplicates({
      groups: [
        { tax_id: "A1", companies: [company("a"), company("b")] },
        { tax_id: "B2", companies: [company("c"), company("d")] },
      ],
    }));
    const user = userEvent.setup();
    render(<DedupeCompaniesPage />);
    await user.click(screen.getByRole("button", { name: "Buscar duplicados por CIF" }));
    const master = await screen.findByLabelText(
      "Marcar todos los grupos") as HTMLInputElement;

    await user.click(screen.getByLabelText(/Marcar grupo A1/));
    expect(master.indeterminate).toBe(true);
    expect(master).not.toBeChecked();

    await user.click(screen.getByLabelText(/Marcar grupo B2/));
    expect(master.indeterminate).toBe(false);
    expect(master).toBeChecked();
  });

  it("el resumen desglosa lo movido y avisa del codcli descartado", async () => {
    const user = userEvent.setup();
    render(<DedupeCompaniesPage />);
    await user.click(screen.getByRole("button", { name: "Buscar duplicados por CIF" }));
    await user.click(await screen.findByLabelText(/Marcar grupo PT503420506/));
    await user.click(screen.getByRole("button", { name: /Fusionar seleccionadas/ }));
    await user.click(await screen.findByRole("button", { name: /Sí, fusionar y borrar/ }));

    const summary = await screen.findByRole("status");
    expect(summary).toHaveTextContent("1 grupo(s) fusionado(s)");
    expect(summary).toHaveTextContent("1 empresa(s) borrada(s)");
    expect(summary).toHaveTextContent("1 contacto(s), 0 pedido(s) y 0 tarea(s)");
    // Puede haber facturación colgando de ese CODCLI: hay que decirlo.
    expect(await screen.findByText(/Códigos FACTUSOL descartados/))
      .toHaveTextContent("2819");
  });

  it("muestra los errores que devuelve el merge", async () => {
    mockMerge.mockResolvedValue({
      merged_groups: 0, companies_deleted: 0, contacts_moved: 0,
      orders_moved: 0, tasks_moved: 0, results: [],
      errors: [{ keep_id: "319bad5e", merge_ids: ["1a874379"],
                 error: "tiene NIF 'B99' y la principal 'PT503': no se fusionan" }],
    });
    const user = userEvent.setup();
    render(<DedupeCompaniesPage />);
    await user.click(screen.getByRole("button", { name: "Buscar duplicados por CIF" }));
    await user.click(await screen.findByLabelText(/Marcar grupo PT503420506/));
    await user.click(screen.getByRole("button", { name: /Fusionar seleccionadas/ }));
    await user.click(await screen.findByRole("button", { name: /Sí, fusionar y borrar/ }));

    expect(await screen.findByText(/no se fusionan/)).toBeInTheDocument();
  });

  it("sin duplicados lo dice y no ofrece fusionar", async () => {
    mockFind.mockResolvedValue({
      total_groups: 0, total_companies_involved: 0, groups: [],
    });
    const user = userEvent.setup();
    render(<DedupeCompaniesPage />);
    await user.click(screen.getByRole("button", { name: "Buscar duplicados por CIF" }));

    expect(await screen.findByText("No hay empresas duplicadas por NIF."))
      .toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Fusionar seleccionadas \(0\)/ }))
      .toBeDisabled();
  });
});
