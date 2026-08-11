import { render, screen } from "@testing-library/react";
import { EmailThreadList } from "./EmailThreadList";
import { listEmailThreads, type EmailThread } from "../../lib/emailsApi";

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
  listEmailThreads: jest.fn(),
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
jest.mock("./AliasFilterDropdown", () => ({ AliasFilterDropdown: () => null }));
jest.mock("./EmailBulkActionsBar", () => ({ EmailBulkActionsBar: () => null }));
jest.mock("./EmailEventBadges", () => ({ EmailEventBadges: () => null }));

const mockedList = listEmailThreads as jest.MockedFunction<
  typeof listEmailThreads
>;

function thread(overrides: Partial<EmailThread>): EmailThread {
  return {
    id: overrides.id ?? "t-1",
    contact_id: null,
    initiated_by_user_id: "u-1",
    gmail_thread_id: overrides.id ?? "t-1",
    gmail_account_user_id: "u-1",
    subject: "Asunto",
    participants: [],
    first_message_at: "2026-08-01T10:00:00Z",
    last_message_at: "2026-08-01T10:00:00Z",
    message_count: 1,
    has_unread_replies: false,
    is_archived: false,
    ...overrides,
  } as EmailThread;
}

describe("EmailThreadList — CRM-ADJUNTOS-UX clip", () => {
  beforeEach(() => {
    searchParams = new URLSearchParams("state=inbox");
    mockedList.mockReset();
  });

  it("muestra el clip 📎 en las filas con has_attachments y no en el resto", async () => {
    mockedList.mockResolvedValue({
      items: [
        thread({ id: "con", subject: "Con adjunto", has_attachments: true }),
        thread({ id: "sin", subject: "Sin adjunto", has_attachments: false }),
      ],
      total: 2,
    });
    const { container } = render(
      <EmailThreadList folders={[]} labels={[]} refreshKey={0} />,
    );
    expect(await screen.findByText("Con adjunto")).toBeInTheDocument();
    // Un solo clip, en la fila con adjuntos.
    const clips = container.querySelectorAll(".email-list-clip");
    expect(clips.length).toBe(1);
    expect(
      screen.getByLabelText("Con adjuntos"),
    ).toBeInTheDocument();
  });
});
