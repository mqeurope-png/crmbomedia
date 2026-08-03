import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WooStoreForm } from "./WooStoreForm";

describe("WooStoreForm", () => {
  it("deshabilita Guardar hasta rellenar todos los campos", async () => {
    const user = userEvent.setup();
    render(<WooStoreForm onSubmit={() => {}} onCancel={() => {}} />);
    const save = screen.getByRole("button", { name: /Guardar tienda/ });
    expect(save).toBeDisabled();
    await user.type(screen.getByLabelText("Slug de tienda"), "boprint");
    await user.type(screen.getByLabelText("Nombre visible"), "boprint.net");
    await user.type(screen.getByLabelText("URL base"), "https://boprint.net");
    await user.type(screen.getByLabelText("Consumer Key"), "ck_abc");
    await user.type(screen.getByLabelText("Consumer Secret"), "cs_xyz");
    expect(save).not.toBeDisabled();
  });

  it("envía el payload con Consumer Key y Consumer Secret", async () => {
    const onSubmit = jest.fn();
    const user = userEvent.setup();
    render(<WooStoreForm onSubmit={onSubmit} onCancel={() => {}} />);
    await user.type(screen.getByLabelText("Slug de tienda"), "artisjet");
    await user.type(screen.getByLabelText("Nombre visible"), "artisjet");
    await user.type(screen.getByLabelText("URL base"), "https://artisjet-printers.eu");
    await user.type(screen.getByLabelText("Consumer Key"), "ck1");
    await user.type(screen.getByLabelText("Consumer Secret"), "cs2");
    await user.click(screen.getByRole("button", { name: /Guardar tienda/ }));
    expect(onSubmit).toHaveBeenCalledWith({
      account_id: "artisjet", display_name: "artisjet",
      base_url: "https://artisjet-printers.eu",
      consumer_key: "ck1", consumer_secret: "cs2",
      external_cutoff_date: null,
    });
  });

  it("incluye la fecha de corte en el payload cuando se rellena (B-2-fix4)", async () => {
    const onSubmit = jest.fn();
    const user = userEvent.setup();
    render(<WooStoreForm onSubmit={onSubmit} onCancel={() => {}} />);
    await user.type(screen.getByLabelText("Slug de tienda"), "boprint");
    await user.type(screen.getByLabelText("Nombre visible"), "boprint");
    await user.type(screen.getByLabelText("URL base"), "https://boprint.net");
    await user.type(screen.getByLabelText("Consumer Key"), "ck1");
    await user.type(screen.getByLabelText("Consumer Secret"), "cs2");
    fireEvent.change(screen.getByLabelText("Fecha de corte"), {
      target: { value: "2026-08-03" },
    });
    await user.click(screen.getByRole("button", { name: /Guardar tienda/ }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ external_cutoff_date: "2026-08-03" }),
    );
  });

  it("normaliza el slug a minúsculas y sin espacios", async () => {
    const user = userEvent.setup();
    render(<WooStoreForm onSubmit={() => {}} onCancel={() => {}} />);
    const input = screen.getByLabelText("Slug de tienda") as HTMLInputElement;
    await user.type(input, "BoPrint");
    expect(input.value).toBe("boprint");
  });

  it("cancelar invoca onCancel", async () => {
    const onCancel = jest.fn();
    const user = userEvent.setup();
    render(<WooStoreForm onSubmit={() => {}} onCancel={onCancel} />);
    await user.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("no muestra los secretos en texto plano", () => {
    render(<WooStoreForm onSubmit={() => {}} onCancel={() => {}} />);
    expect(screen.getByLabelText("Consumer Key")).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("Consumer Secret")).toHaveAttribute("type", "password");
  });
});
