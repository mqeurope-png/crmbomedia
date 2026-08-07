import { render, screen } from "@testing-library/react";
import { EmailThreadList } from "./EmailThreadList";
import { listEmailThreads, type EmailThread } from "../../lib/emailsApi";
import { getCurrentUser, getUsers } from "../../lib/api";
import { listUserAliases } from "../../lib/userAliasesApi";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));
jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: jest.fn(), push: jest.fn() }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/emails",
  useParams: () => ({}),
}));
jest.mock("../../lib/emailsApi", () => ({
  listEmailThreads: jest.fn(),
  starThread: jest.fn(),
  unstarThread: jest.fn(),
}));
jest.mock("../../lib/api", () => ({
  getCurrentUser: jest.fn(),
  getUsers: jest.fn(),
}));
jest.mock("../../lib/userAliasesApi", () => ({
  listUserAliases: jest.fn(),
}));

const mockThreads = listEmailThreads as jest.Mock;

function thread(over: Partial<EmailThread> & { id: string }): EmailThread {
  return {
    contact_id: null,
    initiated_by_user_id: "u1",
    gmail_thread_id: `g-${over.id}`,
    gmail_account_user_id: "u1",
    subject: "Asunto",
    participants: [],
    first_message_at: "2026-08-01T10:00:00Z",
    last_message_at: "2026-08-01T10:00:00Z",
    message_count: 1,
    has_unread_replies: false,
    is_archived: false,
    contact_name: "Cliente",
    tracking: {},
    state: "inbox",
    labels: [],
    ...over,
  };
}

beforeEach(() => {
  (getCurrentUser as jest.Mock).mockResolvedValue({ id: "u1", role: "user" });
  (getUsers as jest.Mock).mockResolvedValue([]);
  (listUserAliases as jest.Mock).mockResolvedValue([]);
});

describe("EmailThreadList row · CRM-GMAIL Parte G (chip Spam)", () => {
  it("muestra el chip «Spam» en el thread marcado y no en el limpio", async () => {
    mockThreads.mockResolvedValue({
      items: [
        thread({ id: "spammy", subject: "Oferta rara", has_spam: true }),
        thread({ id: "limpio", subject: "Pedido normal", has_spam: false }),
      ],
      total: 2,
    });
    render(<EmailThreadList folders={[]} labels={[]} refreshKey={0} />);

    // El thread con spam trae el chip.
    const spamChip = await screen.findByText(/🔴 Spam/);
    expect(spamChip).toBeInTheDocument();
    // Solo uno de los dos threads lo tiene.
    expect(screen.getAllByText(/🔴 Spam/)).toHaveLength(1);
    // Ambos threads se listan (el spam NO se oculta).
    expect(screen.getByText("Oferta rara")).toBeInTheDocument();
    expect(screen.getByText("Pedido normal")).toBeInTheDocument();
  });
});
