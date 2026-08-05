import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FactusolBulkMatchPage from "./page";
import {
  bulkMatchApply,
  bulkMatchByEmailApply,
  bulkMatchByEmailDryRun,
  bulkMatchDryRun,
} from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  bulkMatchDryRun: jest.fn(),
  bulkMatchApply: jest.fn(),
  bulkMatchByEmailDryRun: jest.fn(),
  bulkMatchByEmailApply: jest.fn(),
  BULK_MATCH_FIELDS: ["name", "tax_id", "address_line", "city", "postal_code", "state"],
  BULK_MATCH_FIELD_LABELS: {
    name: "Nombre", tax_id: "NIF", address_line: "Dirección",
    city: "Ciudad", postal_code: "CP", state: "Provincia",
  },
}));
const mockDryRun = bulkMatchDryRun as jest.Mock;
const mockApply = bulkMatchApply as jest.Mock;
const mockEmailDryRun = bulkMatchByEmailDryRun as jest.Mock;
const mockEmailApply = bulkMatchByEmailApply as jest.Mock;

/** El modo por defecto es «Contactos por email»; los tests de C-5 prueban el
 *  modo por empresa, así que cambian antes de nada. */
async function switchToCompanyMode(user: ReturnType<typeof userEvent.setup>) {
  await user.selectOptions(screen.getByLabelText("Modo"), "by_company");
}

function diff(field: string, crm: string, factusol: string, differs: boolean) {
  return { field, crm, factusol, differs };
}

function candidate(codcli: string, over = {}) {
  return {
    factusol_codcli: codcli, factusol_nifcli: "B61444402",
    factusol_nofcli: "AUDIOVISUALES DATA SL",
    factusol_noccli: "AUDIOVISUALES DATA",
    factusol_domcli: "C/ Industria 12", factusol_pobcli: "VILADECANS",
    factusol_cpocli: "08840", factusol_procli: "Barcelona",
    differences: [
      diff("name", "AUDIOVISUALES DATA", "AUDIOVISUALES DATA SL", true),
      diff("tax_id", "B61444402", "B61444402", false),
      diff("city", "", "VILADECANS", true),
    ],
    differing_fields: 2,
    ...over,
  };
}

function dryRun(over = {}) {
  return {
    total_crm_companies: 1, total_factusol_customers: 4533, ejercicio: "2026",
    matches: [{
      crm_company_id: "c1", crm_name: "AUDIOVISUALES DATA",
      crm_tax_id: "B61444402", match_type: "nif", confidence: "high",
      candidates: [candidate("3342")],
    }],
    no_match: [],
    ...over,
  };
}

beforeEach(() => {
  mockDryRun.mockReset();
  mockApply.mockReset();
  mockDryRun.mockResolvedValue(dryRun());
  mockApply.mockResolvedValue({ applied: 1, errors: [] });
  mockEmailDryRun.mockReset();
  mockEmailApply.mockReset();
  mockEmailDryRun.mockResolvedValue({
    total_contacts_with_email: 0, matches: [], no_match_count: 0,
    matches_without_company: 0, truncated: false, ejercicio: "2026",
  });
  mockEmailApply.mockResolvedValue({
    applied: 1, results: [{ contact_id: "ct1", result: "refreshed" }],
    refreshed: 1, created_new_company: 0, linked_existing_company: 0,
    skipped_already_linked_other: 0, errors: [],
  });
});

describe("FactusolBulkMatchPage", () => {
  it("no consulta nada hasta que se pulsa el dry-run", () => {
    render(<FactusolBulkMatchPage />);
    expect(mockDryRun).not.toHaveBeenCalled();
    expect(mockEmailDryRun).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Ejecutar dry-run" })).toBeInTheDocument();
  });

  it("el dry-run llena la tabla con la empresa y su candidato", async () => {
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await switchToCompanyMode(user);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));

    await waitFor(() => expect(mockDryRun).toHaveBeenCalledWith(
      expect.objectContaining({ filter: "unlinked_only" })));
    expect(await screen.findByText("AUDIOVISUALES DATA")).toBeInTheDocument();
    expect(screen.getByText(/nº 3342/)).toBeInTheDocument();
    expect(screen.getByText("NIF exacto")).toBeInTheDocument();
    expect(screen.getByText("2 campo(s)")).toBeInTheDocument();
  });

  it("expandir muestra el diff campo a campo", async () => {
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await switchToCompanyMode(user);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    await user.click(await screen.findByRole("button", { name: "Ver diferencias" }));

    expect(screen.getByText("Nombre")).toBeInTheDocument();
    expect(screen.getByText("AUDIOVISUALES DATA SL")).toBeInTheDocument();
    expect(screen.getAllByText("VILADECANS").length).toBeGreaterThan(0);
  });

  it("«Aplicar» arranca desmarcado: nada se escribe sin decirlo", async () => {
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await switchToCompanyMode(user);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));

    const checkbox = await screen.findByLabelText("Aplicar a AUDIOVISUALES DATA");
    expect(checkbox).not.toBeChecked();
    expect(screen.getByRole("button", { name: /Aplicar seleccionadas \(0\)/ }))
      .toBeDisabled();
  });

  it("marcar y aplicar envía la operación con sus campos", async () => {
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await switchToCompanyMode(user);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    await user.click(await screen.findByLabelText("Aplicar a AUDIOVISUALES DATA"));
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas \(1\)/ }));

    await waitFor(() => expect(mockApply).toHaveBeenCalled());
    const ops = mockApply.mock.calls[0][0];
    expect(ops).toHaveLength(1);
    expect(ops[0].crm_company_id).toBe("c1");
    expect(ops[0].factusol_codcli).toBe("3342");
    expect(ops[0].fields_to_sync).toEqual(
      expect.arrayContaining(["name", "city"]));
    expect(await screen.findByText(/1 empresa\(s\) actualizada\(s\)/))
      .toBeInTheDocument();
  });

  it("desmarcar un campo lo saca del payload", async () => {
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await switchToCompanyMode(user);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    await user.click(await screen.findByRole("button", { name: "Ver diferencias" }));
    await user.click(
      screen.getByLabelText("Sincronizar Nombre de AUDIOVISUALES DATA"));
    await user.click(screen.getByLabelText("Aplicar a AUDIOVISUALES DATA"));
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas/ }));

    await waitFor(() => expect(mockApply).toHaveBeenCalled());
    expect(mockApply.mock.calls[0][0][0].fields_to_sync).not.toContain("name");
  });

  it("con varios candidatos se elige con radio y viaja el elegido", async () => {
    mockDryRun.mockResolvedValue(dryRun({
      matches: [{
        crm_company_id: "c1", crm_name: "LABORATORIOS PORTA",
        crm_tax_id: "B64113590", match_type: "nif", confidence: "high",
        candidates: [
          candidate("1", { factusol_nofcli: "LABORATORIOS PORTA S.L." }),
          candidate("2758", { factusol_nofcli: "LABORATORIOS PORTA SL" }),
        ],
      }],
    }));
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await switchToCompanyMode(user);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));

    // Por defecto el primero; el operador cambia al segundo.
    await user.click(await screen.findByLabelText(
      "Cliente 2758 para LABORATORIOS PORTA"));
    await user.click(screen.getByLabelText("Aplicar a LABORATORIOS PORTA"));
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas/ }));

    await waitFor(() => expect(mockApply).toHaveBeenCalled());
    expect(mockApply.mock.calls[0][0][0].factusol_codcli).toBe("2758");
  });

  it("el filtro «Solo con diferencias» esconde las que ya cuadran", async () => {
    mockDryRun.mockResolvedValue(dryRun({
      matches: [{
        crm_company_id: "c2", crm_name: "EMPRESA YA LIMPIA",
        crm_tax_id: "B1", match_type: "nif", confidence: "high",
        candidates: [candidate("10", { differing_fields: 0 })],
      }],
    }));
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await switchToCompanyMode(user);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    expect(await screen.findByText("EMPRESA YA LIMPIA")).toBeInTheDocument();

    await user.click(screen.getByLabelText("Solo con diferencias"));
    expect(screen.queryByText("EMPRESA YA LIMPIA")).not.toBeInTheDocument();
  });

  it("muestra los errores que devuelve el apply", async () => {
    mockApply.mockResolvedValue({
      applied: 0,
      errors: [{ crm_company_id: "c1", error: "ya está vinculada al cliente 99" }],
    });
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await switchToCompanyMode(user);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    await user.click(await screen.findByLabelText("Aplicar a AUDIOVISUALES DATA"));
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas/ }));

    expect(await screen.findByText(/ya está vinculada al cliente 99/))
      .toBeInTheDocument();
  });

  // --- C-5-fix1: modo «contactos por email» --------------------------------

  function emailMatch(over = {}) {
    return {
      contact_id: "ct1", contact_name: "Juan Pérez",
      contact_email: "juan@laboratoriosporta.com",
      company_id: "c1", company_name: "Labor. Porta",
      company_factusol_id: null,
      candidates: [candidate("1", {
        factusol_nofcli: "LABORATORIOS PORTA S.L.",
      })],
      ...over,
    };
  }

  function emailDryRun(over = {}) {
    return {
      total_contacts_with_email: 10, matches: [emailMatch()],
      no_match_count: 9, matches_without_company: 0, truncated: false,
      ejercicio: "2026",
      ...over,
    };
  }

  it("el modo por email es el que sale por defecto", () => {
    render(<FactusolBulkMatchPage />);
    expect(screen.getByLabelText("Modo")).toHaveValue("by_contact_email");
    // El filtro de empresas solo aplica al modo por NIF/nombre.
    expect(screen.queryByLabelText("Empresas")).not.toBeInTheDocument();
  });

  it("dry-run por email llama a su endpoint y pinta contacto + empresa", async () => {
    mockEmailDryRun.mockResolvedValue(emailDryRun());
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));

    await waitFor(() => expect(mockEmailDryRun).toHaveBeenCalled());
    expect(mockDryRun).not.toHaveBeenCalled();
    // C-5-fix2: sin batch_size — el backend procesa TODOS los contactos.
    expect(mockEmailDryRun).toHaveBeenCalledWith();
    expect(await screen.findByText("Juan Pérez")).toBeInTheDocument();
    expect(screen.getByText("juan@laboratoriosporta.com")).toBeInTheDocument();
    expect(screen.getByText("Labor. Porta")).toBeInTheDocument();
    expect(screen.getByText(/nº 1/)).toBeInTheDocument();
  });

  it("marcar y aplicar manda el contact_id, no el company_id", async () => {
    mockEmailDryRun.mockResolvedValue(emailDryRun());
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    await user.click(await screen.findByLabelText("Aplicar a Juan Pérez"));
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas \(1\)/ }));

    await waitFor(() => expect(mockEmailApply).toHaveBeenCalled());
    const ops = mockEmailApply.mock.calls[0][0];
    expect(ops[0].contact_id).toBe("ct1");
    expect(ops[0].factusol_codcli).toBe("1");
    expect(mockApply).not.toHaveBeenCalled();
  });

  it("contacto SIN empresa: se ofrece crearla, no se bloquea", async () => {
    // C-5-fix2: antes se saltaba; ahora se crea la empresa con los datos F_CLI.
    mockEmailDryRun.mockResolvedValue(emailDryRun({
      matches: [emailMatch({ company_id: null, company_name: null })],
      matches_without_company: 1,
    }));
    mockEmailApply.mockResolvedValue({
      applied: 1, results: [{ contact_id: "ct1", result: "created_new_company",
                              company_id: "new-c" }],
      refreshed: 0, created_new_company: 1, linked_existing_company: 0,
      skipped_already_linked_other: 0, errors: [],
    });
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));

    expect(await screen.findByText("Se creará empresa")).toBeInTheDocument();
    const checkbox = screen.getByLabelText("Aplicar a Juan Pérez");
    expect(checkbox).toBeEnabled();

    await user.click(checkbox);
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas/ }));
    expect(await screen.findByText(/1 empresa\(s\) creada\(s\)/)).toBeInTheDocument();
  });

  it("el resumen desglosa los desenlaces del apply", async () => {
    mockEmailDryRun.mockResolvedValue(emailDryRun());
    mockEmailApply.mockResolvedValue({
      applied: 3,
      results: [
        { contact_id: "a", result: "refreshed" },
        { contact_id: "b", result: "created_new_company" },
        { contact_id: "c", result: "linked_existing_company" },
      ],
      refreshed: 1, created_new_company: 1, linked_existing_company: 1,
      skipped_already_linked_other: 0, errors: [],
    });
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    await user.click(await screen.findByLabelText("Aplicar a Juan Pérez"));
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas/ }));

    const summary = await screen.findByRole("status");
    expect(summary).toHaveTextContent("1 actualizada(s)");
    expect(summary).toHaveTextContent("1 empresa(s) creada(s)");
    expect(summary).toHaveTextContent("1 asignada(s) a empresa existente");
  });

  it("empresa ya vinculada a OTRO codcli: bloqueada con el motivo", async () => {
    mockEmailDryRun.mockResolvedValue(emailDryRun({
      matches: [emailMatch({ company_factusol_id: "9999" })],
    }));
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));

    expect(await screen.findByText("Ya vinculada a 9999")).toBeInTheDocument();
    expect(screen.getByLabelText("Aplicar a Juan Pérez")).toBeDisabled();
  });

  it("los omitidos del apply se muestran, no se tragan", async () => {
    mockEmailDryRun.mockResolvedValue(emailDryRun());
    mockEmailApply.mockResolvedValue({
      applied: 0,
      results: [{ contact_id: "ct1", result: "skipped_already_linked_other",
                  detail: "«Labor. Porta» ya está vinculada al cliente 9999" }],
      refreshed: 0, created_new_company: 0, linked_existing_company: 0,
      skipped_already_linked_other: 1, errors: [],
    });
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    await user.click(await screen.findByLabelText("Aplicar a Juan Pérez"));
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas/ }));

    expect(await screen.findByText(/ya está vinculada al cliente 9999/))
      .toBeInTheDocument();
    expect(screen.getByText(/1 omitida\(s\)/)).toBeInTheDocument();
  });

  it("«Solo con diferencias» filtra igual en modo email", async () => {
    mockEmailDryRun.mockResolvedValue(emailDryRun({
      matches: [emailMatch({ candidates: [candidate("1", { differing_fields: 0 })] })],
    }));
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    expect(await screen.findByText("Juan Pérez")).toBeInTheDocument();

    await user.click(screen.getByLabelText("Solo con diferencias"));
    expect(screen.queryByText("Juan Pérez")).not.toBeInTheDocument();
  });

  it("el resumen no presenta «sin empresa» como una tercera categoría", async () => {
    // Es un SUBCONJUNTO de los con match: 3 + 17 = 20, y de esos 3 hay 1 sin
    // empresa. Presentarlos sumando confundiría los totales.
    mockEmailDryRun.mockResolvedValue(emailDryRun({
      total_contacts_with_email: 20, no_match_count: 17,
      matches_without_company: 1,
    }));
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));

    const resumen = await screen.findByText(/contacto\(s\) con email/);
    expect(resumen).toHaveTextContent("de los cuales 1 sin empresa CRM");
    expect(resumen).toHaveTextContent("17 sin match");
  });

  it("avisa si el resultado viene truncado", async () => {
    mockEmailDryRun.mockResolvedValue(emailDryRun({ truncated: true }));
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));

    expect(await screen.findByText(/Resultado truncado/)).toBeInTheDocument();
  });
});
