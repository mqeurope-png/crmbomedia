import { render, screen } from "@testing-library/react";
import { ContactKeyDataStrip } from "./ContactKeyDataStrip";
import type { Contact } from "../../lib/api";

/** «Origen del lead» es de dónde vino el lead, no con qué integraciones está
 *  sincronizado. Como todo contacto que se sube a Brevo gana una fila de
 *  `external_references`, el origen de TODOS los leads de formulario se
 *  enseñaba como «Brevo · default» en cuanto se sincronizaban. */

const BASE = {
  id: "c1", email: "otrotest@otrotest.com", first_name: "Otro",
  last_name: "Test", phone: null, status: "new", lead_score: 0,
  origin: null, external_references_summary: undefined,
} as unknown as Contact;

function pinta(over: Partial<Contact>) {
  render(
    <ContactKeyDataStrip
      contact={{ ...BASE, ...over } as Contact}
      companyName={null}
      lastActivityAt={null}
      primaryPhone={null}
      onPatch={jest.fn()}
    />,
  );
}

function valorDe(etiqueta: string): string | null {
  const label = screen.queryByText(etiqueta);
  return label?.parentElement?.querySelector(".contact-strip-value")
    ?.textContent ?? null;
}

test("el origen es el del CRM aunque el contacto esté en Brevo", () => {
  pinta({
    origin: "Formulario web · boprint.net (español)",
    external_references_summary: [
      { system: "brevo", account_id: "default" },
    ] as Contact["external_references_summary"],
  });
  expect(valorDe("Origen del lead")).toBe(
    "Formulario web · boprint.net (español)");
  // El vínculo con la integración no desaparece: va aparte.
  expect(valorDe("Sincronizado con")).toBe("Brevo · default");
});

test("sin origen en el CRM, el vínculo sirve de respaldo", () => {
  pinta({
    origin: null,
    external_references_summary: [
      { system: "agilecrm", account_id: "artisjet-europe" },
    ] as Contact["external_references_summary"],
  });
  expect(valorDe("Origen del lead")).toBe("AgileCRM · artisjet-europe");
});

test("sin origen y sin vínculos no se inventa nada", () => {
  pinta({ origin: "   " });
  expect(valorDe("Origen del lead")).toBe("—");
  expect(screen.queryByText("Sincronizado con")).not.toBeInTheDocument();
});
