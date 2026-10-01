import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { VincularEmpresaFactusolModal } from "./VincularEmpresaFactusolModal";
import {
  createFactusolCustomerAndLink,
  linkFactusolCustomer,
  searchFactusolCustomers,
} from "../../lib/erpApi";

/** «Vincular empresa»: cliente FACTUSOL ↔ empresa del CRM, sin salir del flujo
 *  de «Crear pedido». Crear con los datos de FACTUSOL ya puestos, o enlazar una
 *  existente (nunca una que ya es de otro cliente FACTUSOL). */

jest.mock("../../lib/erpApi", () => ({
  searchFactusolCustomers: jest.fn(),
  createFactusolCustomerAndLink: jest.fn(),
  linkFactusolCustomer: jest.fn(),
  alreadyLinkedHolder: jest.fn(() => null),
}));
jest.mock("../CompanySearch", () => ({
  CompanySearch: ({ onPick }: { onPick: (c: unknown) => void }) => (
    <div>
      <button type="button" onClick={() => onPick({ id: "c-libre", name: "Jap Ediciones", factusol_company_id: null })}>
        ELEGIR LIBRE
      </button>
      <button type="button" onClick={() => onPick({ id: "c-otra", name: "Otra SL", factusol_company_id: "999" })}>
        ELEGIR OTRA
      </button>
    </div>
  ),
}));

const CLIENTE = {
  codcli: "385", nombre: "JAP EDICIONES S.L.", nif: "B12345678", nofcli: "JAP EDICIONES S.L.",
  noccli: null, nifcli: "B12345678", domcli: "C/ Mayor 1", pobcli: "Madrid", cpocli: "28001",
  procli: "Madrid", paicli: "724", pais_iso2: "ES", emacli: "admin@jap.es", telcli: " 910000000 ",
  crm_link: null, factusol_matches_crm_id: null,
};

beforeEach(() => {
  jest.clearAllMocks();
  (searchFactusolCustomers as jest.Mock).mockResolvedValue([CLIENTE]);
});

it("crea la empresa con los datos de FACTUSOL ya puestos y la vincula", async () => {
  const user = userEvent.setup();
  const onLinked = jest.fn();
  (createFactusolCustomerAndLink as jest.Mock).mockResolvedValue({
    company_id: "c-nueva", factusol_codcli: "385", created: true,
  });
  render(<VincularEmpresaFactusolModal codcli="385" clienteNombre="JAP EDICIONES S.L."
                                       onClose={jest.fn()} onLinked={onLinked} />);
  await waitFor(() => expect(screen.getByLabelText("CIF / NIF")).toHaveValue("B12345678"));
  expect(screen.getByLabelText("Dirección")).toHaveValue("C/ Mayor 1");
  expect(screen.getByLabelText("País (código)")).toHaveValue("ES");
  expect(screen.getByText("Email en FACTUSOL: admin@jap.es")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Crear empresa y vincular" }));
  await waitFor(() => expect(createFactusolCustomerAndLink).toHaveBeenCalledWith({
    factusol_codcli: "385",
    factusol_customer_data: expect.objectContaining({
      nombre: "JAP EDICIONES S.L.", nif: "B12345678", direccion: "C/ Mayor 1",
      ciudad: "Madrid", cp: "28001", provincia: "Madrid", pais: "ES",
      email: "admin@jap.es", telefono: "910000000",
    }),
  }));
  expect(onLinked).toHaveBeenCalledWith({ id: "c-nueva", name: "JAP EDICIONES S.L." });
});

it("enlaza una empresa existente libre", async () => {
  const user = userEvent.setup();
  const onLinked = jest.fn();
  (linkFactusolCustomer as jest.Mock).mockResolvedValue({});
  render(<VincularEmpresaFactusolModal codcli="385" onClose={jest.fn()} onLinked={onLinked} />);
  await user.click(screen.getByRole("button", { name: "ELEGIR LIBRE" }));
  await waitFor(() => expect(linkFactusolCustomer).toHaveBeenCalledWith({
    crm_type: "company", crm_id: "c-libre", factusol_codcli: "385",
  }));
  expect(onLinked).toHaveBeenCalledWith({ id: "c-libre", name: "Jap Ediciones" });
});

it("no re-vincula una empresa que ya es de otro cliente FACTUSOL", async () => {
  const user = userEvent.setup();
  const onLinked = jest.fn();
  render(<VincularEmpresaFactusolModal codcli="385" onClose={jest.fn()} onLinked={onLinked} />);
  await user.click(screen.getByRole("button", { name: "ELEGIR OTRA" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("ya está vinculada al cliente FACTUSOL 999");
  expect(linkFactusolCustomer).not.toHaveBeenCalled();
  expect(onLinked).not.toHaveBeenCalled();
});
