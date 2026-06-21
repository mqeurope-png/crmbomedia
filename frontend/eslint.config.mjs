import { FlatCompat } from "@eslint/eslintrc";
import { dirname } from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const compat = new FlatCompat({ baseDirectory: __dirname });

const eslintConfig = [
  {
    // PR-Manual-Tutorial-CRM: el bundle del manual (HTML + support.js)
    // vive en `public/manual/` como contenido estático generado por
    // Bart desde Claude Design. NO es código del CRM y no debe
    // linterse — se sustituye en bloque cuando el manual se regenera.
    ignores: [".next/**", "public/manual/**"],
  },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
];

export default eslintConfig;
