import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WooWebhookModal } from "./WooWebhookModal";
import { getWooWebhookStatus, regenerateWooWebhookSecret } from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  getWooWebhookStatus: jest.fn(),
  regenerateWooWebhookSecret: jest.fn(),
}));

const mockStatus = getWooWebhookStatus as jest.Mock;
const mockRegen = regenerateWooWebhookSecret as jest.Mock;

function status(over = {}) {
  return {
    webhook_url: "https://bo-crm.example/webhooks/woocommerce/boprint",
    webhook_secret_last4: "ab12",
    last_received_at: "2026-08-03T18:00:00Z",
    count_24h: 5,
    errors_24h: 0,
    topics_received_24h: ["order.created", "order.updated"],
    ...over,
  };
}

beforeEach(() => {
  mockStatus.mockReset();
  mockRegen.mockReset();
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText: jest.fn().mockResolvedValue(undefined) },
    configurable: true,
  });
});

describe("WooWebhookModal", () => {
  it("muestra la URL y el secreto enmascarado (últimos 4)", async () => {
    mockStatus.mockResolvedValue(status());
    render(<WooWebhookModal storeId="s1" storeName="boprint" onClose={() => {}} />);
    expect(
      await screen.findByDisplayValue("https://bo-crm.example/webhooks/woocommerce/boprint"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Secret enmascarado")).toHaveTextContent("••••••••ab12");
    // Instrucciones de WordPress presentes.
    expect(screen.getByText(/WooCommerce → Ajustes/)).toBeInTheDocument();
  });

  it("regenerar muestra el secret nuevo completo con aviso", async () => {
    mockStatus.mockResolvedValue(status());
    mockRegen.mockResolvedValue({
      webhook_secret: "brand-new-secret-9999",
      webhook_url: "https://bo-crm.example/webhooks/woocommerce/boprint",
    });
    jest.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    render(<WooWebhookModal storeId="s1" storeName="boprint" onClose={() => {}} />);
    await screen.findByLabelText("Secret enmascarado");
    await user.click(screen.getByRole("button", { name: /Regenerar/ }));
    await waitFor(() => expect(mockRegen).toHaveBeenCalledWith("s1"));
    expect(await screen.findByDisplayValue("brand-new-secret-9999")).toBeInTheDocument();
    expect(screen.getByText(/no volverá a ser visible completo/)).toBeInTheDocument();
  });

  it("regenerar cancelado (confirm=false) no llama al endpoint", async () => {
    mockStatus.mockResolvedValue(status());
    jest.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    render(<WooWebhookModal storeId="s1" storeName="boprint" onClose={() => {}} />);
    await screen.findByLabelText("Secret enmascarado");
    await user.click(screen.getByRole("button", { name: /Regenerar/ }));
    expect(mockRegen).not.toHaveBeenCalled();
  });

  it("muestra badge de errores cuando errors_24h > 0", async () => {
    mockStatus.mockResolvedValue(status({ errors_24h: 3 }));
    render(<WooWebhookModal storeId="s1" storeName="boprint" onClose={() => {}} />);
    expect(await screen.findByText(/3 errores/)).toBeInTheDocument();
  });

  it("cerrar invoca onClose", async () => {
    mockStatus.mockResolvedValue(status());
    const onClose = jest.fn();
    const user = userEvent.setup();
    render(<WooWebhookModal storeId="s1" storeName="boprint" onClose={onClose} />);
    await screen.findByLabelText("Secret enmascarado");
    await user.click(screen.getByRole("button", { name: "Cerrar" }));
    expect(onClose).toHaveBeenCalled();
  });
});
