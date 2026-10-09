import {
  humanizeStepLabel,
  humanizeTrigger,
  stepSummary,
  validateStepConfig,
} from "./workflowsHumanize";

describe("workflowsHumanize — respuesta a leads (Fase 1)", () => {
  it("etiqueta el trigger y los pasos nuevos en lenguaje humano", () => {
    expect(humanizeTrigger("lead.received"))
      .toBe("Lead recibido (formulario web o nota de AgileCRM)");
    expect(humanizeStepLabel({ type: "action_classify_lead" })).toBe("Clasificar lead");
    expect(humanizeStepLabel({ type: "action_prepare_email_draft", config: {} }))
      .toBe("Preparar borrador (plantilla por interés e idioma)");
    expect(humanizeStepLabel(
      { type: "action_prepare_email_draft", config: { template_mode: "fija", template_id: "t1" } },
      { templates: { t1: "Lead · Vending (ES)" } },
    )).toBe("Preparar borrador: Lead · Vending (ES)");
    expect(humanizeStepLabel(
      { type: "action_prepare_email_draft", config: { template_mode: "fija" } },
    )).toBe("Preparar borrador: (plantilla sin elegir)");
    expect(humanizeStepLabel(
      { type: "action_add_to_pipeline", config: { stage_id: "s1" } },
      { pipelineStages: { s1: "Nuevo lead" } },
    )).toBe('Añadir a pipeline: "Nuevo lead"');
    expect(humanizeStepLabel({ type: "action_add_to_pipeline", config: {} }))
      .toBe("Añadir a pipeline");
    // El modelo real son contactos en pipelines, no oportunidades.
    expect(humanizeStepLabel(
      { type: "action_move_opportunity_stage", config: { stage_id: "s1" } },
      { pipelineStages: { s1: "Contactado" } },
    )).toBe('Mover contacto a "Contactado"');
    expect(humanizeStepLabel({ type: "action_move_opportunity_stage", config: {} }))
      .toBe("Mover contacto de etapa");
  });

  it("resume la ventana horaria de la espera", () => {
    expect(stepSummary({
      type: "wait_time",
      config: { duration_minutes: 720,
                window: { enabled: true, start: "09:00", end: "18:00", weekdays_only: true } },
    })).toBe("Ventana 09:00–18:00, laborables");
    expect(stepSummary({
      type: "wait_time",
      config: { duration_minutes: 60, window: { start: "08:30", end: "20:00", weekdays_only: false } },
    })).toBe("Ventana 08:30–20:00, todos los días");
    expect(stepSummary({ type: "wait_time", config: { duration_minutes: 720 } })).toBe("");
    expect(stepSummary({ type: "wait_time", config: { window: { enabled: false } } })).toBe("");
  });

  it("valida «Preparar borrador» según el modo elegido y «Añadir a pipeline» por sus ids", () => {
    expect(validateStepConfig({ type: "action_prepare_email_draft", config: {} }))
      .toEqual({ valid: true, missing: [] });
    expect(validateStepConfig({ type: "action_prepare_email_draft", config: { template_mode: "fija" } })
      .missing).toEqual(["template_id"]);
    expect(validateStepConfig({
      type: "action_prepare_email_draft", config: { from_mode: "fijo", owner_mode: "usuario" },
    }).missing).toEqual(["from_alias", "user_id"]);
    expect(validateStepConfig({
      type: "action_prepare_email_draft",
      config: { template_mode: "fija", template_id: "t1", from_mode: "fijo",
                from_alias: "info@pimpam-vending.com", owner_mode: "usuario", user_id: "u1" },
    }).valid).toBe(true);
    expect(validateStepConfig({ type: "action_add_to_pipeline", config: { pipeline_id: "p" } })
      .missing).toEqual(["stage_id"]);
    expect(validateStepConfig({ type: "action_classify_lead", config: {} }).valid).toBe(true);
  });
});
