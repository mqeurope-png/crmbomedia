import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EmailFiltersBar } from "./EmailFiltersBar";

const push = jest.fn();
let searchParams = new URLSearchParams();

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  useSearchParams: () => searchParams,
}));

describe("EmailFiltersBar — CRM-BANDEJA filtros rápidos", () => {
  beforeEach(() => {
    searchParams = new URLSearchParams();
    push.mockClear();
  });

  it("chip «Con adjuntos» activa has_attachments=true en la URL", async () => {
    const user = userEvent.setup();
    render(<EmailFiltersBar />);
    await user.click(
      screen.getByRole("button", { name: /Con adjuntos/i }),
    );
    expect(push).toHaveBeenCalledWith("/emails?has_attachments=true");
  });

  it("chip «Con contacto CRM» activa has_contact=true en la URL", async () => {
    const user = userEvent.setup();
    render(<EmailFiltersBar />);
    await user.click(
      screen.getByRole("button", { name: /Con contacto CRM/i }),
    );
    expect(push).toHaveBeenCalledWith("/emails?has_contact=true");
  });

  it("los chips son acumulables: adjuntos + no leídos conviven", async () => {
    searchParams = new URLSearchParams("has_attachments=true");
    const user = userEvent.setup();
    render(<EmailFiltersBar />);
    // El chip activo se marca.
    expect(
      screen.getByRole("button", { name: /Con adjuntos/i }),
    ).toHaveClass("is-active");
    // Activar No leídos NO borra has_attachments.
    await user.click(screen.getByRole("button", { name: /No leídos/i }));
    const url = push.mock.calls[0][0] as string;
    expect(url).toContain("has_attachments=true");
    expect(url).toContain("has_unread=true");
  });

  it("volver a pulsar un chip activo lo desactiva", async () => {
    searchParams = new URLSearchParams("has_contact=true");
    const user = userEvent.setup();
    render(<EmailFiltersBar />);
    await user.click(
      screen.getByRole("button", { name: /Con contacto CRM/i }),
    );
    expect(push).toHaveBeenCalledWith("/emails");
  });

  it("«Limpiar filtros» aparece con los chips nuevos y los borra", async () => {
    searchParams = new URLSearchParams("has_attachments=true&has_contact=true");
    const user = userEvent.setup();
    render(<EmailFiltersBar />);
    await user.click(
      screen.getByRole("button", { name: /Limpiar filtros/i }),
    );
    expect(push).toHaveBeenCalledWith("/emails");
  });
});
