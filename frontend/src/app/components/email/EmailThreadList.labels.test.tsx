import { render, screen, within } from "@testing-library/react";
import { EmailThreadList } from "./EmailThreadList";
import {
  listEmailThreads,
  type EmailLabel,
  type EmailThread,
} from "../../lib/emailsApi";

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

function label(overrides: Partial<EmailLabel>): EmailLabel {
  return {
    id: "lbl-1",
    name: "AA Facturas",
    color: "#fb4c2f",
    text_color: "#ffffff",
    sort_order: 0,
    ...overrides,
  } as EmailLabel;
}

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

describe("EmailThreadList — CRM-ETIQUETAS-EN-BANDEJA", () => {
  beforeEach(() => {
    searchParams = new URLSearchParams("state=inbox");
    mockedList.mockReset();
  });

  it("renderiza los chips de etiquetas delante del asunto", async () => {
    mockedList.mockResolvedValue({
      items: [
        thread({
          id: "con",
          subject: "RE: Tu factura",
          labels: [
            label({ id: "l1", name: "- Bart - importante" }),
            label({ id: "l2", name: "AA Facturas" }),
          ],
        }),
      ],
      total: 1,
    });
    const { container } = render(
      <EmailThreadList folders={[]} labels={[]} refreshKey={0} />,
    );
    expect(await screen.findByText("RE: Tu factura")).toBeInTheDocument();
    const chips = screen.getByTestId("thread-label-chips");
    expect(within(chips).getByText("- Bart - importante")).toBeInTheDocument();
    expect(within(chips).getByText("AA Facturas")).toBeInTheDocument();
    // Van DENTRO del bloque del asunto (izquierda), no en la meta de la
    // derecha junto a la fecha.
    expect(
      container.querySelector(".email-list-subject .email-list-labels"),
    ).toBeInTheDocument();
    expect(
      container.querySelector(".email-list-meta .email-list-labels"),
    ).not.toBeInTheDocument();
  });

  it("con más de 3 etiquetas muestra 3 chips + «+N» con el resto en el tooltip", async () => {
    mockedList.mockResolvedValue({
      items: [
        thread({
          id: "muchas",
          subject: "Hilo con 5 etiquetas",
          labels: [
            label({ id: "l1", name: "Uno" }),
            label({ id: "l2", name: "Dos" }),
            label({ id: "l3", name: "Tres" }),
            label({ id: "l4", name: "Cuatro" }),
            label({ id: "l5", name: "Cinco" }),
          ],
        }),
      ],
      total: 1,
    });
    render(<EmailThreadList folders={[]} labels={[]} refreshKey={0} />);
    expect(await screen.findByText("Hilo con 5 etiquetas")).toBeInTheDocument();
    expect(screen.getByText("Tres")).toBeInTheDocument();
    expect(screen.queryByText("Cuatro")).not.toBeInTheDocument();
    const more = screen.getByText("+2");
    expect(more).toHaveAttribute("title", "Cuatro, Cinco");
  });

  it("el chip usa el color de fondo y de texto de la etiqueta", async () => {
    mockedList.mockResolvedValue({
      items: [
        thread({
          id: "color",
          subject: "Coloreado",
          labels: [
            label({ id: "l1", name: "Amarilla", color: "#fbe983", text_color: "#000000" }),
          ],
        }),
      ],
      total: 1,
    });
    render(<EmailThreadList folders={[]} labels={[]} refreshKey={0} />);
    const chip = await screen.findByText("Amarilla");
    // Color pleno (no diluido con alpha) + texto de contraste de Gmail.
    expect(chip).toHaveStyle({
      backgroundColor: "#fbe983",
      color: "#000000",
    });
    expect(chip).toHaveAttribute("title", "Amarilla");
  });

  it("una fila sin etiquetas no pinta el bloque de chips", async () => {
    mockedList.mockResolvedValue({
      items: [thread({ id: "pelada", subject: "Sin etiquetas" })],
      total: 1,
    });
    render(<EmailThreadList folders={[]} labels={[]} refreshKey={0} />);
    expect(await screen.findByText("Sin etiquetas")).toBeInTheDocument();
    expect(screen.queryByTestId("thread-label-chips")).not.toBeInTheDocument();
  });
});
