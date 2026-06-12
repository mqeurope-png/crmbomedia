"use client";

/**
 * Icon — literal port of the `<Icon>` SVG helper from
 * `bomedia-v4/index.html` (line 2258). Same name → path map, same
 * 24×24 viewBox, same stroke width. Inline SVG paths are kept
 * verbatim so the visual matches the original Composer one-for-one.
 *
 * Why not lucide-react: the original Composer uses these paths and
 * the visual tuning of the toolbar / sidebar / topbar depends on
 * the exact stroke / curve geometry. Substituting lucide variants
 * shifts the optical balance enough that the screenshot review
 * caught the difference. Keeping the literal SVG paths makes the
 * port pixel-faithful.
 */

import type { ReactNode } from "react";

export interface IconProps {
  name: string;
  size?: number;
}

function pathsFor(name: string): ReactNode {
  switch (name) {
    case "search":
      return (
        <>
          <circle cx="11" cy="11" r="8" />
          <path d="m21 21-4.3-4.3" />
        </>
      );
    case "plus":
      return <path d="M12 5v14M5 12h14" />;
    case "x":
      return <path d="M18 6 6 18M6 6l12 12" />;
    case "trash":
      return (
        <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6h14Z" />
      );
    case "copy":
      return (
        <>
          <rect x="9" y="9" width="13" height="13" rx="2" />
          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
        </>
      );
    case "arrowUp":
      return <path d="m18 15-6-6-6 6" />;
    case "arrowDown":
      return <path d="m6 9 6 6 6-6" />;
    case "eye":
      return (
        <>
          <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" />
          <circle cx="12" cy="12" r="3" />
        </>
      );
    case "eyeOff":
      return (
        <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-10-8-10-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 10 8 10 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24M1 1l22 22" />
      );
    case "code":
      return <path d="m16 18 6-6-6-6M8 6l-6 6 6 6" />;
    case "download":
      return <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" />;
    case "send":
      return <path d="m22 2-7 20-4-9-9-4Zm0 0L11 13" />;
    case "panel":
      return (
        <>
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <path d="M15 3v18" />
        </>
      );
    case "sidebar":
      return (
        <>
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <path d="M9 3v18" />
        </>
      );
    case "layers":
      return (
        <>
          <path d="m12 2 10 5-10 5-10-5 10-5Z" />
          <path d="m2 17 10 5 10-5M2 12l10 5 10-5" />
        </>
      );
    case "text":
      return <path d="M4 7V4h16v3M9 20h6M12 4v16" />;
    case "box":
      return (
        <>
          <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z" />
          <path d="m3.3 7 8.7 5 8.7-5M12 22V12" />
        </>
      );
    case "template":
      return (
        <>
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <path d="M3 9h18M9 21V9" />
        </>
      );
    case "sparkles":
      return (
        <path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3Z" />
      );
    case "grid":
      return (
        <>
          <rect x="3" y="3" width="7" height="7" />
          <rect x="14" y="3" width="7" height="7" />
          <rect x="3" y="14" width="7" height="7" />
          <rect x="14" y="14" width="7" height="7" />
        </>
      );
    case "settings":
      return (
        <>
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" />
        </>
      );
    case "drag":
      return (
        <>
          <circle cx="9" cy="5" r="1" />
          <circle cx="9" cy="12" r="1" />
          <circle cx="9" cy="19" r="1" />
          <circle cx="15" cy="5" r="1" />
          <circle cx="15" cy="12" r="1" />
          <circle cx="15" cy="19" r="1" />
        </>
      );
    case "dots":
      return (
        <>
          <circle cx="5" cy="12" r="1.5" />
          <circle cx="12" cy="12" r="1.5" />
          <circle cx="19" cy="12" r="1.5" />
        </>
      );
    case "monitor":
      return (
        <>
          <rect x="2" y="3" width="20" height="14" rx="2" />
          <path d="M8 21h8M12 17v4" />
        </>
      );
    case "smartphone":
      return (
        <>
          <rect x="5" y="2" width="14" height="20" rx="2" />
          <path d="M12 18h.01" />
        </>
      );
    case "undo":
      return <path d="M3 7v6h6M21 17a9 9 0 0 0-15-6.7L3 13" />;
    case "redo":
      return <path d="M21 7v6h-6M3 17a9 9 0 0 1 15-6.7L21 13" />;
    case "share":
      return (
        <>
          <circle cx="18" cy="5" r="3" />
          <circle cx="6" cy="12" r="3" />
          <circle cx="18" cy="19" r="3" />
          <path d="m8.59 13.51 6.83 3.98M15.41 6.51l-6.82 3.98" />
        </>
      );
    case "zap":
      return <path d="M13 2 3 14h9l-1 8 10-12h-9l1-8Z" />;
    case "database":
      return (
        <>
          <ellipse cx="12" cy="5" rx="9" ry="3" />
          <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5M3 12c0 1.66 4 3 9 3s9-1.34 9-3" />
        </>
      );
    case "chevron":
      return <path d="m9 18 6-6-6-6" />;
    case "star":
      return <path d="m12 2 3.1 6.3 6.9 1-5 4.9 1.2 6.9L12 17.8 5.8 21l1.2-6.9-5-4.9 6.9-1L12 2Z" />;
    default:
      return null;
  }
}

export function Icon({ name, size = 16 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {pathsFor(name)}
    </svg>
  );
}
