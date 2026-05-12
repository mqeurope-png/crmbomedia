import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "CRMBO Media CRM",
  description: "CRM central para contactos, integraciones y trazabilidad RGPD.",
};

// Every page in the CRM is a "use client" component that fetches data
// through the API on mount. Letting Next.js statically prerender the
// initial HTML produces stale shells that nginx happily caches for a
// year (`cache-control: s-maxage=31536000`), so a freshly-imported
// dataset never appears on the dashboard until the next deploy.
// Forcing the root segment to be dynamic propagates to every route and
// keeps the served HTML in lock-step with the database without
// per-page route segment config. See
// `docs/development.md` § "Renderizado dinámico de pantallas" for the
// rationale and the diagnostic command (`x-nextjs-prerender` header).
export const dynamic = "force-dynamic";

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
