import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import { Modal } from "./Modal";
import { ModalCloseButton } from "./ModalCloseButton";
import { useModalBehaviour } from "./useModalBehaviour";

/** Modal mínimo con el hook, como los del ERP / CRM hechos a mano. */
function TestModal({
  label,
  onClose,
  disabled = false,
  confirmClose,
  children,
}: {
  label: string;
  onClose: () => void;
  disabled?: boolean;
  confirmClose?: () => boolean;
  children?: React.ReactNode;
}) {
  const { overlayProps, requestClose } = useModalBehaviour({ onClose, disabled, confirmClose });
  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label={label} {...overlayProps}>
      <div className="modal-dialog erp-modal">
        <h2>{label}</h2>
        <ModalCloseButton onClose={requestClose} disabled={disabled} />
        <input aria-label={`Campo de ${label}`} />
        {children}
      </div>
    </div>
  );
}

/** Página con un botón que abre un modal, y dentro otro anidado. */
function Page({ onCloseOuter, onCloseInner }: { onCloseOuter?: () => void; onCloseInner?: () => void }) {
  const [outer, setOuter] = useState(false);
  const [inner, setInner] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOuter(true)}>Abrir</button>
      {outer ? (
        <TestModal label="Exterior" onClose={() => { onCloseOuter?.(); setOuter(false); }}>
          <button type="button" onClick={() => setInner(true)}>Abrir anidado</button>
          {inner ? (
            <TestModal label="Interior" onClose={() => { onCloseInner?.(); setInner(false); }} />
          ) : null}
        </TestModal>
      ) : null}
    </>
  );
}

describe("useModalBehaviour", () => {
  it("Esc cierra solo el modal de arriba cuando hay dos anidados", async () => {
    const user = userEvent.setup();
    const onCloseOuter = jest.fn();
    const onCloseInner = jest.fn();
    render(<Page onCloseOuter={onCloseOuter} onCloseInner={onCloseInner} />);
    await user.click(screen.getByRole("button", { name: "Abrir" }));
    await user.click(screen.getByRole("button", { name: "Abrir anidado" }));
    expect(screen.getByRole("dialog", { name: "Interior" })).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(onCloseInner).toHaveBeenCalledTimes(1);
    expect(onCloseOuter).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog", { name: "Interior" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Exterior" })).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(onCloseOuter).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("pulsar en la capa cierra; pulsar dentro del diálogo, no", () => {
    const onClose = jest.fn();
    render(<TestModal label="Ejemplo" onClose={onClose} />);
    fireEvent.mouseDown(screen.getByRole("heading", { name: "Ejemplo" }));
    fireEvent.mouseDown(screen.getByLabelText("Campo de Ejemplo"));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.mouseDown(screen.getByRole("dialog", { name: "Ejemplo" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("en un modal anidado, pulsar en su capa cierra solo el de arriba", async () => {
    const user = userEvent.setup();
    const onCloseOuter = jest.fn();
    const onCloseInner = jest.fn();
    render(<Page onCloseOuter={onCloseOuter} onCloseInner={onCloseInner} />);
    await user.click(screen.getByRole("button", { name: "Abrir" }));
    await user.click(screen.getByRole("button", { name: "Abrir anidado" }));
    fireEvent.mouseDown(screen.getByRole("dialog", { name: "Interior" }));
    expect(onCloseInner).toHaveBeenCalledTimes(1);
    expect(onCloseOuter).not.toHaveBeenCalled();
  });

  it("ocupado (disabled): ni Esc, ni la capa, ni el ✕ cierran", async () => {
    const user = userEvent.setup();
    const onClose = jest.fn();
    render(<TestModal label="Guardando" onClose={onClose} disabled />);
    await user.keyboard("{Escape}");
    fireEvent.mouseDown(screen.getByRole("dialog", { name: "Guardando" }));
    const aspa = screen.getByRole("button", { name: "Cerrar" });
    expect(aspa).toBeDisabled();
    await user.click(aspa);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("un Esc ya gestionado dentro (preventDefault) no cierra el modal", () => {
    const onClose = jest.fn();
    render(<TestModal label="Ejemplo" onClose={onClose} />);
    const campo = screen.getByLabelText("Campo de Ejemplo");
    campo.addEventListener("keydown", (e) => e.preventDefault());
    fireEvent.keyDown(campo, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("ignora el Esc de los menús de TinyMCE (.tox)", () => {
    const onClose = jest.fn();
    render(
      <>
        <TestModal label="Firma" onClose={onClose} />
        <div className="tox tox-silver-sink"><button type="button">Menú</button></div>
      </>,
    );
    fireEvent.keyDown(screen.getByRole("button", { name: "Menú" }), { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("al abrir, el foco entra al ✕; al cerrar vuelve al botón que lo abrió", async () => {
    const user = userEvent.setup();
    render(<Page />);
    const abrir = screen.getByRole("button", { name: "Abrir" });
    await user.click(abrir);
    expect(screen.getByRole("button", { name: "Cerrar" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(abrir).toHaveFocus();
  });

  it("no roba el foco a un campo con autoFocus, y aun así lo devuelve al cerrar", async () => {
    function AutoFocusPage() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>Nueva</button>
          {open ? <AutoFocusModal onClose={() => setOpen(false)} /> : null}
        </>
      );
    }
    function AutoFocusModal({ onClose }: { onClose: () => void }) {
      const { overlayProps, requestClose } = useModalBehaviour({ onClose });
      return (
        <div className="modal-overlay" role="dialog" aria-label="Nueva" {...overlayProps}>
          <div className="modal-dialog erp-modal">
            <h2>Nueva</h2>
            <ModalCloseButton onClose={requestClose} />
            <input aria-label="Nombre" autoFocus />
          </div>
        </div>
      );
    }
    const user = userEvent.setup();
    render(<AutoFocusPage />);
    const nueva = screen.getByRole("button", { name: "Nueva" });
    await user.click(nueva);
    expect(screen.getByLabelText("Nombre")).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Cerrar" }));
    expect(nueva).toHaveFocus();
  });

  it("confirmClose que devuelve false impide cerrar (Esc, capa y ✕)", async () => {
    const user = userEvent.setup();
    const onClose = jest.fn();
    const confirmClose = jest.fn(() => false);
    render(<TestModal label="Cambios" onClose={onClose} confirmClose={confirmClose} />);
    await user.keyboard("{Escape}");
    fireEvent.mouseDown(screen.getByRole("dialog", { name: "Cambios" }));
    await user.click(screen.getByRole("button", { name: "Cerrar" }));
    expect(confirmClose).toHaveBeenCalledTimes(3);
    expect(onClose).not.toHaveBeenCalled();
    confirmClose.mockReturnValue(true);
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("un onClose en línea no reengancha el modal ni le roba el foco en cada render", async () => {
    function Parent() {
      const [n, setN] = useState(0);
      return (
        <TestModal label="Contador" onClose={() => undefined}>
          <button type="button" onClick={() => setN((v) => v + 1)}>Sumar {n}</button>
        </TestModal>
      );
    }
    const user = userEvent.setup();
    render(<Parent />);
    const campo = screen.getByLabelText("Campo de Contador");
    await user.click(campo);
    await user.click(screen.getByRole("button", { name: "Sumar 0" }));
    await user.click(campo);
    await user.click(screen.getByRole("button", { name: "Sumar 1" }));
    expect(screen.getByRole("button", { name: "Sumar 2" })).toHaveFocus();
  });
});

describe("Modal (compartido)", () => {
  it("devuelve el foco al disparador y cierra solo el de arriba con Esc", async () => {
    function Shared() {
      const [a, setA] = useState(false);
      const [b, setB] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setA(true)}>Abrir A</button>
          <Modal open={a} onClose={() => setA(false)} title="A">
            <button type="button" onClick={() => setB(true)}>Abrir B</button>
          </Modal>
          <Modal open={b} onClose={() => setB(false)} title="B">
            <p>Contenido B</p>
          </Modal>
        </>
      );
    }
    const user = userEvent.setup();
    render(<Shared />);
    const abrirA = screen.getByRole("button", { name: "Abrir A" });
    await user.click(abrirA);
    const abrirB = screen.getByRole("button", { name: "Abrir B" });
    await user.click(abrirB);
    expect(screen.getAllByRole("dialog")).toHaveLength(2);
    await user.keyboard("{Escape}");
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect(abrirB).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(abrirA).toHaveFocus();
  });
});
