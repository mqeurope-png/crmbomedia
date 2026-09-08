import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CompanyLogoThumbnail } from "./CompanyLogoThumbnail";
import { downloadFactusolCompanyLogo } from "../../lib/erpApi";

jest.mock("../../lib/erpApi", () => ({
  downloadFactusolCompanyLogo: jest.fn(),
}));
const mockDownload = downloadFactusolCompanyLogo as jest.Mock;

beforeAll(() => {
  // jsdom no implementa object URLs.
  URL.createObjectURL = jest.fn(() => "blob:logo");
  URL.revokeObjectURL = jest.fn();
});

beforeEach(() => {
  mockDownload.mockReset();
  mockDownload.mockResolvedValue(new Blob(["x"], { type: "image/png" }));
});

describe("CompanyLogoThumbnail", () => {
  // test_settings_shows_current_logo_thumbnail
  it("muestra la miniatura del logo actual + nombre + botón de quitar", async () => {
    render(
      <CompanyLogoThumbnail
        serie={5} hasLogo filename="serie_5.png" onRemove={() => {}} />,
    );
    // el nombre del fichero es visible…
    expect(screen.getByText("serie_5.png")).toBeInTheDocument();
    // …y la miniatura se descarga y se pinta.
    expect(
      await screen.findByRole("img", { name: /Logo de la serie 5/i }),
    ).toBeInTheDocument();
    expect(mockDownload).toHaveBeenCalledWith(5);
    expect(screen.getByRole("button", { name: "Quitar" })).toBeInTheDocument();
  });

  it("indica «Sin logo» cuando no hay logo (no queda mudo)", () => {
    render(<CompanyLogoThumbnail serie={7} hasLogo={false} onRemove={() => {}} />);
    expect(screen.getByText("Sin logo")).toBeInTheDocument();
    expect(mockDownload).not.toHaveBeenCalled();
  });

  it("«Quitar» llama a onRemove", async () => {
    const onRemove = jest.fn();
    const user = userEvent.setup();
    render(
      <CompanyLogoThumbnail serie={5} hasLogo filename="serie_5.png" onRemove={onRemove} />,
    );
    await user.click(screen.getByRole("button", { name: "Quitar" }));
    expect(onRemove).toHaveBeenCalled();
  });
});
