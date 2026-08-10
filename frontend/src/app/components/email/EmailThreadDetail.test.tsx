import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EmailThreadDetail, formatToSummary } from "./EmailThreadDetail";
import type {
  EmailMessage,
  EmailThreadDetail as EmailThreadDetailType,
} from "../../lib/emailsApi";

jest.mock("../../lib/emailsApi", () => ({
  downloadEmailAttachment: jest.fn(),
}));

function makeMessage(overrides: Partial<EmailMessage>): EmailMessage {
  return {
    id: "m-1",
    thread_id: "t-1",
    gmail_message_id: "g-1",
    direction: "inbound",
    from_email: "cliente@acme.com",
    from_name: "Cliente Acme",
    to_emails: ["info@bomedia.net"],
    cc_emails: null,
    subject: "Asunto",
    body_html: null,
    body_text: "Cuerpo del mensaje",
    snippet: "Cuerpo del mensaje",
    sent_at: "2026-08-01T10:00:00Z",
    contact_id: null,
    created_by_user_id: null,
    read_at: null,
    ...overrides,
  } as EmailMessage;
}

function makeThread(messages: EmailMessage[]): EmailThreadDetailType {
  return {
    id: "t-1",
    contact_id: null,
    initiated_by_user_id: "u-1",
    gmail_thread_id: "thr-1",
    gmail_account_user_id: "u-1",
    subject: "Asunto",
    participants: ["cliente@acme.com", "info@bomedia.net"],
    first_message_at: "2026-08-01T10:00:00Z",
    last_message_at: "2026-08-03T10:00:00Z",
    message_count: messages.length,
    has_unread_replies: false,
    is_archived: false,
    messages,
  } as EmailThreadDetailType;
}

const threeMessages = () => [
  makeMessage({ id: "m-1", snippet: "Primer mensaje del hilo" }),
  makeMessage({
    id: "m-2",
    snippet: "Segundo mensaje",
    from_name: null,
    from_email: "otro@acme.com",
  }),
  makeMessage({
    id: "m-3",
    direction: "outbound",
    from_email: "info@bomedia.net",
    from_name: "Bart",
    to_emails: ["cliente@acme.com"],
    snippet: "Respuesta nuestra",
    body_html: "<p>Hola</p>",
  }),
];

describe("EmailThreadDetail — CRM-BANDEJA", () => {
  it("renderiza N mensajes plegados y solo el último expandido", () => {
    render(
      <EmailThreadDetail
        thread={makeThread(threeMessages())}
        eventsByMessage={{}}
      />,
    );
    expect(screen.getByTestId("email-message-m-1")).toHaveAttribute(
      "data-expanded",
      "false",
    );
    expect(screen.getByTestId("email-message-m-2")).toHaveAttribute(
      "data-expanded",
      "false",
    );
    expect(screen.getByTestId("email-message-m-3")).toHaveAttribute(
      "data-expanded",
      "true",
    );
    // El plegado muestra el snippet en una línea.
    expect(screen.getByText("Primer mensaje del hilo")).toBeInTheDocument();
  });

  it("click en el header de un mensaje plegado lo expande", async () => {
    const user = userEvent.setup();
    render(
      <EmailThreadDetail
        thread={makeThread(threeMessages())}
        eventsByMessage={{}}
      />,
    );
    const headers = screen.getAllByRole("button", {
      name: /Expandir mensaje/i,
    });
    await user.click(headers[0]);
    expect(screen.getByTestId("email-message-m-1")).toHaveAttribute(
      "data-expanded",
      "true",
    );
  });

  it("«Expandir todo» expande todos y luego «Colapsar todo» los pliega", async () => {
    const user = userEvent.setup();
    render(
      <EmailThreadDetail
        thread={makeThread(threeMessages())}
        eventsByMessage={{}}
      />,
    );
    await user.click(
      screen.getByRole("button", { name: /Expandir todo/i }),
    );
    for (const id of ["m-1", "m-2", "m-3"]) {
      expect(screen.getByTestId(`email-message-${id}`)).toHaveAttribute(
        "data-expanded",
        "true",
      );
    }
    await user.click(
      screen.getByRole("button", { name: /Colapsar todo/i }),
    );
    for (const id of ["m-1", "m-2", "m-3"]) {
      expect(screen.getByTestId(`email-message-${id}`)).toHaveAttribute(
        "data-expanded",
        "false",
      );
    }
  });

  it("chips de estado: «Enviado desde CRM» en outbound, «Respuesta entrante» en inbound", () => {
    render(
      <EmailThreadDetail
        thread={makeThread(threeMessages())}
        eventsByMessage={{}}
      />,
    );
    expect(screen.getByText(/Enviado desde CRM/)).toBeInTheDocument();
    expect(screen.getAllByText(/Respuesta entrante/).length).toBe(2);
  });

  it("el body expandido no tiene scroll interno (iframe overflow hidden, altura natural)", () => {
    render(
      <EmailThreadDetail
        thread={makeThread(threeMessages())}
        eventsByMessage={{}}
      />,
    );
    // El último mensaje (expandido) renderiza body_html en iframe
    // auto-height con overflow hidden — el scroll es del panel.
    const iframe = screen.getByTitle("Mensaje m-3");
    expect(iframe).toHaveStyle({ overflow: "hidden" });
    expect(iframe).toHaveClass("email-html-preview-auto");
  });

  it("mensaje expandido muestra «Detalles» y el bloque con from/to/fecha", async () => {
    const user = userEvent.setup();
    render(
      <EmailThreadDetail
        thread={makeThread(threeMessages())}
        eventsByMessage={{}}
      />,
    );
    await user.click(screen.getByRole("button", { name: /Detalles/i }));
    expect(screen.getByText("De")).toBeInTheDocument();
    expect(screen.getByText("Para")).toBeInTheDocument();
    expect(screen.getByText("Fecha")).toBeInTheDocument();
  });

  it("adjuntos como chips con botón de descarga cuando hay binario", () => {
    const messages = [
      makeMessage({
        id: "m-att",
        direction: "outbound",
        attachments: [
          {
            id: "a-1",
            filename: "contrato.pdf",
            mime_type: "application/pdf",
            size_bytes: 2048,
            downloadable: true,
          },
          {
            id: null,
            filename: "grande.zip",
            mime_type: "application/zip",
            size_bytes: 999,
            downloadable: false,
          },
        ],
      }),
    ];
    render(
      <EmailThreadDetail
        thread={makeThread(messages)}
        eventsByMessage={{}}
      />,
    );
    expect(screen.getByText("contrato.pdf")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Descargar contrato.pdf/i }),
    ).toBeInTheDocument();
    // Sin binario → chip visible pero sin botón de descarga.
    expect(screen.getByText("grande.zip")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Descargar grande.zip/i }),
    ).not.toBeInTheDocument();
  });
});

describe("formatToSummary", () => {
  it("resume destinatarios estilo Gmail", () => {
    const own = new Set(["info@bomedia.net"]);
    expect(formatToSummary(["info@bomedia.net"], own)).toBe("para mí");
    expect(formatToSummary(["ana@x.com", "luis@y.com"])).toBe(
      "para ana, luis",
    );
    expect(
      formatToSummary(["a@x.com", "b@x.com", "c@x.com", "d@x.com"]),
    ).toBe("para a, b +2 más");
  });
});
