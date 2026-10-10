import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  ContactPipelinesSummaryCard,
  fueraDePlazo,
} from "./ContactPipelinesSummaryCard";
import { listContactPipelines, type ContactPipelineSummary } from "../../lib/api";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, ...rest }: { children: React.ReactNode; href: string }
    & Record<string, unknown>) => <a href={href} {...rest}>{children}</a>,
}));
jest.mock("../../lib/api", () => ({ listContactPipelines: jest.fn() }));

const mockList = listContactPipelines as jest.Mock;

function fila(over: Partial<ContactPipelineSummary> = {}): ContactPipelineSummary {
  return {
    assignment_id: "a1", pipeline_id: "p1", pipeline_name: "Ventas B2B",
    pipeline_color: null, stage_id: "s1", stage_name: "Contactado", stage_color: null,
    stage_position: 1, is_won: false, is_lost: false, days_in_stage: 5,
    entered_stage_at: "2026-10-05T10:00:00Z", added_to_pipeline_at: "2026-10-01T10:00:00Z",
    target_days: 3, is_overdue: true, ...over,
  };
}

beforeEach(() => mockList.mockReset());

describe("ContactPipelinesSummaryCard (Resumen de la ficha)", () => {
  it("enseña pipeline, etapa, días en la etapa, plazo y si se ha pasado", async () => {
    mockList.mockResolvedValue([
      fila(),
      fila({ assignment_id: "a2", pipeline_id: "p2", pipeline_name: "Distribuidores",
             stage_name: "Cualificado", days_in_stage: 1, target_days: 7, is_overdue: false }),
      fila({ assignment_id: "a3", pipeline_id: "p3", pipeline_name: "Ferias",
             stage_name: "Cerrado ganado", days_in_stage: 40, target_days: null,
             is_overdue: false, is_won: true }),
    ]);
    render(<ContactPipelinesSummaryCard contactId="c1" />);
    expect(await screen.findByRole("link", { name: "Ventas B2B" })).toHaveAttribute("href", "/pipelines/p1");
    expect(mockList).toHaveBeenCalledWith("c1");
    expect(screen.getByText("Contactado")).toBeInTheDocument();
    expect(screen.getByText("5 días en la etapa · plazo 3 días")).toBeInTheDocument();
    expect(screen.getByText("Fuera de plazo")).toHaveClass("bad");
    // Dentro del plazo: sin aviso.
    expect(screen.getByText("1 día en la etapa · plazo 7 días")).toBeInTheDocument();
    expect(screen.getAllByText("Fuera de plazo")).toHaveLength(1);
    // Ganado: ni plazo ni aviso, aunque lleve 40 días.
    expect(screen.getByText("40 días en la etapa · sin plazo")).toBeInTheDocument();
    expect(screen.getByText("Ganado")).toBeInTheDocument();
  });

  it("sin pipelines lo dice en una línea y «Ver pipelines» lleva a la pestaña", async () => {
    mockList.mockResolvedValue([]);
    const onSeeAll = jest.fn();
    const user = userEvent.setup();
    render(<ContactPipelinesSummaryCard contactId="c1" onSeeAll={onSeeAll} />);
    expect(await screen.findByText("El contacto no está en ningún pipeline.")).toBeInTheDocument();
    expect(screen.getByText("Pipelines vinculados")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Ver pipelines/ }));
    expect(onSeeAll).toHaveBeenCalledTimes(1);
  });

  it("si la carga falla enseña el error en el recuadro", async () => {
    mockList.mockRejectedValue(new Error("Sin red"));
    render(<ContactPipelinesSummaryCard contactId="c1" />);
    expect(await screen.findByText("Sin red")).toBeInTheDocument();
  });
});

describe("fueraDePlazo", () => {
  it("manda lo que dice el servidor y, si no lo dice, se calcula con el plazo", () => {
    expect(fueraDePlazo(fila({ is_overdue: true }))).toBe(true);
    expect(fueraDePlazo(fila({ is_overdue: false, days_in_stage: 99 }))).toBe(false);
    expect(fueraDePlazo(fila({ is_overdue: undefined, days_in_stage: 4, target_days: 3 }))).toBe(true);
    expect(fueraDePlazo(fila({ is_overdue: undefined, days_in_stage: 3, target_days: 3 }))).toBe(false);
    expect(fueraDePlazo(fila({ is_overdue: undefined, days_in_stage: 30, target_days: null }))).toBe(false);
  });
});
