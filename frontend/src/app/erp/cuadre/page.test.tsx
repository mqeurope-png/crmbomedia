import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CuadrePage from "./page";

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, href, prefetch, ...rest }: { children: React.ReactNode; href: string;
    prefetch?: boolean } & Record<string, unknown>) => (
    <a href={href} data-prefetch={String(prefetch)} {...rest}>{children}</a>
  ),
}));
jest.mock("../../components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <header><h1>{title}</h1>{actions}</header>
  ),
}));

const tarjeta = (over: Record<string, unknown>) => ({
  descripcion: "Descripción.", fuente: "mysql", grupo: "dinero", dias_defecto: null,
  dias_texto: null, orden: 1, dias: null, abiertos: 0, revisados: 0, nuevos: 0, ...over,
});

const RESUMEN = {
  contadores: { alta: 1, media: 2, baja: 0, total: 3 },
  ultima_pasada: {
    id: "r1", fuente: "mysql", origen: "manual", estado: "ok",
    started_at: "2026-10-03T08:00:00Z", finished_at: "2026-10-03T08:00:05Z",
    created_at: "2026-10-03T08:00:00Z", error: null, resumen: {},
  },
  ultimas_por_fuente: {
    mysql: {
      id: "r1", fuente: "mysql", origen: "manual", estado: "ok",
      started_at: "2026-10-03T08:00:00Z", finished_at: "2026-10-03T08:00:05Z",
      created_at: "2026-10-03T08:00:00Z", error: null,
      resumen: { enviado_sin_aviso: { hallazgos: 2 }, pedido_sin_aprobar: { hallazgos: 0 } },
    },
    factusol: null,
  },
  en_curso: [],
  nocturno: { activo: false, hora: "03:00" },
  checks: [
    tarjeta({ id: "factura_sin_cobro", titulo: "Factura emitida sin cobro", severidad: "alta",
              fuente: "factusol",
              abiertos: 1, nuevos: 1, dias: 30, dias_defecto: 30,
              dias_texto: "Avisar pasados N días desde la factura" }),
    tarjeta({ id: "enviado_sin_aviso", titulo: "Enviado con tracking sin aviso al cliente",
              severidad: "media", abiertos: 2, orden: 5 }),
    tarjeta({ id: "pedido_sin_aprobar", titulo: "Pedido esperando aprobación",
              severidad: "baja", orden: 11 }),
  ],
};

const hallazgo = (over: Record<string, unknown>) => ({
  check_titulo: "", estado: "abierto", entidad_tipo: "pedido", pista_de_arreglo: "Arréglalo allí.",
  datos: {}, motivo: null, visto_por: null, revisado_at: null, primera_vez_at: null,
  ultima_vez_at: null, abierto_at: null, nuevo: false, arreglo_boton: null, ...over,
});

const ITEMS = [
  hallazgo({ id: "f1", check_id: "factura_sin_cobro", severidad: "alta", entidad_id: "5-000080",
             etiqueta: "Factura 5-000080", detalle: "Quedan 100,00 € por cobrar.",
             enlace: "/erp/orders/o1", arreglo_enlace: "/erp/orders/o1",
             arreglo_boton: "Abrir el pedido", nuevo: true }),
  hallazgo({ id: "f2", check_id: "enviado_sin_aviso", severidad: "media", entidad_id: "o2",
             etiqueta: "BOP-2", detalle: "Sin aviso.", enlace: "/erp/orders/o2",
             arreglo_enlace: "/erp/sat?tab=enviados", arreglo_boton: "Ir a «Enviados»" }),
  hallazgo({ id: "f3", check_id: "enviado_sin_aviso", severidad: "media", entidad_id: "o3",
             etiqueta: "BOP-3", detalle: "Sin aviso.", enlace: "/erp/orders/o3",
             arreglo_enlace: "/erp/sat?tab=enviados", arreglo_boton: "Ir a «Enviados»" }),
];

jest.mock("../../lib/erpApi", () => ({
  getCuadreResumen: jest.fn(),
  listCuadreHallazgos: jest.fn(),
  revisarCuadreHallazgo: jest.fn(),
  reincluirCuadreHallazgo: jest.fn(),
  comprobarCuadre: jest.fn(),
  exportCuadreXlsx: jest.fn(),
  saveBlob: jest.fn(),
}));

function api() {
  return jest.requireMock("../../lib/erpApi") as Record<string, jest.Mock>;
}

beforeEach(() => {
  const m = api();
  m.getCuadreResumen.mockResolvedValue(RESUMEN);
  m.listCuadreHallazgos.mockResolvedValue({ items: ITEMS, total: ITEMS.length });
  m.revisarCuadreHallazgo.mockResolvedValue({ ...ITEMS[1], estado: "revisado" });
  m.reincluirCuadreHallazgo.mockResolvedValue({ ...ITEMS[1], estado: "abierto" });
  m.exportCuadreXlsx.mockResolvedValue(new Blob(["x"]));
  m.comprobarCuadre.mockResolvedValue({
    lanzadas: { mysql: { id: "r2", estado: "ok" }, factusol: { id: "r3", estado: "en_cola" } },
    resumen: { ...RESUMEN, en_curso: [{ ...RESUMEN.ultima_pasada, id: "r3", fuente: "factusol",
                                        estado: "en_cola", finished_at: null }] },
  });
});

describe("ERP · Cuadre", () => {
  it("enseña los contadores por severidad, la última pasada y una tarjeta por comprobación", async () => {
    render(<CuadrePage />);
    const contadores = screen.getByRole("region", { name: "Descuadres por severidad" });
    expect(await within(contadores).findByRole("button", { name: /1\s*Severidad alta/ }))
      .toBeInTheDocument();
    expect(within(contadores).getByRole("button", { name: /2\s*Severidad media/ })).toBeInTheDocument();
    expect(within(contadores).getByRole("button", { name: /0\s*Severidad baja/ })).toBeInTheDocument();
    expect(screen.getByText(/Última comprobación:/)).toBeInTheDocument();
    const lista = screen.getByRole("list", { name: "Comprobaciones" });
    expect(within(lista).getAllByRole("listitem")).toHaveLength(3);
    expect(within(lista).getByText("Factura emitida sin cobro")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Enviado con tracking sin aviso al cliente\s*2 abiertos/ }))
      .toBeInTheDocument();
    // FACTUSOL sin ninguna pasada terminada: «Sin comprobar», no «todo cuadra».
    const factura = within(lista).getByText("Factura emitida sin cobro").closest("article")!;
    expect(within(factura).getByText("Sin comprobar")).toBeInTheDocument();
    expect(screen.getByText(/Avisar pasados 30 días desde la factura/)).toBeInTheDocument();
  });

  it("al desplegar una tarjeta lista sus filas con el enlace y el botón de arreglo", async () => {
    const user = userEvent.setup();
    render(<CuadrePage />);
    const toggle = await screen.findByRole("button", { name: /Enviado con tracking sin aviso/ });
    expect(screen.queryByText("BOP-2")).not.toBeInTheDocument();
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("link", { name: "BOP-2" })).toHaveAttribute("href", "/erp/orders/o2");
    // Sin precarga: cientos de filas no lanzan cientos de precargas de rutas.
    expect(screen.getByRole("link", { name: "BOP-2" })).toHaveAttribute("data-prefetch", "false");
    expect(screen.getAllByRole("link", { name: "Ir a «Enviados»" })[0])
      .toHaveAttribute("href", "/erp/sat?tab=enviados");
    await user.click(screen.getByRole("button", { name: /Factura emitida sin cobro/ }));
    expect(screen.getByText("Nuevo")).toBeInTheDocument();
  });

  it("«Revisado / no es un descuadre» exige un motivo y recarga", async () => {
    const user = userEvent.setup();
    render(<CuadrePage />);
    await user.click(await screen.findByRole("button", { name: /Enviado con tracking sin aviso/ }));
    await user.click(screen.getAllByRole("button", { name: "Revisado / no es un descuadre" })[0]);
    const guardar = screen.getByRole("button", { name: "Guardar" });
    expect(guardar).toBeDisabled();                       // sin motivo no se guarda
    await user.type(screen.getByLabelText("Motivo para BOP-2"), "Cliente avisado por teléfono");
    expect(guardar).toBeEnabled();
    const m = api();
    m.listCuadreHallazgos.mockClear();
    await user.click(guardar);
    expect(m.revisarCuadreHallazgo).toHaveBeenCalledWith("f2", "Cliente avisado por teléfono");
    await waitFor(() => expect(m.listCuadreHallazgos).toHaveBeenCalled());
  });

  it("un revisado se puede «Volver a incluir»", async () => {
    const m = api();
    m.listCuadreHallazgos.mockResolvedValue({
      items: [{ ...ITEMS[1], estado: "revisado", motivo: "Ya avisado" }], total: 1,
    });
    const user = userEvent.setup();
    render(<CuadrePage />);
    await user.click(screen.getByRole("checkbox", { name: "Incluir revisados" }));
    await waitFor(() => expect(m.listCuadreHallazgos).toHaveBeenLastCalledWith(
      expect.objectContaining({ incluir_revisados: true }),
    ));
    await user.click(await screen.findByRole("button", { name: /Enviado con tracking sin aviso/ }));
    expect(screen.getByText("Motivo: Ya avisado")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Volver a incluir" }));
    expect(m.reincluirCuadreHallazgo).toHaveBeenCalledWith("f2");
  });

  it("los filtros piden lo nuevo y la severidad al backend", async () => {
    const m = api();
    const user = userEvent.setup();
    render(<CuadrePage />);
    await screen.findByRole("list", { name: "Comprobaciones" });
    await user.click(screen.getByRole("checkbox", { name: "Solo nuevos desde la última vez" }));
    await waitFor(() => expect(m.listCuadreHallazgos).toHaveBeenLastCalledWith(
      expect.objectContaining({ solo_nuevos: true }),
    ));
    await user.selectOptions(screen.getByLabelText("Severidad"), "alta");
    await waitFor(() => expect(m.listCuadreHallazgos).toHaveBeenLastCalledWith(
      expect.objectContaining({ severidad: "alta" }),
    ));
    const lista = screen.getByRole("list", { name: "Comprobaciones" });
    expect(within(lista).getAllByRole("listitem")).toHaveLength(1);
    await user.selectOptions(screen.getByLabelText("Severidad"), "");
    await user.selectOptions(screen.getByLabelText("Comprobación"), "pedido_sin_aprobar");
    expect(within(lista).getAllByRole("listitem")).toHaveLength(1);
    expect(within(lista).getByText("Pedido esperando aprobación")).toBeInTheDocument();
  });

  it("una tarjeta con muchas filas pinta 100 y deja ver el resto con «Ver más»", async () => {
    const m = api();
    const muchas = Array.from({ length: 150 }, (_, i) => hallazgo({
      id: `m${i}`, check_id: "enviado_sin_aviso", severidad: "media", entidad_id: `o${i}`,
      etiqueta: `BOP-${1000 + i}`, detalle: "Sin aviso.", enlace: `/erp/orders/o${i}`,
      arreglo_enlace: "/erp/sat?tab=enviados", arreglo_boton: "Ir a «Enviados»",
    }));
    m.listCuadreHallazgos.mockResolvedValue({ items: muchas, total: muchas.length });
    const user = userEvent.setup();
    render(<CuadrePage />);
    await user.click(await screen.findByRole("button", { name: /Enviado con tracking sin aviso/ }));
    const panel = document.getElementById("cuadre-enviado_sin_aviso")!;
    expect(within(panel).getAllByRole("listitem")).toHaveLength(100);
    expect(screen.getByText("Mostrando 100 de 150.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Ver 50 más" }));
    expect(within(panel).getAllByRole("listitem")).toHaveLength(150);
    expect(screen.queryByRole("button", { name: /más$/ })).not.toBeInTheDocument();
  });

  it("«Descargar Excel» guarda el fichero de los abiertos", async () => {
    const user = userEvent.setup();
    render(<CuadrePage />);
    await user.click(await screen.findByRole("button", { name: "Descargar Excel" }));
    const m = api();
    await waitFor(() => expect(m.saveBlob).toHaveBeenCalled());
    expect(m.saveBlob.mock.calls[0][1]).toMatch(/^cuadre_descuadres_\d{4}-\d{2}-\d{2}\.xlsx$/);
  });

  it("«Comprobar ahora» enseña «comprobando…» mientras FACTUSOL va en segundo plano", async () => {
    const user = userEvent.setup();
    render(<CuadrePage />);
    await user.click(await screen.findByRole("button", { name: "Comprobar ahora" }));
    expect(await screen.findByText(/Comprobando FACTUSOL/)).toBeInTheDocument();
    expect(screen.getByText(/Las de FACTUSOL se comprueban en segundo plano/)).toBeInTheDocument();
    // El botón no se bloquea mientras FACTUSOL va en segundo plano.
    expect(screen.getByRole("button", { name: "Comprobar ahora" })).toBeEnabled();
  });

  it("un pedido pagado que falta enlaza a la tienda en otra pestaña", async () => {
    const m = api();
    m.getCuadreResumen.mockResolvedValue({
      ...RESUMEN,
      checks: [tarjeta({ id: "pedido_woo_pagado_sin_bohub", fuente: "woocommerce",
                         grupo: "integraciones", severidad: "alta", abiertos: 1,
                         titulo: "Pedido pagado en WooCommerce que no está en BoHub" })],
    });
    m.listCuadreHallazgos.mockResolvedValue({ total: 1, items: [hallazgo({
      id: "w1", check_id: "pedido_woo_pagado_sin_bohub", severidad: "alta",
      entidad_tipo: "pedido_woo", entidad_id: "boprint:99976", etiqueta: "Boprint #99976",
      detalle: "Pagado en la tienda (processing) y no está en BoHub.",
      enlace: "https://boprint.example/wp-admin/post.php?post=99976&action=edit",
      arreglo_enlace: "/erp/seguimiento", arreglo_boton: "Poner al día estados Woo",
    })] });
    const user = userEvent.setup();
    render(<CuadrePage />);
    await user.click(await screen.findByRole("button", { name: /Pedido pagado en WooCommerce/ }));
    const enlace = screen.getByRole("link", { name: "Boprint #99976" });
    expect(enlace).toHaveAttribute("href",
      "https://boprint.example/wp-admin/post.php?post=99976&action=edit");
    expect(enlace).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("link", { name: "Poner al día estados Woo" }))
      .toHaveAttribute("href", "/erp/seguimiento");
  });

  it("FACTUSOL y WooCommerce van en segundo plano y se avisa de la que termina mal", async () => {
    jest.useFakeTimers();
    try {
      const m = api();
      const pasada = (id: string, fuente: string) => ({
        ...RESUMEN.ultima_pasada, id, fuente, estado: "en_cola", finished_at: null });
      const enCola = { ...RESUMEN, en_curso: [pasada("r3", "factusol"), pasada("r4", "woocommerce")] };
      m.comprobarCuadre.mockResolvedValue({
        lanzadas: { mysql: { id: "r2", estado: "ok" }, factusol: { id: "r3", estado: "en_cola" },
                    woocommerce: { id: "r4", estado: "en_cola" } },
        resumen: enCola,
      });
      const hecho = { ...RESUMEN, ultimas_por_fuente: { ...RESUMEN.ultimas_por_fuente,
        factusol: { ...RESUMEN.ultima_pasada, id: "r3", fuente: "factusol" } } };
      m.getCuadreResumen.mockResolvedValueOnce(RESUMEN).mockResolvedValue(hecho);
      const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
      render(<CuadrePage />);
      await user.click(await screen.findByRole("button", { name: "Comprobar ahora" }));
      expect(await screen.findByText(/Las de FACTUSOL y WooCommerce se comprueban en segundo plano/))
        .toBeInTheDocument();
      expect(screen.getByText(/Comprobando FACTUSOL .*y WooCommerce/)).toBeInTheDocument();
      for (let i = 0; i < 2; i++) {
        await act(async () => { await jest.advanceTimersByTimeAsync(5000); });
      }
      expect(await screen.findByText("Comprobadas también las de FACTUSOL.")).toBeInTheDocument();
      expect(screen.getByText(/La comprobación de WooCommerce no ha terminado bien \(¿las tiendas/))
        .toBeInTheDocument();
    } finally {
      jest.useRealTimers();
    }
  });

  it("una tarjeta sin descuadres solo dice «Todo cuadra.» si se comprobó bien", async () => {
    const m = api();
    m.getCuadreResumen.mockResolvedValue({
      ...RESUMEN,
      ultimas_por_fuente: {
        ...RESUMEN.ultimas_por_fuente,
        mysql: { ...RESUMEN.ultimas_por_fuente.mysql, estado: "con_errores", resumen: {
          enviado_sin_aviso: { hallazgos: 2 },
          pedido_sin_aprobar: { error: "RuntimeError: boom" },
        } },
      },
    });
    const user = userEvent.setup();
    render(<CuadrePage />);
    await user.click(await screen.findByRole("button", { name: /Pedido esperando aprobación/ }));
    expect(screen.getByText("Falló en la última comprobación")).toBeInTheDocument();
    expect(screen.getByText(/La última comprobación falló: RuntimeError: boom/)).toBeInTheDocument();
    expect(screen.queryByText("Todo cuadra.")).not.toBeInTheDocument();
  });

  describe("mientras FACTUSOL se comprueba en segundo plano", () => {
    const enCola = {
      ...RESUMEN,
      en_curso: [{ ...RESUMEN.ultima_pasada, id: "r3", fuente: "factusol", estado: "en_cola",
                   finished_at: null }],
    };

    beforeEach(() => { jest.useFakeTimers(); });
    afterEach(() => { jest.useRealTimers(); });

    it("sigue preguntando aunque una consulta falle y avisa si FACTUSOL termina mal", async () => {
      const m = api();
      m.comprobarCuadre.mockResolvedValue({
        lanzadas: { mysql: { id: "r2", estado: "ok" }, factusol: { id: "r3", estado: "en_cola" } },
        resumen: enCola,
      });
      m.getCuadreResumen
        .mockResolvedValueOnce(RESUMEN)                       // al entrar
        .mockRejectedValueOnce(new Error("502"))              // un fallo puntual
        .mockResolvedValueOnce(enCola)                        // sigue en cola
        .mockResolvedValue(RESUMEN);                          // terminó… sin pasada nueva
      const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
      render(<CuadrePage />);
      await user.click(await screen.findByRole("button", { name: "Comprobar ahora" }));
      expect(await screen.findByText(/Comprobando FACTUSOL/)).toBeInTheDocument();
      for (let i = 0; i < 3; i++) {
        await act(async () => { await jest.advanceTimersByTimeAsync(5000); });
      }
      expect(await screen.findByText(/La comprobación de FACTUSOL no ha terminado bien/))
        .toBeInTheDocument();
      expect(screen.queryByText(/Comprobando FACTUSOL/)).not.toBeInTheDocument();
      expect(m.getCuadreResumen.mock.calls.length).toBeGreaterThanOrEqual(4);
    });

    it("al terminar bien recarga las filas y lo dice", async () => {
      const m = api();
      m.comprobarCuadre.mockResolvedValue({
        lanzadas: { mysql: { id: "r2", estado: "ok" }, factusol: { id: "r3", estado: "en_cola" } },
        resumen: enCola,
      });
      const hecho = {
        ...RESUMEN,
        ultimas_por_fuente: { ...RESUMEN.ultimas_por_fuente,
          factusol: { ...RESUMEN.ultima_pasada, id: "r3", fuente: "factusol", resumen: {} } },
      };
      m.getCuadreResumen.mockResolvedValueOnce(RESUMEN).mockResolvedValue(hecho);
      const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
      render(<CuadrePage />);
      await user.click(await screen.findByRole("button", { name: "Comprobar ahora" }));
      m.listCuadreHallazgos.mockClear();
      for (let i = 0; i < 2; i++) {
        await act(async () => { await jest.advanceTimersByTimeAsync(5000); });
      }
      expect(await screen.findByText("Comprobadas también las de FACTUSOL.")).toBeInTheDocument();
      await waitFor(() => expect(m.listCuadreHallazgos).toHaveBeenCalled());
    });
  });
});
