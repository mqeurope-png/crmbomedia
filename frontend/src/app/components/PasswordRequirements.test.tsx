import { render, screen } from "@testing-library/react";
import {
  isPasswordCompliant,
  PasswordRequirements,
  passwordChecks,
  PASSWORD_MIN_LENGTH,
} from "./PasswordRequirements";

/** La política es SOLO: mínimo 8, una mayúscula y un número (reflejo de
 *  backend/app/core/passwords.py). Ni minúscula ni «contraseña habitual». */

describe("PasswordRequirements", () => {
  it("la lista tiene exactamente tres puntos y marca ✓ con «Abcdefg1»", () => {
    render(<PasswordRequirements password="Abcdefg1" />);
    const items = screen.getAllByRole("listitem");
    expect(items.map((li) => li.textContent?.replace(/^[✓✗]\s*/, ""))).toEqual([
      "Mínimo 8 caracteres", "Al menos una letra mayúscula", "Al menos un número",
    ]);
    expect(items.every((li) => li.classList.contains("ok"))).toBe(true);
    expect(items.every((li) => li.textContent?.startsWith("✓"))).toBe(true);
  });

  it("mismo veredicto que el backend en los casos de la política", () => {
    expect(PASSWORD_MIN_LENGTH).toBe(8);
    expect(isPasswordCompliant("Abcdefg1")).toBe(true);     // 8, mayúscula, número
    expect(isPasswordCompliant("Abcdefg")).toBe(false);     // sin número
    expect(isPasswordCompliant("abcdefg1")).toBe(false);    // sin mayúscula
    expect(isPasswordCompliant("Abcdef1")).toBe(false);     // 7
    expect(isPasswordCompliant("PASSWORD1")).toBe(true);    // sin minúscula: vale
    expect(isPasswordCompliant("Password1")).toBe(true);    // ya no hay lista de habituales
  });

  it("marca ✗ solo en el punto que falla", () => {
    const checks = passwordChecks("abcdefg1");
    expect(checks.map((c) => [c.id, c.ok])).toEqual([
      ["length", true], ["upper", false], ["digit", true],
    ]);
  });
});
