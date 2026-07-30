import nextJest from "next/jest.js";

// `next/jest` configura el transform con el SWC del propio Next (mismo
// que usa `next build`), maneja CSS modules, imágenes y los alias de
// tsconfig. No añadimos babel ni ts-jest — menos peso y cero divergencia
// con el pipeline de build.
const createJestConfig = nextJest({ dir: "./" });

/** @type {import('jest').Config} */
const config = {
  coverageProvider: "v8",
  testEnvironment: "jsdom",
  setupFilesAfterEnv: ["<rootDir>/jest.setup.ts"],
  // Solo tests co-locados `*.test.ts(x)` bajo src/. Excluye node_modules
  // y .next por defecto.
  testMatch: ["<rootDir>/src/**/*.test.{ts,tsx}"],
  moduleNameMapper: {
    "^@/(.*)$": "<rootDir>/src/$1",
  },
  // `.next/standalone` duplica package.json y otros módulos → colisión de
  // haste-map. Se ignora (el build vive ahí, no el código a testear).
  modulePathIgnorePatterns: ["<rootDir>/.next/"],
  clearMocks: true,
};

export default createJestConfig(config);
