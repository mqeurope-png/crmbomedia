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
    expect(screen.getByText(/Script JS \(recomendado\)/i)).toBeInTheDocument();
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

  it("copia el snippet al clipboard al pulsar Copiar", async () => {
    const user = userEvent.setup();
    // user-event instala su propio clipboard (getter-only) en setup;
    // espiamos su writeText en vez de reasignarlo.
    const spy = jest.spyOn(navigator.clipboard, "writeText");
    render(<WebFormEmbedCode embed={embed} />);

    await user.click(screen.getByRole("button", { name: /Copiar Script JS/i }));
    expect(spy).toHaveBeenCalledWith(embed.script_snippet);
    expect(await screen.findByText("Copiado")).toBeInTheDocument();
  });
});
