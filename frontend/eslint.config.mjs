import { FlatCompat } from "@eslint/eslintrc";
import jest from "eslint-plugin-jest";
import testingLibrary from "eslint-plugin-testing-library";
import { dirname } from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const compat = new FlatCompat({ baseDirectory: __dirname });

const TEST_FILES = ["src/**/*.test.{ts,tsx}"];

const eslintConfig = [
  {
    // PR-Manual-Tutorial-CRM: el bundle del manual (HTML + support.js)
    // vive en `public/manual/` como contenido estático generado por
    // Bart desde Claude Design. NO es código del CRM y no debe
    // linterse — se sustituye en bloque cuando el manual se regenera.
    ignores: [".next/**", "public/manual/**"],
  },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
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
