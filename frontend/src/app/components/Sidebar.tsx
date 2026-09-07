"use client";

import { ChevronsLeft, ChevronsRight } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import type { User } from "../lib/api";
import type { AppMode } from "../lib/appMode";
import { resolveVisibleNav } from "../lib/appNav";
import { getMyBuckets } from "../lib/tasksApi";

/** Poll `my-buckets` so the sidebar badge stays roughly fresh as
 * tasks come due. Cheap call, polls every 90 s. */
function useTasksBadge(user: User | null): number {
  const [count, setCount] = useState(0);
  useEffect(() => {
    if (!user) {
      setCount(0);
      return;
    }
    let cancelled = false;
    const tick = () => {
      getMyBuckets()
        .then((b) => {
          if (!cancelled) setCount(b.overdue.length + b.today.length);
        })
        .catch(() => undefined);
    };
    tick();
    const handle = window.setInterval(tick, 90_000);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, [user]);
  return count;
}

type Props = {
  user: User | null;
  /** ERP-F2 — el modo decide qué entradas se pintan (CRM completo vs solo
   *  ERP). La definición del menú y su ámbito viven en `lib/appNav`. */
  mode: AppMode;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onCloseDrawer: () => void;
};

export function Sidebar({
  user,
  mode,
  collapsed,
  onToggleCollapsed,
  onCloseDrawer,
}: Props) {
  const pathname = usePathname() ?? "";
  const tasksBadge = useTasksBadge(user);
  const items = resolveVisibleNav(user, mode);

  function isActive(href: string): boolean {
    if (href === "/") return pathname === "/";
    return pathname === href || pathname.startsWith(`${href}/`);
  }

  return (
    <aside
      className={`sidebar${collapsed ? " is-collapsed" : ""}`}
      aria-label="Navegación principal"
    >
      <nav className="sidebar-nav">
        <ul>
          {items.map((item) => {
            const Icon = item.icon;
            const sectionActive =
              isActive(item.href) ||
              (item.children?.some((child) => isActive(child.href)) ?? false);
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  onClick={onCloseDrawer}
                  className={`sidebar-link${sectionActive ? " is-active" : ""}`}
                  aria-current={sectionActive ? "page" : undefined}
                  title={collapsed ? item.label : undefined}
                >
                  <Icon size={18} aria-hidden />
                  <span className="sidebar-link-label">{item.label}</span>
                  {item.href === "/tasks" && tasksBadge > 0 ? (
                    <span
                      className="sidebar-badge"
                      title={`${tasksBadge} tareas pendientes`}
                    >
                      {tasksBadge > 99 ? "99+" : tasksBadge}
                    </span>
                  ) : null}
                </Link>
                {item.children && !collapsed && sectionActive ? (
                  <ul className="sidebar-sublist">
                    {item.children.map((child) => (
                      <li key={child.href}>
                        <Link
                          href={child.href}
                          onClick={onCloseDrawer}
                          className={`sidebar-sublink${
                            isActive(child.href) ? " is-active" : ""
                          }`}
                        >
                          {child.label}
                        </Link>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ul>
      </nav>
      <button
        type="button"
        className="sidebar-collapse"
        onClick={onToggleCollapsed}
        aria-label={collapsed ? "Expandir menú" : "Plegar menú"}
      >
        {collapsed ? <ChevronsRight size={16} /> : <ChevronsLeft size={16} />}
        <span className="sidebar-link-label">
          {collapsed ? "Expandir" : "Plegar"}
        </span>
      </button>
    </aside>
  );
}
