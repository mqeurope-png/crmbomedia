import { render, screen } from "@testing-library/react";
import type { FormSubmissionRow } from "../../lib/formsApi";
import { SubmissionsList } from "./SubmissionsList";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

function row(over: Partial<FormSubmissionRow>): FormSubmissionRow {
  return {
    id: "s1",
    contact_id: "c1",
    is_spam: false,
    spam_reason: null,
    contact_action: null,
    recaptcha_score: null,
    ip_address: "1.2.3.4",
    utm_source: null,
    utm_medium: null,
    utm_campaign: null,
    referrer: null,
    landing_page: null,
    payload: { email: "a@b.com" },
    created_at: "2026-08-01T10:47:00Z",
    ...over,
  };
}

describe("SubmissionsList", () => {
  it("muestra el badge «Nuevo» cuando contact_action=created", () => {
    render(<SubmissionsList rows={[row({ id: "s1", contact_action: "created" })]} />);
    expect(screen.getByText(/Nuevo/)).toBeInTheDocument();
  });

  it("muestra el badge «Actualizado» cuando contact_action=updated", () => {
    render(<SubmissionsList rows={[row({ id: "s2", contact_action: "updated" })]} />);
    expect(screen.getByText(/Actualizado/)).toBeInTheDocument();
  });

  it("muestra el badge «Spam» cuando contact_action=spam", () => {
    render(
      <SubmissionsList
        rows={[row({ id: "s3", contact_action: "spam", is_spam: true, spam_reason: "honeypot" })]}
      />,
    );
    expect(screen.getByText(/🔴 Spam/)).toBeInTheDocument();
  });

  it("muestra «—» cuando no hay contact_action (submit pre-migración)", () => {
    render(<SubmissionsList rows={[row({ id: "s4", contact_action: null })]} />);
    expect(screen.getByLabelText("Sin dato")).toBeInTheDocument();
  });

  it("renderiza una fila por submit con enlace al contacto", () => {
    render(
      <SubmissionsList
        rows={[
          row({ id: "s1", contact_action: "created", contact_id: "c1" }),
          row({ id: "s2", contact_action: "updated", contact_id: "c2" }),
        ]}
      />,
    );
    const links = screen.getAllByRole("link", { name: /Ver contacto/ });
    expect(links).toHaveLength(2);
  });
});
