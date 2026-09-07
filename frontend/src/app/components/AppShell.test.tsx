import { render, waitFor } from "@testing-library/react";
import { AppShell } from "./AppShell";
import { getCurrentUser } from "../lib/api";

const replace = jest.fn();
let pathname = "/contacts";
jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: jest.fn() }),
  usePathname: () => pathname,
}));
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, ...rest }: { children: React.ReactNode; href: string } & Record<string, unknown>) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));
jest.mock("../lib/api", () => ({
  getCurrentUser: jest.fn(),
  getStoredToken: jest.fn(() => "tok"),
  logout: jest.fn().mockResolvedValue(undefined),
}));
jest.mock("../lib/tasksApi", () => ({
  getMyBuckets: jest.fn().mockResolvedValue({ overdue: [], today: [] }),
}));
jest.mock("./GlobalSearch", () => ({ GlobalSearch: () => <div /> }));
jest.mock("../lib/useIdleTimeout", () => ({ useIdleTimeout: () => {} }));

const mockUser = getCurrentUser as jest.Mock;

function erpUser() {
  return { id: "p-1", role: "pedidos", full_name: "P", email: "p@x", is_active: true };
}

beforeEach(() => {
  replace.mockClear();
  window.localStorage.clear();
});

// test_erp_user_visiting_crm_url_is_redirected_with_notice
test("un usuario de ERP que teclea una URL del CRM es devuelto a /erp con aviso", async () => {
  pathname = "/contacts";
  mockUser.mockResolvedValue(erpUser());
  render(<AppShell><div>página del CRM</div></AppShell>);
  await waitFor(() =>
    expect(replace).toHaveBeenCalledWith("/erp?desde=%2Fcontacts"));
});

// test_login_redirects_erp_user_to_erp_home
test("tras login, un usuario de ERP en «/» aterriza en /erp", async () => {
  pathname = "/";
  mockUser.mockResolvedValue(erpUser());
  render(<AppShell><div>dashboard CRM</div></AppShell>);
  await waitFor(() =>
    expect(replace).toHaveBeenCalledWith("/erp?desde=%2F"));
});

test("un comercial NO es redirigido fuera del CRM", async () => {
  pathname = "/contacts";
  mockUser.mockResolvedValue({ id: "u-1", role: "user", full_name: "U", email: "u@x", is_active: true });
  render(<AppShell><div>crm</div></AppShell>);
  await waitFor(() => expect(mockUser).toHaveBeenCalled());
  expect(replace).not.toHaveBeenCalledWith(expect.stringContaining("/erp?desde"));
});
