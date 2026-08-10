import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EmailThreadToolbar } from "./EmailThreadToolbar";
import type { EmailThreadDetail } from "../../lib/emailsApi";

function makeThread(): EmailThreadDetail {
  return {
    id: "t-1",
    contact_id: null,
    initiated_by_user_id: "u-1",
    gmail_thread_id: "thr-1",
    gmail_account_user_id: "u-1",
    subject: "Asunto",
    participants: [],
    first_message_at: "2026-08-01T10:00:00Z",
    last_message_at: "2026-08-01T10:00:00Z",
    message_count: 1,
    has_unread_replies: false,
    is_archived: false,
    state: "inbox",
    is_starred: false,
    messages: [],
  } as unknown as EmailThreadDetail;
}

function setupToolbar() {
  const handlers = {
    onStarToggle: jest.fn(),
    onArchiveOrRestore: jest.fn(),
    onTrash: jest.fn(),
    onMarkUnread: jest.fn(),
    onSpam: jest.fn(),
    onMove: jest.fn(),
    onToggleLabel: jest.fn(),
    onReply: jest.fn(),
    onReplyAll: jest.fn(),
    onForward: jest.fn(),
  };
  render(
    <EmailThreadToolbar
      thread={makeThread()}
      folders={[]}
      labels={[]}
      appliedLabelIds={new Set()}
      {...handlers}
    />,
  );
  return handlers;
}

describe("EmailThreadToolbar — CRM-BANDEJA", () => {
  it("agrupa las acciones en 3 secciones con divisores", () => {
    setupToolbar();
    const container = document.body;
    expect(
      screen.getByRole("toolbar", { name: /Acciones del hilo/i }),
    ).toBeInTheDocument();
    // Grupo Estado: estrella, archivar, papelera, marcar no leído.
    expect(
      screen.getByRole("button", { name: /Destacar con estrella/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Archivar/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /papelera/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Marcar como no leído/i }),
    ).toBeInTheDocument();
    // Grupo Clasificar: spam, etiquetar, mover.
    expect(
      screen.getByRole("button", { name: /Marcar como spam/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Etiquetar el hilo/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Mover a carpeta/i }),
    ).toBeInTheDocument();
    // Grupo Acciones principales.
    expect(
      screen.getByRole("button", { name: /^Responder$/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Responder a todos/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Reenviar/i }),
    ).toBeInTheDocument();
    // Divisores verticales entre grupos.
    expect(
      container.querySelectorAll(".email-toolbar-divider").length,
    ).toBe(2);
  });

  it("cada icono lleva tooltip descriptivo (title)", () => {
    setupToolbar();
    expect(
      screen.getByRole("button", { name: /Marcar como no leído/i }),
    ).toHaveAttribute("title", "Marcar como no leído");
    expect(
      screen.getByRole("button", { name: /^Responder$/i }),
    ).toHaveAttribute("title", "Responder al remitente");
  });

  it("dispara los callbacks de las acciones principales", async () => {
    const user = userEvent.setup();
    const handlers = setupToolbar();
    await user.click(screen.getByRole("button", { name: /^Responder$/i }));
    await user.click(
      screen.getByRole("button", { name: /Responder a todos/i }),
    );
    await user.click(screen.getByRole("button", { name: /Reenviar/i }));
    await user.click(
      screen.getByRole("button", { name: /Marcar como no leído/i }),
    );
    expect(handlers.onReply).toHaveBeenCalled();
    expect(handlers.onReplyAll).toHaveBeenCalled();
    expect(handlers.onForward).toHaveBeenCalled();
    expect(handlers.onMarkUnread).toHaveBeenCalled();
  });
});
