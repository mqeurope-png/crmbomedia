import type { Contact } from "../../lib/api";
import {
  buildPayload,
  draftFromContact,
  etiquetaCampoEditado,
} from "./ContactEditForm";

function contacto(custom: Record<string, unknown> | null): Contact {
  return {
    id: "c1",
    first_name: "Ana",
    email: "ana@example.com",
    custom_fields: custom,
  } as unknown as Contact;
}

describe("campos personalizados en el modal Editar", () => {
  it("manda solo las claves que cambian, no el JSON entero", () => {
    const inicial = draftFromContact(
      contacto({ Horario: "9-14", NIVEL: 5, "CONTACTO Persona": "Marta" }),
      [],
    );
    const editado = {
      ...inicial,
      custom_fields: { ...inicial.custom_fields, Horario: "10-18" },
    };
    expect(buildPayload(inicial, editado)).toEqual({
      custom_fields: { Horario: "10-18" },
    });
  });

  it("un campo vaciado se manda vacío (la clave se queda)", () => {
    const inicial = draftFromContact(contacto({ Horario: "9-14" }), []);
    const editado = { ...inicial, custom_fields: { Horario: "" } };
    expect(buildPayload(inicial, editado)).toEqual({ custom_fields: { Horario: "" } });
  });

  it("sin cambios en los campos personalizados no los manda", () => {
    const inicial = draftFromContact(contacto({ Horario: "9-14", NIVEL: 5 }), []);
    const editado = { ...inicial, first_name: "Ana María" };
    expect(buildPayload(inicial, editado)).toEqual({ first_name: "Ana María" });
  });
});

describe("aviso de campos editados a mano", () => {
  it("nombra cada clave de los campos personalizados", () => {
    expect(etiquetaCampoEditado("custom_fields.Horario", {})).toBe(
      "Campo personalizado «Horario»",
    );
  });

  it("los demás campos, con su etiqueta o su nombre", () => {
    const etiquetas = { phone: "Teléfono", custom_fields: "Campos personalizados (todos)" };
    expect(etiquetaCampoEditado("phone", etiquetas)).toBe("Teléfono");
    expect(etiquetaCampoEditado("custom_fields", etiquetas)).toBe(
      "Campos personalizados (todos)",
    );
    expect(etiquetaCampoEditado("job_title", etiquetas)).toBe("job_title");
  });
});
