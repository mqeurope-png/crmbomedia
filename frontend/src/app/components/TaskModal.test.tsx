import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import { TaskModal } from "./TaskModal";

jest.mock("../lib/tasksApi", () => ({
  createTask: jest.fn(),
  updateTask: jest.fn(),
}));
jest.mock("../lib/googleApi", () => ({
  getGoogleStatus: jest.fn().mockResolvedValue({ connected: false, selected_calendar: null }),
}));

function Abridor({ onClose }: { onClose: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>+ Tarea</button>
      {open ? (
        <TaskModal onClose={() => { onClose(); setOpen(false); }} />
      ) : null}
    </>
  );
}

describe("TaskModal · ✕, Esc, clic fuera y foco", () => {
  it("se abre con el foco en el título, el ✕ cierra y el foco vuelve al botón", async () => {
    const onClose = jest.fn();
    const user = userEvent.setup();
    render(<Abridor onClose={onClose} />);
    const abrir = screen.getByRole("button", { name: "+ Tarea" });
    await user.click(abrir);
    // El autoFocus del título se respeta (no se lo roba el ✕).
    expect(screen.getByLabelText("Título")).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Cerrar" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(abrir).toHaveFocus();
  });

  it("Esc cierra (también escribiendo en un campo) y pulsar fuera, también", async () => {
    const onClose = jest.fn();
    const user = userEvent.setup();
    render(<Abridor onClose={onClose} />);
    const abrir = screen.getByRole("button", { name: "+ Tarea" });
    await user.click(abrir);
    await user.type(screen.getByLabelText("Título"), "Llamar{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(abrir).toHaveFocus();

    await user.click(abrir);
    fireEvent.mouseDown(screen.getByRole("dialog"));
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
