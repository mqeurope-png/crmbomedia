import { render, waitFor } from "@testing-library/react";
import { EmailThreadList } from "./EmailThreadList";
import { listEmailThreads } from "../../lib/emailsApi";
import { getCurrentUser, getUsers } from "../../lib/api";

let searchParams = new URLSearchParams();

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
  usePathname: () => "/emails",
  useParams: () => ({}),
  useSearchParams: () => searchParams,
}));
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
jest.mock("../../lib/emailsApi", () => ({
  listEmailThreads: jest.fn().mockResolvedValue({ items: [], total: 0 }),
  starThread: jest.fn(),
  unstarThread: jest.fn(),
}));
jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn().mockResolvedValue({
    id: "u-1",
    role: "user",
    is_active: true,
    full_name: "Norma",
    email: "norma@bomedia.net",
  }),
  getUsers: jest.fn().mockResolvedValue([]),
}));
jest.mock("./AliasFilterDropdown", () => ({
  AliasFilterDropdown: () => null,
}));
jest.mock("./EmailBulkActionsBar", () => ({
  EmailBulkActionsBar: () => null,
}));
jest.mock("./EmailEventBadges", () => ({ EmailEventBadges: () => null }));

const mockedList = listEmailThreads as jest.MockedFunction<
  typeof listEmailThreads
>;

function lastFilters() {
  const call = mockedList.mock.calls[mockedList.mock.calls.length - 1];
  return call[2] as Record<string, unknown>;
}

describe("EmailThreadList — CRM-BANDEJA-FIX-SPAM", () => {
  beforeEach(() => {
    mockedList.mockClear();
    (getCurrentUser as jest.Mock).mockClear();
    (getUsers as jest.Mock).mockClear();
  });

  it("la Bandeja (state=inbox) pide con exclude_spam=true", async () => {
    searchParams = new URLSearchParams("state=inbox");
    render(<EmailThreadList folders={[]} labels={[]} refreshKey={0} />);
    await waitFor(() => expect(mockedList).toHaveBeenCalled());
    expect(lastFilters()).toMatchObject({
      state: "inbox",
      exclude_spam: true,
    });
  });

  it("la Bandeja por defecto (sin state) también excluye spam", async () => {
    searchParams = new URLSearchParams();
    render(<EmailThreadList folders={[]} labels={[]} refreshKey={0} />);
    await waitFor(() => expect(mockedList).toHaveBeenCalled());
    expect(lastFilters()).toMatchObject({
      state: "inbox",
      exclude_spam: true,
    });
  });

  it("la carpeta Spam usa state=spam y NO fuerza exclude_spam", async () => {
    searchParams = new URLSearchParams("state=spam");
    render(<EmailThreadList folders={[]} labels={[]} refreshKey={0} />);
    await waitFor(() => expect(mockedList).toHaveBeenCalled());
    const filters = lastFilters();
    expect(filters.state).toBe("spam");
    expect(filters.exclude_spam).toBeUndefined();
  });
});
