"use client";

import {
  ChevronDown,
  KeyRound,
  LogOut,
  ShieldCheck,
  User as UserIcon,
  UserCircle2,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { clearStoredToken, type User } from "../lib/api";

type Props = {
  user: User | null;
};

export function UserMenu({ user }: Props) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const wrapper = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(event: MouseEvent) {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  function logout() {
    clearStoredToken();
    router.push("/login");
  }

  if (!user) {
    return (
      <Link href="/login" className="button small">
        Iniciar sesión
      </Link>
    );
  }

  const initials = user.full_name
    .split(/\s+/)
    .map((part) => part[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();

  return (
    <div ref={wrapper} className="user-menu">
      <button
        type="button"
        className="user-menu-trigger"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span className="user-menu-avatar" aria-hidden>
          {initials || "?"}
        </span>
        <span className="user-menu-name">{user.full_name}</span>
        <ChevronDown size={14} aria-hidden />
      </button>
      {open ? (
        <div className="user-menu-panel" role="menu">
          <div className="user-menu-meta">
            <strong>{user.full_name}</strong>
            <span className="muted small">{user.email}</span>
            <span className="muted small">Rol: {user.role}</span>
          </div>
          <Link
            href="/account"
            role="menuitem"
            className="user-menu-item"
            onClick={() => setOpen(false)}
          >
            <UserIcon size={14} aria-hidden /> Mi cuenta
          </Link>
          <Link
            href="/account/password"
            role="menuitem"
            className="user-menu-item"
            onClick={() => setOpen(false)}
          >
            <KeyRound size={14} aria-hidden /> Cambiar contraseña
          </Link>
          <Link
            href="/account/security"
            role="menuitem"
            className="user-menu-item"
            onClick={() => setOpen(false)}
          >
            <ShieldCheck size={14} aria-hidden /> Seguridad / 2FA
          </Link>
          <button
            type="button"
            role="menuitem"
            className="user-menu-item"
            onClick={() => {
              setOpen(false);
              logout();
            }}
          >
            <LogOut size={14} aria-hidden /> Cerrar sesión
          </button>
        </div>
      ) : null}
      <span className="user-menu-fallback" aria-hidden>
        <UserCircle2 size={14} />
      </span>
    </div>
  );
}
