import { COURIERS, suggestCourier } from "./couriers";

describe("suggestCourier (formato del nº de seguimiento)", () => {
  it("1Z + 16 → UPS; 0033 + 18 dígitos → CTT Express", () => {
    expect(suggestCourier("1ZFV12345678901234")).toBe("UPS");
    expect(suggestCourier("1zfv 1234 5678 9012 34")).toBe("UPS");
    expect(suggestCourier("0033260080539700026674")).toBe("CTT Express");
  });

  it("lo que no se reconoce con seguridad no propone nada", () => {
    expect(suggestCourier("")).toBeNull();
    expect(suggestCourier(null)).toBeNull();
    expect(suggestCourier("1Z123")).toBeNull();
    expect(suggestCourier("00331234")).toBeNull();
    expect(suggestCourier("ABC123456")).toBeNull();
  });

  it("la lista habitual es la acordada", () => {
    expect(COURIERS).toEqual([
      "UPS", "CTT Express", "MRW", "GLS", "DSV", "FedEx", "DHL", "Correos Express",
      "Seitrans", "TNT", "DB Schenker", "MBE",
    ]);
  });
});
