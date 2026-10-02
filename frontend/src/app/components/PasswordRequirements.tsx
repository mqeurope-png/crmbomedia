"use client";

import { useMemo } from "react";

// Reflejo de backend/app/core/passwords.py (la ÚNICA fuente de verdad de la
// regla). Este componente es una ayuda en vivo para quien teclea, no la
// barrera: el backend vuelve a validar y sus mensajes dicen lo mismo.
//
// La política es SOLO esto (ni minúscula obligatoria ni lista de «contraseñas
// habituales»): mínimo 8 caracteres, una mayúscula y un número.
export const PASSWORD_MIN_LENGTH = 8;
/** Tope técnico del backend (no es una regla de la política ni se enseña):
 *  los campos lo aplican con `maxLength`, así que nunca llega al servidor. */
export const PASSWORD_MAX_LENGTH = 128;

/** Longitud como la cuenta el backend (`len()` de Python: puntos de código, no
 *  unidades UTF-16; un emoji cuenta 1). */
function longitud(p: string): number {
  return Array.from(p).length;
}

type Rule = {
  id: string;
  label: string;
  test: (password: string) => boolean;
};

const RULES: Rule[] = [
  {
    id: "length",
    label: `Mínimo ${PASSWORD_MIN_LENGTH} caracteres`,
    test: (p) => longitud(p) >= PASSWORD_MIN_LENGTH,
  },
  { id: "upper", label: "Al menos una letra mayúscula", test: (p) => /[A-Z]/.test(p) },
  { id: "digit", label: "Al menos un número", test: (p) => /\d/.test(p) },
];

export function passwordChecks(password: string): { id: string; label: string; ok: boolean }[] {
  return RULES.map((rule) => ({ id: rule.id, label: rule.label, ok: rule.test(password) }));
}

export function isPasswordCompliant(password: string): boolean {
  return RULES.every((rule) => rule.test(password));
}

type Strength = { level: "empty" | "weak" | "medium" | "strong"; label: string; score: number };

function computeStrength(password: string): Strength {
  if (password.length === 0) return { level: "empty", label: "—", score: 0 };
  const passed = RULES.filter((r) => r.test(password)).length;
  // La fortaleza es orientativa (no bloquea): los 3 requisitos + un extra por
  // variedad (minúsculas y mayúsculas, un símbolo o 12+ caracteres).
  const bonusVariety =
    (/[a-z]/.test(password) && /[A-Z]/.test(password)) || /[^A-Za-z0-9]/.test(password)
      || longitud(password) >= 12 ? 1 : 0;
  const score = passed + bonusVariety; // 0..4
  if (score <= 2) return { level: "weak", label: "Débil", score };
  if (score === 3) return { level: "medium", label: "Media", score };
  return { level: "strong", label: "Fuerte", score };
}

export function PasswordRequirements({ password }: { password: string }) {
  const checks = useMemo(() => passwordChecks(password), [password]);
  const strength = useMemo(() => computeStrength(password), [password]);

  return (
    <div className="password-requirements" aria-live="polite">
      <ul className="password-rules">
        {checks.map((check) => (
          <li key={check.id} className={check.ok ? "ok" : "miss"}>
            <span aria-hidden="true">{check.ok ? "✓" : "✗"}</span> {check.label}
          </li>
        ))}
      </ul>
      <div className={`password-strength strength-${strength.level}`}>
        <span className="strength-label">Fortaleza: {strength.label}</span>
        <span className="strength-bar" aria-hidden="true">
          <span style={{ width: `${(strength.score / 4) * 100}%` }} />
        </span>
      </div>
    </div>
  );
}
