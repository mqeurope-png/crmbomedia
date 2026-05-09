"use client";

import { useMemo } from "react";

// Mirror of backend/app/core/passwords.py policy. Backend remains the source
// of truth: this component is a hint for the user, not a security gate.
export const PASSWORD_MIN_LENGTH = 12;
export const PASSWORD_MAX_LENGTH = 128;

// Short subset of backend/app/core/common_passwords.txt — only the obvious
// hits, so the user gets immediate feedback while typing. The backend
// blocklist is the authoritative one.
const COMMON_PASSWORDS = new Set<string>([
  "password",
  "password1",
  "password123",
  "passw0rd",
  "p@ssw0rd",
  "p@ssword",
  "qwerty",
  "qwerty123",
  "qwertyuiop",
  "abc123",
  "admin",
  "admin123",
  "administrator",
  "welcome",
  "welcome1",
  "welcome123",
  "letmein",
  "iloveyou",
  "changeme",
  "default",
  "test",
  "test1234",
  "hello",
  "hello123",
  "12345",
  "123456",
  "1234567",
  "12345678",
  "123456789",
  "1234567890",
  "crmbomedia",
]);

type Rule = {
  id: string;
  label: string;
  test: (password: string) => boolean;
};

const RULES: Rule[] = [
  {
    id: "length",
    label: `Mínimo ${PASSWORD_MIN_LENGTH} caracteres`,
    test: (p) => p.length >= PASSWORD_MIN_LENGTH && p.length <= PASSWORD_MAX_LENGTH,
  },
  { id: "upper", label: "Al menos una letra mayúscula", test: (p) => /[A-Z]/.test(p) },
  { id: "lower", label: "Al menos una letra minúscula", test: (p) => /[a-z]/.test(p) },
  { id: "digit", label: "Al menos un número", test: (p) => /\d/.test(p) },
  {
    id: "uncommon",
    label: "No es una contraseña habitual",
    test: (p) => p.length === 0 || !COMMON_PASSWORDS.has(p.toLowerCase()),
  },
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
  const bonusVariety =
    /[^A-Za-z0-9]/.test(password) || password.length >= 16 ? 1 : 0;
  const score = passed + bonusVariety; // 0..6
  if (score <= 3) return { level: "weak", label: "Débil", score };
  if (score === 4) return { level: "medium", label: "Media", score };
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
          <span style={{ width: `${(strength.score / 6) * 100}%` }} />
        </span>
      </div>
    </div>
  );
}
