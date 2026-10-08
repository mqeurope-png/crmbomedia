import { render, screen } from "@testing-library/react";
import { SegmentValueEditor } from "./SegmentValueEditor";
import type { SegmentFieldDescriptor } from "../lib/api";

/** Parte 5 — con 25 formularios, se filtra por la web y por el idioma del
 *  formulario. Los textos los manda el backend (`enum_labels`), así que no
 *  hay que repetir aquí la lista de webs ni la de idiomas. */

const web: SegmentFieldDescriptor = {
  key: "lead_web",
  label: "Web del formulario",
  type: "enum",
  comparators: ["eq", "neq", "in", "not_in"],
  enum_values: ["mboprinters", "mbolasers", "marca-sin-texto"],
  enum_labels: { mboprinters: "mboprinters.com", mbolasers: "mbolasers.com" },
};

describe("SegmentValueEditor · web e idioma del formulario", () => {
  it("usa el texto que manda el backend para cada valor", () => {
    render(<SegmentValueEditor spec={web} comparator="in" value={[]} onChange={() => {}} />);
    expect(screen.getByText("mboprinters.com")).toBeInTheDocument();
    expect(screen.getByText("mbolasers.com")).toBeInTheDocument();
  });

  it("un valor sin texto se enseña tal cual (una web nueva no desaparece)", () => {
    render(<SegmentValueEditor spec={web} comparator="in" value={[]} onChange={() => {}} />);
    expect(screen.getByText("marca-sin-texto")).toBeInTheDocument();
  });

  it("sin enum_labels sigue valiendo la tabla de siempre", () => {
    const origen: SegmentFieldDescriptor = {
      key: "origin_system",
      label: "Sistema de origen",
      type: "enum",
      comparators: ["eq", "in"],
      enum_values: ["agilecrm", "brevo"],
    };
    render(<SegmentValueEditor spec={origen} comparator="in" value={[]} onChange={() => {}} />);
    expect(screen.getByText("AgileCRM")).toBeInTheDocument();
  });
});
