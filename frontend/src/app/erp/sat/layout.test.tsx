import { render, screen } from "@testing-library/react";
import SatLayout from "./layout";
import { getCurrentUser } from "../../lib/api";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: jest.fn() }),
  usePathname: () => "/erp/sat",
}));
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, className }: { children: React.ReactNode; href: string; className?: string }) => (
    <a href={href} className={className}>{children}</a>
  ),
}));
jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(),
  getStoredToken: () => "token",
}));

const mockUser = getCurrentUser as jest.Mock;

/** El enlace de arriba a la derecha de la Cola SAT: el rol «ERP · Taller
 *  (SAT)» no tiene CRM, así que vuelve al inicio del ERP; los demás, al CRM. */
describe("SatLayout · «Volver»", () => {
  it("rol SAT: «Volver al ERP» y lleva a /erp", async () => {
    mockUser.mockResolvedValue({ id: "s", role: "sat", full_name: "Sat", email: "s@x", is_active: true });
    render(<SatLayout><p>cola</p></SatLayout>);
    const link = await screen.findByRole("link", { name: "Volver al ERP" });
    expect(link).toHaveAttribute("href", "/erp");
    expect(screen.queryByRole("link", { name: "Volver al CRM" })).toBeNull();
  });

  it("admin: sigue «Volver al CRM» a la bandeja", async () => {
    mockUser.mockResolvedValue({ id: "a", role: "admin", full_name: "Admin", email: "a@x", is_active: true });
    render(<SatLayout><p>cola</p></SatLayout>);
    expect(await screen.findByRole("link", { name: "Volver al CRM" })).toHaveAttribute("href", "/erp/orders");
    expect(screen.queryByRole("link", { name: "Volver al ERP" })).toBeNull();
  });
});
