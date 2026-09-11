import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { initialPayment, paymentReady, PaymentStep } from "./PaymentStep";
import type { PaymentIntentInput } from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  getContrapartidas: jest.fn(() => Promise.resolve([
    { codigo: "6", nombre: "Bomedia Sabadell" }, { codigo: "8", nombre: "Streamtec Sabadell" },
  ])),
  getFactusolFormasPago: jest.fn(() => Promise.resolve([
    { codigo: "002", nombre: "Transferencia" }, { codigo: "011", nombre: "Recibo domiciliado" },
  ])),
}));

function Harness({ initial, onChange }: {
  initial: PaymentIntentInput; onChange: (v: PaymentIntentInput) => void;
}) {
  const [value, setValue] = useState(initial);
  return (
    <PaymentStep value={value} onChange={(v) => { setValue(v); onChange(v); }} />
  );
}

describe("PaymentStep — paso de confirmación de pago (opción B)", () => {
  it("arranca «sin pago» con la forma de pago del documento y sin cuenta", async () => {
    const onChange = jest.fn();
    render(<Harness initial={initialPayment("011", "Recibo domiciliado")} onChange={onChange} />);
    expect(screen.getByLabelText("Sin pago")).toBeChecked();
    expect(screen.getByLabelText("Pagado")).not.toBeChecked();
    // La forma del documento se preselecciona aunque el catálogo aún no llegue.
    expect(screen.getByLabelText("Forma de pago")).toHaveValue("011");
    expect(screen.queryByLabelText("Cuenta del cobro")).not.toBeInTheDocument();
    expect(screen.getByText(/Solo se apunta la forma de pago/)).toBeInTheDocument();
    expect(paymentReady(initialPayment("011"))).toBe(true);
  });

  it("«Pagado» pide la cuenta (obligatoria) y la fecha, y avisa de que no se emite factura", async () => {
    const onChange = jest.fn();
    const user = userEvent.setup();
    render(<Harness initial={initialPayment("002", "Transferencia")} onChange={onChange} />);
    await user.click(screen.getByLabelText("Pagado"));
    expect(screen.getByLabelText("Fecha del cobro")).toHaveValue(
      new Date().toISOString().slice(0, 10),
    );
    expect(screen.getByText(/No se emite ninguna factura/)).toBeInTheDocument();
    const last = () => onChange.mock.calls[onChange.mock.calls.length - 1][0] as PaymentIntentInput;
    expect(last().paid).toBe(true);
    expect(paymentReady(last())).toBe(false);          // sin cuenta no vale
    await screen.findByRole("option", { name: "6 · Bomedia Sabadell" });
    await user.selectOptions(screen.getByLabelText("Cuenta del cobro"), "6");
    expect(last().contrapartida).toBe("6");
    expect(paymentReady(last())).toBe(true);
    // Cambiar la forma de pago arrastra su nombre del catálogo.
    await user.selectOptions(screen.getByLabelText("Forma de pago"), "011");
    expect(last().forma_pago).toBe("011");
    expect(last().forma_pago_nombre).toBe("Recibo domiciliado");
  });
});
