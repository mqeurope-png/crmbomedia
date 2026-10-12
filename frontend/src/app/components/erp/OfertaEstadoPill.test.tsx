import { render, screen } from "@testing-library/react";
import { OfertaEstadoPill } from "./OfertaEstadoPill";

describe("OfertaEstadoPill", () => {
  it("cada estado con su palabra y su tono: borrador gris, enviada azul, caducada ámbar, convertida verde", () => {
    const { rerender } = render(<OfertaEstadoPill estado="borrador" />);
    expect(screen.getByText("Borrador")).toHaveClass("badge", "muted");
    rerender(<OfertaEstadoPill estado="enviada" />);
    expect(screen.getByText("Enviada")).toHaveClass("badge", "info");
    rerender(<OfertaEstadoPill estado="caducada" />);
    expect(screen.getByText("Caducada")).toHaveClass("badge", "warn");
    rerender(<OfertaEstadoPill estado="convertida" />);
    expect(screen.getByText("Convertida")).toHaveClass("badge", "ok");
  });

  it("«vencida» añade la nota (y el título) salvo que el estado ya lo diga", () => {
    const { rerender } = render(<OfertaEstadoPill estado="borrador" vencida />);
    const pill = screen.getByText(/Borrador/);
    expect(pill).toHaveTextContent("Borrador · vencida");
    expect(pill).toHaveAttribute("title", "La fecha de validez ya pasó.");
    rerender(<OfertaEstadoPill estado="caducada" vencida />);
    expect(screen.getByText(/Caducada/)).toHaveTextContent(/^Caducada$/);
    rerender(<OfertaEstadoPill estado="convertida" vencida />);
    expect(screen.getByText(/Convertida/)).toHaveTextContent(/^Convertida$/);
  });
});
