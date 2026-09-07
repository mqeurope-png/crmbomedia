import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { User } from "../lib/api";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { UserMenu } from "./UserMenu";

const push = jest.fn();
const replace = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace }),
  usePathname: () => "/erp",
}));
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, ...rest }: { children: React.ReactNode; href: string } & Record<string, unknown>) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));
jest.mock("../lib/api", () => ({ logout: jest.fn().mockResolvedValue(undefined) }));
jest.mock("../lib/tasksApi", () => ({
  getMyBuckets: jest.fn().mockResolvedValue({ overdue: [], today: [] }),
}));
jest.mock("./GlobalSearch", () => ({ GlobalSearch: () => <div /> }));

function makeUser(role: User["role"]): User {
  return { id: "u-1", email: `${role}@x.com`, full_name: "Bart Uno", role, is_active: true } as User;
}

beforeEach(() => {
  push.mockClear();
  replace.mockClear();
  window.localStorage.clear();
});

// test_erp_only_user_sees_only_erp_menu
describe("Sidebar por modo", () => {
  it("un usuario de ERP ve solo el menú de ERP", () => {
    render(
      <Sidebar user={makeUser("pedidos")} mode="erp"
        collapsed={false} onToggleCollapsed={() => {}} onCloseDrawer={() => {}} />,
    );
    expect(screen.getByText("ERP · Pedidos")).toBeInTheDocument();
    expect(screen.queryByText("Contactos")).not.toBeInTheDocument();
    expect(screen.queryByText("Emails")).not.toBeInTheDocument();
    expect(screen.queryByText("Marketing")).not.toBeInTheDocument();
  });
});

// test_header_shows_bohub_erp_in_erp_mode
describe("TopBar por modo", () => {
  it("en modo ERP la marca dice «BoHub ERP» y enlaza al inicio del ERP", () => {
    render(<TopBar user={makeUser("pedidos")} userLoaded mode="erp" onToggleDrawer={() => {}} />);
    const brand = screen.getByRole("link", { name: /BoHub ERP/i });
    expect(brand).toHaveAttribute("href", "/erp");
    expect(screen.queryByRole("link", { name: /BoHub CRM/i })).not.toBeInTheDocument();
  });

  it("en modo CRM la marca dice «BoHub CRM» y enlaza al dashboard", () => {
    render(<TopBar user={makeUser("admin")} userLoaded mode="crm" onToggleDrawer={() => {}} />);
    const brand = screen.getByRole("link", { name: /BoHub CRM/i });
    expect(brand).toHaveAttribute("href", "/");
  });
});

// test_admin_mode_switch_persists
describe("UserMenu — conmutador de modo (admin)", () => {
  it("el admin cambia a ERP: persiste y navega; y no aparece para no-admins", async () => {
    const u = userEvent.setup();
    render(<UserMenu user={makeUser("admin")} mode="crm" />);
    await u.click(screen.getByRole("button", { name: /Bart Uno/i }));
    await u.click(screen.getByRole("menuitem", { name: /Cambiar a modo ERP/i }));
    // Persistido por usuario + navegado al inicio del ERP.
    expect(window.localStorage.getItem("crmbo:appmode:u-1")).toBe("erp");
    expect(push).toHaveBeenCalledWith("/erp");
  });

  it("un comercial NO ve el conmutador", async () => {
    const u = userEvent.setup();
    render(<UserMenu user={makeUser("user")} mode="crm" />);
    await u.click(screen.getByRole("button", { name: /Bart Uno/i }));
    expect(screen.queryByRole("menuitem", { name: /Cambiar a modo/i })).not.toBeInTheDocument();
  });
});
