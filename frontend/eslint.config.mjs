// SEC-1 (Next 16): eslint-config-next es flat-config nativo; el puente
// FlatCompat de eslintrc muere con "Converting circular structure to JSON"
// contra los configs nuevos, así que se importan directamente.
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";
import jest from "eslint-plugin-jest";
import testingLibrary from "eslint-plugin-testing-library";

const TEST_FILES = ["src/**/*.test.{ts,tsx}"];

const eslintConfig = [
  {
    // PR-Manual-Tutorial-CRM: el bundle del manual (HTML + support.js)
    // vive en `public/manual/` como contenido estático generado por
    // Bart desde Claude Design. NO es código del CRM y no debe
    // linterse — se sustituye en bloque cuando el manual se regenera.
    ignores: [".next/**", "public/manual/**"],
  },
  ...nextCoreWebVitals,
  ...nextTypescript,
  {
    // SEC-1 (Next 16): eslint-config-next 16 trae react-hooks v6 con las
    // reglas nuevas del React Compiler en "error". Activarlas de golpe son
    // ~140 errores en código que funciona — se degradan a warning para
    // adoptarlas incrementalmente, sin mezclar un refactor masivo con un
    // parche de seguridad urgente. exhaustive-deps ya era warning en 15.x.
    rules: {
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/purity": "warn",
      "react-hooks/refs": "warn",
      "react-hooks/incompatible-library": "warn",
      "react-hooks/exhaustive-deps": "warn",
    },
  },
  // Sprint Frontend-Test-Runner. Reglas `recommended` (suaves) de jest +
  // testing-library SOLO sobre los ficheros de test, para no meter ruido
  // en el código de producción.
  { ...jest.configs["flat/recommended"], files: TEST_FILES },
  { ...testingLibrary.configs["flat/react"], files: TEST_FILES },
  {
    files: TEST_FILES,
    rules: {
      // Los tests de regresión de modales/banner asertan clases CSS
      // (.modal-dialog, .google-banner-warn) que no son queryables por
      // rol/texto → container.querySelector es un uso legítimo aquí.
      "testing-library/no-container": "off",
      "testing-library/no-node-access": "off",
      // Reconoce helpers de aserción propios (p.ej. expectSilent()).
      "jest/expect-expect": [
        "warn",
        { assertFunctionNames: ["expect", "expect*"] },
      ],
    },
  },
];

export default eslintConfig;
