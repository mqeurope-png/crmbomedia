import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WebFormEmbedCode } from "./WebFormEmbedCode";

const embed = {
  script_snippet: '<script src="https://crm/forms/embed/f1.js" async></script>',
  iframe_snippet: '<iframe src="https://crm/forms/f1"></iframe>',
  html_snippet: '<form class="bh-form" action="https://crm/public/forms/f1/submit" method="POST"></form>',
};

describe("WebFormEmbedCode", () => {
  it("muestra los 3 snippets (script + iframe + HTML puro)", () => {
    render(<WebFormEmbedCode embed={embed} />);
    expect(screen.getByText(/Script JS de este formulario/i)).toBeInTheDocument();
    expect(screen.getByText(/iframe \(aislado\)/i)).toBeInTheDocument();
    expect(screen.getByText(/HTML puro/i)).toBeInTheDocument();
    expect(screen.getByText(embed.script_snippet)).toBeInTheDocument();
    expect(screen.getByText(embed.iframe_snippet)).toBeInTheDocument();
    expect(screen.getByText(embed.html_snippet)).toBeInTheDocument();
    // La preview aislada del HTML puro.
    expect(
      screen.getByTitle("Vista previa del HTML puro"),
    ).toBeInTheDocument();
  });

  it("con marca, ofrece el código único de la web y explica el <div>", () => {
    render(<WebFormEmbedCode embed={{
      ...embed,
      site: "mbolasers",
      site_web: "mbolasers.com",
      site_snippet: '<script src="https://crm/forms/embed/mbolasers.js" async></script>\n'
        + '<div data-bohub-form="mbolasers"></div>',
    }} />);
    expect(screen.getByText(/Un solo código para toda la web \(mbolasers\.com\)/))
      .toBeInTheDocument();
    expect(screen.getAllByText(/Pega LAS DOS LÍNEAS/).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/data-bohub-form="mbolasers"/)).toBeInTheDocument();
  });

  it("sin marca no ofrece el código por web", () => {
    render(<WebFormEmbedCode embed={embed} />);
    expect(screen.queryByText(/Un solo código para toda la web/)).toBeNull();
  });

  it("copia el snippet al clipboard al pulsar Copiar", async () => {
    const user = userEvent.setup();
    // user-event instala su propio clipboard (getter-only) en setup;
    // espiamos su writeText en vez de reasignarlo.
    const spy = jest.spyOn(navigator.clipboard, "writeText");
    render(<WebFormEmbedCode embed={embed} />);

    await user.click(screen.getByRole("button", { name: /Copiar Script JS de este formulario/i }));
    expect(spy).toHaveBeenCalledWith(embed.script_snippet);
    expect(await screen.findByText("Copiado")).toBeInTheDocument();
  });
});
