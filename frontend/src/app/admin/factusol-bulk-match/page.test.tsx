import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FactusolBulkMatchPage from "./page";
import {
  bulkMatchApply,
  bulkMatchByEmailApply,
  bulkMatchByEmailDryRun,
  bulkMatchDryRun,
  importOrphansApply,
  importOrphansDryRun,
} from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  bulkMatchDryRun: jest.fn(),
  bulkMatchApply: jest.fn(),
  bulkMatchByEmailDryRun: jest.fn(),
  bulkMatchByEmailApply: jest.fn(),
  importOrphansDryRun: jest.fn(),
  importOrphansApply: jest.fn(),
  // El helper es lógica pura del cliente API: se usa el real para que el test
  // compruebe de verdad la forma del payload, no un doble que la invente.
  orphanToOperation: jest.requireActual("../../lib/erpApi").orphanToOperation,
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
const mockOrphanDryRun = importOrphansDryRun as jest.Mock;
const mockOrphanApply = importOrphansApply as jest.Mock;

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
    reassigned_to_existing_company: 0, reassigned_to_new_company: 0,
    reassigned: 0, skipped_already_linked_other: 0, errors: [],
  });
  mockOrphanDryRun.mockReset();
  mockOrphanApply.mockReset();
  mockOrphanDryRun.mockResolvedValue({
    total_factusol_clientes: 0, linked_already: 0, orphans_to_import: 0,
    with_email: 0, without_email: 0, orphans: [], ejercicio: "2026",
  });
  mockOrphanApply.mockResolvedValue({
    imported_company_and_contact: 1, imported_company_only: 0,
    skipped_race: 0, imported: 1, results: [], errors: [],
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

    // C-5-fix4: por defecto el codcli MAYOR (2758). El operador puede cambiarlo.
    expect(await screen.findByLabelText("Cliente 2758 para LABORATORIOS PORTA"))
      .toBeChecked();
    await user.click(screen.getByLabelText("Cliente 1 para LABORATORIOS PORTA"));
    await user.click(screen.getByLabelText("Aplicar a LABORATORIOS PORTA"));
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas/ }));

    await waitFor(() => expect(mockApply).toHaveBeenCalled());
    expect(mockApply.mock.calls[0][0][0].factusol_codcli).toBe("1");
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

  it("empresa vinculada a OTRO codcli: chip de reasignación, aplicable", async () => {
    // C-5-fix5: antes salía en ámbar «Ya vinculada a 9999» y deshabilitada.
    // Era el 90% de las 128 omisiones del primer apply (caso Vilatzara).
    mockEmailDryRun.mockResolvedValue(emailDryRun({
      matches: [emailMatch({ company_factusol_id: "9999" })],
    }));
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));

    const chip = await screen.findByText("Reasignar → 1");
    expect(chip).toHaveClass("badge", "active");
    expect(screen.queryByText(/Ya vinculada a 9999/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Aplicar a Juan Pérez")).toBeEnabled();
    expect(chip).toHaveAttribute(
      "title", expect.stringContaining("no se toca") as unknown as string);
  });

  it("aplicar una fila de reasignación manda la operación normal", async () => {
    mockEmailDryRun.mockResolvedValue(emailDryRun({
      matches: [emailMatch({ company_factusol_id: "9999" })],
    }));
    mockEmailApply.mockResolvedValue({
      applied: 1,
      results: [{ contact_id: "ct1", result: "reassigned_to_new_company",
                  company_id: "new-c", old_company_id: "c1" }],
      refreshed: 0, created_new_company: 0, linked_existing_company: 0,
      reassigned_to_existing_company: 0, reassigned_to_new_company: 1,
      reassigned: 1, skipped_already_linked_other: 0, errors: [],
    });
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    await user.click(await screen.findByLabelText("Aplicar a Juan Pérez"));
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas \(1\)/ }));

    await waitFor(() => expect(mockEmailApply).toHaveBeenCalled());
    expect(mockEmailApply.mock.calls[0][0][0]).toMatchObject({
      contact_id: "ct1", factusol_codcli: "1",
    });
    const summary = await screen.findByRole("status");
    expect(summary).toHaveTextContent("1 reasignada(s) a empresa correcta");
    expect(summary).toHaveTextContent("0 a empresa existente, 1 a empresa nueva");
  });

  it("el resumen desglosa las reasignaciones entre existente y nueva", async () => {
    mockEmailDryRun.mockResolvedValue(emailDryRun());
    mockEmailApply.mockResolvedValue({
      applied: 5,
      results: [{ contact_id: "ct1", result: "reassigned_to_existing_company" }],
      refreshed: 1, created_new_company: 1, linked_existing_company: 1,
      reassigned_to_existing_company: 2, reassigned_to_new_company: 1,
      reassigned: 3, skipped_already_linked_other: 0, errors: [],
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
    expect(summary).toHaveTextContent(
      "3 reasignada(s) a empresa correcta (2 a empresa existente, 1 a empresa nueva)");
  });

  it("sin reasignaciones el resumen no las menciona", async () => {
    mockEmailDryRun.mockResolvedValue(emailDryRun());
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    await user.click(await screen.findByLabelText("Aplicar a Juan Pérez"));
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas/ }));

    const summary = await screen.findByRole("status");
    expect(summary).not.toHaveTextContent("reasignada");
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

  // --- C-5-fix4: master «Seleccionar todas» + codcli mayor + confirmación ---

  /** N contactos con match, todos aplicables. */
  function manyMatches(n: number) {
    return Array.from({ length: n }, (_, i) => emailMatch({
      contact_id: `ct${i}`, contact_name: `Contacto ${i}`,
      contact_email: `c${i}@x.com`, company_id: `co${i}`,
      company_name: `Empresa ${i}`,
    }));
  }

  it("el master marca todas las filas visibles", async () => {
    mockEmailDryRun.mockResolvedValue(emailDryRun({
      matches: [
        emailMatch({ contact_id: "ct1", contact_name: "Ana" }),
        emailMatch({ contact_id: "ct2", contact_name: "Bea" }),
        // C-5-fix5: su empresa apunta a otro codcli, pero ya no bloquea —
        // se reasigna, así que el master también la marca.
        emailMatch({ contact_id: "ct3", contact_name: "Carla",
                     company_factusol_id: "9999" }),
      ],
    }));
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));

    await user.click(await screen.findByLabelText("Seleccionar todas"));

    expect(screen.getByLabelText("Aplicar a Ana")).toBeChecked();
    expect(screen.getByLabelText("Aplicar a Bea")).toBeChecked();
    expect(screen.getByLabelText("Aplicar a Carla")).toBeChecked();
    expect(screen.getByRole("button", { name: /Aplicar seleccionadas \(3\)/ }))
      .toBeEnabled();
  });

  it("el master queda indeterminado cuando solo hay algunas marcadas", async () => {
    mockEmailDryRun.mockResolvedValue(emailDryRun({
      matches: [
        emailMatch({ contact_id: "ct1", contact_name: "Ana" }),
        emailMatch({ contact_id: "ct2", contact_name: "Bea" }),
      ],
    }));
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));

    const master = await screen.findByLabelText("Seleccionar todas") as HTMLInputElement;
    expect(master).not.toBeChecked();
    expect(master.indeterminate).toBe(false);

    await user.click(screen.getByLabelText("Aplicar a Ana"));
    expect(master).not.toBeChecked();
    expect(master.indeterminate).toBe(true);

    await user.click(screen.getByLabelText("Aplicar a Bea"));
    expect(master).toBeChecked();
    expect(master.indeterminate).toBe(false);

    // Y desmarcarlo las quita todas.
    await user.click(master);
    expect(screen.getByLabelText("Aplicar a Ana")).not.toBeChecked();
    expect(screen.getByRole("button", { name: /Aplicar seleccionadas \(0\)/ }))
      .toBeDisabled();
  });

  it("el master respeta «Solo con diferencias»: no marca lo escondido", async () => {
    mockEmailDryRun.mockResolvedValue(emailDryRun({
      matches: [
        emailMatch({ contact_id: "ct1", contact_name: "Ana" }),
        emailMatch({ contact_id: "ct2", contact_name: "Bea",
                     candidates: [candidate("1", { differing_fields: 0 })] }),
      ],
    }));
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    await screen.findByText("Ana");

    await user.click(screen.getByLabelText("Solo con diferencias"));
    await user.click(screen.getByLabelText("Seleccionar todas"));

    expect(screen.queryByText("Bea")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Aplicar seleccionadas \(1\)/ }))
      .toBeEnabled();
  });

  it("50 o más operaciones piden confirmación antes de escribir", async () => {
    mockEmailDryRun.mockResolvedValue(emailDryRun({ matches: manyMatches(60) }));
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    await user.click(await screen.findByLabelText("Seleccionar todas"));
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas \(60\)/ }));

    expect(await screen.findByRole("dialog")).toHaveTextContent(
      "Vas a aplicar 60 operaciones");
    expect(screen.getByRole("dialog")).toHaveTextContent(
      "reversibles solo via SQL manual (audit_logs)");
    expect(mockEmailApply).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Sí, aplicar 60 cambios" }));
    await waitFor(() => expect(mockEmailApply).toHaveBeenCalled());
    expect(mockEmailApply.mock.calls[0][0]).toHaveLength(60);
  });

  it("cancelar la confirmación no escribe nada y conserva la selección", async () => {
    mockEmailDryRun.mockResolvedValue(emailDryRun({ matches: manyMatches(55) }));
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    await user.click(await screen.findByLabelText("Seleccionar todas"));
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas \(55\)/ }));
    await user.click(await screen.findByRole("button", { name: "Cancelar" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockEmailApply).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Aplicar seleccionadas \(55\)/ }))
      .toBeEnabled();
  });

  it("por debajo del umbral aplica directo, sin confirmación", async () => {
    mockEmailDryRun.mockResolvedValue(emailDryRun({ matches: manyMatches(49) }));
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    await user.click(await screen.findByLabelText("Seleccionar todas"));
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas \(49\)/ }));

    await waitFor(() => expect(mockEmailApply).toHaveBeenCalled());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("multi-match: preselecciona el codcli mayor, no el primero", async () => {
    // Caso real de Bart: evamariamc1@gmail.com casa con 2123, 2210 y 2278.
    // Los CODCLI son autonuméricos → el mayor es el cliente bueno.
    mockEmailDryRun.mockResolvedValue(emailDryRun({
      matches: [emailMatch({
        contact_name: "Eva", contact_email: "evamariamc1@gmail.com",
        candidates: [candidate("2123"), candidate("2278"), candidate("2210")],
      })],
    }));
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));

    expect(await screen.findByLabelText("Cliente 2278 para Eva")).toBeChecked();
    expect(screen.getByLabelText("Cliente 2123 para Eva")).not.toBeChecked();
    expect(screen.getByLabelText("Cliente 2210 para Eva")).not.toBeChecked();

    await user.click(screen.getByLabelText("Aplicar a Eva"));
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas/ }));
    await waitFor(() => expect(mockEmailApply).toHaveBeenCalled());
    expect(mockEmailApply.mock.calls[0][0][0].factusol_codcli).toBe("2278");
  });

  it("compara los codcli como número: «999» no gana a «2278»", async () => {
    mockEmailDryRun.mockResolvedValue(emailDryRun({
      matches: [emailMatch({
        contact_name: "Eva",
        candidates: [candidate("999"), candidate("2278")],
      })],
    }));
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));

    expect(await screen.findByLabelText("Cliente 2278 para Eva")).toBeChecked();
  });

  it("con un solo candidato, ese va seleccionado de salida", async () => {
    mockEmailDryRun.mockResolvedValue(emailDryRun({
      matches: [emailMatch({ contact_name: "Eva",
                             candidates: [candidate("2278")] })],
    }));
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    await user.click(await screen.findByLabelText("Aplicar a Eva"));
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas/ }));

    await waitFor(() => expect(mockEmailApply).toHaveBeenCalled());
    expect(mockEmailApply.mock.calls[0][0][0].factusol_codcli).toBe("2278");
  });

  it("el operador puede cambiar el candidato preseleccionado", async () => {
    mockEmailDryRun.mockResolvedValue(emailDryRun({
      matches: [emailMatch({
        contact_name: "Eva",
        candidates: [candidate("2123"), candidate("2278")],
      })],
    }));
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));

    await user.click(await screen.findByLabelText("Cliente 2123 para Eva"));
    await user.click(screen.getByLabelText("Aplicar a Eva"));
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas/ }));

    await waitFor(() => expect(mockEmailApply).toHaveBeenCalled());
    expect(mockEmailApply.mock.calls[0][0][0].factusol_codcli).toBe("2123");
  });

  // --- C-6: importar F_CLI huérfanas ---------------------------------------

  async function switchToOrphanMode(user: ReturnType<typeof userEvent.setup>) {
    await user.selectOptions(screen.getByLabelText("Modo"), "import_orphans");
  }

  function orphan(codcli: string, over = {}) {
    return {
      codcli, nofcli: "ACME S.L.", noccli: "ACME", nifcli: "B12345678",
      domcli: "C. Mayor 1", pobcli: "Barcelona", cpocli: "08001",
      procli: "Barcelona", paicli: "724", emacli: "info@acme.example",
      telcli: "934567890", will_create_contact: true,
      ...over,
    };
  }

  function orphanDryRun(over = {}) {
    return {
      total_factusol_clientes: 4533, linked_already: 3200,
      orphans_to_import: 1, with_email: 1, without_email: 0,
      orphans: [orphan("1234")], ejercicio: "2026",
      ...over,
    };
  }

  it("el modo importación pinta la tabla de huérfanas y su resumen", async () => {
    mockOrphanDryRun.mockResolvedValue(orphanDryRun());
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await switchToOrphanMode(user);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));

    await waitFor(() => expect(mockOrphanDryRun).toHaveBeenCalledWith(
      { filter: "all" }));
    expect(mockEmailDryRun).not.toHaveBeenCalled();
    expect(mockDryRun).not.toHaveBeenCalled();
    expect(await screen.findByText("ACME S.L.")).toBeInTheDocument();
    expect(screen.getByText("nº 1234")).toBeInTheDocument();
    expect(screen.getByText("B12345678")).toBeInTheDocument();
    const resumen = screen.getByText(/F_CLI huérfana/);
    expect(resumen).toHaveTextContent("3200 ya vinculado(s)");
    expect(resumen).toHaveTextContent("4533 cliente(s) en FACTUSOL");
  });

  it("el chip depende de si la F_CLI trae email", async () => {
    mockOrphanDryRun.mockResolvedValue(orphanDryRun({
      orphans: [
        orphan("1", { nofcli: "CON EMAIL" }),
        orphan("2", { nofcli: "SIN EMAIL", emacli: null,
                      will_create_contact: false }),
      ],
      orphans_to_import: 2, with_email: 1, without_email: 1,
    }));
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await switchToOrphanMode(user);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));

    expect(await screen.findByText("Se creará empresa + contacto"))
      .toBeInTheDocument();
    expect(screen.getByText("Se creará solo empresa")).toBeInTheDocument();
    expect(screen.getByText("(sin email)")).toBeInTheDocument();
  });

  it("«Solo los que tengan email» viaja al backend como filtro", async () => {
    mockOrphanDryRun.mockResolvedValue(orphanDryRun());
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await switchToOrphanMode(user);
    // «Solo con diferencias» no aplica aquí: no hay nada previo que comparar.
    expect(screen.queryByLabelText("Solo con diferencias")).not.toBeInTheDocument();

    await user.click(screen.getByLabelText("Solo los que tengan email"));
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));

    await waitFor(() => expect(mockOrphanDryRun).toHaveBeenCalledWith(
      { filter: "only_with_email" }));
  });

  it("marcar e importar manda los codclis y resume los desenlaces", async () => {
    mockOrphanDryRun.mockResolvedValue(orphanDryRun({
      orphans: [orphan("1", { nofcli: "UNA" }),
                orphan("2", { nofcli: "OTRA", emacli: null,
                              will_create_contact: false })],
      orphans_to_import: 2, with_email: 1, without_email: 1,
    }));
    mockOrphanApply.mockResolvedValue({
      imported_company_and_contact: 1, imported_company_only: 1,
      skipped_race: 0, imported: 2, results: [], errors: [],
    });
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await switchToOrphanMode(user);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    await user.click(await screen.findByLabelText("Seleccionar todas"));
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas \(2\)/ }));

    await waitFor(() => expect(mockOrphanApply).toHaveBeenCalled());
    expect(mockOrphanApply.mock.calls[0][0].map((o: { codcli: string }) => o.codcli))
      .toEqual(["1", "2"]);
    const summary = await screen.findByRole("status");
    expect(summary).toHaveTextContent("1 empresa(s) creada(s) con contacto");
    expect(summary).toHaveTextContent("1 empresa(s) creada(s) sin contacto");
  });

  it("el resumen enseña las omitidas por conflicto y los errores", async () => {
    mockOrphanDryRun.mockResolvedValue(orphanDryRun());
    mockOrphanApply.mockResolvedValue({
      imported_company_and_contact: 0, imported_company_only: 0,
      skipped_race: 1, imported: 0, results: [],
      errors: [{ codcli: "9", error: "el cliente FACTUSOL 9 no existe" }],
    });
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await switchToOrphanMode(user);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    await user.click(await screen.findByLabelText("Importar ACME S.L."));
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas/ }));

    const summary = await screen.findByRole("status");
    expect(summary).toHaveTextContent("1 omitida(s) por conflicto");
    expect(summary).toHaveTextContent("1 fallida(s)");
    expect(await screen.findByText(/no existe/)).toBeInTheDocument();
  });

  // --- C-6-fix1: el apply manda los datos, y las fallidas se reintentan ----

  it("el apply manda los datos F_CLI de cada fila, no solo el codcli", async () => {
    // El backend releía F_CLI entera para esto y un KO de DELSOL se llevaba el
    // lote entero por delante con un 502. El navegador ya tiene los datos.
    mockOrphanDryRun.mockResolvedValue(orphanDryRun());
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await switchToOrphanMode(user);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    await user.click(await screen.findByLabelText("Importar ACME S.L."));
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas/ }));

    await waitFor(() => expect(mockOrphanApply).toHaveBeenCalled());
    expect(mockOrphanApply.mock.calls[0][0]).toEqual([{
      codcli: "1234",
      factusol_data: {
        nofcli: "ACME S.L.", noccli: "ACME", nifcli: "B12345678",
        domcli: "C. Mayor 1", pobcli: "Barcelona", cpocli: "08001",
        procli: "Barcelona", paicli: "724", emacli: "info@acme.example",
        telcli: "934567890",
      },
    }]);
  });

  it("con fallidas aparece «Reintentar fallidas» y reenvía solo esas", async () => {
    mockOrphanDryRun.mockResolvedValue(orphanDryRun({
      orphans: [orphan("1", { nofcli: "UNA" }), orphan("2", { nofcli: "OTRA" })],
      orphans_to_import: 2, with_email: 2, without_email: 0,
    }));
    mockOrphanApply.mockResolvedValueOnce({
      imported_company_and_contact: 1, imported_company_only: 0,
      skipped_race: 0, imported: 1, results: [],
      errors: [{ codcli: "2", error: "factusol_unavailable: reinténtalo" }],
    });
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await switchToOrphanMode(user);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    await user.click(await screen.findByLabelText("Seleccionar todas"));
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas \(2\)/ }));

    const retry = await screen.findByRole("button", {
      name: "Reintentar fallidas (1)",
    });
    mockOrphanApply.mockResolvedValue({
      imported_company_and_contact: 1, imported_company_only: 0,
      skipped_race: 0, imported: 1, results: [], errors: [],
    });
    await user.click(retry);

    await waitFor(() => expect(mockOrphanApply).toHaveBeenCalledTimes(2));
    // Solo el que falló, con sus datos.
    expect(mockOrphanApply.mock.calls[1][0]).toHaveLength(1);
    expect(mockOrphanApply.mock.calls[1][0][0].codcli).toBe("2");
    expect(mockOrphanApply.mock.calls[1][0][0].factusol_data.nofcli).toBe("OTRA");
    // Y cuando ya no queda ninguna fallida, el botón desaparece.
    await waitFor(() => expect(
      screen.queryByRole("button", { name: /Reintentar fallidas/ }),
    ).not.toBeInTheDocument());
  });

  it("sin fallidas no se ofrece reintentar", async () => {
    mockOrphanDryRun.mockResolvedValue(orphanDryRun());
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await switchToOrphanMode(user);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    await user.click(await screen.findByLabelText("Importar ACME S.L."));
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas/ }));

    await screen.findByRole("status");
    expect(screen.queryByRole("button", { name: /Reintentar fallidas/ }))
      .not.toBeInTheDocument();
  });

  it("importar 50 o más pide confirmación, con el texto de crear", async () => {
    const many = Array.from({ length: 60 }, (_, i) =>
      orphan(String(i), { nofcli: `EMPRESA ${i}` }));
    mockOrphanDryRun.mockResolvedValue(orphanDryRun({
      orphans: many, orphans_to_import: 60, with_email: 60, without_email: 0,
    }));
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await switchToOrphanMode(user);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    await user.click(await screen.findByLabelText("Seleccionar todas"));
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas \(60\)/ }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("creará 60 empresas nuevas en el CRM");
    expect(mockOrphanApply).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Sí, aplicar 60 cambios" }));
    await waitFor(() => expect(mockOrphanApply).toHaveBeenCalled());
    expect(mockOrphanApply.mock.calls[0][0]).toHaveLength(60);
  });

  it("cambiar de modo limpia la tabla anterior", async () => {
    mockOrphanDryRun.mockResolvedValue(orphanDryRun());
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await switchToOrphanMode(user);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    expect(await screen.findByText("ACME S.L.")).toBeInTheDocument();

    await switchToCompanyMode(user);
    expect(screen.queryByText("ACME S.L.")).not.toBeInTheDocument();
  });

  it("el master también funciona en el modo por empresa", async () => {
    mockDryRun.mockResolvedValue(dryRun({
      matches: [
        { crm_company_id: "c1", crm_name: "UNA", crm_tax_id: "B1",
          match_type: "nif", confidence: "high", candidates: [candidate("10")] },
        { crm_company_id: "c2", crm_name: "OTRA", crm_tax_id: "B2",
          match_type: "nif", confidence: "high", candidates: [candidate("20")] },
      ],
    }));
    const user = userEvent.setup();
    render(<FactusolBulkMatchPage />);
    await switchToCompanyMode(user);
    await user.click(screen.getByRole("button", { name: "Ejecutar dry-run" }));
    await user.click(await screen.findByLabelText("Seleccionar todas"));

    expect(screen.getByLabelText("Aplicar a UNA")).toBeChecked();
    expect(screen.getByLabelText("Aplicar a OTRA")).toBeChecked();
    await user.click(screen.getByRole("button", { name: /Aplicar seleccionadas \(2\)/ }));

    await waitFor(() => expect(mockApply).toHaveBeenCalled());
    expect(mockApply.mock.calls[0][0]).toHaveLength(2);
  });
});
