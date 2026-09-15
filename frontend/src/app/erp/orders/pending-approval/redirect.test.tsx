import { render } from "@testing-library/react";
import { redirect } from "next/navigation";
import PendingApprovalRedirect from "./page";

/** ERP · Lote 2 D — la antigua Cola PEDIDOS es ahora la cola «Por revisar»
 *  de la bandeja: la ruta se queda solo para redirigir. */

jest.mock("next/navigation", () => ({ redirect: jest.fn() }));

describe("ERP · /erp/orders/pending-approval", () => {
  it("redirige a la bandeja filtrada por «Por revisar»", () => {
    render(<PendingApprovalRedirect />);
    expect(redirect).toHaveBeenCalledTimes(1);
    expect(redirect).toHaveBeenCalledWith("/erp/orders?queue=por_revisar");
  });
});
