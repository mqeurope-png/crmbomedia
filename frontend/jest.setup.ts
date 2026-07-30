// Matchers extendidos de Testing Library: toBeInTheDocument, toHaveClass,
// toBeDisabled, etc. Importarlo aquí lo aplica globalmente + augmenta los
// tipos de `expect` para TypeScript.
import "@testing-library/jest-dom";

// jsdom no implementa estas APIs que algunos componentes tocan al montar.
// Stubs mínimos para que el render no explote.
if (typeof window !== "undefined") {
  window.matchMedia =
    window.matchMedia ||
    ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }));

  if (!window.URL.createObjectURL) {
    window.URL.createObjectURL = () => "blob:jest";
  }
  if (!window.URL.revokeObjectURL) {
    window.URL.revokeObjectURL = () => {};
  }
}
