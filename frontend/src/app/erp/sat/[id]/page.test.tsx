import { render } from "@testing-library/react";
import SatOrderWorkRedirect from "./page";

const mockReplace = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), replace: mockReplace }),
}));

describe("/erp/sat/[id] (antiguo modo trabajo)", () => {
  it("ya no es una pantalla completa: vuelve a la Cola SAT (preparar va en modal)", () => {
    const { container } = render(<SatOrderWorkRedirect />);
    expect(mockReplace).toHaveBeenCalledWith("/erp/sat");
    expect(container).toBeEmptyDOMElement();
  });
});
