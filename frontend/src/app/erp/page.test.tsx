import { render, screen, waitFor } from "@testing-library/react";
import ErpHome from "./page";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, ...rest }: { children: React.ReactNode; href: string } & Record<string, unknown>) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));
jest.mock("../lib/api", () => ({
  getCurrentUser: jest.fn().mockResolvedValue({
    id: "p", role: "pedidos", full_name: "Bart Uno", email: "b@x", is_active: true,
  }),
}));
jest.mock("../lib/erpApi", () => ({
  listPendingApproval: jest.fn().mockResolvedValue([{}, {}, {}]),
  getSatQueue: jest.fn().mockResolvedValue({ preparing: [{}], ready_for_pickup: [{}] }),
}));

describe("ErpHome", () => {
  it("muestra accesos de ERP y contadores para un usuario de ERP", async () => {
    window.history.replaceState({}, "", "/erp");
    render(<ErpHome />);
    expect(await screen.findByText("ERP · Pedidos")).toBeInTheDocument();
    expect(screen.queryByText("Contactos")).not.toBeInTheDocument();
    // Contador de pedidos pendientes (best-effort).
    expect(
      await screen.findByText("Pedidos pendientes de aprobación"),
    ).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("enseña el aviso cuando se llega desde una URL del CRM (?desde)", async () => {
    window.history.replaceState({}, "", "/erp?desde=%2Fcontacts");
    render(<ErpHome />);
    expect(await screen.findByText(/es del CRM/i)).toBeInTheDocument();
    // y limpia la query de la barra.
    await waitFor(() => expect(window.location.search).toBe(""));
  });
});
