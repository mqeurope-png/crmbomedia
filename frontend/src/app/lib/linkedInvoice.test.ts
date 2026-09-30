import { invoiceLabel } from "./linkedInvoice";

describe("invoiceLabel (factura vinculada)", () => {
  it("«serie-número» con la factura vinculada, sea cual sea el origen", () => {
    expect(invoiceLabel({
      factusol_invoice: { serie: 2, codigo: 526107, numero: "2-526107" },
      factusol_invoice_problem: null, factusol_invoice_number: "526107",
    })).toBe("2-526107");
  });

  it("sin serie: el número y «falta la serie»; sin factura: vacío", () => {
    expect(invoiceLabel({
      factusol_invoice: null, factusol_invoice_problem: "sin_serie",
      factusol_invoice_number: "260721",
    })).toBe("260721 · falta la serie");
    expect(invoiceLabel({
      factusol_invoice: null, factusol_invoice_problem: "sin_factura",
      factusol_invoice_number: null,
    })).toBe("");
  });
});
