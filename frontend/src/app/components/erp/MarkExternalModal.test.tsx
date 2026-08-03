import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MarkExternalModal } from "./MarkExternalModal";

describe("MarkExternalModal", () => {
  it("titula en singular cuando count=1", () => {
    render(<MarkExternalModal count={1} onConfirm={() => {}} onCancel={() => {}} />);
    expect(
      screen.getByRole("heading", { name: /Marcar pedido como procesado externamente/ }),
    ).toBeInTheDocument();
  });

  it("titula en plural con el número cuando count>1", () => {
    render(<MarkExternalModal count={3} onConfirm={() => {}} onCancel={() => {}} />);
    expect(
      screen.getByRole("heading", { name: /Marcar 3 pedidos como procesados externamente/ }),
    ).toBeInTheDocument();
  });

  it("confirma pasando la nota escrita (trim)", async () => {
    const onConfirm = jest.fn();
    const user = userEvent.setup();
    render(<MarkExternalModal count={1} onConfirm={onConfirm} onCancel={() => {}} />);
    await user.type(screen.getByLabelText("Nota"), "  Excel antiguo  ");
    await user.click(screen.getByRole("button", { name: /Marcar como externalizado/ }));
    expect(onConfirm).toHaveBeenCalledWith("Excel antiguo");
  });

  it("cancelar invoca onCancel", async () => {
    const onCancel = jest.fn();
    const user = userEvent.setup();
    render(<MarkExternalModal count={1} onConfirm={() => {}} onCancel={onCancel} />);
    await user.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(onCancel).toHaveBeenCalled();
  });
});
