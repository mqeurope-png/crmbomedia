import { render, screen, waitFor } from "@testing-library/react";
import { GoogleConnectionBanner } from "./GoogleConnectionBanner";
import { getCurrentUser } from "../lib/api";
import { getGoogleStatus } from "../lib/googleApi";

jest.mock("../lib/api", () => ({ getCurrentUser: jest.fn() }));
jest.mock("../lib/googleApi", () => ({ getGoogleStatus: jest.fn() }));

const mockUser = getCurrentUser as jest.Mock;
const mockStatus = getGoogleStatus as jest.Mock;

function isoInHours(h: number): string {
  return new Date(Date.now() + h * 3_600_000).toISOString();
}

function setup(role: string, status: Record<string, unknown>) {
  mockUser.mockResolvedValue({ id: "u1", role });
  mockStatus.mockResolvedValue({
    connected: true,
    status: "active",
    ...status,
  });
  return render(<GoogleConnectionBanner />);
}

async function expectSilent(container: HTMLElement) {
  await waitFor(() => expect(mockStatus).toHaveBeenCalled());
  await waitFor(() =>
    expect(container.querySelector(".google-banner")).toBeNull(),
  );
}

describe("GoogleConnectionBanner", () => {
  it("se silencia si la app OAuth está verificada (app_verified)", async () => {
    const { container } = setup("admin", {
      app_verified: true,
      refresh_token_expires_at: isoInHours(1),
      refresh_token_expired: true,
    });
    await expectSilent(container);
  });

  it("no se muestra a comerciales (role user), solo a admins", async () => {
    const { container } = setup("user", {
      refresh_token_expiring_soon: true,
      refresh_token_expires_at: isoInHours(24),
    });
    await expectSilent(container);
  });

  it("verde/silencio si el refresh token vence en >48h", async () => {
    const { container } = setup("admin", {
      refresh_token_expiring_soon: false,
      refresh_token_expires_at: isoInHours(72),
    });
    await expectSilent(container);
  });

  it("ámbar (warn) si vence en <48h", async () => {
    const { container } = setup("admin", {
      refresh_token_expiring_soon: true,
      refresh_token_expires_at: isoInHours(24),
    });
    await screen.findByRole("link", { name: /Reconectar/i });
    expect(container.querySelector(".google-banner-warn")).toBeInTheDocument();
  });

  it("rojo (danger) si el refresh token ya caducó", async () => {
    const { container } = setup("admin", {
      refresh_token_expired: true,
      refresh_token_expires_at: isoInHours(-1),
    });
    await screen.findByRole("link", { name: /Reconectar/i });
    expect(container.querySelector(".google-banner-danger")).toBeInTheDocument();
  });

  it("rojo (danger) si la conexión está caída (needs_reconnect)", async () => {
    const { container } = setup("admin", {
      status: "needs_reconnect",
      refresh_token_expires_at: isoInHours(72),
    });
    await screen.findByRole("link", { name: /Reconectar/i });
    expect(container.querySelector(".google-banner-danger")).toBeInTheDocument();
  });
});
