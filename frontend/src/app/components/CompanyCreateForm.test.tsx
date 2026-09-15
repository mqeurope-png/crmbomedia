import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CompanyCreateForm, describeCheckedAt, regimeConsequence } from "./CompanyCreateForm";
import { createCompany, fiscalCheck, type ViesStatus } from "../lib/companiesApi";
import { createFactusolCustomer } from "../lib/erpApi";
import { getCurrentUser } from "../lib/api";

/** «Crear empresa» · revisión Lote 2 §9: anti-duplicados al teclear el NIF
 *  (candidata con «Usar esta» y botón desactivado con motivo), VIES con
 *  cuatro estados y hora + «Volver a comprobar», régimen explicado por su
 *  consecuencia y «Crear también en FACTUSOL» con la suya. Y nada de lo de
 *  antes se pierde: crear con FACTUSOL, sin FACTUSOL, el 409, el fallo. */

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className, ...rest }: {
    children: React.ReactNode; href: string; className?: string;
  }) => <a href={href} className={className} {...rest}>{children}</a>,
}));
jest.mock("../lib/api", () => ({ getCurrentUser: jest.fn() }));
jest.mock("../lib/companiesApi", () => ({
  createCompany: jest.fn(),
  fiscalCheck: jest.fn(),
}));
jest.mock("../lib/erpApi", () => ({
  ERP_EDIT_ROLES: ["admin", "pedidos"],
  createFactusolCustomer: jest.fn(),
}));

const mockUser = getCurrentUser as jest.Mock;
const mockCheck = fiscalCheck as jest.Mock;
const mockCreate = createCompany as jest.Mock;
const mockFactusol = createFactusolCustomer as jest.Mock;

const FACTUSOL_WHY =
  "Se dará de alta como cliente en el software de facturación. Hace falta para poder emitir facturas a esta empresa.";

function check(over: Record<string, unknown> = {}) {
  return {
    country_iso2: "FR", in_eu: true, regime: "intracomunitario",
    regime_label: "Intracomunitario (exento)",
    regime_reason: "FR (UE) con NIF-IVA FR16339753527 → intracomunitario",
    vat_normalized: "FR16339753527",
    duplicates: { crm: [], factusol: null, factusol_checked: true, factusol_error: null },
    vies: {
      applies: true, vat: "FR16339753527", status: "pendiente", valid: null,
      checked_at: null, name: null, address: null, stale: false,
    },
    ...over,
  };
}

function vies(over: Record<string, unknown>) {
  return { ...check().vies, ...over };
}

/** ISO de «hoy» a una hora fija local, para comprobar «hoy a las HH:MM». */
function todayAt(h: number, m: number): { iso: string; hhmm: string } {
  const d = new Date();
  d.setHours(h, m, 0, 0);
  return { iso: d.toISOString(), hhmm: `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}` };
}

async function typeFr(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("NIF-IVA (VAT intracomunitario)"), "FR16339753527");
  await user.type(screen.getByLabelText("País"), "FR");
}

const regimen = () => screen.findByRole("status", { name: "Régimen de IVA" });
const viesBox = () => screen.findByRole("status", { name: "Validación VIES" });

beforeEach(() => {
  mockUser.mockReset();
  mockUser.mockResolvedValue({ role: "admin" });
  mockCheck.mockReset();
  mockCheck.mockResolvedValue(check());
  mockCreate.mockReset();
  mockCreate.mockResolvedValue({
    id: "c-new", name: "SAS La Maison de la Plaque", factusol_company_id: null,
  });
  mockFactusol.mockReset();
  mockFactusol.mockResolvedValue({
    factusol_codcli: "4471", created: true, regime_label: "Intracomunitario (exento)",
  });
});

describe("CompanyCreateForm — «Crear empresa»", () => {
  it("detecta el régimen al vuelo por país + NIF-IVA y lo explica por su consecuencia (+ el porqué)", async () => {
    const user = userEvent.setup();
    render(<CompanyCreateForm onCreated={() => {}} />);
    await typeFr(user);
    await waitFor(() => expect(mockCheck).toHaveBeenLastCalledWith(
      { tax_id: "", vat: "FR16339753527", country: "FR" },
    ));
    const detect = await regimen();
    expect(detect).toHaveTextContent("Régimen detectado: Intracomunitario · sin IVA.");
    expect(detect).toHaveTextContent("La factura saldrá sin IVA si el VAT es válido.");
    expect(detect).toHaveTextContent("FR (UE) con NIF-IVA FR16339753527 → intracomunitario");
    expect(detect.className).toContain("is-p");
    expect(await viesBox()).toHaveTextContent("VIES: pendiente de validar");
    expect(screen.getByText(/No existe en FACTUSOL con ese NIF/)).toBeInTheDocument();
    // Sin datos fiscales no hay nada que comprobar: el bloque se va.
    await user.clear(screen.getByLabelText("NIF-IVA (VAT intracomunitario)"));
    await user.clear(screen.getByLabelText("País"));
    await waitFor(() => expect(screen.queryByRole("status", { name: "Régimen de IVA" })).toBeNull());
  });

  it("VIES: válido (con hora y nombre), no válido (régimen nacional con IVA) y VIES caído, cada uno con su color", async () => {
    const user = userEvent.setup();
    const { iso, hhmm } = todayAt(11, 42);

    mockCheck.mockResolvedValue(check({
      regime_reason: "FR (UE) con NIF-IVA FR16339753527 verificado en VIES → intracomunitario",
      vies: vies({ status: "valido", valid: true, checked_at: iso, name: "SAS LA MAISON DE LA PLAQUE" }),
    }));
    let view = render(<CompanyCreateForm onCreated={() => {}} />);
    await typeFr(user);
    let box = await viesBox();
    expect(box).toHaveTextContent(`VAT válido en VIES · comprobado hoy a las ${hhmm}`);
    expect(box).toHaveTextContent("Según VIES: SAS LA MAISON DE LA PLAQUE");
    expect(box.className).toContain("is-valido");
    expect(await regimen()).toHaveTextContent("La factura saldrá sin IVA: el VAT está verificado en VIES.");
    expect(within(box).getByRole("button", { name: "Volver a comprobar" })).toBeInTheDocument();
    view.unmount();

    mockCheck.mockResolvedValue(check({
      regime: "nacional", regime_label: "Nacional (con IVA)",
      regime_reason: "FR (UE) con NIF-IVA FR16339753527 NO válido en VIES → nacional (no se puede eximir)",
      vies: vies({ status: "no_valido", valid: false, checked_at: iso }),
    }));
    view = render(<CompanyCreateForm onCreated={() => {}} />);
    await typeFr(user);
    box = await viesBox();
    expect(box).toHaveTextContent(`VAT no válido en VIES · comprobado hoy a las ${hhmm}`);
    expect(box).toHaveTextContent("No se puede eximir de IVA");
    expect(box.className).toContain("is-no_valido");
    const detect = await regimen();
    expect(detect).toHaveTextContent("Nacional · con IVA");
    expect(detect).toHaveTextContent("La factura saldrá con IVA: el VAT no es válido en VIES y no se puede eximir.");
    expect(detect.className).toContain("is-n");
    view.unmount();

    mockCheck.mockResolvedValue(check({
      vies: vies({ status: "desconocido", error: "VIES HTTP 500", checked_at: iso }),
    }));
    render(<CompanyCreateForm onCreated={() => {}} />);
    await typeFr(user);
    box = await viesBox();
    expect(box).toHaveTextContent(`VIES no disponible ahora · último intento hoy a las ${hhmm}`);
    expect(box).toHaveTextContent("Motivo: VIES HTTP 500");
    expect(box.className).toContain("is-desconocido");
    expect(await regimen()).toHaveTextContent("Intracomunitario · sin IVA");   // no bloquea
  });

  it("«Volver a comprobar» consulta VIES forzado (salta la caché) y mientras tanto dice «Comprobando»", async () => {
    const user = userEvent.setup();
    const { iso } = todayAt(9, 5);
    mockCheck.mockResolvedValue(check({ vies: vies({ status: "desconocido", checked_at: iso }) }));
    render(<CompanyCreateForm onCreated={() => {}} />);
    await typeFr(user);
    expect(await viesBox()).toHaveTextContent("VIES no disponible ahora");

    let resolve: (v: unknown) => void = () => {};
    mockCheck.mockImplementationOnce(() => new Promise((r) => { resolve = r; }));
    await user.click(screen.getByRole("button", { name: "Volver a comprobar" }));
    expect(mockCheck).toHaveBeenLastCalledWith(
      { tax_id: "", vat: "FR16339753527", country: "FR", force: true },
    );
    const box = await viesBox();
    expect(box).toHaveTextContent("Comprobando el VAT en VIES…");
    expect(box.className).toContain("is-comprobando");
    expect(within(box).queryByRole("button", { name: "Volver a comprobar" })).toBeNull();

    const { iso: later, hhmm } = todayAt(9, 6);
    resolve(check({ vies: vies({ status: "valido", valid: true, checked_at: later }) }));
    await waitFor(() => expect(screen.getByRole("status", { name: "Validación VIES" }))
      .toHaveTextContent(`VAT válido en VIES · comprobado hoy a las ${hhmm}`));
  });

  it("crea la empresa con sus datos fiscales y, con la casilla marcada, también el cliente en FACTUSOL", async () => {
    const onCreated = jest.fn();
    const user = userEvent.setup();
    render(<CompanyCreateForm onCreated={onCreated} />);
    await user.type(screen.getByLabelText("Nombre fiscal *"), "SAS La Maison de la Plaque");
    await typeFr(user);
    await user.type(screen.getByLabelText("Domicilio"), "Rue du Chemin Noir");
    await user.type(screen.getByLabelText("CP"), "21120");
    await user.type(screen.getByLabelText("Población"), "Is-sur-Tille");
    const casilla = await screen.findByRole("checkbox", { name: "Crear también en FACTUSOL" });
    expect(casilla).toBeChecked();
    // La casilla explica su efecto debajo de su etiqueta y va con lo fiscal.
    expect(casilla).toHaveAccessibleDescription(FACTUSOL_WHY);
    expect(screen.getByRole("region", { name: "Identificación fiscal" })).toContainElement(casilla);
    await user.click(screen.getByRole("button", { name: "Crear empresa" }));
    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith(expect.objectContaining({
      name: "SAS La Maison de la Plaque", vat: "FR16339753527", country: "FR",
      address_line: "Rue du Chemin Noir", postal_code: "21120", city: "Is-sur-Tille",
      tax_id: null,
    })));
    await waitFor(() => expect(mockFactusol).toHaveBeenCalledWith(expect.objectContaining({
      crm_type: "company", crm_id: "c-new", nombre: "SAS La Maison de la Plaque",
      nif: "FR16339753527", vat: "FR16339753527", pais: "FR", cp: "21120",
    })));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith({
      company: expect.objectContaining({ id: "c-new" }),
      factusol: { codcli: "4471", created: true, regime_label: "Intracomunitario (exento)" },
      factusolError: null,
    }));
  });

  it("sin la casilla (o sin rol ERP) no toca FACTUSOL ni enseña la frase", async () => {
    mockUser.mockResolvedValue({ role: "user" });
    const onCreated = jest.fn();
    const user = userEvent.setup();
    render(<CompanyCreateForm onCreated={onCreated} />);
    await user.type(screen.getByLabelText("Nombre fiscal *"), "Solo CRM SL");
    expect(screen.queryByRole("checkbox", { name: "Crear también en FACTUSOL" })).toBeNull();
    expect(screen.queryByText(FACTUSOL_WHY)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Crear empresa" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(mockFactusol).not.toHaveBeenCalled();
  });

  it("anti-duplicados al teclear el NIF: candidata con «Usar esta» y «Crear empresa» desactivado con el motivo", async () => {
    mockCheck.mockResolvedValue(check({
      duplicates: {
        crm: [{ id: "maison", name: "SAS La Maison de la Plaque", tax_id: null,
                vat: "FR16339753527", country: "FR", city: "Is-sur-Tille",
                factusol_company_id: "2760" }],
        factusol: { codcli: "2760", nombre: "LA MAISON DE LA PLAQUE", nif: "FR16339753527" },
        factusol_checked: true, factusol_error: null,
      },
    }));
    const onUseExisting = jest.fn();
    const user = userEvent.setup();
    render(<CompanyCreateForm onCreated={() => {}} onUseExisting={onUseExisting} />);
    await user.type(screen.getByLabelText("Nombre fiscal *"), "Maison bis");
    await user.type(screen.getByLabelText("NIF-IVA (VAT intracomunitario)"), "FR16339753527");
    const aviso = await screen.findByRole("alert");
    expect(aviso).toHaveTextContent("Ya existe en el CRM con ese NIF");
    // La tarjeta candidata: nombre, NIF (mono), población y vínculo.
    const tarjeta = within(aviso).getByRole("listitem");
    expect(tarjeta).toHaveTextContent("SAS La Maison de la Plaque");
    expect(tarjeta.querySelector(".mono")).toHaveTextContent("FR16339753527");
    expect(tarjeta).toHaveTextContent("Is-sur-Tille");
    expect(tarjeta).toHaveTextContent("En FACTUSOL · nº 2760");
    const usar = within(tarjeta).getByRole("button", { name: "Usar «SAS La Maison de la Plaque»" });
    expect(usar).toHaveTextContent("Usar esta");
    expect(usar.className).not.toContain("secondary");       // en FACTUSOL → primario
    // El botón de crear no deja seguir, y dice por qué.
    const crear = screen.getByRole("button", { name: "Crear empresa" });
    expect(crear).toBeDisabled();
    expect(crear).toHaveAccessibleDescription(
      "No se puede crear: ya existe «SAS La Maison de la Plaque» con ese NIF. Usa la existente o corrige el NIF.",
    );
    expect(screen.getByText(/Ya existe en FACTUSOL con ese NIF: cliente nº 2760/)).toBeInTheDocument();
    await user.click(usar);
    expect(onUseExisting).toHaveBeenCalledWith({ id: "maison", name: "SAS La Maison de la Plaque" });
    expect(mockCreate).not.toHaveBeenCalled();
    // Corregido el NIF (ya sin duplicado) vuelve a poder crear.
    mockCheck.mockResolvedValue(check());
    await user.clear(screen.getByLabelText("NIF-IVA (VAT intracomunitario)"));
    await user.type(screen.getByLabelText("NIF-IVA (VAT intracomunitario)"), "FR99999999999");
    await waitFor(() => expect(screen.getByRole("button", { name: "Crear empresa" })).toBeEnabled());
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("en la pantalla (sin onUseExisting) la candidata «Solo CRM» enlaza a su ficha con botón secundario", async () => {
    mockCheck.mockResolvedValue(check({
      duplicates: {
        crm: [{ id: "cadeau", name: "La Maison du Cadeau", tax_id: "B98765432", vat: null,
                country: "ES", city: "Madrid", factusol_company_id: null }],
        factusol: null, factusol_checked: true, factusol_error: null,
      },
    }));
    const user = userEvent.setup();
    render(<CompanyCreateForm onCreated={() => {}} />);
    await user.type(screen.getByLabelText("NIF / CIF"), "B98765432");
    const aviso = await screen.findByRole("alert");
    expect(aviso).toHaveTextContent("Solo CRM");
    expect(aviso).toHaveTextContent("Madrid");
    const abrir = within(aviso).getByRole("link", { name: "Abrir «La Maison du Cadeau»" });
    expect(abrir).toHaveAttribute("href", "/companies/cadeau");
    expect(abrir).toHaveTextContent("Abrir esta");
    expect(abrir.className).toContain("secondary");
    expect(screen.getByRole("button", { name: "Crear empresa" })).toBeDisabled();
  });

  it("si el alta en FACTUSOL falla, la empresa queda creada y se informa (no se pierde)", async () => {
    mockFactusol.mockRejectedValue(new Error("DELSOL caído"));
    const onCreated = jest.fn();
    const user = userEvent.setup();
    render(<CompanyCreateForm onCreated={onCreated} />);
    await user.type(screen.getByLabelText("Nombre fiscal *"), "Nueva SL");
    await screen.findByRole("checkbox", { name: "Crear también en FACTUSOL" });
    await user.click(screen.getByRole("button", { name: "Crear empresa" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(expect.objectContaining({
      company: expect.objectContaining({ id: "c-new" }),
      factusol: null,
      factusolError: expect.stringMatching(/DELSOL caído/),
    })));
  });

  it("el 409 del backend por NIF duplicado se enseña como error, sin crear", async () => {
    mockCreate.mockRejectedValue(new Error("Ya existe una empresa con ese NIF: «Duplicoder SL»"));
    mockUser.mockResolvedValue({ role: "user" });
    const user = userEvent.setup();
    render(<CompanyCreateForm onCreated={() => {}} />);
    await user.type(screen.getByLabelText("Nombre fiscal *"), "Duplicoder bis");
    await user.click(screen.getByRole("button", { name: "Crear empresa" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Ya existe una empresa con ese NIF/);
  });
});

describe("helpers de «Crear empresa»", () => {
  it("describeCheckedAt: hoy / ayer / fecha completa, siempre con la hora", () => {
    const now = new Date(2026, 8, 15, 12, 0, 0);
    expect(describeCheckedAt(new Date(2026, 8, 15, 11, 42).toISOString(), now)).toBe("hoy a las 11:42");
    expect(describeCheckedAt(new Date(2026, 8, 14, 9, 5).toISOString(), now)).toBe("ayer a las 09:05");
    expect(describeCheckedAt(new Date(2026, 7, 30, 18, 30).toISOString(), now)).toBe("el 30/08/2026 a las 18:30");
    expect(describeCheckedAt(null, now)).toBeNull();
    expect(describeCheckedAt("no es fecha", now)).toBeNull();
  });

  it("regimeConsequence: la consecuencia en la factura, no la etiqueta", () => {
    const v = (status: ViesStatus | null, applies = true) => ({ ...check().vies, status, applies });
    expect(regimeConsequence({ regime: "intracomunitario", vies: v("pendiente") }))
      .toBe("La factura saldrá sin IVA si el VAT es válido.");
    expect(regimeConsequence({ regime: "intracomunitario", vies: v("valido") }))
      .toBe("La factura saldrá sin IVA: el VAT está verificado en VIES.");
    expect(regimeConsequence({ regime: "nacional", vies: v("no_valido") }))
      .toBe("La factura saldrá con IVA: el VAT no es válido en VIES y no se puede eximir.");
    expect(regimeConsequence({ regime: "nacional", vies: v(null, false) }))
      .toBe("La factura saldrá con IVA español.");
    expect(regimeConsequence({ regime: "exportacion", vies: v(null, false) }))
      .toBe("La factura saldrá sin IVA: exportación fuera de la UE.");
  });
});
